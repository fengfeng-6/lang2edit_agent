"""ExecutionPreflight（§85-§87）。

顺序：plan.validation.status 门 → source_media → 素材 → 分析产物
→ Backend capability → workspace。资源类问题收集全部 dependency，
只有计划门 fail-fast。capability mismatch 绝不自动降级（§87）。
"""

from __future__ import annotations

import os
from typing import List, Optional

from editing_planner.models import PlanItemStatus, PlanOperation

from ..dsl.models import (
    BackendCapabilityManifest,
    CapabilityStatus,
)
from ..models import (
    ExecutorDependency,
    ExecutorInput,
    ExecutionFailure,
    FailureCategory,
    _stable_uid,
)

ACTIVE_STATUSES = {PlanItemStatus.planned.value, PlanItemStatus.degraded.value}

#: operation → 允许的 media_type 集合（§86 media_type 合法）
_ALLOWED_MEDIA_TYPES = {
    "add_overlay": {"image"},
    "track_overlay": {"image"},
    "add_effect": {"image", "video"},
    "replace_background": {"image", "video"},
    "add_music": {"audio"},
    "replace_music": {"audio"},
    "add_sound_effect": {"audio"},
}


class PreflightResult:
    def __init__(
        self,
        status: str,
        dependencies: Optional[List[ExecutorDependency]] = None,
        failures: Optional[List[ExecutionFailure]] = None,
        warnings: Optional[List[str]] = None,
    ) -> None:
        self.status = status
        self.dependencies = dependencies or []
        self.failures = failures or []
        self.warnings = warnings or []


