"""SourceTimelineCompiler（§13-§16）。

存在 freeze 时，主视频不再是单一 0→duration 对象，而是：
    SourceSlice A → FreezeFrame → SourceSlice B …

Module 3 已算好 timeline_mapping（project_time 全部材料化），
这里只消费最终 project time，不重算 event time（§14）。
"""

from __future__ import annotations

from typing import List, Optional

from editing_planner.models import PlanOperation, ProjectTime

from ..models import TimelineObject, _stable_uid
from .context import CompileContext
from .mutations import MAIN_VIDEO_UID, ORIGINAL_AUDIO_UID
from .operations import (
    PARAM_FREEZE_AUDIO_POLICY,
    PARAM_MUTE_RANGES,
    PARAM_SOURCE_END,
    PARAM_SOURCE_START,
    PARAM_SOURCE_TIME,
    SOURCE_VIDEO_MEDIA_REF,
)


def compile_source_timeline(ctx: CompileContext) -> None:
    """根据 freeze 项把主视频切为 slice 序列，并建原音频对象。"""
    source = ctx.source_media
    duration = ctx.plan.timeline_mapping.total_duration or source.duration
    freezes = _freeze_objects(ctx)

    if not freezes:
        ctx.add_object(
            _system_object(
                uid=MAIN_VIDEO_UID,
                role="main_video",
                project_time=ProjectTime(start=0.0, end=source.duration),
                parameters={
                    PARAM_SOURCE_START: 0.0,
                    PARAM_SOURCE_END: source.duration,
                },
                object_type="source_slice",
                semantic_label="main_video",
            )
        )
    else:
        cur_src = 0.0
        cur_proj = 0.0
        index = 0
        for freeze in sorted(freezes, key=lambda o: o.project_time.start):
            anchor = float(freeze.parameters.get(PARAM_SOURCE_TIME) or 0.0)
            if anchor > cur_src:
                index += 1
                ctx.add_object(
                    _slice_object(index, cur_src, anchor, cur_proj, freeze.project_time.start)
                )
            cur_src = anchor
            cur_proj = freeze.project_time.end
        if cur_src < source.duration:
            index += 1
            ctx.add_object(
                _slice_object(index, cur_src, source.duration, cur_proj, duration)
            )
    _original_audio(ctx, duration, freezes)


def _freeze_objects(ctx: CompileContext) -> List[TimelineObject]:
    return [
        obj
        for obj in ctx.objects.values()
        if obj.role == "freeze_frame" or obj.object_type == "freeze"
    ]


def _slice_object(
    index: int,
    source_start: float,
    source_end: float,
    project_start: float,
    project_end: float,
) -> TimelineObject:
    """切片 uid 内容派生：边界变化产生诚实的 DELETE+CREATE 而非幻影 UPDATE。"""
    uid = _stable_uid("tlobj", "src_slice", index, source_start, source_end)
    return _system_object(
        uid=uid,
        role="source_slice",
        project_time=ProjectTime(start=project_start, end=project_end),
        parameters={PARAM_SOURCE_START: source_start, PARAM_SOURCE_END: source_end},
        object_type="source_slice",
        semantic_label=f"source_slice_{index:02d}",
    )


def _system_object(
    uid: str,
    role: str,
    project_time: ProjectTime,
    parameters: dict,
    object_type: str,
    semantic_label: str,
) -> TimelineObject:
    return TimelineObject(
        timeline_object_uid=uid,
        object_key=f"system:{role}:{uid}",
        origin="system",
        object_type=object_type,
        role=role,
        semantic_label=semantic_label,
        track_uid="trk_main_video",
        media_ref=SOURCE_VIDEO_MEDIA_REF,
        project_time=project_time,
        parameters=parameters,
    )


def _original_audio(
    ctx: CompileContext, total_duration: float, freezes: List[TimelineObject]
) -> None:
    """原音频独立建模（§13）；freeze_audio_policy=silence → mute_ranges。"""
    if not ctx.source_media.has_audio:
        return
    parameters = {}
    mute_ranges = [
        {"start": f.project_time.start, "end": f.project_time.end}
        for f in sorted(freezes, key=lambda o: o.project_time.start)
        if f.parameters.get(PARAM_FREEZE_AUDIO_POLICY) == "silence"
    ]
    if mute_ranges:
        parameters[PARAM_MUTE_RANGES] = mute_ranges
    if any(
        f.parameters.get(PARAM_FREEZE_AUDIO_POLICY) == "hold" for f in freezes
    ):
        ctx.warnings.append("freeze_audio_policy=hold 暂缓支持（待 Milestone 5.4）")
    obj = TimelineObject(
        timeline_object_uid=ORIGINAL_AUDIO_UID,
        object_key=f"system:original_audio:{ORIGINAL_AUDIO_UID}",
        origin="system",
        object_type="audio",
        role="original_audio",
        semantic_label="original_audio",
        track_uid="trk_audio",
        media_ref=SOURCE_VIDEO_MEDIA_REF,
        project_time=ProjectTime(start=0.0, end=total_duration),
        parameters=parameters,
    )
    ctx.add_object(obj)


def freeze_item_uids(ctx: CompileContext) -> List[str]:
    """当前 plan 中 freeze 项 uid（辅助索引/诊断）。"""
    return [
        item.plan_item_uid
        for item in ctx.plan.resolved_items
        if item.operation == PlanOperation.freeze
    ]
