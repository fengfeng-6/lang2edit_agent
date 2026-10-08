"""IntentPatch 归一化（先翻译再 apply_patch——intent 始终是真相源）。

四步，顺序敏感：

1. replace→update：单例语义（music/background）的 replace 新增需求改写为
   对既有需求的 update，沿用旧 req id —— plan_item_uid 稳定，executor 走
   replace_media。（规则抽取器 IdAllocator 续编号只产新 id，不归一会出现
   双音乐需求。）
2. 歧义坍塌：unresolved remove 的全部候选对象共享同一来源需求 → 删除该
   需求（"把爱心删掉"且只有一个爱心绑定时删整绑定）。
3. 实例删除翻译：remove_object_requirement_ids 里的 pln_* 实例 id —
   源需求已被删 → 丢弃；源需求仍在 → 转成 remove op 沉淀进 intent，
   后续 replan 不复活。
4. unresolved op 剔除：target 仍是 object_reference 的 op 不并入 intent，
   否则每轮残留 op 产 unsupported plan item 刷 warning。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from editing_planner.models import PlanItem
from gesture_intent.models import (
    EditingIntent,
    ExplicitOperation,
    IntentPatch,
    ObjectAction,
    OperationType,
    TargetReference,
    UnresolvedReference,
)

from .models import PatchNote

#: 单例语义类型：同类需求并存无意义，replace 一律归一到既有需求
_SINGLETON_TYPES = {"music", "background"}


def normalize_patch(
    patch: IntentPatch,
    unresolved: List[UnresolvedReference],
    intent: EditingIntent,
    plan_items: Optional[List[PlanItem]] = None,
) -> Tuple[IntentPatch, List[UnresolvedReference], List[PatchNote]]:
    notes: List[PatchNote] = []
    plan_by_uid = {i.plan_item_uid: i for i in (plan_items or [])}
    unresolved = list(unresolved)

    _collapse_replace(patch, intent, notes)
    unresolved = _collapse_ambiguous_removes(
        patch, unresolved, intent, plan_by_uid, notes
    )
    _triage_instance_removes(patch, intent, plan_by_uid, notes)
    _drop_unresolved_ops(patch, notes)
    return patch, unresolved, notes


def _collapse_replace(
    patch: IntentPatch, intent: EditingIntent, notes: List[PatchNote]
) -> None:
    keep = []
    for req in patch.add_object_requirements:
        existing = next(
            (r for r in intent.object_requirements
             if r.object_type == req.object_type),
            None,
        ) if (
            req.object_type.value in _SINGLETON_TYPES
            and req.action == ObjectAction.replace
        ) else None
        if existing is None:
            keep.append(req)
            continue
        req.id = existing.id
        # 保留原 action——_object_operation 由 action 定 PlanOperation，
        # 动作不变则 plan_key/plan_item_uid 稳定 → executor 走 asset 就地替换
        req.action = existing.action
        patch.update_object_requirements.append(req)
        notes.append(PatchNote(
            kind="replace_to_update",
            detail=f"{existing.id}: 同类型 {req.object_type.value} 需求已存在，replace 归一为 update",
        ))
    patch.add_object_requirements = keep


def _collapse_ambiguous_removes(
    patch: IntentPatch,
    unresolved: List[UnresolvedReference],
    intent: EditingIntent,
    plan_by_uid: Dict[str, Any],
    notes: List[PatchNote],
) -> List[UnresolvedReference]:
    """候选对象同属一个需求 → 歧义实为"删整绑定"，路由到 remove_*_ids。"""
    req_ids = _intent_req_ids(intent)
    kept: List[UnresolvedReference] = []
    for ref in unresolved:
        if ref.type != "object_reference" or len(ref.candidates or []) < 2:
            kept.append(ref)
            continue
        items = [plan_by_uid.get(c) for c in ref.candidates]
        if any(i is None or not i.source_requirement_ids for i in items):
            kept.append(ref)  # 候选是需求 id 或已消失对象——真歧义，保留
            continue
        reqs = {i.source_requirement_ids[0] for i in items}
        if len(reqs) != 1:
            kept.append(ref)
            continue
        req_id = next(iter(reqs))
        _route_req_removal(patch, intent, req_id)
        notes.append(PatchNote(
            kind="collapse",
            detail=f"'{ref.raw}' 的候选同属 {req_id}，坍塌为需求级删除",
        ))
    return kept


def _triage_instance_removes(
    patch: IntentPatch,
    intent: EditingIntent,
    plan_by_uid: Dict[str, Any],
    notes: List[PatchNote],
) -> None:
    req_ids = _intent_req_ids(intent)
    kept: List[str] = []
    for rid in patch.remove_object_requirement_ids:
        if rid in req_ids:
            kept.append(rid)
            continue
        item = plan_by_uid.get(rid)
        src = (
            item.source_requirement_ids[0]
            if item is not None and item.source_requirement_ids
            else None
        )
        if src is None or src not in req_ids:
            notes.append(PatchNote(
                kind="instance_remove",
                detail=f"{rid}: 源需求已删，实例移除随需求级删除完成",
            ))
            continue
        patch.add_operations.append(ExplicitOperation(
            id=f"op_remove_{rid}",
            operation=OperationType.remove,
            target=TargetReference(type="object_id", value=rid),
            source_text=f"删除实例 {rid}",
        ))
        notes.append(PatchNote(
            kind="instance_remove",
            detail=f"{rid}: 实例级删除翻译为 remove op（需求 {src} 保留）",
        ))
    patch.remove_object_requirement_ids = kept


def _drop_unresolved_ops(
    patch: IntentPatch, notes: List[PatchNote]
) -> None:
    def unresolved_target(op: ExplicitOperation) -> bool:
        return op.target.type == "object_reference"

    for op in patch.add_operations + patch.update_operations:
        if unresolved_target(op):
            notes.append(PatchNote(
                kind="drop_unresolved",
                detail=f"{op.id}: 目标 '{op.target.value}' 未解析，不并入 intent",
            ))
    patch.add_operations = [
        op for op in patch.add_operations if not unresolved_target(op)
    ]
    patch.update_operations = [
        op for op in patch.update_operations if not unresolved_target(op)
    ]


def _intent_req_ids(intent: EditingIntent) -> set:
    return {r.id for r in intent.object_requirements} | {
        r.id for r in intent.event_bound_requirements
    }


def _route_req_removal(
    patch: IntentPatch, intent: EditingIntent, req_id: str
) -> None:
    if any(r.id == req_id for r in intent.event_bound_requirements):
        if req_id not in patch.remove_event_bound_requirement_ids:
            patch.remove_event_bound_requirement_ids.append(req_id)
    elif req_id not in patch.remove_object_requirement_ids:
        patch.remove_object_requirement_ids.append(req_id)


def patch_effectively_empty(patch: IntentPatch) -> bool:
    """归一化后是否仍无可执行内容（与 parser._patch_empty 同构）。"""
    return not (
        patch.add_object_requirements
        or patch.update_object_requirements
        or patch.remove_object_requirement_ids
        or patch.add_event_bound_requirements
        or patch.update_event_bound_requirements
        or patch.remove_event_bound_requirement_ids
        or patch.add_operations
        or patch.update_operations
        or patch.remove_operation_ids
        or patch.add_constraints
        or patch.update_constraints
        or patch.remove_constraint_ids
        or patch.global_updates
    )
