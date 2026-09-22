"""Accessibility Context Builder（§9 流水线第二环）。

把输入侧的可动性画像归一为 ``AccessibilityPlanningProfile``：

    显式 accessibility_profile（AccessibilityPlanningProfile /
        MobilityProfile / dict，两者 schema 由 Union 校验区分）
        → semantic_view.subject_profile（模块二 §8 补充字段）
        → AccessibilityPlanningProfile() 默认值

Planner 不从稠密轨迹重新推断主体可动性（§8）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from video_understanding.models import MobilityProfile

from ..models import AccessibilityPlanningProfile, LayoutPreferences


def resolve_accessibility_profile(
    explicit: Optional[Union[AccessibilityPlanningProfile, MobilityProfile, Dict[str, Any]]],
    semantic_view: Optional[Any],
) -> AccessibilityPlanningProfile:
    """按优先级解析无障碍画像；返回的实例会被盖到 LogicalEditingPlan 上。"""

    if explicit is not None:
        return to_planning_profile(explicit)
    subject = getattr(semantic_view, "subject_profile", None) if semantic_view is not None else None
    if isinstance(semantic_view, dict):
        subject = semantic_view.get("subject_profile")
    if isinstance(subject, MobilityProfile):
        return AccessibilityPlanningProfile.from_mobility_profile(subject)
    if isinstance(subject, dict) and subject:
        # SemanticView.subject_profile 是 dict（MobilityProfile dump）
        return AccessibilityPlanningProfile.from_mobility_profile(
            MobilityProfile.parse_obj(subject)
            if not hasattr(MobilityProfile, "model_validate")
            else MobilityProfile.model_validate(subject)
        )
    return AccessibilityPlanningProfile()


def to_planning_profile(
    value: Union[AccessibilityPlanningProfile, MobilityProfile, Dict[str, Any]],
) -> AccessibilityPlanningProfile:
    """任意可接受的画像表示 → AccessibilityPlanningProfile。"""

    if isinstance(value, AccessibilityPlanningProfile):
        return value
    if isinstance(value, MobilityProfile):
        return AccessibilityPlanningProfile.from_mobility_profile(value)
    if isinstance(value, dict):
        # MobilityProfile 形态（available_hands/amplitude）先转换；
        # 否则按 AccessibilityPlanningProfile 字段校验。
        if "available_hands" in value or "amplitude" in value:
            return AccessibilityPlanningProfile.from_mobility_profile(
                MobilityProfile.parse_obj(value)
                if not hasattr(MobilityProfile, "model_validate")
                else MobilityProfile.model_validate(value)
            )
        merged = dict(value)
        layout = merged.get("layout_preferences")
        if isinstance(layout, dict):
            merged["layout_preferences"] = LayoutPreferences.parse_obj(layout) \
                if not hasattr(LayoutPreferences, "model_validate") \
                else LayoutPreferences.model_validate(layout)
        return AccessibilityPlanningProfile.parse_obj(merged) \
            if not hasattr(AccessibilityPlanningProfile, "model_validate") \
            else AccessibilityPlanningProfile.model_validate(merged)
    raise TypeError(f"unsupported accessibility_profile type: {type(value)!r}")
