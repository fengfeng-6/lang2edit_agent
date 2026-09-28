"""Object-Mutation Operation 编译（§21.2）：不产出新对象，只改已有对象。

目标解析链（确定性，候选按 object_key 排序）：
    target.type == plan_item_ref → plan_item_index
    target.type == event        → source_event_uids 匹配（排除 system 对象）
    target.type == video        → tlobj_main_video（freeze 时含 project_time 的切片）
    target.type == audio_track  → original_audio / music
    target.type == track        → 该轨道全部对象
    空/未知                     → 角色默认
未解析 → warning + skip（非阻塞）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from editing_planner.models import PlanOperation, ResolvedPlanItem

from ..models import TimelineObject
from .context import CompileContext
from .operations import _asset_media_ref_uid

MAIN_VIDEO_UID = "tlobj_main_video"
ORIGINAL_AUDIO_UID = "tlobj_original_audio"


def apply_mutation(
    item: ResolvedPlanItem, ctx: CompileContext
) -> List[str]:
    """把 mutation 应用到 ctx.objects 中的目标对象，返回被修改的 uid。"""
    targets = resolve_mutation_target(item, ctx)
    op = item.operation.value
    if not targets:
        if op == PlanOperation.replace_music.value:
            created = _replace_music_fallback(item, ctx)
            if created is not None:
                return [created.timeline_object_uid]
        ctx.warnings.append(
            f"{item.plan_item_uid}: mutation {op} 目标未解析，跳过"
        )
        return []
    for obj in targets:
        _apply(item, op, obj, ctx)
    return [obj.timeline_object_uid for obj in targets]


def resolve_mutation_target(
    item: ResolvedPlanItem, ctx: CompileContext
) -> List[TimelineObject]:
    target = item.target or {}
    ttype = target.get("type")
    value = target.get("value")
    objects = ctx.objects

    if ttype == "plan_item_ref" and value:
        uids = ctx.plan_item_index.get(str(value), [])
        return _sorted(objects[u] for u in uids if u in objects)

    if ttype == "event" and value:
        return _sorted(
            obj
            for obj in objects.values()
            if obj.origin != "system" and str(value) in obj.source_event_uids
        )

    if ttype == "video":
        slices = [
            obj
            for obj in objects.values()
            if obj.object_type == "source_slice"
            and obj.project_time.start <= item.project_time.start < obj.project_time.end
        ]
        if slices:
            return _sorted(slices)
        main = objects.get(MAIN_VIDEO_UID)
        return [main] if main is not None else []

    if ttype == "audio_track":
        if str(value) == "original_audio":
            obj = objects.get(ORIGINAL_AUDIO_UID)
            return [obj] if obj is not None else []
        if value:
            uids = ctx.plan_item_index.get(str(value), [])
            hits = [objects[u] for u in uids if u in objects]
            if hits:
                return _sorted(hits)
        return _sorted(obj for obj in objects.values() if obj.role == "music")

    if ttype == "track" and value:
        track_uid = f"trk_{value}"
        return _sorted(obj for obj in objects.values() if obj.track_uid == track_uid)

    # 空/未知 target → 角色默认
    if item.operation.value == PlanOperation.replace_music.value:
        return _sorted(obj for obj in objects.values() if obj.role == "music")
    if item.operation.value == PlanOperation.volume_adjust.value:
        obj = objects.get(ORIGINAL_AUDIO_UID)
        return [obj] if obj is not None else []
    return []


def _sorted(objs: Any) -> List[TimelineObject]:
    return sorted((o for o in objs if o is not None), key=lambda o: o.object_key)


def _apply(
    item: ResolvedPlanItem, op: str, obj: TimelineObject, ctx: CompileContext
) -> None:
    if op == PlanOperation.scale_adjust.value:
        if obj.transform is None:
            return
        if item.transform is not None:
            obj.transform.scale = item.transform.scale
        elif "scale_factor" in item.parameters:
            obj.transform.scale = obj.transform.scale * float(
                item.parameters["scale_factor"]
            )
    elif op == PlanOperation.position_adjust.value:
        if obj.transform is None:
            return
        if item.transform is not None:
            obj.transform.position = item.transform.position
        offset = item.parameters.get("position_offset")
        if isinstance(offset, dict):
            dx = float(offset.get("dx") or 0.0)
            dy = float(offset.get("dy") or 0.0)
            pos = obj.transform.position
            obj.transform.position = (pos[0] + dx, pos[1] + dy)
    elif op == PlanOperation.volume_adjust.value:
        delta = float(item.parameters.get("delta_db") or 0.0)
        current = float(obj.parameters.get("volume_db") or 0.0)
        obj.parameters["volume_db"] = current + delta
        cap = item.parameters.get("volume_cap")
        if cap is not None:
            obj.parameters["volume_db"] = min(
                obj.parameters["volume_db"], float(cap)
            )
    elif op == PlanOperation.replace_music.value:
        obj.asset_uid = item.asset_uid
        obj.media_ref = _asset_media_ref_uid(item.asset_uid)


def _replace_music_fallback(
    item: ResolvedPlanItem, ctx: CompileContext
) -> Optional[TimelineObject]:
    """replace_music 目标缺失：按 desired-graph 诚实原则创建音乐对象 + warning。"""
    from .operations import MusicCompiler

    ctx.warnings.append(
        f"{item.plan_item_uid}: replace_music 未找到已有音乐对象，"
        "按 add_music 语义创建"
    )
    created = MusicCompiler("music").compile(item, ctx)
    for obj in created:
        ctx.add_object(obj)
    return created[0] if created else None
