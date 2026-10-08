"""PlanPatch：局部重规划（§52/§60）。

保持 ``requirement_id ↔ plan_item_uid`` 映射常驻；只重规划
变更/新增/删除的需求，未变需求的 PlanItem 原样保留——即使
Creative Planner 是 LLM，补丁也不扩散到无关项。

diff 规则：以 ``plan_key`` 对齐；结构字段（operation/target/
temporal_spec/spatial_spec/parameters/asset_request_ref）一致即复用
旧项——style_spec / provenance 差异不触发更新（吸收 LLM 抖动）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from gesture_intent.models import (
    Constraint,
    EditingIntent,
    IntentPatch,
    ObjectType,
    OperationType,
    model_dump,
    model_validate,
)

from ..models import (
    AccessibilityPlanningProfile,
    EditingPlannerInput,
    GlobalStrategy,
    LogicalEditingPlan,
    PlanItem,
    PlanPatch,
    ToolCapabilityProfile,
    short_hash,
)

#: 复用判定时忽略的子字段（创作抖动与 provenance 不算结构变化）
_VOLATILE_FIELDS = {"style_spec", "provenance", "asset_request_ref", "status"}

#: 依附型操作：只修改/删除已有项，永不拥有自己的 PlanItem。
#: 这类 op 常驻 intent.explicit_operations，若按"无旧项=changed"判定
#: 会让每个持久 op 每轮都强制全量重规划（LLM 抖动 × N）。
_PATCH_LEVEL_OPS = {
    OperationType.scale_adjust,
    OperationType.position_adjust,
    OperationType.replace_text,
    OperationType.remove,
}


def _structural_dump(item: PlanItem) -> Dict[str, Any]:
    data = model_dump(item)
    for key in _VOLATILE_FIELDS:
        data.pop(key, None)
    return data


def _req_hash_of(item: PlanItem) -> Optional[str]:
    for rule in item.provenance.rules:
        if rule.startswith("req:"):
            return rule[4:]
    return None


def build_plan_patch(
    planner: Any,  # EditingPlanner（避免循环导入）
    plan: LogicalEditingPlan,
    intent: EditingIntent,
    patch: Optional[IntentPatch],
    view: Optional[Any],
    tool_capabilities: Optional[Any],
    accessibility_profile: Optional[Any],
    planner_context: Optional[Any],
) -> PlanPatch:
    out = PlanPatch()

    # ---- 收集新意图的需求/操作/约束 id 与内容哈希 ----
    new_reqs: Dict[str, Any] = {}
    for r in intent.object_requirements + intent.event_bound_requirements:
        new_reqs[r.id] = r
    for o in intent.explicit_operations:
        new_reqs[o.id] = o
    for c in intent.constraints:
        new_reqs[c.id] = c

    # 旧计划按"第一来源需求"分组（多来源项归入首项）
    old_by_req: Dict[str, List[PlanItem]] = {}
    for item in plan.plan_items:
        rid = item.source_requirement_ids[0] if item.source_requirement_ids else ""
        old_by_req.setdefault(rid, []).append(item)
    old_by_key = {i.plan_key: i for i in plan.plan_items}

    # ---- 删除：旧需求在新意图中不存在 ----
    removed_uids: Set[str] = set()
    for rid, items in old_by_req.items():
        if rid and rid not in new_reqs:
            for item in items:
                removed_uids.add(item.plan_item_uid)
    out.remove_plan_item_uids = sorted(removed_uids)

    # ---- 变更检测：内容哈希不一致的需求需要重规划 ----
    op_by_id = {o.id: o for o in intent.explicit_operations}
    adjusted_ops = {
        rule[len("adjusted_by:"):]
        for item in plan.plan_items
        for rule in item.provenance.rules
        if rule.startswith("adjusted_by:")
    }
    changed: Set[str] = set()
    for rid, req in new_reqs.items():
        old_items = old_by_req.get(rid)
        if not old_items:
            if isinstance(req, Constraint):
                # 约束不拥有 PlanItem：任一宿主项的 cstr 哈希一致即未变更
                if _constraint_unchanged(req, plan.plan_items):
                    continue
            else:
                op = op_by_id.get(rid)
                if op is not None and op.operation in _PATCH_LEVEL_OPS:
                    # 已被打到项上（adjusted_by 标记）或当前无可解析
                    # 目标 → 效果已固化/无法生效，不视为变更
                    if rid in adjusted_ops or not _op_targets(
                        op, plan.plan_items
                    ):
                        continue
            changed.add(rid)
            continue
        old_hash = _req_hash_of(old_items[0])
        if old_hash != short_hash(req)[:8]:
            changed.add(rid)

    # 约束不走自己的 PlanItem——变更沿 constraint_refs 传播到宿主需求；
    # 新增约束（旧项还没有它的 ref）按适用面 _constraint_applies 找宿主
    constraint_changed = {
        rid for rid in changed
        if isinstance(new_reqs.get(rid), Constraint)
    }
    if patch:
        constraint_changed |= set(patch.remove_constraint_ids)
    for cid in constraint_changed:
        constraint = next(
            (c for c in intent.constraints if c.id == cid), None
        )
        for item in plan.plan_items:
            hit = cid in item.constraint_refs
            if not hit and constraint is not None:
                hit = _constraint_hit(constraint, item)
            if hit:
                for rid in item.source_requirement_ids:
                    changed.add(rid)
    # 被删除的显式操作同理：曾打到项上的 adjusted_by 需要重规划回滚
    if patch:
        for oid in patch.remove_operation_ids:
            for item in plan.plan_items:
                if f"adjusted_by:{oid}" in item.provenance.rules:
                    for rid in item.source_requirement_ids:
                        if rid != oid:
                            changed.add(rid)

    # ---- 显式操作中对已有项的修改/删除（§60 路径）----
    # 先解析 patch 里 scale/position/replace_text/remove 的目标项——不经
    # 全量重规划：调整类克隆旧项打参数补丁，删除类直接进 remove 列表。
    touched: Set[str] = set()
    if patch is not None:
        for op in list(patch.add_operations) + list(patch.update_operations):
            if op.operation == OperationType.volume_adjust:
                # 克隆路径写的 volume_offset 是死参数——executor 只认独立
                # volume item 的 delta_db；留给全量重规划生成
                continue
            targets = _op_targets(op, plan.plan_items)
            if not targets:
                continue
            if op.operation == OperationType.remove:
                for item in targets:
                    touched.add(item.plan_item_uid)
                    if item.plan_item_uid not in removed_uids:
                        removed_uids.add(item.plan_item_uid)
                        out.remove_plan_item_uids.append(item.plan_item_uid)
                changed.discard(op.id)
                continue
            for item in targets:
                clone = model_validate(PlanItem, model_dump(item))
                _apply_op_to_item(op, clone)
                touched.add(clone.plan_item_uid)
                out.update_plan_items.append(clone)
            changed.discard(op.id)  # 操作本身不再需要全量项
        # patch 里的删除直接映射
        for rid in patch.remove_object_requirement_ids + patch.remove_event_bound_requirement_ids:
            for item in old_by_req.get(rid, []):
                if item.plan_item_uid not in removed_uids:
                    removed_uids.add(item.plan_item_uid)
                    out.remove_plan_item_uids.append(item.plan_item_uid)

    # ---- 需要全量重规划的需求 ----
    needs_replan = bool(changed)
    if needs_replan and view is None:
        raise ValueError("replan 需要 semantic_view 以展开需求（§52）")

    if needs_replan:
        # 只把变更需求喂给 plan()——全量 intent 会让 Creative LLM 每轮
        # 为所有需求重新创作（上下文大、抖动面大），而过滤逻辑
        # 本就只保留 changed 项。两类宿主必须随行：
        # 1) 目标落在 scope 内的持久 op——否则载体项重发射后
        #    _apply_adjust 不再运行，scale/remove 效果被回滚；
        # 2) music 需求——api.py 的 has_music 开关影响事件项生成，
        #    scope 外置会让已变更需求产出不同结果。
        scope_ids = set(changed)
        for o in intent.explicit_operations:
            if o.id in changed:
                for item in _op_targets(o, plan.plan_items):
                    scope_ids.update(item.source_requirement_ids)
        scope_ids.update(
            r.id
            for r in intent.object_requirements
            if r.object_type == ObjectType.music
        )
        for o in intent.explicit_operations:
            if o.id in scope_ids:
                continue
            if any(
                rid in scope_ids
                for item in _op_targets(o, plan.plan_items)
                for rid in item.source_requirement_ids
            ):
                scope_ids.add(o.id)
        scoped_intent = EditingIntent(
            global_intent=intent.global_intent,
            object_requirements=[
                r for r in intent.object_requirements if r.id in scope_ids
            ],
            event_bound_requirements=[
                r for r in intent.event_bound_requirements if r.id in scope_ids
            ],
            explicit_operations=[
                o for o in intent.explicit_operations if o.id in scope_ids
            ],
            constraints=list(intent.constraints),
            unresolved=list(intent.unresolved),
        )
        input_model = EditingPlannerInput(
            editing_intent=scoped_intent,
            semantic_view=view,
            accessibility_profile=accessibility_profile,
            tool_capabilities=tool_capabilities or ToolCapabilityProfile(),
            existing_plan=plan,
            planner_context=planner_context
            if planner_context is not None
            else _default_context(),
        )
        new_plan = planner.plan(input_model)
        new_items = [
            i for i in new_plan.plan_items
            if i.source_requirement_ids and i.source_requirement_ids[0] in changed
            and i.plan_item_uid not in touched
        ]
        new_keys = {i.plan_key for i in new_items}
        for ni in new_items:
            old = old_by_key.get(ni.plan_key)
            if old is None:
                out.add_plan_items.append(ni)
            elif _structural_dump(ni) != _structural_dump(old):
                # 保留旧 uid（plan_key 相同 → uid 本就相同）+ 新内容
                ni.plan_item_uid = old.plan_item_uid
                out.update_plan_items.append(ni)
            elif _cstr_tags(ni) != _cstr_tags(old):
                # 结构一致但约束哈希过期（约束内容变了、项结构未变）——
                # 刷新 provenance 让下轮判为 unchanged
                ni.plan_item_uid = old.plan_item_uid
                out.update_plan_items.append(ni)
            # 结构一致 → 复用旧项（不进补丁，LLM 抖动被挡住）
        for rid in changed:
            for oi in old_by_req.get(rid, []):
                if (
                    oi.plan_key not in new_keys
                    and oi.plan_item_uid not in removed_uids
                    and oi.plan_item_uid not in touched
                ):
                    removed_uids.add(oi.plan_item_uid)
                    out.remove_plan_item_uids.append(oi.plan_item_uid)

        # 素材请求 diff：dedup_key 对齐
        old_req_keys = {r.dedup_key: r.request_uid for r in plan.asset_requests}
        for req in new_plan.asset_requests:
            if req.dedup_key not in old_req_keys:
                out.add_asset_requests.append(req)
        # 新项引用到已存在 dedup_key 的 → 改写为旧 request_uid
        for item in out.add_plan_items + out.update_plan_items:
            ref = item.asset_request_ref
            if not ref:
                continue
            new_req = next(
                (r for r in new_plan.asset_requests if r.request_uid == ref), None
            )
            if new_req and new_req.dedup_key in old_req_keys:
                item.asset_request_ref = old_req_keys[new_req.dedup_key]

    # ---- 全局策略更新 ----
    from ..strategy.global_strategy import build_global_strategy

    profile_model = plan.accessibility_profile
    if accessibility_profile is not None:
        if isinstance(accessibility_profile, dict):
            profile_model = model_validate(AccessibilityPlanningProfile, accessibility_profile)
        elif isinstance(accessibility_profile, AccessibilityPlanningProfile):
            profile_model = accessibility_profile
        else:
            from ..accessibility.context import to_planning_profile

            profile_model = to_planning_profile(accessibility_profile)
    new_strategy = build_global_strategy(
        intent, profile_model, {}
    )
    if model_dump(new_strategy) != model_dump(plan.global_strategy):
        out.global_strategy_updates = model_dump(new_strategy)

    out.requires_rematerialization = bool(
        out.add_plan_items
        or out.update_plan_items
        or out.remove_plan_item_uids
        or out.add_asset_requests
    )
    return out


def apply_plan_patch(plan: LogicalEditingPlan, patch: PlanPatch) -> LogicalEditingPlan:
    """按 plan_item_uid / request_uid 合并；version+1，plan_uid 不变。"""
    items = {i.plan_item_uid: i for i in plan.plan_items}
    for uid in patch.remove_plan_item_uids:
        items.pop(uid, None)
    for item in patch.add_plan_items + patch.update_plan_items:
        items[item.plan_item_uid] = item
    plan.plan_items = list(items.values())

    requests = {r.request_uid: r for r in plan.asset_requests}
    for uid in patch.remove_asset_request_uids:
        requests.pop(uid, None)
    for req in patch.add_asset_requests + patch.update_asset_requests:
        requests[req.request_uid] = req
    plan.asset_requests = list(requests.values())

    if patch.global_strategy_updates:
        plan.global_strategy = model_validate(
            GlobalStrategy, patch.global_strategy_updates
        )
    plan.version = plan.version + 1
    return plan


def _default_context():
    from ..models import PlannerContext

    return PlannerContext()


def _op_targets(op, items: List[PlanItem]) -> List[PlanItem]:
    from ..api import _resolve_target_items  # 延迟导入打破 api↔replan 环

    return _resolve_target_items(op.target, items)


def _constraint_unchanged(constraint: Constraint, items: List[PlanItem]) -> bool:
    tag = f"cstr:{constraint.id}:{short_hash(model_dump(constraint))[:8]}"
    return any(tag in i.provenance.rules for i in items)


def _constraint_hit(constraint: Constraint, item: PlanItem) -> bool:
    from ..api import _constraint_applies  # 延迟导入打破 api↔replan 环

    return _constraint_applies(
        constraint, item, str((constraint.scope or {}).get("target") or "")
    )


def _cstr_tags(item: PlanItem) -> Set[str]:
    return {r for r in item.provenance.rules if r.startswith("cstr:")}


def _apply_op_to_item(op, item: PlanItem) -> None:
    """把显式操作打到克隆项上（与 api._apply_adjust 同语义）。"""
    if op.operation == OperationType.scale_adjust:
        direction = str(op.parameters.get("direction") or "")
        factor = 0.8 if direction in ("smaller", "小") else 1.25
        if item.spatial_spec is not None:
            policy = dict(item.spatial_spec.scale_policy or {"mode": "relative", "value": 0.16})
            policy["value"] = round(float(policy.get("value", 0.16)) * factor, 4)
            item.spatial_spec.scale_policy = policy
        item.parameters["scale_factor"] = factor
    elif op.operation == OperationType.position_adjust:
        item.parameters["position_offset"] = op.parameters.get("offset") or {
            "dx": 0.0,
            "dy": 0.0,
        }
    elif op.operation == OperationType.replace_text:
        if op.parameters.get("value") is not None:
            item.parameters["text"] = op.parameters["value"]
    item.provenance.rules.append(f"adjusted_by:{op.id}")
    if op.id not in item.source_requirement_ids:
        item.source_requirement_ids.append(op.id)
