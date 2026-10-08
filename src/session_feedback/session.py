"""FeedbackSession：模块六门面——首轮编排 + 反馈闭环 + Undo/Redo。

串起五个上游模块（§4.6/§9）：

    start:  VU 分析 → parse(initial) → 增量查询 → plan → resolve_assets
            → materialize → executor.apply → 落盘
    reply:  meta 拦截 → project/video view → parse(patch) → patch_flow
            归一化 → apply_patch(intent) → replan → 增量素材 →
            materialize → apply → 快照 + 落盘
    undo:   恢复快照三件套 + executor.apply(旧 resolved) —— diff 自动
            反向（git-revert 式前进，revision 单调递增）
"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from asset_manager import AssetManager
from edit_executor import EditingExecutor
from edit_executor.models import ExecutionOptions
from editing_planner import EditingPlanner
from editing_planner.models import (
    LogicalEditingPlan,
    ResolvedEditingPlan,
)
from gesture_intent.models import (
    EditingIntent,
    IntentParserInput,
    RequestType,
    SemanticProjectView,
    UnresolvedReference,
    model_dump,
    model_validate,
)
from gesture_intent.parser import IntentParser
from gesture_intent.state import IntentStateManager
from gesture_intent.store import IntentStore
from video_understanding import VideoUnderstanding
from video_understanding.events.router import normalize_query, query_id_for

from .bridge import build_project_view
from .meta import MetaCommand, classify, resolve_switch_target
from .models import (
    FeedbackResult,
    FeedbackStatus,
    SessionState,
    TurnRecord,
)
from .patch_flow import normalize_patch, patch_effectively_empty
from .store import SessionStore


class FeedbackSession:
    """一个 project 一条会话。workspace_root 下与上游模块共享 <pid>/ 目录。"""

    def __init__(
        self,
        workspace_root: Union[str, Path],
        project_id: str,
        *,
        vu: Optional[VideoUnderstanding] = None,
        parser: Optional[IntentParser] = None,
        planner: Optional[EditingPlanner] = None,
        asset_manager: Optional[AssetManager] = None,
        executor: Optional[EditingExecutor] = None,
        tool_capabilities: Optional[Any] = None,
        accessibility_profile: Optional[Any] = None,
    ):
        self.workspace_root = Path(workspace_root)
        self.project_id = project_id
        self.project_dir = self.workspace_root / project_id
        self.tool_capabilities = tool_capabilities
        self.accessibility_profile = accessibility_profile

        self.store = SessionStore(self.workspace_root, project_id)
        self.state = self.store.load_state()
        self.vu = vu or VideoUnderstanding(workspace_dir=self.project_dir)
        self._owns_vu = vu is None
        self.parser = parser or IntentParser()
        self.planner = planner or EditingPlanner()
        self.asset_mgr = asset_manager or AssetManager(
            str(self.workspace_root), project_id=project_id
        )
        self.executor = executor or EditingExecutor(self.workspace_root)
        self.intent_store = IntentStore(self.store.intent_dir)
        self._intent_state = IntentStateManager()

    # ------------------------------------------------------------------
    # 首轮编排
    # ------------------------------------------------------------------

    def start(
        self,
        video: Any,
        utterance: str,
        *,
        source_uri: Optional[str] = None,
        dry_run: bool = False,
    ) -> FeedbackResult:
        if self.state is not None:
            raise RuntimeError(
                f"session for project '{self.project_id}' already started; use reply()"
            )
        state = SessionState(project_id=self.project_id, turn=0)
        try:
            # VU 分析（CPU/CV）与首轮 parse（LLM 网络）互不依赖——
            # parse 输入只有 utterance，subject_profile 在 join 后才取
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                analysis_fut = pool.submit(self.vu.analyze_video, video)
                output = self.parser.parse(
                    IntentParserInput(user_utterance=utterance)
                )
                analysis = analysis_fut.result()
            state.video_id = getattr(analysis.video, "video_id", "") or ""
            state.source_uri = source_uri or _source_uri_of(video, analysis)
            if self.accessibility_profile is None:
                # analyze 后 VU 推断的 subject_profile（坐姿/低幅度）默认进 planner
                self.accessibility_profile = getattr(
                    getattr(self.vu, "state", None), "subject_profile", None
                )
            intent = output.editing_intent or EditingIntent()
            self._absorb_queries(state, output.required_video_queries)
            view = self._semantic_view(state)
            plan_input: Dict[str, Any] = {
                "editing_intent": intent,
                "semantic_view": view,
                "accessibility_profile": self.accessibility_profile,
            }
            if self.tool_capabilities is not None:
                plan_input["tool_capabilities"] = self.tool_capabilities
            plan = self.planner.plan(plan_input)
            assets = self.asset_mgr.resolve_assets(plan.asset_requests)
            resolved = self.planner.materialize(
                self._copy_plan(plan),
                bindings=assets.bindings,
                semantic_view=view,
                tool_capabilities=self.tool_capabilities,
            )
            result = self.executor.apply(
                self._executor_input(resolved, state, dry_run=dry_run)
            )
            state.turn = 1
            state.last_revision = result.revision
            self.state = state
            self._persist_all(intent, plan, resolved, state)
            self.intent_store.save_state(intent, utterances=[utterance])
            self.intent_store.append_history(utterance, output)
            status = _status_of_execution(result, dry_run=dry_run)
            self._record_turn(state, utterance, status, output, result)
            return self._result(
                status, utterance, output=output, execution=result
            )
        except Exception as exc:
            result = self._result(FeedbackStatus.failed, utterance, message=str(exc))
            self._record_turn(state, utterance, FeedbackStatus.failed, None, None)
            return result

    # ------------------------------------------------------------------
    # 反馈轮
    # ------------------------------------------------------------------

    def reply(self, utterance: str, *, dry_run: bool = False) -> FeedbackResult:
        state = self._require_state()
        command = classify(utterance)
        if command == MetaCommand.undo:
            return self.undo()
        if command == MetaCommand.redo:
            return self.redo()
        if command == MetaCommand.switch:
            return self._switch(utterance, dry_run=dry_run)
        try:
            return self._reply_edit(utterance, dry_run=dry_run)
        except Exception as exc:
            self._record_turn(state, utterance, FeedbackStatus.failed, None, None)
            return self._result(FeedbackStatus.failed, utterance, message=str(exc))

    def _reply_edit(self, utterance: str, *, dry_run: bool) -> FeedbackResult:
        state = self.state
        intent = self._load_intent()
        plan = self._load_plan()
        resolved = self._load_resolved()

        project_view = build_project_view(
            self.executor.get_edit_view(self.project_id), resolved, intent
        )
        output = self.parser.parse(IntentParserInput(
            user_utterance=utterance,
            current_effective_intent=intent,
            semantic_project_view=project_view,
            semantic_video_view=self.vu.build_semantic_view(as_intent_view=True),
        ))
        patch = output.intent_patch
        if patch is None:
            return self._result(
                FeedbackStatus.failed, utterance, output=output,
                message="parser returned no intent_patch",
            )
        self._absorb_queries(state, output.required_video_queries)
        patch, unresolved, notes = normalize_patch(
            patch, output.unresolved, intent, plan.plan_items
        )
        if patch_effectively_empty(patch):
            status = (
                FeedbackStatus.needs_clarification
                if unresolved
                else FeedbackStatus.no_change
            )
            self.state.turn += 1
            self._persist_state(self.state)
            self._record_turn(
                state, utterance, status, output, None,
                unresolved=unresolved,
            )
            self.intent_store.append_history(utterance, output)
            return self._result(
                status, utterance, output=output, unresolved=unresolved,
                notes=notes,
            )

        new_intent = self._intent_state.apply_patch(intent, patch)
        view = self._semantic_view(state)
        plan_patch = self.planner.replan(
            plan, new_intent, intent_patch=patch,
            semantic_view=view, tool_capabilities=self.tool_capabilities,
        )
        new_plan = self.planner.apply_patch(plan, plan_patch)
        new_requests = (
            plan_patch.add_asset_requests + plan_patch.update_asset_requests
        )
        asset_status = ""
        if new_requests:
            assets = self.asset_mgr.resolve_assets(new_requests)
            asset_status = assets.status.value
        bindings = self._planner_bindings(new_plan)
        new_resolved = self.planner.materialize(
            self._copy_plan(new_plan),
            bindings=bindings,
            semantic_view=view,
            tool_capabilities=self.tool_capabilities,
        )
        result = self.executor.apply(
            self._executor_input(new_resolved, state, dry_run=dry_run)
        )

        status = _status_of_execution(result, dry_run=dry_run)
        if not dry_run and result.status in (
            "completed", "completed_noop", "needs_dependency",
        ):
            # 成功 apply 前快照旧态（git-revert 式 undo）
            self._push_undo_snapshot(state, intent, plan, resolved)
            state.redo_stack = []
            state.turn += 1
            state.last_revision = result.revision
            self._persist_all(new_intent, new_plan, new_resolved, state)
            self.intent_store.save_state(new_intent)
        self.intent_store.append_history(utterance, output)
        self._record_turn(
            state, utterance, status, output, result, unresolved=unresolved
        )
        return self._result(
            status, utterance, output=output, unresolved=unresolved,
            plan_patch=_patch_counts(plan_patch), asset_status=asset_status,
            execution=result, notes=notes,
        )

    # ------------------------------------------------------------------
    # "换一个" fast-path：候选池切换，不走 parse/replan
    # ------------------------------------------------------------------

    def _switch(self, utterance: str, *, dry_run: bool) -> FeedbackResult:
        state = self._require_state()
        try:
            intent = self._load_intent()
            plan = self._load_plan()
            resolved = self._load_resolved()
            project_view = build_project_view(
                self.executor.get_edit_view(self.project_id), resolved, intent
            )
            target, candidates = resolve_switch_target(
                utterance, project_view.objects
            )
            if target is None:
                unresolved = [UnresolvedReference(
                    type="object_reference",
                    raw=utterance,
                    candidates=[c.id for c in candidates],
                    confidence=0.4,
                    reason="switch target ambiguous" if candidates else "no switchable object",
                )]
                self._record_turn(
                    state, utterance, FeedbackStatus.needs_clarification,
                    None, None, unresolved=unresolved,
                )
                return self._result(
                    FeedbackStatus.needs_clarification, utterance,
                    unresolved=unresolved,
                )
            item = next(
                (i for i in plan.plan_items
                 if i.plan_item_uid == target.id),
                None,
            )
            ref = item.asset_request_ref if item is not None else None
            binding = (
                self.asset_mgr.switch_alternative(ref) if ref else None
            )
            if binding is None:
                unresolved = [UnresolvedReference(
                    type="asset_alternative",
                    raw=utterance,
                    candidates=[target.id],
                    confidence=0.0,
                    reason="no alternative candidate for the object's asset request",
                )]
                self._record_turn(
                    state, utterance, FeedbackStatus.needs_clarification,
                    None, None, unresolved=unresolved,
                )
                return self._result(
                    FeedbackStatus.needs_clarification, utterance,
                    unresolved=unresolved,
                )
            view = self._semantic_view(state)
            new_resolved = self.planner.materialize(
                self._copy_plan(plan),
                bindings=self._planner_bindings(plan),
                semantic_view=view,
                tool_capabilities=self.tool_capabilities,
            )
            result = self.executor.apply(
                self._executor_input(new_resolved, state, dry_run=dry_run)
            )
            ok = result.status in ("completed", "completed_noop")
            if dry_run:
                status = (
                    FeedbackStatus.preview
                    if result.status == "dry_run"
                    else FeedbackStatus.failed
                )
            else:
                status = (
                    FeedbackStatus.switched if ok else FeedbackStatus.failed
                )
            if not dry_run and ok:
                self._push_undo_snapshot(state, intent, plan, resolved)
                state.redo_stack = []
                state.turn += 1
                state.last_revision = result.revision
                self._persist_all(intent, plan, new_resolved, state)
            self._record_turn(state, utterance, status, None, result)
            return self._result(
                status, utterance, execution=result,
                message=(
                    "" if ok or result.status == "dry_run"
                    else "; ".join(
                        e.message for e in (result.errors or [])
                    ) or str(result.status)
                ),
            )
        except Exception as exc:
            self._record_turn(state, utterance, FeedbackStatus.failed, None, None)
            return self._result(FeedbackStatus.failed, utterance, message=str(exc))

    # ------------------------------------------------------------------
    # Undo / Redo（turn 级快照，git-revert 式前进）
    # ------------------------------------------------------------------

    def undo(self) -> FeedbackResult:
        state = self._require_state()
        return self._rewind(state.undo_stack, "redo")

    def redo(self) -> FeedbackResult:
        state = self._require_state()
        return self._rewind(state.redo_stack, "undo")

    def _rewind(self, pop: List[str], other: str) -> FeedbackResult:
        state = self._require_state()
        if not pop:
            status = (
                FeedbackStatus.nothing_to_undo
                if other == "redo"
                else FeedbackStatus.nothing_to_redo
            )
            return self._result(status, "")
        name = pop[-1]
        intent = self._load_intent()
        plan = self._load_plan()
        resolved = self._load_resolved()
        old_intent, old_plan, old_resolved = self.store.load_snapshot(name)
        result = self.executor.apply(
            self._executor_input(old_resolved, state)
        )
        # 当前态进另一栈；弹出的快照生效
        pop.pop()
        other_name = f"snap_{state.next_snap:04d}"
        state.next_snap += 1
        self.store.save_snapshot(other_name, intent, plan, resolved)
        (state.redo_stack if other == "redo" else state.undo_stack).append(
            other_name
        )
        state.turn += 1
        state.last_revision = result.revision
        self._persist_all(old_intent, old_plan, old_resolved, state)
        self.intent_store.save_state(old_intent)
        status = (
            FeedbackStatus.undone if other == "redo" else FeedbackStatus.redone
        )
        self._record_turn(
            state, f"[{status.value}] {name}", status, None, result
        )
        return self._result(status, "", execution=result)

    # ------------------------------------------------------------------
    # 只读接口
    # ------------------------------------------------------------------

    def get_view(self):
        return self.executor.get_edit_view(self.project_id)

    def get_state(self) -> Dict[str, Any]:
        state = self.state
        return {
            "project_id": self.project_id,
            "started": state is not None,
            "turn": state.turn if state else 0,
            "revision": self.executor.get_revision(self.project_id),
            "undo_depth": len(state.undo_stack) if state else 0,
            "redo_depth": len(state.redo_stack) if state else 0,
            "queries_seen": len(state.queries_seen) if state else 0,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_state(self) -> SessionState:
        if self.state is None:
            # 会话恢复：磁盘有 state → 补载（VU 恢复后 _video_input 缺失，
            # on-demand 手部补跑不可用——已知限制 P10）
            self.state = self.store.load_state()
        if self.state is None:
            raise RuntimeError(
                f"session for project '{self.project_id}' not started; call start() first"
            )
        if self._owns_vu and self.vu.state is None:
            # VU 恢复后 _video_input 缺失，on-demand 手部补跑不可用（P10）
            state_file = self.project_dir / "semantic_video_state.json"
            if state_file.exists():
                self.vu = VideoUnderstanding.load(self.project_dir)
        return self.state

    def _ensure_dirs(self) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.plan_dir.mkdir(parents=True, exist_ok=True)

    def _semantic_view(self, state: SessionState):
        queries = state.queries_seen or None
        return self.vu.build_semantic_view({"queries": queries})

    def _absorb_queries(self, state: SessionState, queries: Any) -> None:
        """累积下发过模块二的查询（去重按 query_id）。"""
        if not queries:
            return
        self.vu.resolve_queries(queries)
        seen = {
            query_id_for(normalize_query(q))
            for q in state.queries_seen
        }
        for q in queries:
            data = normalize_query(model_dump(q) if not isinstance(q, dict) else q)
            if query_id_for(data) in seen:
                continue
            seen.add(query_id_for(data))
            state.queries_seen.append(data)

    def _planner_bindings(self, plan: LogicalEditingPlan):
        """按 plan.asset_requests 重建模块三契约 bindings（不做快照——
        binding_mgr.active_for + search_history 可随时重建）。"""
        out = []
        for req in plan.asset_requests:
            record = self.asset_mgr.binding_mgr.active_for(req.request_uid)
            if record is None:
                continue
            asset = self.asset_mgr.registry.get(record.asset_uid)
            out.append(self.asset_mgr.binding_mgr.to_planner(record, asset))
        return out

    def _executor_input(self, resolved, state: SessionState, *, dry_run=False):
        return self.executor.build_input(
            self.project_id,
            resolved,
            source_uri=state.source_uri or None,
            execution_options=ExecutionOptions(dry_run=dry_run),
        )

    def _push_undo_snapshot(
        self,
        state: SessionState,
        intent: EditingIntent,
        plan: LogicalEditingPlan,
        resolved: ResolvedEditingPlan,
    ) -> None:
        name = f"snap_{state.next_snap:04d}"
        state.next_snap += 1
        self.store.save_snapshot(name, intent, plan, resolved)
        state.undo_stack.append(name)

    @staticmethod
    def _copy_plan(plan: LogicalEditingPlan) -> LogicalEditingPlan:
        """materialize/apply_patch 原地改 plan——快照与落盘要的是纯净逻辑计划。"""
        return model_validate(LogicalEditingPlan, model_dump(plan))

    def _load_intent(self) -> EditingIntent:
        return self.intent_store.load_current_intent()

    def _load_plan(self) -> LogicalEditingPlan:
        return EditingPlanner.load(self.store.plan_dir)

    def _load_resolved(self) -> ResolvedEditingPlan:
        return self.store.load_resolved()

    def _persist_state(self, state: SessionState) -> None:
        self._ensure_dirs()
        self.store.save_state(state)

    def _persist_all(
        self,
        intent: EditingIntent,
        plan: LogicalEditingPlan,
        resolved: ResolvedEditingPlan,
        state: SessionState,
    ) -> None:
        self._ensure_dirs()
        self.intent_store.save_state(intent)
        self.planner.save(plan, self.store.plan_dir)
        self.store.save_resolved(resolved)
        self.store.save_state(state)

    def _record_turn(
        self,
        state: SessionState,
        utterance: str,
        status: FeedbackStatus,
        output: Any,
        execution: Any,
        *,
        unresolved: Optional[List[UnresolvedReference]] = None,
    ) -> None:
        self.store.append_turn(TurnRecord(
            turn=state.turn,
            utterance=utterance,
            status=status.value if isinstance(status, FeedbackStatus) else status,
            request_type=(
                output.request_type.value
                if output is not None and getattr(output, "request_type", None)
                else ""
            ),
            parser_mode=getattr(output, "parser_mode", "") or "",
            base_revision=getattr(execution, "base_revision", 0) or 0,
            revision=getattr(execution, "revision", 0) or 0,
            unresolved=[model_dump(u) for u in (unresolved or [])],
            snapshot=state.undo_stack[-1] if state.undo_stack else "",
        ))

    def _result(
        self,
        status: FeedbackStatus,
        utterance: str,
        *,
        output: Any = None,
        unresolved: Optional[List[UnresolvedReference]] = None,
        plan_patch: Optional[Dict[str, int]] = None,
        asset_status: str = "",
        execution: Any = None,
        notes: Optional[List[Any]] = None,
        message: str = "",
    ) -> FeedbackResult:
        state = self.state
        edit_view = None
        if status in (
            FeedbackStatus.applied, FeedbackStatus.switched,
            FeedbackStatus.undone, FeedbackStatus.redone,
            FeedbackStatus.preview, FeedbackStatus.no_change,
        ):
            try:
                edit_view = self.executor.get_edit_view(self.project_id)
            except Exception:
                edit_view = getattr(execution, "edit_view", None)
        return FeedbackResult(
            status=status,
            turn=state.turn if state else 0,
            utterance=utterance,
            message=message,
            request_type=(
                output.request_type.value
                if output is not None and getattr(output, "request_type", None)
                else ""
            ),
            parser_mode=getattr(output, "parser_mode", "") or "",
            unresolved=list(
                unresolved
                if unresolved is not None
                else (output.unresolved if output is not None else [])
            ),
            plan_patch=plan_patch or {},
            asset_status=asset_status,
            execution=execution,
            edit_view=edit_view,
            revision=state.last_revision if state else 0,
            dependencies=_collect_dependencies(output, execution),
            notes=[n.detail for n in (notes or [])],
            warnings=list(getattr(execution, "warnings", []) or []),
        )


def _source_uri_of(video: Any, analysis: Any) -> str:
    if isinstance(video, (str, Path)):
        return str(video)
    if isinstance(video, dict):
        return str(video.get("path") or video.get("source_uri") or "")
    return str(getattr(getattr(analysis, "video", None), "source_uri", "") or "")


def _status_of_execution(result: Any, *, dry_run: bool) -> FeedbackStatus:
    status = getattr(result, "status", "") or ""
    if dry_run or status == "dry_run":
        return FeedbackStatus.preview
    if status in ("completed", "completed_noop"):
        return FeedbackStatus.applied
    if status in ("needs_dependency",):
        return FeedbackStatus.needs_clarification
    return FeedbackStatus.failed


def _patch_counts(patch: Any) -> Dict[str, int]:
    return {
        "add_items": len(patch.add_plan_items),
        "update_items": len(patch.update_plan_items),
        "remove_items": len(patch.remove_plan_item_uids),
        "add_asset_requests": len(patch.add_asset_requests),
        "update_asset_requests": len(patch.update_asset_requests),
    }


def _collect_dependencies(output: Any, execution: Any) -> List[Dict[str, Any]]:
    deps: List[Dict[str, Any]] = []
    for dep in getattr(execution, "dependencies", []) or []:
        deps.append(model_dump(dep) if not isinstance(dep, dict) else dep)
    return deps
