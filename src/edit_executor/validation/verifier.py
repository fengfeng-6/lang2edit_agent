"""Core Verification（§73）：软件无关的 Desired Graph 校验。

    所有 active PlanItem 均有对应对象
    所有引用有效（media_ref / mask_ref / track_uid）
    时间范围有效
    Graph 没有 dangling reference
    总时长正确
    Asset 存在
"""

from __future__ import annotations

from typing import Optional

from editing_planner.models import PlanItemStatus, PlanOperation, ResolvedEditingPlan

from ..models import (
    AnalysisArtifactRegistry,
    DesiredProjectGraph,
    VerificationIssue,
    VerificationReport,
)
from ..compiler.operations import MUTATION_OPERATIONS, OPERATION_COMPILERS

_ACTIVE = {PlanItemStatus.planned.value, PlanItemStatus.degraded.value}


def verify_desired_graph(
    graph: DesiredProjectGraph,
    plan: ResolvedEditingPlan,
    artifact_registry: Optional[AnalysisArtifactRegistry] = None,
) -> VerificationReport:
    issues = []

    def issue(code: str, severity: str, message: str, uids=None) -> None:
        issues.append(
            VerificationIssue(
                code=code,
                severity=severity,
                message=message,
                object_uids=list(uids or []),
            )
        )

    active_items = [
        i
        for i in plan.resolved_items
        if getattr(i.status, "value", i.status) in _ACTIVE
    ]
    producing = [
        i for i in active_items if i.operation.value in OPERATION_COMPILERS
    ]
    for item in producing:
        if not ctx_has_item_object(graph, item.plan_item_uid):
            issue(
                "missing_object",
                "hard",
                f"{item.plan_item_uid}（{item.operation.value}）未生成对象",
            )
    # freeze 场景：切片必须覆盖且总时长一致
    for obj in graph.objects.values():
        if obj.project_time.start >= obj.project_time.end:
            issue(
                "bad_time_range",
                "hard",
                f"{obj.timeline_object_uid} 时间范围非法 "
                f"[{obj.project_time.start},{obj.project_time.end})",
                [obj.timeline_object_uid],
            )
        if obj.media_ref and obj.media_ref not in graph.media_refs:
            issue(
                "dangling_media_ref",
                "hard",
                f"{obj.timeline_object_uid} 引用未知 media {obj.media_ref}",
                [obj.timeline_object_uid],
            )
        if obj.track_uid and obj.track_uid not in graph.tracks:
            issue(
                "dangling_track",
                "hard",
                f"{obj.timeline_object_uid} 引用未知轨道 {obj.track_uid}",
                [obj.timeline_object_uid],
            )
        if obj.mask_ref:
            found = (
                artifact_registry.artifacts.get(obj.mask_ref)
                if artifact_registry is not None
                else None
            )
            if artifact_registry is not None and found is None:
                issue(
                    "dangling_mask_ref",
                    "hard",
                    f"{obj.timeline_object_uid} 引用未知蒙版 {obj.mask_ref}",
                    [obj.timeline_object_uid],
                )
    expected_total = plan.timeline_mapping.total_duration
    if expected_total and abs(graph.total_duration - expected_total) > 1e-6:
        issue(
            "total_duration",
            "hard",
            f"total_duration={graph.total_duration} ≠ "
            f"timeline_mapping.total_duration={expected_total}",
        )
    status = (
        "fail"
        if any(i.severity == "hard" for i in issues)
        else ("warn" if issues else "pass")
    )
    return VerificationReport(status=status, issues=issues)


def ctx_has_item_object(graph: DesiredProjectGraph, plan_item_uid: str) -> bool:
    return any(
        obj.source_plan_item_uid == plan_item_uid
        for obj in graph.objects.values()
    )