def run_preflight(
    executor_input: ExecutorInput,
    manifest: BackendCapabilityManifest,
) -> PreflightResult:
    plan = executor_input.resolved_plan
    dependencies: List[ExecutorDependency] = []
    failures: List[ExecutionFailure] = []
    warnings: List[str] = []
    seq = {"n": 0}

    def dep(dtype: str, target: str, resource: str, reason: str) -> None:
        seq["n"] += 1
        dependencies.append(
            ExecutorDependency(
                dependency_uid=f"dep_pf_{seq['n']:03d}",
                type=dtype,
                target=target,
                required_resource=resource,
                blocking=True,
                reason=reason,
            )
        )

    # 1. plan.validation.status 门（§85）
    plan_status = getattr(plan.validation, "status", "valid")
    plan_status = getattr(plan_status, "value", plan_status)
    if plan_status == "blocked":
        failures.append(
            ExecutionFailure(
                failure_uid=_stable_uid("fail", plan.plan_uid, "blocked"),
                execution_uid="",
                category=FailureCategory.preflight.value,
                code="plan_blocked",
                message="ResolvedEditingPlan.validation.status=blocked，拒绝执行",
            )
        )
        return PreflightResult("blocked", failures=failures, warnings=warnings)

    # 2. source_media（§85）
    source = executor_input.source_media
    if not source.local_uri:
        dep("source_media", source.media_uid, "local_uri",
            "SourceMedia.local_uri 为空")
    elif not executor_input.backend_config.options.get(
        "skip_media_fs_check"
    ) and not os.path.isfile(source.local_uri):
        dep("source_media", source.media_uid, "readable local_uri",
            f"源视频不可读：{source.local_uri}")
    if not source.content_hash:
        warnings.append("SourceMedia.content_hash 为空（建议提供以支持资源缓存）")

    registry = (
        executor_input.asset_registry.registry
        if executor_input.asset_registry is not None
        else {}
    )

    for item in plan.resolved_items:
        status = getattr(item.status, "value", item.status)
        # 3. pending_dependency 项（§85 needs_dependency）
        if status == PlanItemStatus.pending_dependency.value:
            dep(
                _dep_type_for(item),
                item.plan_item_uid,
                item.operation.value,
                f"{item.plan_item_uid}: 状态 pending_dependency",
            )
            continue
        if status not in ACTIVE_STATUSES:
            continue
        # 4. asset 检查（§86）
        if item.asset_uid:
            record = registry.get(item.asset_uid)
            if record is None:
                dep("asset", item.asset_uid, "AssetRecord",
                    f"{item.plan_item_uid}: asset_uid 不在 registry")
            else:
                if not record.local_uri or (
                    not executor_input.backend_config.options.get(
                        "skip_media_fs_check"
                    )
                    and not os.path.isfile(record.local_uri)
                ):
                    dep("asset", item.asset_uid, "readable local_uri",
                        f"{item.plan_item_uid}: 素材本地文件不可读")
                if not record.integrity.decodable:
                    dep("asset", item.asset_uid, "integrity.decodable",
                        f"{item.plan_item_uid}: integrity.decodable=false")
                allowed = _ALLOWED_MEDIA_TYPES.get(item.operation.value)
                if allowed and record.media_type and record.media_type not in allowed:
                    dep("asset", item.asset_uid,
                        f"media_type∈{sorted(allowed)}",
                        f"{item.plan_item_uid}: media_type={record.media_type} 不合法")
        # 5. 分析产物（§6/§84）
        if item.operation.value == PlanOperation.replace_background.value:
            _check_mask(item, executor_input, dep)
        follow = item.follow or {}
        capability = item.resolved_capability or _infer_capability(item)
        if capability == "tracking" or follow.get("trajectory_ref"):
            art = executor_input.analysis_artifacts.require(
                "spatial_tracks", str(follow.get("target") or "head")
            )
            if art is None:
                dep("analysis_artifact",
                    str(follow.get("target") or "head"),
                    "spatial_tracks",
                    f"{item.plan_item_uid}: tracking 需要空间轨迹产物")
        # 6. capability 对照（§87）
        if capability and capability not in ("static", "none"):
            status_entry = manifest.status_of(capability)
            if status_entry != CapabilityStatus.supported.value:
                seq["n"] += 1
                dependencies.append(
                    ExecutorDependency(
                        dependency_uid=f"dep_cap_{seq['n']:03d}",
                        type="backend_capability",
                        target=capability,
                        required_resource=f"capability:{capability}",
                        blocking=True,
                        reason=(
                            f"{item.plan_item_uid}: resolved_capability="
                            f"{capability}，Backend={status_entry}（不自动降级）"
                        ),
                    )
                )
                failures.append(
                    ExecutionFailure(
                        failure_uid=_stable_uid(
                            "fail", item.plan_item_uid, "capability"
                        ),
                        execution_uid="",
                        category=FailureCategory.capability.value,
                        code="capability_mismatch",
                        message=(
                            f"{item.plan_item_uid}: 需要 {capability}，"
                            f"Backend 为 {status_entry}"
                        ),
                        affected_objects=[item.plan_item_uid],
                    )
                )

    if any(f.code == "capability_mismatch" for f in failures):
        return PreflightResult(
            "capability_mismatch",
            dependencies=dependencies,
            failures=failures,
            warnings=warnings,
        )
    if dependencies or plan_status == "needs_dependency":
        return PreflightResult(
            "needs_dependency",
            dependencies=dependencies,
            failures=failures,
            warnings=warnings,
        )
    return PreflightResult("ok", warnings=warnings)


def _dep_type_for(item: object) -> str:
    if getattr(item.operation, "value", item.operation) == PlanOperation.replace_background.value:
        return "analysis_artifact"
    return "asset" if getattr(item, "asset_uid", None) else "analysis_artifact"


def _check_mask(item, executor_input: ExecutorInput, dep) -> None:
    artifacts = executor_input.analysis_artifacts
    preserve = bool(item.parameters.get("preserve_mobility_device"))
    candidates = artifacts.find("foreground_subject_mask", "foreground_subject")
    if preserve:
        ok = any(
            a.semantic_properties.get("contains_person")
            and a.semantic_properties.get("contains_mobility_device")
            for a in candidates
        )
        if not ok:
            dep(
                "analysis_artifact",
                "foreground_subject",
                "foreground_subject_mask[contains_person,contains_mobility_device]",
                f"{item.plan_item_uid}: preserve_mobility_device 需要含轮椅主体蒙版"
                "（不得自动退化为普通人像抠图，§6/场景6）",
            )
        return
    if candidates:
        return
    if artifacts.require("person_mask", "person") is None:
        dep(
            "analysis_artifact",
            "foreground_subject",
            "foreground_subject_mask|person_mask",
            f"{item.plan_item_uid}: 背景替换缺少前景蒙版",
        )


def _infer_capability(item) -> str:
    follow = item.follow or {}
    if follow.get("keyframes"):
        return "keyframes"
    if follow.get("trajectory_ref"):
        return "tracking"
    return "static"
