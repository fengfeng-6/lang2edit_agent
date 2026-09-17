"""空间原语与快照构造（§7-9、§27-29）。

- ``build_snapshot``：在指定时刻从稠密轨道取一帧，组装 SpatialSnapshot，
  含 person/face bbox、头/双手位置、事件锚点与保护区域。
- ``event_anchor``：无单一主体点的事件（如双手比心）取双手中点（§28）。
- ``protected_region``：face 等重要区域外扩安全边距 m（§29）。
"""

from __future__ import annotations

from typing import Optional

from ..models import (
    DenseSpatialTracks,
    Direction,
    FrameObservation,
    Point2,
    Region,
    SpatialSnapshot,
)

DEFAULT_PROTECT_MARGIN = 0.05  # Expand(R_face, m) 的推荐安全边距（§29）


def hand_pair_anchor(frame: FrameObservation) -> Optional[Point2]:
    """双手事件锚点：p_event = (p_left + p_right) / 2（§28）。"""
    left = frame.hand_center("left")
    right = frame.hand_center("right")
    if left and right:
        return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
    return left or right


def build_snapshot(
    tracks: DenseSpatialTracks,
    timestamp: float,
    *,
    spatial_id: str,
    anchor: Optional[Point2] = None,
    direction: Optional[Direction] = None,
    protect_margin: float = DEFAULT_PROTECT_MARGIN,
) -> Optional[SpatialSnapshot]:
    """取 timestamp 处的空间状态，组装事件级空间快照（§27）。

    默认使用 peak_time 对应的空间状态；anchor 缺省时取双手中点。
    face 存在时自动产出 protected_regions["face"]。
    """
    frame = tracks.at(timestamp)
    if frame is None:
        return None
    face = frame.face_bbox
    protected = {}
    if face is not None:
        protected["face"] = Region(bbox=face).expand(protect_margin).bbox
    return SpatialSnapshot(
        spatial_id=spatial_id,
        timestamp=frame.timestamp,
        person_bbox=frame.person_bbox,
        face_bbox=face,
        head_center=frame.head_center(),
        left_hand=frame.hand_center("left"),
        right_hand=frame.hand_center("right"),
        event_anchor=anchor or hand_pair_anchor(frame) or frame.body_center(),
        direction=direction,
        protected_regions=protected,
    )


def normalized_distance(a: Point2, b: Point2, frame: FrameObservation) -> float:
    """按人物宽度归一化的两点距离（§21）：d_norm = ||p_a - p_b|| / w_person。

    person_bbox 缺失时退化为未归一化欧氏距离。
    """
    import math

    dist = math.hypot(a[0] - b[0], a[1] - b[1])
    if frame.person_bbox:
        width = frame.person_bbox[2] - frame.person_bbox[0]
        if width > 1e-6:
            return dist / width
    return dist


def shoulder_width(frame: FrameObservation) -> Optional[float]:
    left = frame.keypoints.get("left_shoulder")
    right = frame.keypoints.get("right_shoulder")
    if left and right:
        return abs(left[0] - right[0])
    return None
