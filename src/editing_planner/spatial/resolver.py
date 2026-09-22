"""SpatialSpec 组装（§28-32）：锚点选择、避让区声明、跟随策略。

Logical 阶段只写"引用 + 策略"（哪个 event、哪条轨道、什么关系），
具体坐标由 materialize 计算；placement.py 的几何逻辑在两端复用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from gesture_intent.models import Constraint, EventBoundRequirement

from ..accessibility.policies import follow_policy
from ..models import (
    AccessibilityPlanningProfile,
    FollowSpec,
    PlanOperation,
    SpatialAnchor,
    SpatialAnchorType,
    SpatialRelation,
    SpatialSpec,
)

#: 用户 avoid_overlap 约束的 reference → avoid_regions 名
_CONSTRAINT_REGION = {
    "face": "face",
    "person": "person",
    "hand": "active_hands",
    "hands": "active_hands",
    "gesture": "active_gesture",
}


def constraint_avoid_regions(constraints: Sequence[Constraint]) -> List[str]:
    """avoid_overlap / preserve_subject 约束 → 额外避让区名。"""
    out: List[str] = []
    for c in constraints:
        if c.type == "avoid_overlap" and c.reference:
            name = _CONSTRAINT_REGION.get(c.reference, c.reference)
            if name not in out:
                out.append(name)
        elif c.type == "preserve_subject" and "person" not in out:
            out.append("person")
    return out


def spatial_spec_for_event(
    requirement: EventBoundRequirement,
    event_uid: str,
    operation: PlanOperation,
    profile: AccessibilityPlanningProfile,
    constraints: Sequence[Constraint],
    *,
    relation_preference: Optional[str] = None,
    follow: bool = False,
) -> SpatialSpec:
    """事件绑定项的 SpatialSpec。

    - 锚点默认 event_anchor；follow=True 时换 spatial_track（§30）；
    - 坐姿 → avoid_regions 补 upper_torso；preserve_mobility_device →
      mobility_device（§21）；
    - point_* 类方向事件 → direction_policy=match_event_direction（§54）。
    """
    avoid = default_avoid_regions(profile)
    for name in constraint_avoid_regions(constraints):
        if name not in avoid:
            avoid.append(name)

    canonical = (requirement.trigger.event.canonical or "")
    direction_policy: Dict[str, Any] = {"mode": "none"}
    if canonical.startswith("point_") or canonical in ("wave_hand", "move_left", "move_right"):
        direction_policy = {"mode": "match_event_direction"}

    anchor = SpatialAnchor(type=SpatialAnchorType.event_anchor, event_uid=event_uid)
    follow_spec = FollowSpec(enabled=False)

    relation = _default_relation(operation)
    if relation_preference in SpatialRelation._value2member_map_:
        relation = SpatialRelation(relation_preference)

    if follow:
        target = _follow_target(profile)
        anchor = SpatialAnchor(type=SpatialAnchorType.spatial_track, target=target)
        policy = follow_policy(profile.tremor, {})
        follow_spec = FollowSpec(enabled=True, mode="trajectory", **policy)
        relation = SpatialRelation.follow

    return SpatialSpec(
        anchor=anchor,
        relation=relation,
        avoid_regions=avoid,
        follow=follow_spec,
        direction_policy=direction_policy,
    )


def spatial_spec_for_global(
    operation: PlanOperation,
    profile: AccessibilityPlanningProfile,
    constraints: Sequence[Constraint],
    *,
    relation_preference: Optional[str] = None,
) -> SpatialSpec:
    """非事件项（全局贴纸/文字/背景）的 SpatialSpec：结构槽位锚点。"""
    avoid = default_avoid_regions(profile)
    for name in constraint_avoid_regions(constraints):
        if name not in avoid:
            avoid.append(name)
    relation = _default_relation(operation)
    if relation_preference in SpatialRelation._value2member_map_:
        relation = SpatialRelation(relation_preference)
    return SpatialSpec(
        anchor=SpatialAnchor(
            type=SpatialAnchorType.structural, structure_key="screen_center"
        ),
        relation=relation,
        avoid_regions=avoid,
    )


def default_avoid_regions(profile: AccessibilityPlanningProfile) -> List[str]:
    """按 §21-22 与 layout_preferences 组装默认避让区名。"""
    prefs = profile.layout_preferences
    out: List[str] = []
    if prefs.protect_face:
        out.append("face")
    if prefs.protect_gesture_region:
        out.append("active_gesture")
    if prefs.protect_active_hands:
        out.append("active_hands")
    if profile.posture == "seated":
        if "upper_torso" not in out:
            out.append("upper_torso")
        if profile.preserve_mobility_device and "mobility_device" not in out:
            out.append("mobility_device")
    return out


def _default_relation(operation: PlanOperation) -> SpatialRelation:
    if operation == PlanOperation.add_text:
        return SpatialRelation.below
    if operation == PlanOperation.add_effect:
        return SpatialRelation.centered_on
    return SpatialRelation.above


def _follow_target(profile: AccessibilityPlanningProfile) -> str:
    """跟随目标：单手主体用活跃手（§32），否则默认 head。"""
    if len(profile.active_hands) == 1:
        return f"{profile.active_hands[0]}_hand"
    return "head"
