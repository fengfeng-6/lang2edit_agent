"""Constraint Validator + ValidationReport 组装（§43/§45）。

五个确定性子报告（semantic/temporal/spatial/capability/asset），
accessibility 子报告由 ``accessibility/validator.py`` 独立产出后并入。

总体状态优先级（§45/§59）：

    矛盾类失败（hard 需求无法满足、约束互斥） → blocked
    纯数据缺口（等上游补分析/分割/确认）       → needs_dependency
    其余问题                                  → valid_with_warnings
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from gesture_intent.models import Constraint, ConstraintLevel

from ..models import (
    DegradationPolicy,
    LogicalEditingPlan,
    PlanItemStatus,
    PlanOperation,
    SubReport,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)

_ACTIVE = {PlanItemStatus.planned, PlanItemStatus.degraded, PlanItemStatus.pending_dependency}
_INACTIVE = {
    PlanItemStatus.blocked,
    PlanItemStatus.skipped,
    PlanItemStatus.unfulfilled,
    PlanItemStatus.unsupported,
}

_SCALE_BOUNDS = (0.02, 0.8)


def _report(issues: List[ValidationIssue]) -> SubReport:
    if any(i.severity == "hard" for i in issues):
        return SubReport(status="fail", issues=issues)
    if issues:
        return SubReport(status="warn", issues=issues)
    return SubReport(status="pass", issues=issues)


def validate_constraints(
    plan: LogicalEditingPlan,
    constraints: Sequence[Constraint],
    event_index: Any,
    requirement_levels: Dict[str, ConstraintLevel],
) -> Dict[str, SubReport]:
    """ConstraintValidator 五个子报告（§43）。"""
    reports: Dict[str, SubReport] = {}
    reports["semantic"] = _report(_check_semantic(plan, constraints))
    reports["temporal"] = _report(_check_temporal(plan, event_index))
    reports["spatial"] = _report(_check_spatial(plan))
    reports["capability"] = _report(_check_capability(plan, requirement_levels))
    reports["asset"] = _report(_check_asset(plan))
    return reports


def _check_semantic(plan: LogicalEditingPlan, constraints: Sequence[Constraint]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    unresolved_ids = {u.requirement_id for u in plan.unresolved}
    # hard 需求：要么有非跳过项，要么有 UnfulfilledRequirement 记录
    active_reqs = {
        rid
        for item in plan.plan_items
        if item.status in _ACTIVE
        for rid in item.source_requirement_ids
    }
    for unfulfilled in plan.unresolved:
        if unfulfilled.severity == "hard" and unfulfilled.status == "blocked":
            issues.append(ValidationIssue(
                code="hard_requirement_blocked",
                severity="hard",
                message=f"hard 需求 {unfulfilled.requirement_id} 无法满足（{unfulfilled.reason}）",
                requirement_ids=[unfulfilled.requirement_id],
            ))
    # prohibit_object：不得有对应 object_type 的活动项
    prohibited = {
        c.reference for c in constraints if c.type == "prohibit_object" and c.reference
    }
    for item in plan.plan_items:
        if item.status in _INACTIVE:
            continue
        req_types = {item.parameters.get("object_type")}
        if req_types & prohibited:
            issues.append(ValidationIssue(
                code="prohibited_object",
                severity="hard",
                message=f"{item.plan_item_uid} 违反 prohibit_object({item.parameters.get('object_type')})",
                item_uids=[item.plan_item_uid],
                requirement_ids=item.source_requirement_ids,
            ))
    # preserve_original_music：replace/remove 原音乐 → 违例
    if any(c.type == "preserve_original_music" for c in constraints):
        for item in plan.plan_items:
            if item.operation in (PlanOperation.replace_music,) and item.status not in _INACTIVE:
                issues.append(ValidationIssue(
                    code="original_music_conflict",
                    severity="hard",
                    message=f"{item.plan_item_uid} 与 preserve_original_music 冲突",
                    item_uids=[item.plan_item_uid],
                    requirement_ids=item.source_requirement_ids,
                ))
    # preserve_duration：insert_duration 项总位移必须为 0
    if any(c.type == "preserve_duration" for c in constraints):
        inserted = sum(
            (item.temporal_spec.duration.value or 0.0)
            for item in plan.plan_items
            if item.status not in _INACTIVE
            and item.temporal_spec is not None
            and item.timeline_effect.value == "insert_duration"
        )
        if inserted > 0:
            issues.append(ValidationIssue(
                code="duration_conflict",
                severity="hard",
                message=f"存在 insert_duration 项（共 +{inserted:.2f}s），与 preserve_duration 冲突",
            ))
    return issues


def _check_temporal(plan: LogicalEditingPlan, event_index: Any) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    source_duration = plan.timeline_structure.source_duration
    for item in plan.plan_items:
        spec = item.temporal_spec
        if spec is None or item.status in _INACTIVE:
            continue
        for anchor in (spec.start_anchor, spec.end_anchor):
            if anchor is None:
                continue
            if anchor.type.value == "semantic_event" and anchor.event_uid:
                if event_index is not None and event_index.brief(anchor.event_uid) is None:
                    issues.append(ValidationIssue(
                        code="dangling_event_anchor",
                        severity="hard",
                        message=f"{item.plan_item_uid} 引用了不存在的 event {anchor.event_uid}",
                        item_uids=[item.plan_item_uid],
                    ))
        if spec.start_anchor and spec.end_anchor:
            # during/until/between 的 start<end 在 materialize 数值校验；
            # 这里只对 absolute 提示
            pass
        if (
            spec.source_time_hint is not None
            and source_duration > 0
            and not (0 <= spec.source_time_hint <= source_duration)
        ):
            issues.append(ValidationIssue(
                code="time_out_of_range",
                severity="warning",
                message=f"{item.plan_item_uid} 锚点时间超出视频时长",
                item_uids=[item.plan_item_uid],
            ))
        d = spec.duration
        if d and d.mode.value == "range" and d.min is not None and d.max is not None and d.min > d.max:
            issues.append(ValidationIssue(
                code="bad_duration_range",
                severity="hard",
                message=f"{item.plan_item_uid} duration range min>max",
                item_uids=[item.plan_item_uid],
            ))
    return issues


def _check_spatial(plan: LogicalEditingPlan) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for item in plan.plan_items:
        spec = item.spatial_spec
        if spec is None or item.status in _INACTIVE:
            continue
        scale = spec.scale_policy.get("value")
        if isinstance(scale, (int, float)) and not (_SCALE_BOUNDS[0] <= scale <= _SCALE_BOUNDS[1]):
            issues.append(ValidationIssue(
                code="scale_out_of_range",
                severity="warning",
                message=f"{item.plan_item_uid} scale={scale} 超出 [{_SCALE_BOUNDS[0]}, {_SCALE_BOUNDS[1]}]",
                item_uids=[item.plan_item_uid],
            ))
        if spec.anchor and spec.anchor.point:
            x, y = spec.anchor.point
            if not (0 <= x <= 1 and 0 <= y <= 1):
                issues.append(ValidationIssue(
                    code="anchor_out_of_frame",
                    severity="hard",
                    message=f"{item.plan_item_uid} 绝对锚点越界 ({x:.2f},{y:.2f})",
                    item_uids=[item.plan_item_uid],
                ))
    return issues


def _check_capability(plan: LogicalEditingPlan, requirement_levels: Dict[str, ConstraintLevel]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for item in plan.plan_items:
        # pending_dependency 项等上游数据，能力解析留待数据到齐后复评
        if item.status in _INACTIVE or item.status == PlanItemStatus.pending_dependency:
            continue
        if item.resolved_capability is None:
            issues.append(ValidationIssue(
                code="capability_unresolved",
                severity="hard",
                message=f"{item.plan_item_uid} 没有可用能力链底",
                item_uids=[item.plan_item_uid],
                requirement_ids=item.source_requirement_ids,
            ))
        if item.degradation_policy == DegradationPolicy.strict and item.degradation_applied:
            issues.append(ValidationIssue(
                code="strict_degradation",
                severity="hard",
                message=f"{item.plan_item_uid} strict 策略却被降级 {item.degradation_applied}",
                item_uids=[item.plan_item_uid],
                requirement_ids=item.source_requirement_ids,
            ))
    return issues


def _check_asset(plan: LogicalEditingPlan) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    request_uids = {r.request_uid for r in plan.asset_requests}
    seen_keys: Dict[str, str] = {}
    for req in plan.asset_requests:
        if req.dedup_key in seen_keys:
            issues.append(ValidationIssue(
                code="duplicate_asset_request",
                severity="warning",
                message=f"{req.request_uid} 与 {seen_keys[req.dedup_key]} dedup_key 相同",
            ))
        seen_keys[req.dedup_key] = req.request_uid
    for item in plan.plan_items:
        if item.status in _INACTIVE:
            continue
        if item.parameters.get("needs_asset") and not item.asset_request_ref:
            issues.append(ValidationIssue(
                code="missing_asset_request",
                severity="hard",
                message=f"{item.plan_item_uid} 需要素材但没有 asset_request_ref",
                item_uids=[item.plan_item_uid],
            ))
        if item.asset_request_ref and item.asset_request_ref not in request_uids:
            issues.append(ValidationIssue(
                code="dangling_asset_request",
                severity="hard",
                message=f"{item.plan_item_uid} 引用了不存在的 {item.asset_request_ref}",
                item_uids=[item.plan_item_uid],
            ))
        if item.operation == PlanOperation.add_text and item.status not in _INACTIVE:
            if not item.parameters.get("text"):
                issues.append(ValidationIssue(
                    code="missing_text_content",
                    severity="warning",
                    message=f"{item.plan_item_uid} 文字项缺少内容",
                    item_uids=[item.plan_item_uid],
                ))
    return issues


def assemble_report(
    plan: LogicalEditingPlan,
    sub_reports: Dict[str, SubReport],
    accessibility_issues: List[ValidationIssue],
) -> ValidationReport:
    """汇总子报告 + 依赖 → ValidationReport（§45 状态优先级）。"""
    hard: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    for report in sub_reports.values():
        for issue in report.issues:
            (hard if issue.severity == "hard" else warnings).append(issue)
    acc_report = _report(accessibility_issues)
    for issue in accessibility_issues:
        (hard if issue.severity == "hard" else warnings).append(issue)

    has_pending = any(
        item.status == PlanItemStatus.pending_dependency for item in plan.plan_items
    ) or any(u.status == "pending_dependency" for u in plan.unresolved)
    blocking_deps = [d.request_uid for d in plan.dependency_requests if d.blocking]

    # 矛盾 → blocked；纯数据缺口（pending 依赖而无 hard 违规）→ needs_dependency
    if hard:
        status = ValidationStatus.blocked
    elif has_pending and blocking_deps:
        status = ValidationStatus.needs_dependency
    elif warnings:
        status = ValidationStatus.valid_with_warnings
    else:
        status = ValidationStatus.valid

    return ValidationReport(
        status=status,
        semantic=sub_reports.get("semantic", SubReport()),
        temporal=sub_reports.get("temporal", SubReport()),
        spatial=sub_reports.get("spatial", SubReport()),
        capability=sub_reports.get("capability", SubReport()),
        asset=sub_reports.get("asset", SubReport()),
        accessibility=acc_report,
        hard_violations=hard,
        warnings=warnings,
        unresolved_dependencies=[d.request_uid for d in plan.dependency_requests],
    )
