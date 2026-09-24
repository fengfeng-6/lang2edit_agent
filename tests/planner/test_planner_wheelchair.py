"""轮椅/坐姿用户典型剪辑偏好场景（§3/§20-24/§31-33/§44/§55/§59）。

覆盖的画像组合与偏好：

- 单侧上肢："贴纸跟着我右手" → 跟随目标是活跃手而非双手中点；
- 震颤："皇冠跟着头" → 强平滑 + 低灵敏度 + 大死区 + 抽稀关键帧；
- 低幅度：仍按语义强度卡点（§33 不看位移大小）；
- 坐姿构图：素材落入画面下三分之一（轮椅/腿部区域）要报警告；
- 轮椅保护区：mobility_device 进避让区 + 用户显式约束可命名；
- 背景替换：preserve_mobility_device 时必须有前景主体 mask 数据前提。
"""

from __future__ import annotations

import math

import pytest

from planner_fixtures import (
    _brief,
    _spatial,
    event_req,
    follow_intent,
    heart_intent,
    make_view,
)
from gesture_intent.models import (
    Constraint,
    ConstraintLevel,
    EditingIntent,
    EventBoundRequirement,
    EventReference,
    EventRequirement,
    EventTrigger,
    EventType,
    GlobalIntent,
    ObjectAction,
    ObjectRequirement,
    ObjectType,
    Occurrence,
    OccurrenceType,
    SemanticValue,
    TemporalRelation,
)

from editing_planner import EditingPlanner
from editing_planner.accessibility.validator import validate_accessibility
from editing_planner.expansion.requirements import EventIndex
from editing_planner.models import (
    AccessibilityPlanningProfile,
    AssetBinding,
    LogicalEditingPlan,
    PlanItem,
    PlanItemStatus,
    PlanOperation,
    SpatialSpec,
    ToolCapabilityProfile,
)

