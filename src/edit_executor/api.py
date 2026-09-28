"""EditingExecutor：模块五对外 facade（§52, §83-§96）。

    executor = EditingExecutor(workspace_root="workspace")
    executor_input = executor.build_input(project_id, resolved_plan)
    result = executor.apply(executor_input)

apply() 全链（§63 状态机）：
    recover → lock → preflight → compile → core verify → diff → patch
    → run dir → candidate revision → backend.execute → backend.verify
    → save → commit → history
dry_run 不产生任何执行号 / 状态 / history / lock（§89）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from editing_planner.models import ResolvedEditingPlan
from gesture_intent.models import model_dump

from . import __version__
from .backends.base import BackendOperationError, ExecutorBackend
from .backends.ffmpeg import FFmpegBackend
from .backends.memory import MemoryBackend
from .compiler.fingerprint import graph_fingerprint
from .compiler.graph import compile_graph
from .context.builder import build_executor_input
from .diff.engine import GraphDiff, diff_graphs
from .dsl.builder import build_patch
from .dsl.models import (
    BackendVerificationReport,
    ExportResult,
    OperationStatus,
)
from .models import (
    BackendConfig,
    DesiredProjectGraph,
    ExecutionFailure,
    ExecutionOptions,
    ExecutionResult,
    ExecutionStatus,
    ExecutorInput,
    ExportSpec,
    ExportStatus,
    FailureCategory,
    HistoryEntry,
    JournalEntry,
    ProjectEditView,
    ProjectExecutionState,
    _stable_uid,
)
from .state.manager import ExecutionStateManager
from .state.revision import commit_candidate, write_candidate
from .state.store import ExecutionStore
from .validation.preflight import run_preflight
from .validation.verifier import verify_desired_graph

_EARLY_STATUS = {
    "blocked": ExecutionStatus.blocked.value,
    "needs_dependency": ExecutionStatus.needs_dependency.value,
    "capability_mismatch": ExecutionStatus.capability_mismatch.value,
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class EditingExecutor:
    """模块五入口（§52）：workspace_root 下每项目一份 execution/ 状态。"""

    def __init__(
        self,
        workspace_root: Union[str, Path],
        backend: Optional[ExecutorBackend] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self._backend = backend

    # ------------------------------------------------------------------
    # §96 输入组装
    # ------------------------------------------------------------------

    def build_input(
        self,
        project_id: str,
        resolved_plan: ResolvedEditingPlan,
        **kwargs: Any,
    ) -> ExecutorInput:
        return build_executor_input(
            self.workspace_root, project_id, resolved_plan, **kwargs
        )

    # ------------------------------------------------------------------
    # 只读派生（不执行）
    # ------------------------------------------------------------------

    def compile(self, executor_input: ExecutorInput) -> DesiredProjectGraph:
        """仅编译 Desired Graph（调试/inspect 用，§52）。"""
        return compile_graph(executor_input)

    def get_diff(self, executor_input: ExecutorInput) -> GraphDiff:
        """desired vs committed 的 GraphDiff（§96）。"""
        graph = compile_graph(executor_input)
        base = self._manager(executor_input.project_id).current_graph()
        return diff_graphs(base, graph)

    def get_revision(self, project_id: str) -> int:
        return self._manager(project_id).current_revision()

    def get_state(self, project_id: str) -> Optional[ProjectExecutionState]:
        return self._manager(project_id).current()

    def get_edit_view(self, project_id: str) -> ProjectEditView:
        """模块六主接口（§75-§76）。"""
        return self._manager(project_id).build_edit_view()

    def inspect(self, project_id: str) -> Dict[str, Any]:
        """execution/ 状态摘要（§96）。"""
        store = ExecutionStore(str(self.workspace_root), project_id)
        manager = ExecutionStateManager(store)
        state = manager.current()
        graph = manager.current_graph()
        runs = (
            sorted(p.name for p in store.runs_dir.iterdir())
            if store.runs_dir.exists()
            else []
        )
        recovery = manager.recover()
        return {
            "project_id": project_id,
            "revision": manager.current_revision(),
            "status": state.status if state is not None else "empty",
            "backend_id": state.backend_id if state is not None else "",
            "objects": len(graph.objects) if graph is not None else 0,
            "tracks": len(graph.tracks) if graph is not None else 0,
            "media_refs": len(graph.media_refs) if graph is not None else 0,
            "total_duration": graph.total_duration if graph is not None else 0.0,
            "executions": runs,
            "history": [model_dump(e) for e in manager.history()],
            "recovery": {
                "adopted_revision": recovery.adopted_revision,
                "quarantined": recovery.quarantined,
                "notes": recovery.notes,
            },
        }

    # ------------------------------------------------------------------
    # apply（§52/§63/§83）
    # ------------------------------------------------------------------

    def apply(self, executor_input: ExecutorInput) -> ExecutionResult:
        options = executor_input.execution_options
        project_id = executor_input.project_id
        store = ExecutionStore(str(self.workspace_root), project_id)
        manager = ExecutionStateManager(store)
        backend = self._resolve_backend(executor_input, store)
        manifest = (
            backend.describe_capabilities() if backend is not None else None
        )
        warnings: List[str] = []

        # dry_run：零副作用（§89），不分配 exe / 不写状态 / 不碰锁
        if options.dry_run:
            return self._dry_run(executor_input, backend, manifest, warnings)

        store.ensure_dirs()
        recovery = manager.recover()
        warnings.extend(recovery.notes)

        execution_uid = manager.next_execution_uid()
        if not manager.lock(execution_uid, force=options.force):
            self._history(
                manager,
                execution_uid,
                status=ExecutionStatus.locked.value,
            )
            return ExecutionResult(
                execution_uid=execution_uid,
                status=ExecutionStatus.locked.value,
                warnings=warnings + ["project.lock 被占用"],
            )
        try:
            return self._execute_pipeline(
                executor_input,
                store,
                manager,
                backend,
                manifest,
                execution_uid,
                warnings,
            )
        finally:
            manager.unlock(execution_uid)

    def _execute_pipeline(
        self,
        executor_input: ExecutorInput,
        store: ExecutionStore,
        manager: ExecutionStateManager,
        backend: Optional[ExecutorBackend],
        manifest,
        execution_uid: str,
        warnings: List[str],
    ) -> ExecutionResult:
        plan = executor_input.resolved_plan
        base_revision = manager.current_revision()

        def early(status: str, **kwargs: Any) -> ExecutionResult:
            self._history(
                manager,
                execution_uid,
                base=base_revision,
                target=base_revision,
                status=status,
            )
            return ExecutionResult(
                execution_uid=execution_uid,
                status=status,
                base_revision=base_revision,
                revision=base_revision,
                warnings=list(warnings),
                **kwargs,
            )

        # ---- preflight（§85-§87）----
        if backend is None or manifest is None:
            return early(
                ExecutionStatus.failed.value,
                errors=[
                    self._failure(
                        execution_uid,
                        FailureCategory.capability,
                        "unsupported_backend",
                        f"backend_id={executor_input.backend_config.backend_id} "
                        "无对应实现",
                    )
                ],
            )
        pre = run_preflight(executor_input, manifest)
        warnings.extend(pre.warnings)
        if pre.status != "ok":
            return early(
                _EARLY_STATUS.get(pre.status, ExecutionStatus.preflight_failed.value),
                dependencies=pre.dependencies,
                errors=pre.failures,
            )

        # ---- compile + core verify（§21/§73）----
        graph = compile_graph(executor_input)
        warnings.extend(graph.warnings)
        verification = verify_desired_graph(
            graph, plan, executor_input.analysis_artifacts
        )
        if verification.status == "fail":
            return early(
                ExecutionStatus.failed.value,
                verification=verification,
                errors=[
                    self._failure(
                        execution_uid,
                        FailureCategory.validation,
                        issue.code,
                        issue.message,
                        affected=issue.object_uids,
                    )
                    for issue in verification.issues
                    if issue.severity == "hard"
                ],
            )

        # ---- diff → patch（§32/§35）----
        base_graph = manager.current_graph()
        graph_diff = diff_graphs(base_graph, graph)
        patch = build_patch(graph_diff, graph, executor_input, base_revision)
        self._write_run(store, execution_uid, graph, patch, graph_diff)

        # ---- 全 NOOP：completed_noop，revision 不变（§58）----
        s = patch.summary
        if not patch.operations or (s.created + s.updated + s.deleted) == 0:
            status = ExecutionStatus.completed_noop.value
            self._history(
                manager,
                execution_uid,
                base=base_revision,
                target=base_revision,
                summary=patch.summary,
                patch_uid=patch.patch_uid,
                status=status,
            )
            return ExecutionResult(
                execution_uid=execution_uid,
                status=status,
                base_revision=base_revision,
                revision=base_revision,
                patch=patch,
                patch_summary=patch.summary,
                object_changes=patch.summary.object_changes,
                verification=verification,
                warnings=warnings,
                project_status="active" if manager.current() else "unknown",
                active_asset_usage=(
                    manager.current().asset_usage_index
                    if manager.current() is not None
                    else {}
                ),
                edit_view=manager.build_edit_view(base_graph, base_revision),
            )

        # ---- candidate revision（§66-§68）----
        target_revision = patch.target_revision
        info = backend.backend_info()
        state = manager.build_state(
            graph,
            patch,
            target_revision,
            backend_id=info.backend_id,
            backend_version=info.backend_version,
            app_version=__version__,
            project_ref=None,
            graph_fingerprint=graph_fingerprint(graph),
        )
        rev_manifest = write_candidate(
            store, target_revision, execution_uid, patch.patch_uid, graph, state
        )

        # ---- backend.execute（§50）----
        session = backend.begin_session(
            {
                "project_id": executor_input.project_id,
                "revision": target_revision,
                "backend_id": info.backend_id,
            }
        )
        report = backend.execute(session, patch, graph)
        self._journal(manager, execution_uid, patch, report.op_results)
        op_failures = [
            r for r in report.op_results
            if r.status == OperationStatus.failed.value
        ]
        if report.error or op_failures:
            return early(
                ExecutionStatus.failed.value,
                patch=patch,
                patch_summary=patch.summary,
                object_changes=patch.summary.object_changes,
                errors=[
                    self._failure(
                        execution_uid,
                        FailureCategory.backend,
                        "op_failed",
                        r.error or report.error or "operation failed",
                        op_uid=r.operation_uid,
                    )
                    for r in op_failures
                ]
                or [
                    self._failure(
                        execution_uid,
                        FailureCategory.backend,
                        "backend_error",
                        report.error or "backend.execute 失败",
                    )
                ],
            )

        # ---- backend.verify（§51）----
        backend_verify = backend.verify(session, graph)
        if not backend_verify.ok:
            return early(
                ExecutionStatus.failed.value,
                patch=patch,
                patch_summary=patch.summary,
                errors=[
                    self._failure(
                        execution_uid,
                        FailureCategory.backend,
                        "backend_verify",
                        "; ".join(backend_verify.issues) or "verify 未通过",
                    )
                ],
            )

        # ---- stale 防御：锁内仍复核一次 ----
        if manager.current_revision() != base_revision:
            return early(
                ExecutionStatus.stale_patch.value,
                patch=patch,
                patch_summary=patch.summary,
            )

        # ---- save + promote（§68-§69）----
        try:
            project_ref = backend.save(session)
        except BackendOperationError as exc:
            return early(
                ExecutionStatus.failed.value,
                patch=patch,
                patch_summary=patch.summary,
                errors=[
                    self._failure(
                        execution_uid,
                        FailureCategory.backend,
                        "backend_save",
                        str(exc),
                    )
                ],
            )
        rev_manifest.backend_project_ref = project_ref
        state.project_ref = project_ref
        commit_candidate(store, rev_manifest, graph, state)

        # ---- export（可选，§74）----
        export_status = ExportStatus.not_requested.value
        if executor_input.export_spec is not None:
            export_result = backend.export(session, executor_input.export_spec)
            export_status = export_result.status

        self._history(
            manager,
            execution_uid,
            base=base_revision,
            target=target_revision,
            summary=patch.summary,
            patch_uid=patch.patch_uid,
            status=ExecutionStatus.completed.value,
        )
        return ExecutionResult(
            execution_uid=execution_uid,
            status=ExecutionStatus.completed.value,
            base_revision=base_revision,
            revision=target_revision,
            project_ref=project_ref,
            patch=patch,
            patch_summary=patch.summary,
            object_changes=patch.summary.object_changes,
            execution_state=state,
            edit_view=manager.build_edit_view(graph, target_revision),
            verification=verification,
            warnings=warnings,
            project_status="active",
            export_status=export_status,
            active_asset_usage=state.asset_usage_index,
        )

    def _dry_run(
        self,
        executor_input: ExecutorInput,
        backend: Optional[ExecutorBackend],
        manifest,
        warnings: List[str],
    ) -> ExecutionResult:
        """§89：编译 + diff + 返回 patch，零状态副作用。"""
        if backend is None or manifest is None:
            return ExecutionResult(
                status=ExecutionStatus.failed.value,
                errors=[
                    self._failure(
                        "",
                        FailureCategory.capability,
                        "unsupported_backend",
                        f"backend_id={executor_input.backend_config.backend_id} "
                        "无对应实现",
                    )
                ],
                warnings=warnings,
            )
        pre = run_preflight(executor_input, manifest)
        warnings.extend(pre.warnings)
        base_revision = self._manager(executor_input.project_id).current_revision()
        if pre.status != "ok":
            return ExecutionResult(
                status=_EARLY_STATUS.get(
                    pre.status, ExecutionStatus.preflight_failed.value
                ),
                base_revision=base_revision,
                revision=base_revision,
                dependencies=pre.dependencies,
                errors=pre.failures,
                warnings=warnings,
            )
        graph = compile_graph(executor_input)
        warnings.extend(graph.warnings)
        verification = verify_desired_graph(
            graph, executor_input.resolved_plan, executor_input.analysis_artifacts
        )
        base_graph = self._manager(executor_input.project_id).current_graph()
        graph_diff = diff_graphs(base_graph, graph)
        patch = build_patch(graph_diff, graph, executor_input, base_revision)
        return ExecutionResult(
            status=ExecutionStatus.dry_run.value,
            base_revision=base_revision,
            revision=base_revision,
            patch=patch,
            patch_summary=patch.summary,
            object_changes=patch.summary.object_changes,
            verification=verification,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # backend verify / export（§96）
    # ------------------------------------------------------------------

    def verify(self, project_id: str) -> BackendVerificationReport:
        """已提交工程 vs Backend 侧状态核对（§51/§96）。"""
        store = ExecutionStore(str(self.workspace_root), project_id)
        manager = ExecutionStateManager(store)
        graph = manager.current_graph()
        if graph is None:
            report = BackendVerificationReport(ok=False)
            report.issues.append("无已提交工程")
            return report
        backend = self._backend_for_project(project_id, store)
        session = backend.begin_session(
            {"project_id": project_id, "revision": manager.current_revision()}
        )
        return backend.verify(session, graph)

    def export(
        self,
        project_id: str,
        export_spec: Optional[ExportSpec] = None,
    ) -> ExportResult:
        """独立导出入口（§74）；MemoryBackend / FFmpegBackend 均 unsupported。"""
        store = ExecutionStore(str(self.workspace_root), project_id)
        backend = self._backend_for_project(project_id, store)
        session = backend.begin_session({"project_id": project_id})
        return backend.export(session, export_spec or ExportSpec())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _manager(self, project_id: str) -> ExecutionStateManager:
        return ExecutionStateManager(
            ExecutionStore(str(self.workspace_root), project_id)
        )

    def _resolve_backend(
        self, executor_input: ExecutorInput, store: ExecutionStore
    ) -> Optional[ExecutorBackend]:
        if self._backend is not None:
            return self._backend
        config = executor_input.backend_config
        return self._backend_for_id(
            config.backend_id, store, executor_input.project_id, config
        )

    def _backend_for_project(
        self, project_id: str, store: ExecutionStore
    ) -> ExecutorBackend:
        if self._backend is not None:
            return self._backend
        state = ExecutionStateManager(store).current()
        backend_id = state.backend_id if state is not None else "memory"
        backend = self._backend_for_id(backend_id, store, project_id)
        if backend is None:
            backend = MemoryBackend(
                sim_dir=store.backend_dir / "memory" / project_id,
                config=BackendConfig(backend_id="memory"),
            )
        return backend

    def _backend_for_id(
        self,
        backend_id: str,
        store: ExecutionStore,
        project_id: str,
        config: Optional[BackendConfig] = None,
    ) -> Optional[ExecutorBackend]:
        if backend_id == "memory":
            return MemoryBackend(
                sim_dir=store.backend_dir / "memory" / project_id,
                config=config or BackendConfig(backend_id="memory"),
                fail_plan=(config.options.get("fail_plan") if config else None),
            )
        if backend_id == "ffmpeg":
            return FFmpegBackend(config=config)
        return None

    def _write_run(
        self,
        store: ExecutionStore,
        execution_uid: str,
        graph: DesiredProjectGraph,
        patch,
        graph_diff: GraphDiff,
    ) -> None:
        run_dir = store.run_dir(execution_uid)
        run_dir.mkdir(parents=True, exist_ok=True)
        store.write_json(run_dir / "graph.json", model_dump(graph))
        store.write_json(run_dir / "patch.json", model_dump(patch))
        store.write_json(
            run_dir / "diff.json",
            {
                "first_run": graph_diff.first_run,
                "summary": model_dump(graph_diff.summary),
                "diffs": [
                    {
                        "uid": d.uid,
                        "action": d.action,
                        "changes": [
                            {"property": c.property, "op_type": c.op_type}
                            for c in d.changed_properties
                        ],
                    }
                    for d in graph_diff.diffs
                ],
            },
        )

    def _history(
        self,
        manager: ExecutionStateManager,
        execution_uid: str,
        *,
        base: int = 0,
        target: int = 0,
        summary=None,
        patch_uid: str = "",
        status: str,
    ) -> None:
        manager.append_history(
            HistoryEntry(
                execution_uid=execution_uid,
                patch_uid=patch_uid,
                base_revision=base,
                target_revision=target,
                created=summary.created if summary else 0,
                updated=summary.updated if summary else 0,
                deleted=summary.deleted if summary else 0,
                noop=summary.noop if summary else 0,
                status=status,
                created_at=_now(),
            )
        )

    def _journal(
        self,
        manager: ExecutionStateManager,
        execution_uid: str,
        patch,
        op_results,
    ) -> None:
        op_by_uid = {op.operation_uid: op for op in patch.operations}
        for result in op_results:
            op = op_by_uid.get(result.operation_uid)
            manager.append_journal(
                JournalEntry(
                    execution_uid=execution_uid,
                    operation_uid=result.operation_uid,
                    operation_type=op.op_type if op is not None else "",
                    target_uid=op.target_uid if op is not None else None,
                    status=result.status,
                    created_at=_now(),
                )
            )

    @staticmethod
    def _failure(
        execution_uid: str,
        category: FailureCategory,
        code: str,
        message: str,
        *,
        op_uid: Optional[str] = None,
        affected: Optional[List[str]] = None,
    ) -> ExecutionFailure:
        return ExecutionFailure(
            failure_uid=_stable_uid("fail", execution_uid, code, message),
            execution_uid=execution_uid,
            operation_uid=op_uid,
            category=category.value,
            code=code,
            message=message,
            affected_objects=list(affected or []),
        )
