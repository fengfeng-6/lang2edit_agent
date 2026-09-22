"""Capability Resolver（§40-42/§58-59）：工具能力检查与降级。

降级链按操作声明（如 follow 项 tracking→keyframes→static）；
每步降级都记入 ``degradation_applied`` 并产生 warning——绝不静默。

策略 × 需求强度矩阵：

- ``strict``（需求在 protected_requirements 或参数显式锁定机制）：
  只允许链头，链头不满足 → blocked；
- hard → ``allow_equivalent``：可走"等价"链段（tracking→keyframes），
  ``static`` 链底不算等价——§58：hard 跟随在 tracking/keyframes 都没有时
  是 blocked 而非静态放置；
- soft/open → ``allow_simplification``：允许落到 static 兜底 + warning。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from gesture_intent.models import ConstraintLevel

from ..models import (
    AccessibilityPlanningProfile,
    DegradationPolicy,
    DependencyType,
    PlanItem,
    PlanItemStatus,
    PlanOperation,
    PlannerDependencyRequest,
    ToolCapabilityProfile,
)

#: 操作 → 能力降级链（§41）：链头是首选实现，越往后越简化；
#: 末尾 "static" 是简化兜底（不等价），只对 allow_simplification 开放
_CAPABILITY_LADDER: Dict[PlanOperation, List[str]] = {
    PlanOperation.track_overlay: ["tracking", "keyframes", "static"],
    PlanOperation.add_overlay: ["overlay"],
    PlanOperation.add_text: ["text"],
    PlanOperation.add_effect: ["overlay"],  # 无动画能力时退化为静态 overlay
    PlanOperation.add_music: ["audio"],
    PlanOperation.replace_music: ["audio"],
    PlanOperation.add_sound_effect: ["audio"],
    PlanOperation.volume_adjust: ["audio"],
    PlanOperation.freeze: ["freeze"],
    PlanOperation.scale_adjust: ["scale_animation", "keyframes", "static"],
    PlanOperation.position_adjust: ["position_animation", "keyframes", "static"],
    PlanOperation.replace_background: ["background_replacement"],
}

#: "static" 是简化链底——对 allow_equivalent 不可用（§58）
_STATIC_RUNG = "static"


def capability_chain(item: PlanItem) -> List[str]:
    """PlanItem 的能力链；follow 项一律走 tracking→keyframes→static。"""
    if item.spatial_spec is not None and item.spatial_spec.follow.enabled:
        return ["tracking", "keyframes", _STATIC_RUNG]
    return list(_CAPABILITY_LADDER.get(item.operation, [_STATIC_RUNG]))


def degradation_policy_for(
    constraint_level: ConstraintLevel,
    protected: bool,
    parameters: Dict[str, Any],
) -> DegradationPolicy:
    """§42：需求强度 → 降级策略。"""
    if protected or parameters.get("require_native"):
        return DegradationPolicy.strict
    if constraint_level == ConstraintLevel.hard:
        return DegradationPolicy.allow_equivalent
    return DegradationPolicy.allow_simplification


def resolve_capability(
    item: PlanItem,
    capabilities: ToolCapabilityProfile,
    profile: AccessibilityPlanningProfile,
    *,
    dep_seq: int,
) -> Tuple[PlanItem, Optional[PlannerDependencyRequest], List[str]]:
    """沿能力链走到首个可用能力；返回 (item, dependency?, warnings)。"""
    warnings: List[str] = []
    chain = capability_chain(item)
    item.capability_requirements = list(chain)

    # §59/§24：坐姿换背景是数据前提而非能力问题——
    # 执行端有能力但缺 foreground_subject_mask 时发依赖请求。
    if (
        item.operation == PlanOperation.replace_background
        and capabilities.background_replacement
        and profile.preserve_mobility_device
        and not capabilities.foreground_subject_mask
    ):
        item.status = PlanItemStatus.pending_dependency
        dep = PlannerDependencyRequest(
            request_uid=f"dep_{dep_seq:02d}",
            type=DependencyType.video_analysis,
            required_queries=[{"type": "segmentation", "reference": "foreground_subject"}],
            reason=(
                "坐姿/轮椅场景的背景替换要求 foreground_subject_mask 覆盖"
                "主体+辅助设备（§24）；当前数据不满足，不得静默抠掉轮椅"
            ),
            blocking=True,
            requirement_ids=list(item.source_requirement_ids),
        )
        return item, dep, warnings

    # 策略决定可用链段：strict 只用链头；allow_equivalent 排除 static；
    # allow_simplification 全链可用
    if item.degradation_policy == DegradationPolicy.strict:
        allowed = chain[:1]
    elif item.degradation_policy == DegradationPolicy.allow_equivalent:
        allowed = [r for r in chain if r != _STATIC_RUNG]
    else:
        allowed = list(chain)

    resolved = next(
        (r for r in allowed if _supports(r, capabilities)), None
    )
    if resolved is None and item.degradation_policy == DegradationPolicy.allow_simplification:
        resolved = _STATIC_RUNG  # static 恒可用

    item.resolved_capability = resolved
    if resolved is None:
        item.status = PlanItemStatus.blocked
        warnings.append(
            f"{item.plan_item_uid}: hard 需求所需能力均不可用（{chain}）→ blocked"
        )
        return item, None, warnings

    if resolved != chain[0]:
        item.degradation_applied.append(f"{chain[0]}→{resolved}")
        item.status = PlanItemStatus.degraded
        warnings.append(
            f"{item.plan_item_uid}: {chain[0]} 不可用，降级为 {resolved}"
        )
    else:
        item.status = PlanItemStatus.planned
    return item, None, warnings


def _supports(capability: str, capabilities: ToolCapabilityProfile) -> bool:
    if capability == _STATIC_RUNG:
        return True
    return bool(getattr(capabilities, capability, False))

