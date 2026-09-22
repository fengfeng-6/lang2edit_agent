"""Accessibility Validator + §57/§59 验收场景。"""

from __future__ import annotations

from planner_fixtures import (
    background_intent,
    event_req,
    heart_intent,
    make_view,
)
from gesture_intent.models import (
    ConstraintLevel,
    EditingIntent,
    GlobalIntent,
    SemanticValue,
)

from editing_planner import EditingPlanner
from editing_planner.models import (
    AccessibilityPlanningProfile,
    PlanItemStatus,
    ToolCapabilityProfile,
    ValidationStatus,
)


def _plan(intent: EditingIntent, view=None, caps=None, profile=None):
    payload = {
        "editing_intent": intent,
        "semantic_view": view or make_view(),
    }
    if caps is not None:
        payload["tool_capabilities"] = caps
    if profile is not None:
        payload["accessibility_profile"] = profile
    return EditingPlanner().plan(payload)


def test_seated_profile_stamps_on_plan():
    """坐姿是一级策略：profile 解析 + layout/accessibility strategy。"""
    plan = _plan(heart_intent())
    assert plan.accessibility_profile.posture == "seated"
    assert plan.accessibility_profile.primary_action_region == "upper_body"
    assert plan.accessibility_profile.preserve_mobility_device is True
    assert plan.global_strategy.layout_strategy == "upper_body_aware"
    assert plan.global_strategy.accessibility_strategy.gesture_emphasis == "high"


def test_low_amplitude_prefers_semantic_enhancement():
    """§57：低幅度"更有活力" → 手势反馈/光效/节拍，不是镜头运动。"""
    intent = heart_intent()
    intent.global_intent = GlobalIntent(
        mood=SemanticValue(raw="更有活力一点", tags=["energetic"])
    )
    plan = _plan(intent)
    strategy = plan.global_strategy.accessibility_strategy
    assert strategy.camera_motion_intensity == "low"
    # 创意输出走 §20 白名单动画（soft_pop/glow 等），无相机运动项
    for item in plan.plan_items:
        assert item.style_spec.animation in (
            "soft_pop", "glow", "pop", "fade", "flash", "particle", "float"
        )
        assert "camera" not in item.operation.value
    # 节拍/手势事件视觉反馈存在
    assert plan.global_strategy.motion_language or any(
        i.style_spec.animation for i in plan.plan_items
    )


def test_no_standing_event_reliance_for_seated():
    """坐姿引用 jump 等站姿事件 → accessibility 违规（防御检查 §44-8）。"""
    intent = EditingIntent(
        event_bound_requirements=[
            event_req("event_req_09", "jump", source_text="跳跃时加特效",
                      description="闪光", canonical_desc="flash")
        ]
    )
    # 构造含 jump 事件的视图（绕过模块二 not_seated 过滤，验证 planner 防御）
    from planner_fixtures import _brief
    view = make_view(events=[
        _brief("evt_j1", "body_jump_01", "jump", 1, 5.0, 5.3, 5.6,
               etype="body_action"),
    ])
    plan = _plan(intent, view=view)
    codes = [i.code for i in plan.validation.accessibility.issues]
    assert "standing_event_reliance" in codes


def test_seated_background_needs_dependency():
    """§59：坐姿换背景且 preserve_mobility_device → pending + dependency。"""
    plan = _plan(background_intent())
    assert plan.validation.status in (
        ValidationStatus.needs_dependency, ValidationStatus.blocked
    )
    assert plan.dependency_requests, "应发出 foreground_subject 分割依赖"
    dep = plan.dependency_requests[0]
    assert dep.type.value == "video_analysis"
    item = next(i for i in plan.plan_items
                if i.operation.value == "replace_background")
    assert item.status == PlanItemStatus.pending_dependency


def test_background_without_preserve_flag_plans_normally():
    """站姿主体换背景 → 无 mask 前提，正常 planned。"""
    plan = _plan(
        background_intent(),
        profile=AccessibilityPlanningProfile(posture="standing"),
        caps=ToolCapabilityProfile(background_replacement=True),
    )
    item = next(i for i in plan.plan_items
                if i.operation.value == "replace_background")
    assert item.status == PlanItemStatus.planned
    assert not plan.dependency_requests


def test_face_occlusion_flagged_when_unavoidable():
    """脸保护区无法避让时：hard avoid_overlap(face) → accessibility hard。"""
    from planner_fixtures import _spatial
    # 全屏都是脸保护区——任何位置都撞脸，placement 只能 fell_back
    view = make_view(spatial={
        "gesture_heart_01": _spatial(
            anchor=(0.5, 0.62), face=(0.0, 0.0, 1.0, 1.0)),
        "gesture_heart_02": _spatial(
            anchor=(0.5, 0.62), face=(0.0, 0.0, 1.0, 1.0)),
    })
    plan = _plan(heart_intent(), view=view)
    codes = [i.code for i in plan.validation.accessibility.issues]
    assert "face_occlusion" in codes
    hard = [i.code for i in plan.validation.hard_violations]
    assert "face_occlusion" in hard


def test_low_amplitude_never_dismissed():
    """§3 不变量：低幅度动作不得被当作无效动作。"""
    plan = _plan(heart_intent())
    reasons = [u.reason for u in plan.unresolved]
    assert "low_amplitude" not in reasons and "no_motion" not in reasons
    assert not any(
        i.code == "low_amplitude_dismissed"
        for i in plan.validation.accessibility.issues
    )
