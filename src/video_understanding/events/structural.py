"""结构化视频事件解析（§39）。

video_start / video_end 是确定性信息；first_action / last_action 优先
复用已检测到的动作事件（最早 / 最晚），没有检测结果时退化为
运动能量启发式（能量持续抬升的最早 / 最晚区间），置信度相应降低。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..models import ConfidenceStatus, DenseSpatialTracks, SemanticEvent, TemporalSpan
from ..pose.motion import motion_energy
from .aggregator import AggregatedSpan


def _point_span(t: float, conf: float) -> AggregatedSpan:
    return AggregatedSpan(
        temporal=TemporalSpan(start_time=t, peak_time=t, end_time=t),
        confidence=conf, status=ConfidenceStatus.confirmed, sample_count=1,
    )


def resolve_structural(
    canonical: str,
    *,
    duration: float,
    tracks: Optional[DenseSpatialTracks],
    events: List[SemanticEvent],
) -> Tuple[List[AggregatedSpan], dict]:
    """返回 (候选区间, properties)。

    first_action / last_action 的 properties["source_event"] 记录依据的
    事件 uid，供追踪来源。
    """
    if canonical == "video_start":
        return [_point_span(0.0, 1.0)], {}
    if canonical == "video_end":
        return [_point_span(duration, 1.0)], {}

    action_events = [e for e in events
                     if e.event_type.value in ("gesture", "body_action", "pose_condition")
                     and not e.invalidated
                     and e.canonical not in ("first_action", "last_action")]
    if canonical == "first_action":
        if action_events:
            first = min(action_events, key=lambda e: e.temporal.start_time)
            # 复用源事件的完整区间，Planner 可拿到 start/peak/end 与时长
            return [AggregatedSpan(
                temporal=first.temporal,
                confidence=min(0.9, first.confidence.overall),
                status=ConfidenceStatus.confirmed, sample_count=1,
            )], {"source_event": first.event_uid}
        span = _energy_edge(tracks, duration, first=True)
        return ([span] if span else []), {}
    if canonical == "last_action":
        if action_events:
            last = max(action_events, key=lambda e: e.temporal.end_time)
            return [AggregatedSpan(
                temporal=last.temporal,
                confidence=min(0.9, last.confidence.overall),
                status=ConfidenceStatus.confirmed, sample_count=1,
            )], {"source_event": last.event_uid}
        span = _energy_edge(tracks, duration, first=False)
        return ([span] if span else []), {}
    return [], {}


def _energy_edge(tracks: Optional[DenseSpatialTracks], duration: float, *, first: bool) -> Optional[AggregatedSpan]:
    """运动能量的最早 / 最晚显著活动点。"""
    if tracks is None:
        return None
    energies = motion_energy(tracks)
    if not energies:
        return None
    peak = max(v for _, v in energies)
    if peak <= 1e-6:
        return None
    threshold = max(0.5, peak * 0.3)
    active = [t for t, v in energies if v >= threshold]
    if not active:
        return None
    t = min(active) if first else max(active)
    return _point_span(t, 0.6)
