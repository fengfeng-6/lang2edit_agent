"""Temporal Resolver（§25-27/§33）：时态关系 → TemporalSpec。

只在 Logical 阶段生成事件相对锚点与时长声明；落到具体秒数
是 materialize 的职责。

    at_event      → start_anchor = event.peak
    during_event  → start = event.start, end = event.end（event_span）
    before_event  → start = event.start - duration（end_anchor = start）
    after_event   → start = event.end
    from_event    → start = event.start
    until_event   → end = event.start
    between_events→ start = 前事件.end, end = 后事件.start（展开阶段产 pair）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from gesture_intent.models import EventBoundRequirement, TemporalRelation

from ..accessibility.policies import BEAT_SNAP_MAX_SHIFT, BEAT_SNAP_PRIORITY
from ..models import (
    AnchorType,
    DurationMode,
    DurationSpec,
    EventBoundary,
    PlanOperation,
    TemporalSpec,
    TimeAnchor,
)

#: 各操作的默认时长（秒）；覆盖优先级：ExplicitOperation 参数 > 此处默认
DEFAULT_DURATIONS: Dict[PlanOperation, float] = {
    PlanOperation.add_overlay: 0.8,
    PlanOperation.add_effect: 0.8,
    PlanOperation.add_text: 1.6,
    PlanOperation.add_sound_effect: 0.0,  # 0 = 素材自然长度
    PlanOperation.freeze: 1.0,
    PlanOperation.track_overlay: 0.0,  # 由事件跨度决定
}

#: 锚点在锚点之前的关系（before/until 需要把时长向前放）
_BOUNDARY_BY_RELATION = {
    "at_event": EventBoundary.peak,
    "during_event": EventBoundary.start,
    "before_event": EventBoundary.start,
    "after_event": EventBoundary.end,
    "from_event": EventBoundary.start,
    "until_event": EventBoundary.start,
}


def beat_snap_spec(canonical: str, has_music: bool) -> Optional[Dict[str, Any]]:
    """§33 节拍对齐标记：只在存在音乐需求时标记，按语义优先级限幅。"""
    if not has_music:
        return None
    priority = BEAT_SNAP_PRIORITY.get(canonical)
    if not priority or priority == "low":
        return None
    return {"priority": priority, "max_shift": BEAT_SNAP_MAX_SHIFT[priority]}


def temporal_spec_for_event(
    requirement: EventBoundRequirement,
    event_uid: str,
    operation: PlanOperation,
    *,
    has_music: bool,
    explicit_duration: Optional[float] = None,
) -> TemporalSpec:
    """事件绑定需求 → TemporalSpec。"""
    trigger = requirement.trigger
    relation = trigger.temporal_relation.value
    canonical = trigger.event.canonical or ""

    duration = _duration_for(operation, explicit_duration)
    start_anchor = TimeAnchor(
        type=AnchorType.semantic_event,
        event_uid=event_uid,
        boundary=_BOUNDARY_BY_RELATION.get(relation, EventBoundary.peak),
    )
    spec = TemporalSpec(
        mode="event_relative",
        start_anchor=start_anchor,
        duration=duration,
        beat_snap=beat_snap_spec(canonical, has_music),
    )

    if relation == "during_event":
        spec.duration = DurationSpec(mode=DurationMode.event_span)
        spec.end_anchor = TimeAnchor(
            type=AnchorType.semantic_event,
            event_uid=event_uid,
            boundary=EventBoundary.end,
        )
    elif relation == "before_event":
        # start = event.start - duration → end_anchor 钉在事件开始
        spec.end_anchor = TimeAnchor(
            type=AnchorType.semantic_event,
            event_uid=event_uid,
            boundary=EventBoundary.start,
        )
        spec.start_anchor = None  # 由 end - duration 反推
    elif relation == "until_event":
        spec.end_anchor = TimeAnchor(
            type=AnchorType.semantic_event,
            event_uid=event_uid,
            boundary=EventBoundary.start,
        )
        spec.start_anchor = None
    return spec


def temporal_spec_for_gap(
    pair: tuple,
    operation: PlanOperation,
    *,
    has_music: bool,
    canonical: str = "",
) -> TemporalSpec:
    """between_events：前事件 end → 后事件 start（§26）。"""
    before, after = pair
    return TemporalSpec(
        mode="event_relative",
        start_anchor=TimeAnchor(
            type=AnchorType.semantic_event,
            event_uid=before.get("event_uid"),
            boundary=EventBoundary.end,
        ),
        end_anchor=TimeAnchor(
            type=AnchorType.semantic_event,
            event_uid=after.get("event_uid"),
            boundary=EventBoundary.start,
        ),
        duration=DurationSpec(mode=DurationMode.event_span),
        beat_snap=beat_snap_spec(canonical, has_music),
    )


def temporal_spec_for_structure(structure_key: str) -> TemporalSpec:
    """video_start / video_end / first_action / last_action 结构锚点。"""
    boundary = EventBoundary.end if structure_key in ("video_end", "last_action") else EventBoundary.start
    if structure_key in ("first_action", "last_action"):
        boundary = EventBoundary.peak  # 动作定格锚在 peak（结尾姿势保持）
    return TemporalSpec(
        mode="structural",
        start_anchor=TimeAnchor(
            type=AnchorType.video_structure,
            structure_key=structure_key,
            boundary=boundary,
        ),
        duration=DurationSpec(mode=DurationMode.preferred, value=1.0),
    )


def temporal_spec_full_video(duration_hint: Optional[float] = None) -> TemporalSpec:
    """全视频范围（背景音乐/背景替换等非事件项）。"""
    return TemporalSpec(
        mode="structural",
        start_anchor=TimeAnchor(
            type=AnchorType.video_structure,
            structure_key="video_start",
            boundary=EventBoundary.start,
        ),
        end_anchor=TimeAnchor(
            type=AnchorType.video_structure,
            structure_key="video_end",
            boundary=EventBoundary.end,
        ),
        duration=DurationSpec(mode=DurationMode.event_span),
    )


def _duration_for(operation: PlanOperation, explicit: Optional[float]) -> DurationSpec:
    if explicit is not None and explicit > 0:
        return DurationSpec(mode=DurationMode.exact, value=explicit)
    default = DEFAULT_DURATIONS.get(operation, 0.8)
    if default <= 0:
        return DurationSpec(mode=DurationMode.event_span)
    return DurationSpec(mode=DurationMode.preferred, value=default)


def freeze_duration(parameters: Dict[str, Any]) -> float:
    """freeze 显式时长参数（模块一产出 {"duration": {"value": 1.0}}）。"""
    raw = parameters.get("duration")
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0
