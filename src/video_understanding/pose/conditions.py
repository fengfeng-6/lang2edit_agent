"""Pose / Motion Condition Compiler（§21-23）。

把模块一输出的结构化姿态条件（如 ``{"subject": "hand", "relation": "above",
"reference": "head"}``）编译为对单帧 ``FrameObservation`` 的可执行谓词，
输出 [0,1] 置信度交给 Temporal Event Aggregator 聚合成事件。

距离一律用人物宽度归一化（§21 的 d_norm），避免人物远近造成的尺度漂移。
左 / 右按画面坐标系（x 向右）而非人物自身左右——与 Planner 的
"素材出现在画面左侧"语义一致。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ..models import FrameObservation, Point2
from ..spatial.snapshot import normalized_distance

# 关系缺省阈值（归一化坐标 / 人物宽度归一化距离）
DEFAULT_ABOVE_DELTA = 0.04
DEFAULT_NEAR_DELTA = 0.55  # 单位：person 宽度（手贴脸约 0.2–0.4）
DEFAULT_SIDE_DELTA = 0.03
DEFAULT_CROSS_MAX_DIST = 0.9


def _subject_points(frame: FrameObservation, subject: str) -> List[Tuple[str, Point2]]:
    """subject → [(名字, 坐标)]。hand=任一手，hands=双手。"""
    if subject == "hand":
        return [(s, p) for s in ("left", "right") if (p := frame.hand_center(s))]
    if subject in ("hands", "both_hands"):
        return [(s, p) for s in ("left", "right") if (p := frame.hand_center(s))]
    if subject == "head":
        pt = frame.head_center()
        return [("head", pt)] if pt else []
    if subject in ("left_hand", "right_hand"):
        pt = frame.hand_center(subject.split("_")[0])
        return [(subject, pt)] if pt else []
    pt = frame.keypoints.get(subject)
    return [(subject, pt)] if pt else []


def _reference_point(frame: FrameObservation, reference: str) -> Optional[Point2]:
    """reference → 坐标：head / face / chest / person / 关键点名。"""
    if reference == "head":
        return frame.head_center()
    if reference == "face":
        if frame.face_bbox:
            x1, y1, x2, y2 = frame.face_bbox
            return ((x1 + x2) / 2, (y1 + y2) / 2)
        return frame.head_center()
    if reference in ("chest", "torso"):
        left = frame.keypoints.get("left_shoulder")
        right = frame.keypoints.get("right_shoulder")
        if left and right:
            return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
        return frame.body_center()
    if reference in ("person", "body"):
        return frame.body_center() or frame.person_center()
    if reference == "person_top":
        if frame.person_bbox:
            x1, y1, x2, _ = frame.person_bbox
            return ((x1 + x2) / 2, y1)
        return frame.head_center()
    return frame.keypoints.get(reference)


def compile_condition(condition: dict) -> Callable[[FrameObservation], float]:
    """结构化条件 → 单帧评分谓词（0..1）。

    支持 relation：above / below / left_of / right_of / near / crossed。
    条件可带 ``delta`` 覆盖默认阈值；``min_duration`` 由聚合层读取。
    """
    subject = condition.get("subject", "hand")
    relation = condition.get("relation", "near")
    reference = condition.get("reference", "head")
    delta = float(condition.get("delta") or 0.0)

    def _score_pair(a: Point2, b: Point2, frame: FrameObservation) -> float:
        if relation == "above":
            d = delta or DEFAULT_ABOVE_DELTA
            return 1.0 if a[1] < b[1] - d else 0.0
        if relation == "below":
            d = delta or DEFAULT_ABOVE_DELTA
            return 1.0 if a[1] > b[1] + d else 0.0
        if relation == "left_of":
            d = delta or DEFAULT_SIDE_DELTA
            return 1.0 if a[0] < b[0] - d else 0.0
        if relation == "right_of":
            d = delta or DEFAULT_SIDE_DELTA
            return 1.0 if a[0] > b[0] + d else 0.0
        if relation in ("near", "beside", "close_to"):
            d = delta or DEFAULT_NEAR_DELTA
            dist = normalized_distance(a, b, frame)
            return max(0.0, 1.0 - dist / d)
        return 0.0

    def _crossed(frame: FrameObservation) -> float:
        subjects = subject if isinstance(subject, list) else ["left_hand", "right_hand"]
        pts = {name: pt for name, pt in
               (item for s in subjects for item in _subject_points(frame, s))}
        left = pts.get("left_hand") or pts.get("left")
        right = pts.get("right_hand") or pts.get("right")
        if not left or not right:
            return 0.0
        # 面对镜头时"双手交叉"= 左手出现在画面右侧（left.x > right.x）。
        if left[0] <= right[0]:
            return 0.0
        dist = normalized_distance(left, right, frame)
        if dist > DEFAULT_CROSS_MAX_DIST:
            return 0.0
        ref = _reference_point(frame, reference) if reference else None
        if ref is not None:
            mid = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
            if normalized_distance(mid, ref, frame) > (delta or DEFAULT_NEAR_DELTA):
                return 0.0
        return max(0.5, 1.0 - dist / DEFAULT_CROSS_MAX_DIST)

    def evaluate(frame: FrameObservation) -> float:
        if relation == "crossed":
            return _crossed(frame)
        ref = _reference_point(frame, reference)
        if ref is None:
            return 0.0
        pts = _subject_points(frame, subject)
        if not pts:
            return 0.0
        scores = [_score_pair(pt, ref, frame) for _, pt in pts]
        if subject in ("hands", "both_hands") or isinstance(subject, list):
            return min(scores)  # 双手条件需全部满足
        return max(scores)  # "hand"：任一手满足即可

    return evaluate


def condition_min_duration(condition: dict, default: float) -> float:
    """条件自带的持续时长约束（§12 的 t_end - t_start > τ）。"""
    try:
        return float(condition.get("min_duration", default))
    except (TypeError, ValueError):
        return default
