"""Timeline Composer（§38-39）：逻辑轨道分配与时间线效应。

第一阶段只有六条逻辑轨道（具体剪映轨道由模块五决定）：

    background z=0 / main_video z=100 / overlay z=200
    effect z=250 / text z=300 / audio z=-1（非视觉轨道，z 仅为标识）

``insert_duration``（freeze）会改变源→工程时间映射；移位表在
materialize 生成，这里只负责分类与轨道分配。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import PlanItem, PlanOperation, TimelineEffect, TimelineStructure, TimelineTrack

#: operation → 逻辑轨道名
TRACK_OF_OPERATION: Dict[PlanOperation, str] = {
    PlanOperation.replace_background: "background",
    PlanOperation.add_overlay: "overlay",
    PlanOperation.track_overlay: "overlay",
    PlanOperation.add_text: "text",
    PlanOperation.add_effect: "effect",
    PlanOperation.add_music: "audio",
    PlanOperation.replace_music: "audio",
    PlanOperation.add_sound_effect: "audio",
    PlanOperation.volume_adjust: "audio",
    PlanOperation.freeze: "main_video",
    PlanOperation.scale_adjust: "main_video",
    PlanOperation.position_adjust: "main_video",
    PlanOperation.trim: "main_video",
    PlanOperation.split: "main_video",
    PlanOperation.remove_segment: "main_video",
    PlanOperation.speed_adjust: "main_video",
}

_TRACK_Z = {
    "audio": -1,
    "background": 0,
    "main_video": 100,
    "overlay": 200,
    "effect": 250,
    "text": 300,
}

#: insert_duration（改变时间线结构）；其余 non_structural（§39）
_INSERT_DURATION_OPS = {PlanOperation.freeze}


def track_for(operation: PlanOperation) -> str:
    return TRACK_OF_OPERATION.get(operation, "overlay")


def timeline_effect_for(operation: PlanOperation) -> TimelineEffect:
    if operation in _INSERT_DURATION_OPS:
        return TimelineEffect.insert_duration
    return TimelineEffect.non_structural


def compose_timeline(items: List[PlanItem], source_duration: float) -> TimelineStructure:
    """按轨道分配 PlanItem uid，产出逻辑 TimelineStructure。"""
    buckets: Dict[str, List[str]] = {name: [] for name in _TRACK_Z}
    for item in items:
        buckets.setdefault(track_for(item.operation), []).append(item.plan_item_uid)
    tracks = [
        TimelineTrack(name=name, z_order=_TRACK_Z[name], item_uids=uids)
        for name, uids in buckets.items()
        if uids or name in ("background", "main_video")
    ]
    tracks.sort(key=lambda t: t.z_order)
    return TimelineStructure(tracks=tracks, source_duration=source_duration)


def freeze_shifts(items: List[PlanItem]) -> List[Dict[str, float]]:
    """收集 insert_duration 项的移位锚点（source_time_hint 由
    materialize 解析后回填；此处仅声明顺序结构）。"""
    out: List[Dict[str, float]] = []
    for item in items:
        if item.timeline_effect != TimelineEffect.insert_duration:
            continue
        hint = None
        if item.temporal_spec is not None:
            hint = item.temporal_spec.source_time_hint
        duration = 0.0
        if item.temporal_spec and item.temporal_spec.duration:
            duration = item.temporal_spec.duration.value or 0.0
        if hint is not None and duration > 0:
            out.append({"from_source_time": hint, "delta": duration})
    out.sort(key=lambda s: s["from_source_time"])
    return out


def source_to_project(source_time: float, shifts: List[Dict[str, float]]) -> float:
    """源时间 → 工程时间：严格在移位点之后的源时间整体平移。"""
    project = source_time
    for shift in shifts:
        if source_time > shift["from_source_time"]:
            project += shift["delta"]
    return project
