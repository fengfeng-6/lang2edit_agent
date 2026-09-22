"""Spatial placement（§21-22/§28-32/§54）：关系偏移、避让、跟随。"""

from __future__ import annotations

from planner_fixtures import make_view

from editing_planner.accessibility.policies import follow_policy
from editing_planner.models import AccessibilityPlanningProfile, SpatialRelation
from editing_planner.spatial.placement import (
    apply_relation,
    bbox_around,
    choose_position,
    clamp_point,
    collect_avoid_regions,
    direction_relation,
    iou,
    single_hand_target,
)
from editing_planner.spatial.resolver import (
    default_avoid_regions,
    spatial_spec_for_event,
)
from planner_fixtures import event_req


PROFILE = AccessibilityPlanningProfile(posture="seated", active_hands=["left", "right"])


def test_relation_offsets():
    anchor = (0.5, 0.5)
    assert apply_relation(anchor, "above", 0.1) == (0.5, 0.4)
    assert apply_relation(anchor, "below", 0.1) == (0.5, 0.6)
    assert apply_relation(anchor, "left_of", 0.1) == (0.4, 0.5)
    # 屏幕侧槽位固定 x，保留锚点 y
    assert apply_relation(anchor, "screen_right", 0.1) == (0.8, 0.5)


def test_direction_relation_mapping():
    assert direction_relation("right") == "screen_right"
    assert direction_relation("lower_left") == "screen_left"
    assert direction_relation("up") == "above"
    assert direction_relation("down") == "below"


def test_iou_basics():
    a = (0.0, 0.0, 0.2, 0.2)
    b = (0.1, 0.1, 0.3, 0.3)
    assert 0 < iou(a, b) < 0.2
    assert iou(a, (0.5, 0.5, 0.6, 0.6)) == 0.0


def test_choose_position_avoids_face():
    """§56：anchor 上方素材不得碰脸；撞脸则按 fallback 序回退。"""
    anchor = (0.5, 0.50)
    face = (0.42, 0.30, 0.58, 0.44)  # 脸压住 anchor 正上方
    avoid = [("face", 0, face)]
    point, rel, fell = choose_position(
        anchor, "above", ["upper_right", "right_of"], avoid,
        distance=0.08, scale=0.16, hard_p0=True,
    )
    assert rel != "above"  # above 会撞脸
    assert fell is False   # fallback 里有无冲突位置
    box = bbox_around(point, 0.08)
    assert iou(box, face) <= 0.01


def test_choose_position_all_blocked_falls_back():
    """全部候选撞 P0 → fell_back=True + 冲突最小者。"""
    anchor = (0.5, 0.5)
    face = (0.0, 0.0, 1.0, 1.0)  # 全屏脸（病态输入）
    point, rel, fell = choose_position(
        anchor, "above", ["below"], [("face", 0, face)], scale=0.16,
    )
    assert fell is True


def test_collect_avoid_regions_single_hand():
    """§32：单手主体只保护活跃手。"""
    brief = {
        "event_anchor": [0.5, 0.6],
        "protected_regions": {"face": [0.4, 0.1, 0.6, 0.3]},
        "hands": {"left": [0.3, 0.5], "right": [0.7, 0.5]},
        "person_bbox": [0.2, 0.05, 0.8, 0.95],
    }
    profile = AccessibilityPlanningProfile(
        posture="seated", active_hands=["right"], preserve_mobility_device=True
    )
    regions = collect_avoid_regions(
        brief, ["face", "active_hands", "upper_torso", "mobility_device"], profile
    )
    names = [n for n, _, _ in regions]
    assert "face" in names
    assert "active_hand_right" in names
    assert "active_hand_left" not in names
    assert "upper_torso" in names and "mobility_device" in names


def test_tremor_follow_policy():
    """§31：tremor → 强平滑 + 低灵敏度 + 大死区 + 低关键帧密度。"""
    normal = follow_policy(False, {})
    tremor = follow_policy(True, {})
    assert tremor["smoothing"] == "strong"
    assert tremor["sensitivity"] < normal["sensitivity"]
    assert tremor["dead_zone"] > normal["dead_zone"]
    assert tremor["keyframe_density"] < normal["keyframe_density"]


def test_single_hand_target():
    assert single_hand_target(
        AccessibilityPlanningProfile(active_hands=["right"])
    ) == "right_hand"
    assert single_hand_target(AccessibilityPlanningProfile()) is None


def test_clamp_to_frame():
    assert clamp_point((-0.2, 1.3)) == (0.10, 0.90)


def test_default_avoid_regions_seated():
    regions = default_avoid_regions(PROFILE)
    assert "face" in regions and "active_gesture" in regions
    assert "active_hands" in regions
    assert "upper_torso" in regions  # 坐姿补 P2
    standing = default_avoid_regions(AccessibilityPlanningProfile(posture="standing"))
    assert "upper_torso" not in standing


def test_direction_policy_auto_set_for_point():
    """§54：point_* 事件自动开 match_event_direction。"""
    req = event_req("r", "point_right")
    spec = spatial_spec_for_event(req, "evt_p1", __import__(
        "editing_planner.models", fromlist=["PlanOperation"]).PlanOperation.add_overlay,
        PROFILE, [])
    assert spec.direction_policy["mode"] == "match_event_direction"
    req2 = event_req("r2", "heart_gesture")
    spec2 = spatial_spec_for_event(req2, "evt_h1", __import__(
        "editing_planner.models", fromlist=["PlanOperation"]).PlanOperation.add_overlay,
        PROFILE, [])
    assert spec2.direction_policy["mode"] == "none"
