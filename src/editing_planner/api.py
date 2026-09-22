"""EditingPlanner 门面（§9/§47）：plan → materialize → replan。

``plan()`` 内部流水线（§9）：

    输入归一化 → Accessibility Context → Global Strategy →
    Requirement Expander → Creative Planner（LLM 接缝，§19/§61）→
    PlanItem 骨架 → Temporal → Spatial → AssetRequest+去重 →
    Timeline Compose → Capability Resolver → Constraint Validator →
    Accessibility Validator → LogicalEditingPlan

Planner 永不调用模块二/模块四——缺数据走 PlannerDependencyRequest（§46），
素材结果经 materialize(bindings=...) 传入，轨迹数据经 spatial_tracks 传入。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gesture_intent.models import (
    Constraint,
    ConstraintLevel,
    EditingIntent,
    EventBoundRequirement,
    ExplicitOperation,
    IntentPatch,
    ObjectAction,
    ObjectType,
    OperationType,
    TemporalRelation,
    model_dump,
    model_validate,
)
from video_understanding.models import SemanticView

from . import __version__ as _PKG_VERSION
from .accessibility.context import resolve_accessibility_profile
from .accessibility.policies import follow_policy
from .accessibility.validator import validate_accessibility
from .assets.requests import AssetRequestBuilder, dedup_key_for
from .capability.resolver import (
    degradation_policy_for,
    resolve_capability,
)
from .expansion.requirements import (
    EventIndex,
    ExpandedRequirement,
    expand_requirements,
    missing_to_dependency,
)
from .llm.planner import (
    RuleBasedCreativePlanner,
    StructuredCreativePlanner,
    default_creative_planner,
)
from .materialize.materializer import materialize_plan
from .models import (
    AccessibilityPlanningProfile,
    AssetBinding,
    DurationSpec,
    EditingPlannerInput,
    FollowSpec,
    LogicalEditingPlan,
    PlanItem,
    PlanItemProvenance,
    PlanItemStatus,
    PlanOperation,
    PlanPatch,
    PlannerDependencyRequest,
    PlanProvenance,
    ResolvedEditingPlan,
    SpatialAnchor,
    SpatialAnchorType,
    SpatialRelation,
    ToolCapabilityProfile,
    UnfulfilledRequirement,
    ValidationReport,
    _stable_uid,
    short_hash,
)
from .replan.patch import apply_plan_patch, build_plan_patch
from .spatial.placement import (
    choose_position,
    collect_avoid_regions,
    direction_relation,
    single_hand_target,
)
from .spatial.resolver import (
    spatial_spec_for_event,
    spatial_spec_for_global,
)
from .strategy.creative import (
    build_creative_context,
    merge_creative_output,
    plan_creative,
    requirement_digest,
)
from .strategy.global_strategy import build_global_strategy
from .temporal.resolver import (
    freeze_duration,
    temporal_spec_for_event,
    temporal_spec_for_gap,
    temporal_spec_for_structure,
    temporal_spec_full_video,
)
from .temporal.timeline import compose_timeline, timeline_effect_for
from .validation.validator import assemble_report, validate_constraints

#: 模块一 OperationType → 模块三 PlanOperation
_OP_MAP: Dict[OperationType, PlanOperation] = {
    OperationType.freeze: PlanOperation.freeze,
    OperationType.scale_adjust: PlanOperation.scale_adjust,
    OperationType.position_adjust: PlanOperation.position_adjust,
    OperationType.volume_adjust: PlanOperation.volume_adjust,
    OperationType.trim: PlanOperation.trim,
    OperationType.split: PlanOperation.split,
    OperationType.remove: PlanOperation.remove_segment,
    OperationType.speed_adjust: PlanOperation.speed_adjust,
}

#: 事件绑定需求 object_type → 默认 PlanOperation
_EVENT_OP_MAP: Dict[ObjectType, PlanOperation] = {
    ObjectType.sticker: PlanOperation.add_overlay,
    ObjectType.overlay: PlanOperation.add_overlay,
    ObjectType.image: PlanOperation.add_overlay,
    ObjectType.text: PlanOperation.add_text,
    ObjectType.effect: PlanOperation.add_effect,
    ObjectType.music: PlanOperation.add_music,
    ObjectType.sound_effect: PlanOperation.add_sound_effect,
    ObjectType.background: PlanOperation.replace_background,
}

_ASSET_BACKED_OPS = {
    PlanOperation.add_overlay,
    PlanOperation.add_music,
    PlanOperation.replace_music,
    PlanOperation.add_sound_effect,
    PlanOperation.replace_background,
    PlanOperation.track_overlay,
}


class EditingPlanner:
    """模块三门面。所有入口接受模型或 dict（与模块二 api 同惯例）。"""

    def __init__(self, *, creative_planner: Optional[StructuredCreativePlanner] = None):
        self.creative_planner = creative_planner or default_creative_planner()
        self.rule_fallback = RuleBasedCreativePlanner()

    # ------------------------------------------------------------------
    # §47 第一阶段：plan()
    # ------------------------------------------------------------------

    def plan(self, input: Any) -> LogicalEditingPlan:
        input = self._normalize_input(input)
        intent = input.editing_intent
        view = input.semantic_view
        options = input.planner_context.options
        warnings: List[str] = []

        profile = resolve_accessibility_profile(input.accessibility_profile, view)
        event_index = EventIndex(view)
        strategy = build_global_strategy(intent, profile, dict(view.video or {}))

        # ---- Requirement Expander（§14-17）----
        expanded = expand_requirements(intent.event_bound_requirements, event_index)
        dependencies: List[PlannerDependencyRequest] = []
        unresolved: List[UnfulfilledRequirement] = []
        dep_seq = 0
        for e in expanded:
            if e.missing_reason is None:
                if e.skipped_uncertain:
                    warnings.append(
                        f"{e.requirement.id}: 跳过 {e.skipped_uncertain} 个 uncertain 事件"
                    )
                continue
            dep_seq += 1
            dep, unfulfilled, note = missing_to_dependency(e, dep_seq)
            if dep is not None:
                dependencies.append(dep)
            if unfulfilled is not None:
                unresolved.append(unfulfilled)
            if note:
                warnings.append(note)

        # ---- Creative Planner（§19：唯一 LLM 环节）----
        digests = self._requirement_digests(intent)
        context = build_creative_context(input, profile, digests)
        directives, strategy, planner_mode, fallback_reason = plan_creative(
            self.creative_planner, self.rule_fallback, context, strategy
        )

        # ---- PlanItem 骨架 + Temporal + Spatial ----
        items: List[PlanItem] = []
        protected = set(
            (intent.global_intent.autonomy.protected_requirements if intent.global_intent.autonomy else [])
        )
        has_music = any(
            r.object_type == ObjectType.music for r in intent.object_requirements
        ) or bool(view.audio_summary)

        items += self._items_for_object_requirements(
            intent, profile, directives, protected, options
        )
        items += self._items_for_event_requirements(
            expanded, profile, intent.constraints, directives, has_music,
            protected, warnings
        )
        op_items, op_warnings = self._items_for_operations(
            intent.explicit_operations, items, event_index, profile, has_music
        )
        items += op_items
        warnings += op_warnings

        # constraint_refs：适用约束挂到相关项上
        self._attach_constraints(items, intent.constraints)

        # plan 阶段先跑一次放置：决定 relation/fallback，供校验与 provenance
        item_positions = self._resolve_positions(items, event_index, profile, options, warnings)

        # ---- AssetRequest + 去重（§34-36）----
        asset_builder = AssetRequestBuilder(
            style_context={
                "visual_language": strategy.visual_language,
                "palette": strategy.consistency_strategy.get("palette", []),
            }
        )
        item_keys: Dict[str, str] = {}
        for item in items:
            req = self._asset_request_for(item, intent, asset_builder)
            if req is not None:
                item_keys[item.plan_item_uid] = req.dedup_key
                item.parameters["needs_asset"] = True
        asset_requests = asset_builder.requests()
        by_key = {r.dedup_key: r.request_uid for r in asset_requests}
        for uid, key in item_keys.items():
            item = next(i for i in items if i.plan_item_uid == uid)
            item.asset_request_ref = by_key[key]

        # ---- Timeline Compose（§38-39）----
        source_duration = float((view.video or {}).get("duration") or 0.0)
        timeline = compose_timeline(
            [i for i in items if i.status not in (PlanItemStatus.unsupported,)],
            source_duration,
        )

        # ---- Capability Resolver（§40-42）----
        for item in items:
            if item.status != PlanItemStatus.planned:
                continue
            item, dep, cap_warnings = resolve_capability(
                item, input.tool_capabilities, profile, dep_seq=dep_seq + 1
            )
            if dep is not None:
                dependencies.append(dep)
                dep_seq += 1
            warnings += cap_warnings

        # ---- 组装 LogicalEditingPlan ----
        intent_hash = short_hash(model_dump(intent))
        view_hash = short_hash(model_dump(view))
        video = dict(view.video or {})
        plan = LogicalEditingPlan(
            plan_uid=_stable_uid("plan", video.get("video_id"), intent_hash, view_hash),
            version=1,
            provenance=PlanProvenance(
                video_id=str(video.get("video_id") or ""),
                video_version=str(video.get("version") or ""),
                semantic_state_version=getattr(view, "state_version", None)
                or (view.get("state_version") if isinstance(view, dict) else None),
                semantic_view_hash=view_hash,
                intent_hash=intent_hash,
                planner_version=_PKG_VERSION,
                planner_mode=planner_mode,
                fallback_reason=fallback_reason,
            ),
            global_strategy=strategy,
            accessibility_profile=profile,
            plan_items=items,
            asset_requests=asset_requests,
            timeline_structure=timeline,
            dependency_requests=dependencies,
            warnings=warnings,
            unresolved=unresolved,
        )

        # ---- 校验（§43-45）----
        levels = self._requirement_levels(intent)
        sub_reports = validate_constraints(plan, intent.constraints, event_index, levels)
        hard_face = any(
            c.type == "avoid_overlap" and c.reference == "face"
            and c.constraint_level == ConstraintLevel.hard
            for c in intent.constraints
        )
        acc_issues = validate_accessibility(
            plan, profile, event_index, item_positions, hard_avoid_face=hard_face
        )
        plan.validation = assemble_report(plan, sub_reports, acc_issues)
        return plan

    # ------------------------------------------------------------------
    # §47 第二阶段：materialize()
    # ------------------------------------------------------------------

    def materialize(
        self,
        plan: Any,
        bindings: Optional[Any] = None,
        semantic_view: Optional[Any] = None,
        tool_capabilities: Optional[Any] = None,
        spatial_tracks: Optional[Dict[str, Any]] = None,
    ) -> ResolvedEditingPlan:
        plan = self._normalize_plan(plan)
        binding_list = self._normalize_bindings(bindings)
        view = self._normalize_view(semantic_view)
        caps = (
            model_validate(ToolCapabilityProfile, tool_capabilities)
            if isinstance(tool_capabilities, dict)
            else tool_capabilities or ToolCapabilityProfile()
        )
        return materialize_plan(
            plan,
            binding_list,
            view,
            caps,
            spatial_tracks=spatial_tracks,
        )

    # ------------------------------------------------------------------
    # §52 局部重规划
    # ------------------------------------------------------------------

    def replan(
        self,
        existing_plan: Any,
        editing_intent: Any,
        intent_patch: Optional[Any] = None,
        semantic_view: Optional[Any] = None,
        tool_capabilities: Optional[Any] = None,
        accessibility_profile: Optional[Any] = None,
        planner_context: Optional[Any] = None,
    ) -> PlanPatch:
        """需求级 diff → PlanPatch（§52/§60）。

        未变需求的 PlanItem 原样保留——即使 Creative Planner 是 LLM，
        补丁也不触碰无关项。
        """
        plan = self._normalize_plan(existing_plan)
        intent = (
            model_validate(EditingIntent, editing_intent)
            if isinstance(editing_intent, dict)
            else editing_intent
        )
        patch = (
            model_validate(IntentPatch, intent_patch)
            if isinstance(intent_patch, dict)
            else intent_patch
        )
        view = self._normalize_view(semantic_view)
        return build_plan_patch(
            self, plan, intent, patch, view, tool_capabilities,
            accessibility_profile, planner_context,
        )

    def apply_patch(self, plan: Any, patch: Any) -> LogicalEditingPlan:
        plan = self._normalize_plan(plan)
        patch = (
            model_validate(PlanPatch, patch) if isinstance(patch, dict) else patch
        )
        return apply_plan_patch(plan, patch)

    # ------------------------------------------------------------------
    # 持久化（与 gesture_intent/store.py 同惯例：tmp+replace 原子写）
    # ------------------------------------------------------------------

    def save(self, plan: Any, directory: Any) -> Path:
        plan = self._normalize_plan(plan)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "logical_editing_plan.json"
        payload = json.dumps(model_dump(plan), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    @classmethod
    def load(cls, directory: Any) -> LogicalEditingPlan:
        path = Path(directory) / "logical_editing_plan.json"
        return model_validate(
            LogicalEditingPlan, json.loads(path.read_text(encoding="utf-8"))
        )

    # ------------------------------------------------------------------
    # 输入归一化
    # ------------------------------------------------------------------

    def _normalize_input(self, value: Any) -> EditingPlannerInput:
        if isinstance(value, EditingPlannerInput):
            return value
        return model_validate(EditingPlannerInput, value)

    def _normalize_plan(self, value: Any) -> LogicalEditingPlan:
        if isinstance(value, LogicalEditingPlan):
            return value
        return model_validate(LogicalEditingPlan, value)

    def _normalize_view(self, value: Optional[Any]) -> Optional[SemanticView]:
        if value is None or isinstance(value, SemanticView):
            return value
        return model_validate(SemanticView, value)

    def _normalize_bindings(self, value: Optional[Any]) -> List[AssetBinding]:
        if value is None:
            return []
        if isinstance(value, dict):
            value = value.get("asset_bindings") or value.get("bindings") or []
        out = []
        for b in value:
            out.append(model_validate(AssetBinding, b) if isinstance(b, dict) else b)
        return out

    # ------------------------------------------------------------------
    # PlanItem 构建
    # ------------------------------------------------------------------

    def _requirement_digests(self, intent: EditingIntent) -> List[Dict[str, Any]]:
        digests: List[Dict[str, Any]] = []
        for r in intent.object_requirements:
            digests.append(requirement_digest(
                r.id, r.object_type.value, r.description, r.constraint_level.value
            ))
        for r in intent.event_bound_requirements:
            digests.append(requirement_digest(
                r.id, r.requirement.object_type.value,
                r.requirement.semantic_description, r.constraint_level.value
            ))
        return digests

    def _requirement_levels(self, intent: EditingIntent) -> Dict[str, ConstraintLevel]:
        levels: Dict[str, ConstraintLevel] = {}
        for r in intent.object_requirements:
            levels[r.id] = r.constraint_level
        for r in intent.event_bound_requirements:
            levels[r.id] = r.constraint_level
        for o in intent.explicit_operations:
            levels[o.id] = o.constraint_level
        return levels

    def _new_item(
        self,
        req_id: str,
        event_uid: Optional[str],
        operation: PlanOperation,
        source_text: str,
        *,
        parameters: Optional[Dict[str, Any]] = None,
        requirement_hash: str = "",
    ) -> PlanItem:
        key = f"{req_id}:{event_uid or '-'}:{operation.value}"
        return PlanItem(
            plan_item_uid=_stable_uid("pln", key),
            plan_key=key,
            operation=operation,
            source_requirement_ids=[req_id],
            timeline_effect=timeline_effect_for(operation),
            parameters=parameters or {},
            provenance=PlanItemProvenance(
                source_text=source_text, planner_stage="assemble",
                rules=[f"req:{requirement_hash[:8]}"] if requirement_hash else [],
            ),
        )

    def _items_for_object_requirements(
        self,
        intent: EditingIntent,
        profile: AccessibilityPlanningProfile,
        directives: Dict[str, Any],
        protected: set,
        options: Dict[str, Any],
    ) -> List[PlanItem]:
        items: List[PlanItem] = []
        for req in intent.object_requirements:
            directive = directives.get(req.id)
            req_hash = short_hash(model_dump(req))
            if req.action in (ObjectAction.remove, ObjectAction.update):
                # MVP：remove/update 走 replan 路径而非初始计划（§13 保留）
                operation = (
                    PlanOperation.remove_segment
                    if req.action == ObjectAction.remove
                    else _object_operation(req)
                )
                item = self._new_item(
                    req.id, None, operation, req.source_text,
                    parameters={"object_type": req.object_type.value},
                    requirement_hash=req_hash,
                )
                item.status = PlanItemStatus.unsupported
                item.provenance.rules.append(f"object_action:{req.action.value}")
                items.append(item)
                continue

            operation = _object_operation(req)
            item = self._new_item(
                req.id, None, operation, req.source_text,
                parameters={"object_type": req.object_type.value},
                requirement_hash=req_hash,
            )
            if req.object_type == ObjectType.text:
                if req.content:
                    item.parameters["text"] = req.content
                else:
                    item.status = PlanItemStatus.unfulfilled
                    item.provenance.rules.append("missing_text_content")
            follow = _detect_follow(req)
            item.temporal_spec = temporal_spec_full_video()
            if operation in (PlanOperation.add_overlay, PlanOperation.add_text,
                             PlanOperation.add_effect, PlanOperation.track_overlay):
                item.spatial_spec = spatial_spec_for_global(
                    operation, profile, intent.constraints,
                    relation_preference=directive.relation_preference if directive else None,
                )
                if follow:
                    item.operation = PlanOperation.track_overlay
                    item.plan_key = f"{req.id}:-:track_overlay"
                    item.plan_item_uid = _stable_uid("pln", item.plan_key)
                    item.spatial_spec.relation = SpatialRelation.follow
                    target = single_hand_target(profile) or _follow_target_from_text(
                        f"{getattr(req.description, 'raw', '')} {req.source_text}"
                    )
                    policy = follow_policy(profile.tremor, options)
                    item.spatial_spec.anchor = SpatialAnchor(
                        type=SpatialAnchorType.spatial_track, target=target
                    )
                    item.spatial_spec.follow = FollowSpec(enabled=True, mode="trajectory", **policy)
            item.target = {"type": "video", "value": "main_video"}
            if directive:
                item.style_spec = directive
            item.degradation_policy = degradation_policy_for(
                req.constraint_level,
                req.id in protected,
                item.parameters,
            )
            items.append(item)
        return items

    def _items_for_event_requirements(
        self,
        expanded: List[ExpandedRequirement],
        profile: AccessibilityPlanningProfile,
        constraints: Sequence[Constraint],
        directives: Dict[str, Any],
        has_music: bool,
        protected: set,
        warnings: List[str],
    ) -> List[PlanItem]:
        items: List[PlanItem] = []
        for e in expanded:
            req = e.requirement
            directive = directives.get(req.id)
            req_hash = short_hash(model_dump(req))
            inner = req.requirement
            operation = (
                _OP_MAP.get(inner.operation) if inner.operation else None
            ) or _EVENT_OP_MAP.get(inner.object_type, PlanOperation.add_overlay)
            follow = _detect_follow(req)

            if not e.has_items and e.missing_reason is not None:
                continue  # 缺失/依赖由 expansion 阶段已处理

            for binding in e.bindings:
                event_uid = binding.event_uid
                brief = binding.brief
                item = self._new_item(
                    req.id, event_uid, operation, req.source_text,
                    parameters={
                        "object_type": inner.object_type.value,
                        "occurrence_index": (brief or {}).get("occurrence_index"),
                    },
                    requirement_hash=req_hash,
                )
                if binding.uncertain or e.missing_reason == "all_uncertain":
                    item.status = PlanItemStatus.pending_dependency
                if inner.content:
                    item.parameters["text"] = inner.content
                elif inner.object_type == ObjectType.text:
                    item.status = PlanItemStatus.unfulfilled
                    item.provenance.rules.append("missing_text_content")

                # temporal
                if binding.pair is not None:
                    item.temporal_spec = temporal_spec_for_gap(
                        binding.pair, operation,
                        has_music=has_music,
                        canonical=req.trigger.event.canonical or "",
                    )
                    item.target = {"type": "event", "value": binding.pair[0].get("event_uid")}
                elif event_uid:
                    item.temporal_spec = temporal_spec_for_event(
                        req, event_uid, operation, has_music=has_music
                    )
                    item.target = {"type": "event", "value": event_uid}
                else:
                    # 结构事件（video_start/end/first_action/last_action）
                    canonical = req.trigger.event.canonical or ""
                    item.temporal_spec = temporal_spec_for_structure(canonical)
                    item.target = {"type": "video", "value": canonical}

                # spatial（音频/结构类无空间项）
                if operation in (
                    PlanOperation.add_overlay, PlanOperation.add_text,
                    PlanOperation.add_effect, PlanOperation.track_overlay,
                ):
                    if event_uid:
                        item.spatial_spec = spatial_spec_for_event(
                            req, event_uid, operation, profile, constraints,
                            relation_preference=directive.relation_preference if directive else None,
                            follow=follow,
                        )
                    else:
                        item.spatial_spec = spatial_spec_for_global(
                            operation, profile, constraints,
                            relation_preference=directive.relation_preference if directive else None,
                        )
                    if follow:
                        item.operation = PlanOperation.track_overlay
                        item.plan_key = f"{req.id}:{event_uid or '-'}:track_overlay"
                        item.plan_item_uid = _stable_uid("pln", item.plan_key)
                if directive:
                    item.style_spec = directive
                    if directive.duration_hint and item.temporal_spec and item.temporal_spec.duration.mode.value == "preferred":
                        item.temporal_spec.duration.value = directive.duration_hint
                item.degradation_policy = degradation_policy_for(
                    req.constraint_level, req.id in protected, item.parameters
                )
                items.append(item)
        return items

    def _items_for_operations(
        self,
        operations: Sequence[ExplicitOperation],
        items: List[PlanItem],
        event_index: EventIndex,
        profile: AccessibilityPlanningProfile,
        has_music: bool,
    ) -> Tuple[List[PlanItem], List[str]]:
        """ExplicitOperation → PlanItem / 对已有项的参数级修改。"""
        out: List[PlanItem] = []
        warnings: List[str] = []
        for op in operations:
            op_hash = short_hash(model_dump(op))
            if op.operation == OperationType.freeze:
                item = self._freeze_item(op, event_index, op_hash, warnings)
                if item is not None:
                    out.append(item)
                continue
            if op.operation in (
                OperationType.scale_adjust, OperationType.position_adjust,
            ):
                warnings += self._apply_adjust(op, items)
                continue
            if op.operation == OperationType.volume_adjust:
                item = self._new_item(
                    op.id, None, PlanOperation.volume_adjust, op.source_text,
                    parameters={
                        "direction": op.parameters.get("direction"),
                        "delta_db": _volume_delta(op.parameters),
                    },
                    requirement_hash=op_hash,
                )
                item.target = {
                    "type": "audio_track",
                    "value": _audio_target(op.target.value),
                }
                out.append(item)
                continue
            # trim/split/remove/speed_adjust/replace_*：超出 MVP（§13 保留）
            mapped = _OP_MAP.get(op.operation, PlanOperation.trim)
            item = self._new_item(
                op.id, None, mapped, op.source_text, requirement_hash=op_hash
            )
            item.status = PlanItemStatus.unsupported
            item.target = {"type": op.target.type, "value": op.target.value}
            warnings.append(f"{op.id}: 操作 {op.operation.value} 超出 MVP 范围，标记 unsupported")
            out.append(item)
        return out, warnings

    def _freeze_item(
        self,
        op: ExplicitOperation,
        event_index: EventIndex,
        op_hash: str,
        warnings: List[str],
    ) -> Optional[PlanItem]:
        """freeze → insert_duration 项；锚定 ending_pose/last_action/video_end。"""
        duration = freeze_duration(op.parameters) or 1.0
        target_value = op.target.value
        item = self._new_item(
            op.id, None, PlanOperation.freeze, op.source_text,
            parameters={"duration": duration},
            requirement_hash=op_hash,
        )
        structure_keys = {"video_start", "video_end", "first_action", "last_action"}
        if target_value in structure_keys:
            item.temporal_spec = temporal_spec_for_structure(target_value)
        else:
            # semantic_event target：canonical → 找事件取 uid
            candidates = event_index.events_for(target_value)
            if candidates:
                brief = candidates[-1]
                item.target = {"type": "event", "value": brief.get("event_uid")}
                item.temporal_spec = temporal_spec_for_event(
                    # 构造最小触发对象复用映射：at_event→peak
                    _fake_trigger(req_id=op.id, event_uid=brief.get("event_uid")),
                    brief.get("event_uid"),
                    PlanOperation.freeze,
                    has_music=False,
                )
                item.plan_key = f"{op.id}:{brief.get('event_uid')}:freeze"
                item.plan_item_uid = _stable_uid("pln", item.plan_key)
            else:
                item.status = PlanItemStatus.unfulfilled
                item.provenance.rules.append("freeze_target_missing")
                warnings.append(f"{op.id}: freeze 目标 '{target_value}' 未找到对应事件")
                return item
        if item.temporal_spec is not None:
            item.temporal_spec.duration = DurationSpec(mode="exact", value=duration)
        item.degradation_policy = degradation_policy_for(
            op.constraint_level, False, item.parameters
        )
        return item

    def _apply_adjust(
        self, op: ExplicitOperation, items: List[PlanItem]
    ) -> List[str]:
        """scale/position_adjust：直接修改被引用项的 policy（§60 语义）。"""
        warnings: List[str] = []
        targets = _resolve_target_items(op.target, items)
        if not targets:
            warnings.append(
                f"{op.id}: 无法把 '{op.target.value}' 解析到已有 PlanItem，跳过"
            )
            return warnings
        direction = str(op.parameters.get("direction") or "")
        for item in targets:
            if op.operation == OperationType.scale_adjust:
                factor = 0.8 if direction in ("smaller", "小") else 1.25
                if item.spatial_spec is not None:
                    policy = dict(item.spatial_spec.scale_policy or {"mode": "relative", "value": 0.16})
                    policy["value"] = round(float(policy.get("value", 0.16)) * factor, 4)
                    item.spatial_spec.scale_policy = policy
                item.parameters["scale_factor"] = factor
            else:
                delta = op.parameters.get("offset") or {"dx": 0.0, "dy": 0.0}
                item.parameters["position_offset"] = delta
            item.provenance.rules.append(f"adjusted_by:{op.id}")
            if op.id not in item.source_requirement_ids:
                item.source_requirement_ids.append(op.id)
        return warnings

    def _attach_constraints(
        self, items: List[PlanItem], constraints: Sequence[Constraint]
    ) -> None:
        """把适用约束挂到相关项的 constraint_refs + 注入避让区。"""
        for constraint in constraints:
            scope_target = str((constraint.scope or {}).get("target") or "")
            for item in items:
                if not _constraint_applies(constraint, item, scope_target):
                    continue
                if constraint.id not in item.constraint_refs:
                    item.constraint_refs.append(constraint.id)
                if item.spatial_spec is not None:
                    if constraint.type == "avoid_overlap" and constraint.reference:
                        region = {
                            "face": "face", "person": "person",
                            "hand": "active_hands", "hands": "active_hands",
                            "gesture": "active_gesture",
                        }.get(constraint.reference, constraint.reference)
                        if region not in item.spatial_spec.avoid_regions:
                            item.spatial_spec.avoid_regions.append(region)
                if constraint.type == "audio_volume" and item.operation in (
                    PlanOperation.add_music, PlanOperation.replace_music,
                ):
                    item.parameters["volume_cap"] = True
                    if constraint.preference is not None:
                        item.parameters["volume_preference"] = constraint.preference.raw

    def _resolve_positions(
        self,
        items: List[PlanItem],
        event_index: EventIndex,
        profile: AccessibilityPlanningProfile,
        options: Dict[str, Any],
        warnings: List[str],
    ) -> Dict[str, Tuple[Tuple[float, float], float]]:
        """plan 阶段的放置决策：确定 relation + 近似位置（供校验/记录）。

        materialize 用同一套几何在最新视图数据上重算坐标。
        """
        positions: Dict[str, Tuple[Tuple[float, float], float]] = {}
        tau = float(options.get("avoid_iou_threshold", 0.05))
        for item in items:
            spec = item.spatial_spec
            if spec is None or item.status in (
                PlanItemStatus.blocked, PlanItemStatus.skipped,
                PlanItemStatus.unfulfilled, PlanItemStatus.unsupported,
            ):
                continue
            scale = float(spec.scale_policy.get("value", 0.16))
            anchor = self._anchor_point(item, event_index, options)
            if anchor is None:
                continue
            brief = self._item_spatial_brief(item, event_index)

            relation = spec.relation.value
            if spec.direction_policy.get("mode") == "match_event_direction" and brief:
                label = ((brief.get("direction") or {}).get("label"))
                chosen = direction_relation(label or "")
                if chosen:
                    relation = chosen
                    item.provenance.rules.append(f"direction:{label}")

            avoid = collect_avoid_regions(
                brief, spec.avoid_regions, profile, asset_half=scale / 2.0
            )
            hard_p0 = "face" in spec.avoid_regions
            point, chosen_rel, fell_back = choose_position(
                anchor, relation, spec.fallback_positions, avoid,
                distance=float(spec.offset_policy.get("distance", 0.08)),
                scale=scale, iou_threshold=tau, hard_p0=hard_p0,
            )
            spec.relation = SpatialRelation(chosen_rel)
            item.provenance.rules.append(f"placement:{chosen_rel}")
            if fell_back:
                warnings.append(
                    f"{item.plan_item_uid}: 全部候选位置与保护区冲突，取冲突最小者"
                )
            positions[item.plan_item_uid] = (point, scale)
        return positions

    def _anchor_point(
        self, item: PlanItem, event_index: EventIndex, options: Dict[str, Any]
    ) -> Optional[Tuple[float, float]]:
        spec = item.spatial_spec
        if spec is None or spec.anchor is None:
            return None
        anchor = spec.anchor
        if anchor.type.value == "event_anchor" and anchor.event_uid:
            brief = event_index.brief(anchor.event_uid)
            spatial = event_index.spatial_brief(brief)
            if spatial:
                if spatial.get("event_anchor"):
                    return tuple(spatial["event_anchor"])
                if spatial.get("head_center"):
                    return tuple(spatial["head_center"])
                if spatial.get("person_bbox"):
                    x1, y1, x2, y2 = spatial["person_bbox"]
                    return ((x1 + x2) / 2, (y1 + y2) / 2)
            return (0.5, 0.3)
        if anchor.type.value == "structural":
            return {"screen_center": (0.5, 0.5)}.get(anchor.structure_key, (0.5, 0.5))
        if anchor.type.value == "absolute" and anchor.point:
            return tuple(anchor.point)
        # spatial_track：逻辑阶段用事件锚点近似（轨迹在 materialize 解析）
        if anchor.type.value == "spatial_track":
            return (0.5, 0.3)
        return None

    def _item_spatial_brief(self, item: PlanItem, event_index: EventIndex):
        if item.spatial_spec and item.spatial_spec.anchor and item.spatial_spec.anchor.event_uid:
            brief = event_index.brief(item.spatial_spec.anchor.event_uid)
            return event_index.spatial_brief(brief)
        if item.target.get("type") == "event":
            brief = event_index.brief(item.target.get("value"))
            return event_index.spatial_brief(brief)
        return None

    def _asset_request_for(
        self, item: PlanItem, intent: EditingIntent, builder: AssetRequestBuilder
    ):
        if item.operation not in _ASSET_BACKED_OPS or item.status in (
            PlanItemStatus.unfulfilled, PlanItemStatus.unsupported,
            PlanItemStatus.blocked, PlanItemStatus.skipped,
        ):
            return None
        req_id = item.source_requirement_ids[0]
        req_model = self._find_requirement(intent, req_id)
        if req_model is None:
            return None
        object_type, description, level = _describe_requirement(req_model)
        return builder.request_for(
            object_type, description,
            constraint_level=level,
            usage={"plan_item": item.plan_item_uid},
        )

    def _find_requirement(self, intent: EditingIntent, req_id: str):
        for r in intent.object_requirements:
            if r.id == req_id:
                return r
        for r in intent.event_bound_requirements:
            if r.id == req_id:
                return r
        return None


# ---------------------------------------------------------------------------
# 模块内工具
# ---------------------------------------------------------------------------



def _object_operation(req) -> PlanOperation:
    if req.object_type == ObjectType.background:
        return PlanOperation.replace_background
    if req.object_type == ObjectType.music:
        return (
            PlanOperation.replace_music
            if req.action == ObjectAction.replace
            else PlanOperation.add_music
        )
    return {
        ObjectType.text: PlanOperation.add_text,
        ObjectType.effect: PlanOperation.add_effect,
        ObjectType.sound_effect: PlanOperation.add_sound_effect,
        ObjectType.sticker: PlanOperation.add_overlay,
        ObjectType.image: PlanOperation.add_overlay,
        ObjectType.overlay: PlanOperation.add_overlay,
    }.get(req.object_type, PlanOperation.add_overlay)


def _describe_requirement(req_model):
    """(object_type, description, constraint_level) for asset request。"""
    if hasattr(req_model, "requirement"):
        inner = req_model.requirement
        return inner.object_type, inner.semantic_description, req_model.constraint_level
    return req_model.object_type, req_model.description, req_model.constraint_level


_FOLLOW_HINTS = ("跟着", "跟随", "跟住", "follow", "track")


def _detect_follow(requirement) -> bool:
    """需求描述/原文含"跟着/跟随"等措辞 → 跟随项（§30）。

    素材描述与原始子句都看——"皇冠一直跟着头"里"跟着头"可能落在
    description 也可能只留在 source_text。
    """
    description = getattr(requirement, "description", None)
    if description is None and hasattr(requirement, "requirement"):
        description = requirement.requirement.semantic_description
    raw = " ".join(
        part for part in (
            getattr(description, "raw", "") if description else "",
            getattr(requirement, "source_text", "") or "",
        )
        if part
    ).lower()
    return any(h in raw for h in _FOLLOW_HINTS)


def _follow_target_from_text(raw: str) -> str:
    if "头" in raw:
        return "head"
    if "脸" in raw:
        return "face"
    if "手" in raw:
        return "right_hand"
    return "head"


def _volume_delta(parameters: Dict[str, Any]) -> float:
    direction = str(parameters.get("direction") or "")
    magnitude = parameters.get("value")
    try:
        amount = abs(float(magnitude)) if magnitude is not None else 3.0
    except (TypeError, ValueError):
        amount = 3.0
    if direction in ("smaller", "quieter", "down", "小", "低"):
        return -amount
    return amount


def _audio_target(value: str) -> str:
    if value in ("current music", "original_music", "原音乐", "当前音乐"):
        return "original_audio"
    return value


def _fake_trigger(req_id: str, event_uid: str):
    """为 freeze 的 canonical 目标构造最小 EventBoundRequirement 形对象，
    复用 temporal_spec_for_event 的 at_event→peak 映射。"""
    from gesture_intent.models import (
        EventReference, EventRequirement, EventTrigger,
        EventBoundRequirement, EventType, Occurrence, OccurrenceType,
        TemporalRelation, ObjectType,
    )

    return EventBoundRequirement(
        id=req_id,
        source_text="",
        trigger=EventTrigger(
            event=EventReference(type=EventType.gesture, raw=event_uid, canonical=event_uid),
            occurrence=Occurrence(type=OccurrenceType.all),
            temporal_relation=TemporalRelation.at_event,
        ),
        requirement=EventRequirement(object_type=ObjectType.overlay),
    )


def _resolve_target_items(target, items: List[PlanItem]) -> List[PlanItem]:
    """ExplicitOperation.target → 已有 PlanItem 列表。

    支持：plan_item_uid / plan_key / requirement_id / event display_id /
    event_uid / 带序号的 "xxx_NN"（按绑定事件 occurrence_index 第 N 个）。
    """
    value = target.value
    by_uid = {i.plan_item_uid: i for i in items}
    if value in by_uid:
        return [by_uid[value]]
    by_key = {i.plan_key: i for i in items}
    if value in by_key:
        return [by_key[value]]
    by_req = [i for i in items if value in i.source_requirement_ids]
    if by_req:
        return by_req
    by_event = [
        i for i in items
        if i.target.get("type") == "event" and i.target.get("value") == value
    ]
    if by_event:
        return by_event
    import re

    m = re.match(r"^(.*?)(\d+)$", value.replace("_", "")) if value else None
    if m:
        n = int(m.group(2))
        event_items = [
            i for i in items
            if i.target.get("type") == "event"
            and isinstance(i.parameters.get("occurrence_index"), int)
        ]
        picked = [i for i in event_items if i.parameters["occurrence_index"] == n]
        if picked:
            return picked
    return []


def _constraint_applies(constraint: Constraint, item: PlanItem, scope_target: str) -> bool:
    """约束 scope.target 过滤：空 scope 默认作用于全部空间/音频项。"""
    if not scope_target:
        return True
    mapping = {
        "event_sticker": {PlanOperation.add_overlay, PlanOperation.track_overlay},
        "sticker": {PlanOperation.add_overlay, PlanOperation.track_overlay},
        "text": {PlanOperation.add_text},
        "effect": {PlanOperation.add_effect},
        "music": {PlanOperation.add_music, PlanOperation.replace_music,
                  PlanOperation.volume_adjust},
        "audio": {PlanOperation.add_music, PlanOperation.replace_music,
                  PlanOperation.add_sound_effect, PlanOperation.volume_adjust},
        "background": {PlanOperation.replace_background},
    }
    ops = mapping.get(scope_target)
    if ops is None:
        return True
    return item.operation in ops
