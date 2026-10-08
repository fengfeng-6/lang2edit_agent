"""EditView + ResolvedPlan + Intent → SemanticProjectView（§75 引用解析桥）。

模块一的 ``_resolve_raw_target`` 按 id 精确命中、关键词+序号、候选列表
三级解析。这里把可编辑对象翻成它吃的形态：

- ``id`` 用 ``source_plan_item_uid``（pln_xxx）——解析出的 object_id 会在
  planner ``_resolve_target_items`` 的 by_uid 分支直接命中，避免
  display_id → occurrence 模糊匹配。
- ``object_type`` / ``description`` 反查 source requirement——EditView 的
  object_type 是语义类（music 对象显示 "audio"），不是模块一枚举。
- ``event_ref`` 用事件绑定需求的 trigger.event.canonical——``_route_remove_target``
  靠它决定删整绑定还是删单实例。
- ``order`` = 同一需求内按 project_time.start 排序（"第二个爱心"的序号基准）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from editing_planner.models import ResolvedEditingPlan
from gesture_intent.models import (
    EditingIntent,
    ObjectType,
    SemanticProjectObject,
    SemanticProjectView,
)

_ROLE_TO_OBJECT_TYPE = {
    "overlay": ObjectType.overlay,
    "text": ObjectType.text,
    "effect": ObjectType.effect,
    "music": ObjectType.music,
    "sound_effect": ObjectType.sound_effect,
    "background": ObjectType.background,
}


def build_project_view(
    edit_view: Any,
    resolved_plan: Optional[ResolvedEditingPlan],
    intent: Optional[EditingIntent],
) -> SemanticProjectView:
    req_info = _requirement_index(intent)
    plan_items = {
        item.plan_item_uid: item
        for item in (resolved_plan.resolved_items if resolved_plan else [])
    }
    objects: List[SemanticProjectObject] = []
    groups: Dict[str, List[Any]] = {}
    for obj in getattr(edit_view, "objects", []) or []:
        uid = obj.source_plan_item_uid
        if not uid:
            continue  # main_video / source_slice / original_audio 不可指代
        plan_item = plan_items.get(uid)
        req_id = (
            plan_item.source_requirement_ids[0]
            if plan_item is not None and plan_item.source_requirement_ids
            else (obj.source_requirement_ids[0] if obj.source_requirement_ids else "")
        )
        info = req_info.get(req_id)
        object_type = (
            info[0] if info is not None else _ROLE_TO_OBJECT_TYPE.get(obj.role)
        )
        if object_type is None:
            continue  # freeze_frame / source_slice 等无用户可指代类型
        objects.append(
            SemanticProjectObject(
                id=uid,
                object_type=object_type,
                description=(info[1] if info else "") or obj.semantic_label or obj.display_id,
                event_ref=info[2] if info else None,
                aliases=[
                    a for a in (
                        obj.display_id, obj.semantic_label, obj.role,
                        obj.object_type, obj.object_uid,
                    )
                    if a
                ],
            )
        )
        groups.setdefault(req_id, []).append((objects[-1], obj))

    for members in groups.values():
        members.sort(key=lambda pair: (pair[1].project_time.start, pair[0].id))
        for rank, (project_object, _obj) in enumerate(members, start=1):
            project_object.order = rank
    objects.sort(key=lambda o: (o.order or 0, o.id))
    return SemanticProjectView(objects=objects)


def _requirement_index(
    intent: Optional[EditingIntent],
) -> Dict[str, Tuple[ObjectType, str, Optional[str]]]:
    """req_id → (object_type, description, event_ref)。"""
    out: Dict[str, Tuple[ObjectType, str, Optional[str]]] = {}
    if intent is None:
        return out
    for req in intent.object_requirements:
        desc = req.description.raw if req.description else (req.content or "")
        out[req.id] = (req.object_type, desc, req.target_ref)
    for req in intent.event_bound_requirements:
        inner = req.requirement
        desc = (
            inner.semantic_description.raw
            if inner.semantic_description
            else (inner.content or "")
        )
        event = req.trigger.event
        out[req.id] = (inner.object_type, desc, event.canonical or event.raw)
    return out
