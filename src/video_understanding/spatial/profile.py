"""可动性画像：显式声明 + 轨道自动推断 + 镜像修正（无障碍适配）。

手势舞的重要用户群体包含单侧上肢、坐姿（轮椅）、低幅度运动、震颤等
身体状况。``infer_mobility_profile`` 不要求用户显式申报——从稠密轨道
的覆盖度与运动分布推断；``mirrored=True`` 时 ``mirror_tracks`` 互换
左右手/腕标签（前置自拍的镜像问题）。

推断是尽力而为：输出 ``inferred=True``，调用方可用显式声明覆盖。
"""

from __future__ import annotations

import math
from statistics import median
from typing import Dict, List, Optional, Tuple

from ..models import DenseSpatialTracks, FrameObservation, MobilityProfile, Point2


def infer_mobility_profile(tracks: DenseSpatialTracks) -> MobilityProfile:
    """从轨道覆盖度与运动分布推断主体可动性。"""
    frames = tracks.frames
    if not frames:
        return MobilityProfile(inferred=True)

    # ---- 可用手：某侧手部覆盖率 < 5% 视为不可用（截肢/始终不可见）----
    # 轨道带 hand 观测时以 hand 为准（缺手侧腕点可能是前臂末端）；
    # 只有 pose 关键点时以腕点近似。
    use_hands_obs = any(f.hands for f in frames)

    def _has_hand(frame: FrameObservation, side: str) -> bool:
        if use_hands_obs:
            return frame.hands.get(side) is not None
        return frame.hand_center(side) is not None

    coverage = {
        side: sum(1 for f in frames if _has_hand(f, side)) / len(frames)
        for side in ("left", "right")
    }
    hands = [s for s, c in coverage.items() if c >= 0.05] or ["left", "right"]

    # ---- 姿态：脚踝基本不可见或 person bbox 高宽比小 → 坐姿/上半身出镜 ----
    ankle_cov = sum(
        1 for f in frames
        if "left_ankle" in f.keypoints or "right_ankle" in f.keypoints
    ) / len(frames)
    ratios = [
        (f.person_bbox[3] - f.person_bbox[1]) / max(f.person_bbox[2] - f.person_bbox[0], 1e-6)
        for f in frames if f.person_bbox
    ]
    posture = "seated" if (ankle_cov < 0.1 or (ratios and median(ratios) < 1.5)) else "standing"

    # ---- 幅度：关键点速度的 90 分位数 ----
    speeds = _keypoint_speeds(frames)
    p90 = _percentile(speeds, 0.9)
    if p90 < 0.15:
        amplitude, scale = "very_low", 0.35
    elif p90 < 0.3:
        amplitude, scale = "low", 0.6
    else:
        amplitude, scale = "normal", 1.0

    # ---- 震颤：腕部水平速度方向反转频率 > ~6/s ----
    tremor = any(_reversal_rate(tracks.series(f"{s}_wrist")) > 6.0 for s in ("left", "right"))
    if not tremor:
        tremor = any(_reversal_rate(tracks.series(f"{s}_hand")) > 6.0 for s in hands)

    return MobilityProfile(
        available_hands=hands, posture=posture, amplitude=amplitude,
        tremor=tremor, inferred=True, amplitude_scale=scale,
    )


def _keypoint_speeds(frames: List[FrameObservation]) -> List[float]:
    speeds: List[float] = []
    prev: Optional[Dict[str, Point2]] = None
    prev_t = 0.0
    for frame in frames:
        if prev is not None:
            dt = max(frame.timestamp - prev_t, 1e-6)
            for name, pt in frame.keypoints.items():
                if name in prev:
                    speeds.append(math.hypot(pt[0] - prev[name][0], pt[1] - prev[name][1]) / dt)
        prev = frame.keypoints
        prev_t = frame.timestamp
    return speeds


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[idx]


def _reversal_rate(series: List[Tuple[float, Point2]]) -> float:
    """每秒水平方向反转次数（x 速度符号变化），震颤的粗粒度度量。"""
    if len(series) < 3:
        return 0.0
    reversals = 0
    prev_sign = 0
    t0, t1 = series[0][0], series[-1][0]
    for i in range(1, len(series)):
        dx = series[i][1][0] - series[i - 1][1][0]
        sign = (dx > 1e-4) - (dx < -1e-4)
        if sign and prev_sign and sign != prev_sign:
            reversals += 1
        if sign:
            prev_sign = sign
    return reversals / max(t1 - t0, 1e-6)


def mirror_tracks(tracks: DenseSpatialTracks) -> DenseSpatialTracks:
    """镜像修正：互换 left_/right_ 关键点名与 hands 标签。

    用于前置摄像头镜像自拍：画面中人物自身左右被翻转，而下游语义
    （pointing_hand 等）需要人物真实左右。画面方向（point_left 指向
    观众视角左）不变——那正是 Planner 要摆放素材的方向。
    """
    def swap_name(name: str) -> str:
        if name.startswith("left_"):
            return "right_" + name[5:]
        if name.startswith("right_"):
            return "left_" + name[6:]
        return name

    for frame in tracks.frames:
        frame.keypoints = {swap_name(k): v for k, v in frame.keypoints.items()}
        frame.hands = {("right" if k == "left" else "left" if k == "right" else k): v
                       for k, v in frame.hands.items()}
    return tracks
