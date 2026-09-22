"""Accessibility-aware Spatial Planner —— 放置几何（§21-22/§29/§32）。

纯确定性几何：关系→偏移、保护区域优先级（P0..P3）、IoU 避让、
候选/回退位置选择、画面钳制。不产生任何 LLM 决策。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..accessibility.policies import (
    DEFAULT_AVOID_IOU_THRESHOLD,
    GESTURE_REGION_RADIUS,
    HAND_REGION_RADIUS,
    REGION_PRIORITY,
    SEATED_LOWER_THIRD_Y,
    SINGLE_HAND_TARGET,
)
from ..models import AccessibilityPlanningProfile, SpatialRelation

Point2 = Tuple[float, float]
BBox = Tuple[float, float, float, float]

#: relation → (dx, dy) 归一化偏移方向（y 向下为正）
RELATION_OFFSET: Dict[str, Point2] = {
    "above": (0.0, -1.0),
    "below": (0.0, 1.0),
    "left_of": (-1.0, 0.0),
    "right_of": (1.0, 0.0),
    "upper_left": (-0.8, -0.8),
    "upper_right": (0.8, -0.8),
    "centered_on": (0.0, 0.0),
    "follow": (0.0, -1.0),  # follow 默认出现在锚点上方（如头顶皇冠）
}

#: 屏幕侧槽位（§54 Directional Appearance）：固定 x，保留锚点 y
_SCREEN_SLOT_X = {"screen_left": 0.2, "screen_right": 0.8}

#: 素材默认归一化半宽（scale 0.16 → 半宽 ~0.08）
DEFAULT_MARGIN = 0.10


def clamp_point(point: Point2, margin: float = DEFAULT_MARGIN) -> Point2:
    x, y = point
    return (min(max(x, margin), 1.0 - margin), min(max(y, margin), 1.0 - margin))


def bbox_around(point: Point2, half_w: float, half_h: Optional[float] = None,
                aspect: float = 1.0) -> BBox:
    """以点为中心构造 bbox；9:16 竖屏把纵向半径按 aspect 放大。"""
    half_h = half_h if half_h is not None else half_w * aspect
    x, y = point
    return (
        max(0.0, x - half_w),
        max(0.0, y - half_h),
        min(1.0, x + half_w),
        min(1.0, y + half_h),
    )


def iou(a: BBox, b: BBox) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = min(ax2, bx2) - max(ax1, bx1)
    iy = min(ay2, by2) - max(ay1, by1)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    area = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / area if area > 0 else 0.0


def apply_relation(anchor: Point2, relation: str, distance: float) -> Point2:
    """anchor + relation 偏移 → 素材中心点。"""
    if relation in _SCREEN_SLOT_X:
        return (_SCREEN_SLOT_X[relation], anchor[1])
    dx, dy = RELATION_OFFSET.get(relation, (0.0, -1.0))
    return (anchor[0] + dx * distance, anchor[1] + dy * distance)


def direction_relation(label: str) -> Optional[str]:
    """事件方向标签 → 屏幕侧关系（§54：point_left → 左侧素材）。"""
    if label in ("left", "upper_left", "lower_left"):
        return "screen_left"
    if label in ("right", "upper_right", "lower_right"):
        return "screen_right"
    if label == "up":
        return "above"
    if label == "down":
        return "below"
    return None


def collect_avoid_regions(
    spatial_brief: Optional[Dict[str, Any]],
    avoid_names: Sequence[str],
    profile: AccessibilityPlanningProfile,
    asset_half: float = 0.08,
) -> List[Tuple[str, int, BBox]]:
    """按 §22 优先级组装避让区：(name, priority, bbox)。

    - face（P0）：protected_regions.face 或 face_bbox；
    - active_gesture（P1）：event_anchor 邻域；
    - active_hands（P1）：brief["hands"] 按 profile.active_hands 取——
      单手主体只保护活跃手（§32）；
    - upper_torso（P2）：seated 时 person_bbox 上半（软避让）；
    - mobility_device（P3）：preserve_mobility_device 时 person_bbox 下带。

    注意手势/手部区域用固定半径——素材自身尺寸已由 IoU 交并比体现，
    再把 asset_half 加进保护区会让"紧邻手势上方"永远无法通过。
    """
    if not spatial_brief or not avoid_names:
        return []
    out: List[Tuple[str, int, BBox]] = []
    brief = spatial_brief

    face = None
    protected = brief.get("protected_regions") or {}
    if "face" in protected:
        face = tuple(protected["face"])
    elif brief.get("face_bbox"):
        face = tuple(brief["face_bbox"])
    if face and "face" in avoid_names:
        out.append(("face", REGION_PRIORITY["face"], face))

    anchor = brief.get("event_anchor")
    if anchor and any(n in avoid_names for n in ("active_gesture", "gesture_region")):
        out.append((
            "active_gesture",
            REGION_PRIORITY["active_gesture"],
            bbox_around(tuple(anchor), GESTURE_REGION_RADIUS),
        ))

    hands = brief.get("hands") or {}
    if "active_hands" in avoid_names and hands:
        for side in profile.active_hands:
            point = hands.get(side)
            if point:
                out.append((
                    f"active_hand_{side}",
                    REGION_PRIORITY["active_hands"],
                    bbox_around(tuple(point), HAND_REGION_RADIUS),
                ))

    person = brief.get("person_bbox")
    if person and profile.posture == "seated":
        x1, y1, x2, y2 = person
        mid = y1 + (y2 - y1) * 0.55
        if "upper_torso" in avoid_names:
            out.append(("upper_torso", REGION_PRIORITY["upper_torso"], (x1, y1, x2, mid)))
        if "mobility_device" in avoid_names and profile.preserve_mobility_device:
            out.append(("mobility_device", REGION_PRIORITY["mobility_device"], (x1, mid, x2, y2)))

    if person and "person" in avoid_names:
        out.append(("person", REGION_PRIORITY["person"], tuple(person)))
    return out


def choose_position(
    anchor: Point2,
    relation: str,
    fallbacks: Sequence[str],
    avoid: List[Tuple[str, int, BBox]],
    *,
    distance: float = 0.08,
    scale: float = 0.16,
    iou_threshold: float = DEFAULT_AVOID_IOU_THRESHOLD,
    hard_p0: bool = True,
    p0_epsilon: float = 0.01,
) -> Tuple[Point2, str, bool]:
    """候选序 [relation] + fallbacks → 首个满足避让的位置。

    返回 (position, chosen_relation, fell_back)。
    - P0 区域：IoU > p0_epsilon 即拒绝（§22 R_asset ∩ R_face ≈ ∅，
      ε 容忍边缘擦碰）；
    - P1：IoU ≥ τ 拒绝；P2/P3 只计分不拒绝。
    - 全部候选失败 → 取加权分最低者 + fell_back=True。
    """
    asset_half = scale / 2.0
    candidates = [relation] + [f for f in fallbacks if f != relation]
    scored: List[Tuple[float, Point2, str]] = []
    for rel in candidates:
        point = clamp_point(apply_relation(anchor, rel, distance + asset_half))
        box = bbox_around(point, asset_half)
        p0_hit = any(iou(box, b) > p0_epsilon for name, p, b in avoid if p == 0)
        p1_bad = any(
            iou(box, b) >= iou_threshold for name, p, b in avoid if p == 1
        )
        if hard_p0 and p0_hit:
            continue
        if p1_bad:
            continue
        return point, rel, False
    # 全部失败：加权分最低 + fallback 警告由调用方记
    for rel in candidates:
        point = clamp_point(apply_relation(anchor, rel, distance + asset_half))
        box = bbox_around(point, asset_half)
        score = sum(iou(box, b) * (4 - p) for _name, p, b in avoid)
        scored.append((score, point, rel))
    scored.sort(key=lambda s: s[0])
    if scored:
        return scored[0][1], scored[0][2], True
    return clamp_point(anchor), relation, True


def single_hand_target(profile: AccessibilityPlanningProfile) -> Optional[str]:
    """单侧上肢 → 跟随目标手（§32：不强制双手中点）。"""
    if len(profile.active_hands) == 1:
        return SINGLE_HAND_TARGET[profile.active_hands[0]]
    return None