SEATED = AccessibilityPlanningProfile(
    posture="seated", preserve_mobility_device=True
)
RIGHT_ONLY = AccessibilityPlanningProfile(
    posture="seated", active_hands=["right"], preserve_mobility_device=True
)
TREMOR = AccessibilityPlanningProfile(
    posture="seated", tremor=True, preserve_mobility_device=True
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


def _validator_issues(item: PlanItem, profile, position=None, scale=0.16, view=None):
    """直接驱动 accessibility validator（绕过 placement，钉死 §44 检查项）。"""
    plan = LogicalEditingPlan(
        plan_uid="pln_test", plan_items=[item], accessibility_profile=profile
    )
    positions = {item.plan_item_uid: (position, scale)} if position else {}
    return validate_accessibility(
        plan, profile, EventIndex(view or make_view()), positions
    )


# ---------------------------------------------------------------------------
# §32 单侧上肢
# ---------------------------------------------------------------------------


def test_single_hand_follow_targets_active_hand():
    """"贴纸跟着我" + 只有右手 → track_overlay 锚定 right_hand（§32）。"""
    plan = _plan(follow_intent(), profile=RIGHT_ONLY)
    item = plan.plan_items[0]
    assert item.operation == PlanOperation.track_overlay
    assert item.spatial_spec.anchor.type.value == "spatial_track"
    assert item.spatial_spec.anchor.target == "right_hand"
    assert item.spatial_spec.follow.enabled


def test_single_hand_left_variant():
    """左利手/左侧上肢用户 → left_hand，绝不退化成双手中点。"""
    profile = AccessibilityPlanningProfile(
        posture="seated", active_hands=["left"], preserve_mobility_device=True
    )
    plan = _plan(follow_intent(), profile=profile)
    assert plan.plan_items[0].spatial_spec.anchor.target == "left_hand"


def test_event_bound_follow_uses_active_hand():
    """事件级跟随（"挥手时星星跟着手"）同样锚定活跃手。"""
    intent = EditingIntent(event_bound_requirements=[
        event_req("req_wave", "wave_hand", source_text="挥手时星星跟着手",
                  description="星星跟着手", canonical_desc="star")
    ])
    view = make_view(
        events=[_brief("evt_w1", "gesture_wave_01", "wave_hand", 1, 3.0, 3.3, 3.6)],
        spatial={"gesture_wave_01": _spatial(anchor=(0.6, 0.5))},
    )
    plan = _plan(intent, view=view, profile=RIGHT_ONLY)
    item = plan.plan_items[0]
    assert item.operation == PlanOperation.track_overlay
    assert item.spatial_spec.anchor.target == "right_hand"
    assert item.spatial_spec.follow.enabled


def test_single_hand_gesture_events_plan_normally():
    """single_hand_heart / finger_heart / hand_raise 是单手主体的正常事件。"""
    intent = EditingIntent(event_bound_requirements=[
        event_req("r1", "single_hand_heart", description="爱心", canonical_desc="heart"),
        event_req("r2", "finger_heart", description="光效", canonical_desc="glow"),
        event_req("r3", "hand_raise", description="彩带", canonical_desc="confetti"),
    ])
    view = make_view(events=[
        _brief("e1", "gesture_sh_01", "single_hand_heart", 1, 2.0, 2.3, 2.6),
        _brief("e2", "gesture_fh_01", "finger_heart", 1, 5.0, 5.3, 5.6),
        _brief("e3", "gesture_hr_01", "hand_raise", 1, 8.0, 8.3, 8.6),
    ], spatial={
        "gesture_sh_01": _spatial(),
        "gesture_fh_01": _spatial(),
        "gesture_hr_01": _spatial(),
    })
    plan = _plan(intent, view=view, profile=RIGHT_ONLY)
    assert len(plan.plan_items) == 3
    assert all(i.status == PlanItemStatus.planned for i in plan.plan_items)
    # §33：单手比心类仍是 high 优先级卡点
    snap = {i.plan_key.split(":")[0]: (i.temporal_spec.beat_snap or {}).get("priority")
            for i in plan.plan_items}
    assert snap["r1"] == "high" and snap["r2"] == "high"
    assert snap["r3"] == "medium_high"


# ---------------------------------------------------------------------------
# §33 节拍对齐不按动作幅度
# ---------------------------------------------------------------------------


def test_low_amplitude_keeps_semantic_beat_priority():
    """very_low 幅度用户的比心/拍手仍是 high 优先级卡点（§3/§33）。"""
    profile = AccessibilityPlanningProfile(
        posture="seated", motion_amplitude="very_low", amplitude_scale=0.3,
        preserve_mobility_device=True,
    )
    plan = _plan(heart_intent(), profile=profile)
    heart = next(i for i in plan.plan_items
                 if i.parameters.get("occurrence_index") == 1)
    assert heart.temporal_spec.beat_snap == {"priority": "high", "max_shift": 0.20}


def test_beat_snap_priority_ladder():
    """point_* → medium_high(0.12)；minor motion → 不对齐。"""
    intent = EditingIntent(event_bound_requirements=[
        event_req("r_point", "point_right", description="箭头", canonical_desc="arrow"),
        event_req("r_minor", "shoulder_shrug", description="点缀", canonical_desc="dot"),
    ])
    view = make_view(events=[
        _brief("e1", "gesture_point_01", "point_right", 1, 6.0, 6.3, 6.6),
        _brief("e2", "gesture_shrug_01", "shoulder_shrug", 1, 7.0, 7.2, 7.4),
    ], spatial={
        "gesture_point_01": _spatial(direction="right"),
        "gesture_shrug_01": _spatial(),
    })
    plan = _plan(intent, view=view)
    point = next(i for i in plan.plan_items if "r_point" in i.source_requirement_ids)
    minor = next(i for i in plan.plan_items if "r_minor" in i.source_requirement_ids)
    assert point.temporal_spec.beat_snap["max_shift"] == 0.12
    assert minor.temporal_spec.beat_snap is None


def test_beat_driven_pacing_keeps_camera_calm():
    """"卡点"+低幅度：节奏可以 beat_driven，镜头运动仍保持 low。"""
    intent = heart_intent()
    intent.global_intent.pacing = SemanticValue(raw="卡点时间轴", canonical="beat")
    plan = _plan(intent)
    assert plan.global_strategy.pacing_strategy == "beat_driven"
    assert plan.global_strategy.accessibility_strategy.camera_motion_intensity == "low"


# ---------------------------------------------------------------------------
# §31 震颤稳定化
# ---------------------------------------------------------------------------


def test_tremor_follow_stabilized_params():
    """tremor 主体的跟随必须强平滑参数，且不触发 follow_jitter。"""
    plan = _plan(follow_intent(), profile=TREMOR)
    spec = plan.plan_items[0].spatial_spec.follow
    assert spec.smoothing == "strong"
    assert spec.sensitivity == 0.5
    assert spec.dead_zone == 0.03
    assert spec.keyframe_density == 0.3
    assert not any(
        i.code == "follow_jitter" for i in plan.validation.accessibility.issues
    )


def test_tremor_degradation_keeps_stabilization():
    """tracking→keyframes 降级后稳定化参数不丢（降级≠放弃平滑）。"""
    caps = ToolCapabilityProfile(tracking=False, keyframes=True)
    plan = _plan(follow_intent(), caps=caps, profile=TREMOR)
    item = plan.plan_items[0]
    assert item.status == PlanItemStatus.degraded
    assert item.degradation_applied == ["tracking→keyframes"]
    assert item.spatial_spec.follow.smoothing == "strong"


def test_tremor_materialize_thins_keyframes():
    """materialize：同一条抖动轨迹，tremor 抽稀出远少于正常的关键帧。"""
    points = [
        {
            "t": i / 5.0,
            "x": 0.5 + 0.06 * math.sin(i * 0.25) + 0.005 * math.sin(i * 2.3),
            "y": 0.16 + 0.04 * math.cos(i * 0.2) + 0.004 * math.cos(i * 1.9),
        }
        for i in range(61)
    ]
    tracks = {"head": {"points": points}}
    caps = ToolCapabilityProfile(tracking=False, keyframes=True)
    planner = EditingPlanner()

    counts = []
    for profile in (TREMOR, SEATED):
        plan = planner.plan({
            "editing_intent": follow_intent(),
            "semantic_view": make_view(),
            "accessibility_profile": profile,
            "tool_capabilities": caps,
        })
        item = plan.plan_items[0]
        res = planner.materialize(
            plan,
            bindings=[AssetBinding(
                asset_request_uid=item.asset_request_ref, asset_uid="a_crown"
            )],
            semantic_view=make_view(),
            spatial_tracks=tracks,
        )
        counts.append(len(res.resolved_items[0].follow["keyframes"]))
    assert counts[0] < counts[1] / 2  # 震颤：~4 vs 正常：~13


def test_tremor_weak_smoothing_flagged():
    """§44-7：tremor 画像 + 未稳定化跟随 → follow_jitter hard。"""
    from editing_planner.models import FollowSpec, SpatialAnchor, SpatialAnchorType

    item = PlanItem(
        plan_item_uid="pln_j", plan_key="k",
        operation=PlanOperation.track_overlay,
        spatial_spec=SpatialSpec(
            anchor=SpatialAnchor(type=SpatialAnchorType.spatial_track, target="head"),
            follow=FollowSpec(enabled=True, smoothing="none", keyframe_density=0.9),
        ),
    )
    issues = _validator_issues(item, TREMOR, position=(0.5, 0.3))
    jitter = [i for i in issues if i.code == "follow_jitter"]
    assert jitter and jitter[0].severity == "hard"


# ---------------------------------------------------------------------------
# §21-23 坐姿构图与轮椅保护区
# ---------------------------------------------------------------------------


def test_seated_lower_third_placement_warns():
    """胸口锚点下方的文字落入画面下三分之一 → seated_composition 警告。"""
    req = event_req("r_txt", "heart_gesture", object_type=ObjectType.text,
                    description="加油", canonical_desc="cheer")
    req.requirement.content = "加油"
    plan = _plan(EditingIntent(event_bound_requirements=[req]))
    codes = [i.code for i in plan.validation.accessibility.issues]
    assert "seated_composition" in codes
    # 只是 warning——不阻塞计划，但提示人工确认
    assert all(i.severity != "hard" for i in plan.validation.accessibility.issues)


def test_standing_lower_third_not_flagged():
    """站姿主体的下三分之一是正常腿部区域——不报 seated_composition。"""
    req = event_req("r_txt", "heart_gesture", object_type=ObjectType.text,
                    description="加油", canonical_desc="cheer")
    req.requirement.content = "加油"
    plan = _plan(
        EditingIntent(event_bound_requirements=[req]),
        profile=AccessibilityPlanningProfile(posture="standing"),
    )
    codes = [i.code for i in plan.validation.accessibility.issues]
    assert "seated_composition" not in codes


def test_mobility_device_in_avoid_regions():
    """§21/§23：preserve_mobility_device → mobility_device 进避让区名。"""
    plan = _plan(heart_intent())
    for item in plan.plan_items:
        assert "mobility_device" in item.spatial_spec.avoid_regions
        assert "upper_torso" in item.spatial_spec.avoid_regions


def test_user_constraint_can_name_wheelchair():
    """"别挡到我的轮椅"/"别贴到人身上" → 避让区补上 mobility_device/person。"""
    intent = heart_intent()
    intent.constraints.append(Constraint(
        id="constraint_wheelchair", type="avoid_overlap",
        reference="mobility_device", raw="别挡到我的轮椅",
    ))
    intent.constraints.append(Constraint(
        id="constraint_person", type="avoid_overlap",
        reference="person", raw="贴纸别贴到人身上",
    ))
    plan = _plan(intent)
    item = plan.plan_items[0]
    assert "mobility_device" in item.spatial_spec.avoid_regions
    assert "person" in item.spatial_spec.avoid_regions
    assert "constraint_wheelchair" in item.constraint_refs
    assert "constraint_person" in item.constraint_refs


def test_occlusion_codes_for_gesture_and_hands():
    """§44-1/3：素材压住手势区/活跃手 → 对应遮挡 warning（不碰脸时无 face 项）。"""
    from editing_planner.models import SpatialAnchor, SpatialAnchorType

    item = PlanItem(
        plan_item_uid="pln_occ", plan_key="k",
        operation=PlanOperation.add_overlay,
        spatial_spec=SpatialSpec(
            anchor=SpatialAnchor(
                type=SpatialAnchorType.event_anchor, event_uid="evt_h1"
            ),
        ),
    )
    # 素材正压在 event_anchor (0.5,0.62) 上：手势区与双手区都重叠
    issues = _validator_issues(item, SEATED, position=(0.5, 0.62))
    codes = {i.code for i in issues}
    assert "gesture_region_occlusion" in codes
    assert "active_hands_occlusion" in codes
    assert "face_occlusion" not in codes


# ---------------------------------------------------------------------------
# §24/§59 背景替换保护轮椅
# ---------------------------------------------------------------------------


def test_bg_replace_plans_when_mask_data_available():
    """§59 正向：执行端能消费 foreground_subject_mask → 正常 planned。"""
    caps = ToolCapabilityProfile(
        background_replacement=True, foreground_subject_mask=True
    )
    from planner_fixtures import background_intent

    plan = _plan(background_intent(), caps=caps)
    item = next(i for i in plan.plan_items
                if i.operation == PlanOperation.replace_background)
    assert item.status == PlanItemStatus.planned
    assert not plan.dependency_requests


def test_preserve_device_without_seated_still_needs_mask():
    """站姿但显式声明保留辅助设备（假肢/支具）→ 同样要求 mask 前提。"""
    from planner_fixtures import background_intent

    profile = AccessibilityPlanningProfile(
        posture="standing", preserve_mobility_device=True
    )
    caps = ToolCapabilityProfile(
        background_replacement=True, foreground_subject_mask=False
    )
    plan = _plan(background_intent(), caps=caps, profile=profile)
    item = next(i for i in plan.plan_items
                if i.operation == PlanOperation.replace_background)
    assert item.status == PlanItemStatus.pending_dependency
    assert plan.dependency_requests


def test_bg_replace_without_capability_is_not_silent():
    """执行端不支持换背景 → hard 需求 blocked，而不是静默出半成品。"""
    from planner_fixtures import background_intent

    caps = ToolCapabilityProfile(background_replacement=False)
    plan = _plan(background_intent(), caps=caps)
    item = next(i for i in plan.plan_items
                if i.operation == PlanOperation.replace_background)
    assert item.status == PlanItemStatus.blocked


# ---------------------------------------------------------------------------
# §44-6/§44-8 防御性检查
# ---------------------------------------------------------------------------


def test_crop_safety_on_subject_target():
    """§55：对主体的缩放/移动是 hard 违规——宁可少裁。"""
    item = PlanItem(
        plan_item_uid="pln_crop", plan_key="k",
        operation=PlanOperation.scale_adjust,
        target={"type": "video", "value": "main_video"},
    )
    issues = _validator_issues(item, SEATED)
    crop = [i for i in issues if i.code == "crop_safety"]
    assert crop and crop[0].severity == "hard"


def test_all_standing_only_events_flagged():
    """§44-8：jump/squat/stand_up 全部命中 standing_event_reliance。"""
    events = [
        _brief("e_j", "body_jump_01", "jump", 1, 1.0, 1.2, 1.4, etype="body_action"),
        _brief("e_s", "body_squat_01", "squat", 1, 2.0, 2.2, 2.4, etype="body_action"),
        _brief("e_u", "body_stand_01", "stand_up", 1, 3.0, 3.2, 3.4, etype="body_action"),
    ]
    view = make_view(events=events)
    plan = LogicalEditingPlan(
        plan_uid="pln_test",
        plan_items=[
            PlanItem(
                plan_item_uid=f"pln_{uid}", plan_key=f"k:{uid}",
                operation=PlanOperation.add_overlay,
                target={"type": "event", "value": uid},
            )
            for uid in ("e_j", "e_s", "e_u")
        ],
        accessibility_profile=SEATED,
    )
    issues = validate_accessibility(plan, SEATED, EventIndex(view), {})
    flagged = [i for i in issues if i.code == "standing_event_reliance"]
    assert len(flagged) == 3
    assert all(i.severity == "hard" for i in flagged)


# ---------------------------------------------------------------------------
# 画像解析与透传
# ---------------------------------------------------------------------------


def test_mirrored_profile_passes_through():
    """前置镜像自拍用户：mirrored 标记透传到 plan（左右已由模块二互换）。"""
    profile = AccessibilityPlanningProfile(
        posture="seated", mirrored=True, preserve_mobility_device=True
    )
    plan = _plan(heart_intent(), profile=profile)
    assert plan.accessibility_profile.mirrored is True
    assert plan.accessibility_profile.posture == "seated"


def test_standing_profile_gets_full_body_strategy():
    """对照组：站姿主体 → full_body 布局 + 常规强调（坐姿不是全局默认）。"""
    plan = _plan(
        heart_intent(),
        profile=AccessibilityPlanningProfile(posture="standing"),
    )
    strategy = plan.global_strategy
    assert strategy.layout_strategy == "full_body_aware"
    assert strategy.accessibility_strategy.gesture_emphasis == "normal"
    assert strategy.accessibility_strategy.camera_motion_intensity == "low_to_medium"
    item = plan.plan_items[0]
    assert "mobility_device" not in item.spatial_spec.avoid_regions
    assert "upper_torso" not in item.spatial_spec.avoid_regions


def test_subject_profile_dict_from_view():
    """模块二 subject_profile dict 驱动画像——用户也可显式覆盖（§8）。"""
    view = make_view(subject_profile={
        "posture": "seated", "available_hands": ["left"],
        "amplitude": "very_low", "amplitude_scale": 0.3,
        "tremor": True, "mirrored": False, "inferred": True,
    })
    plan = _plan(follow_intent(), view=view)
    profile = plan.accessibility_profile
    assert profile.posture == "seated"
    assert profile.active_hands == ["left"]
    assert profile.motion_amplitude == "very_low"
    assert profile.tremor is True
    assert profile.inferred is True
    # 单手 + 震颤：跟随目标是左手且参数稳定化
    follow = plan.plan_items[0].spatial_spec.follow
    assert plan.plan_items[0].spatial_spec.anchor.target == "left_hand"
    assert follow.smoothing == "strong"
