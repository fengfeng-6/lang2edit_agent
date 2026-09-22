"""Capability Resolver（§40-42/§58）：降级链与策略矩阵。"""

from __future__ import annotations

from gesture_intent.models import ConstraintLevel

from editing_planner.capability.resolver import (
    capability_chain,
    degradation_policy_for,
    resolve_capability,
)
from editing_planner.models import (
    AccessibilityPlanningProfile,
    DegradationPolicy,
    FollowSpec,
    PlanItem,
    PlanItemStatus,
    PlanOperation,
    SpatialAnchor,
    SpatialAnchorType,
    SpatialSpec,
    ToolCapabilityProfile,
)

PROFILE = AccessibilityPlanningProfile()


def _follow_item(level: ConstraintLevel = ConstraintLevel.hard) -> PlanItem:
    """皇冠跟头式 follow 项（§58）。"""
    item = PlanItem(
        plan_item_uid="pln_crown",
        plan_key="req_sticker_01:-:track_overlay",
        operation=PlanOperation.track_overlay,
        source_requirement_ids=["req_sticker_01"],
        spatial_spec=SpatialSpec(
            anchor=SpatialAnchor(type=SpatialAnchorType.spatial_track, target="head"),
            follow=FollowSpec(enabled=True),
        ),
    )
    item.degradation_policy = degradation_policy_for(level, False, {})
    return item


def test_tracking_native_when_supported():
    caps = ToolCapabilityProfile(tracking=True)
    item, dep, warns = resolve_capability(_follow_item(), caps, PROFILE, dep_seq=1)
    assert item.resolved_capability == "tracking"
    assert item.status == PlanItemStatus.planned
    assert not item.degradation_applied and dep is None


def test_keyframes_when_no_tracking():
    caps = ToolCapabilityProfile(tracking=False, keyframes=True)
    item, _, warns = resolve_capability(_follow_item(), caps, PROFILE, dep_seq=1)
    assert item.resolved_capability == "keyframes"
    assert item.status == PlanItemStatus.degraded
    assert item.degradation_applied == ["tracking→keyframes"]
    assert warns  # 降级绝不静默


def test_hard_blocked_when_all_missing():
    """§58：tracking/keyframes 都没有 → hard 是 blocked（static 不算等价）。"""
    caps = ToolCapabilityProfile(tracking=False, keyframes=False)
    item, _, _ = resolve_capability(
        _follow_item(ConstraintLevel.hard), caps, PROFILE, dep_seq=1
    )
    assert item.resolved_capability is None
    assert item.status == PlanItemStatus.blocked


def test_strict_policy_blocks_any_degradation():
    item = _follow_item(ConstraintLevel.hard)
    item.degradation_policy = DegradationPolicy.strict
    caps = ToolCapabilityProfile(tracking=False, keyframes=True)
    item, _, _ = resolve_capability(item, caps, PROFILE, dep_seq=1)
    assert item.status == PlanItemStatus.blocked


def test_soft_static_fallback_with_warning():
    caps = ToolCapabilityProfile(tracking=False, keyframes=False)
    item, _, warns = resolve_capability(
        _follow_item(ConstraintLevel.soft), caps, PROFILE, dep_seq=1
    )
    assert item.resolved_capability == "static"
    assert item.status == PlanItemStatus.degraded
    assert warns


def test_overlay_needs_overlay_capability():
    item = PlanItem(
        plan_item_uid="pln_x", plan_key="k", operation=PlanOperation.add_overlay,
        source_requirement_ids=["r"],
    )
    caps = ToolCapabilityProfile(overlay=False)
    item, _, _ = resolve_capability(item, caps, PROFILE, dep_seq=1)
    assert item.status in (PlanItemStatus.degraded, PlanItemStatus.blocked)


def test_seated_background_needs_foreground_mask():
    """§59：preserve_mobility_device + 无前景主体 mask → 依赖而非能力失败。"""
    profile = AccessibilityPlanningProfile(
        posture="seated", preserve_mobility_device=True
    )
    item = PlanItem(
        plan_item_uid="pln_bg", plan_key="k:bg",
        operation=PlanOperation.replace_background,
        source_requirement_ids=["req_background_01"],
    )
    caps = ToolCapabilityProfile(background_replacement=True,
                                 foreground_subject_mask=False)
    item, dep, _ = resolve_capability(item, caps, profile, dep_seq=3)
    assert item.status == PlanItemStatus.pending_dependency
    assert dep is not None and dep.type.value == "video_analysis"
    assert dep.request_uid == "dep_03"
    # 有 mask 数据前提 → 正常规划
    caps2 = ToolCapabilityProfile(background_replacement=True,
                                  foreground_subject_mask=True)
    item2 = PlanItem(
        plan_item_uid="pln_bg2", plan_key="k:bg2",
        operation=PlanOperation.replace_background,
        source_requirement_ids=["req_background_01"],
    )
    item2, dep2, _ = resolve_capability(item2, caps2, profile, dep_seq=4)
    assert item2.status == PlanItemStatus.planned and dep2 is None
