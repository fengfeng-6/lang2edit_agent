"""运动规则底层工具（§22）：位移、速度、运动能量、静止度。

供 dedicated detector 与 open-semantic 的 cheap proposal 共用。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from ..models import DenseSpatialTracks, Point2

Series = List[Tuple[float, Point2]]


def velocities(series: Series) -> List[Tuple[float, Point2]]:
    """(t, (vx, vy))：相邻点中心差分。首帧速度取与第二帧相同值。"""
    out: List[Tuple[float, Point2]] = []
    for i in range(len(series)):
        if i == 0:
            if len(series) < 2:
                out.append((series[0][0], (0.0, 0.0)))
                continue
            j = 1
        else:
            j = i
        t1, p1 = series[j - 1]
        t2, p2 = series[j]
        dt = max(t2 - t1, 1e-6)
        out.append((series[i][0], ((p2[0] - p1[0]) / dt, (p2[1] - p1[1]) / dt)))
    return out


def displacement(series: Series, t1: float, t2: float) -> Optional[Point2]:
    """窗口两端点位移 Δ = p(t2) - p(t1)（§22）。"""
    pts = [(t, p) for t, p in series if t1 <= t <= t2]
    if len(pts) < 2:
        return None
    return (pts[-1][1][0] - pts[0][1][0], pts[-1][1][1] - pts[0][1][1])


def motion_energy(tracks: DenseSpatialTracks) -> List[Tuple[float, float]]:
    """逐帧总运动量：相邻帧关键点归一化位移之和（供 stillness / proposal）。"""
    energies: List[Tuple[float, float]] = []
    prev: Optional[Dict[str, Point2]] = None
    prev_t = 0.0
    for frame in tracks.frames:
        if prev is None:
            energies.append((frame.timestamp, 0.0))
        else:
            dt = max(frame.timestamp - prev_t, 1e-6)
            total = 0.0
            for name, pt in frame.keypoints.items():
                if name in prev:
                    total += math.hypot(pt[0] - prev[name][0], pt[1] - prev[name][1])
            energies.append((frame.timestamp, total / dt))
        prev = frame.keypoints
        prev_t = frame.timestamp
    return energies


def window_stat(samples: List[Tuple[float, float]], t1: float, t2: float, fn=max) -> float:
    """窗口内标量序列的聚合值（max/mean），无样本返回 0。"""
    values = [v for t, v in samples if t1 <= t <= t2]
    if not values:
        return 0.0
    if fn is max:
        return max(values)
    return sum(values) / len(values)
