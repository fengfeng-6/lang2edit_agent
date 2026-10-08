"""PlanPatch / 局部重规划（§52/§60）。"""

from __future__ import annotations

from planner_fixtures import (
    event_req,
    heart_intent,
    make_view,
    smaller_second_heart_patch,
)
from gesture_intent.models import (
    ConstraintLevel,
    EditingIntent,
    ObjectAction,
    ObjectType,
    SemanticValue,
)

from editing_planner import EditingPlanner


def _base_plan():
    return EditingPlanner().plan({
        "editing_intent": heart_intent(),
        "semantic_view": make_view(),
    })


def _spy_planner(seen):
    """返回 planner，其 .plan 会记录收到的 EditingPlannerInput。"""
    planner = EditingPlanner()
    orig_plan = planner.plan

    def _spy(input_model):
        intent = getattr(input_model, "editing_intent", None)
        if intent is None:
            intent = input_model["editing_intent"]
        seen["intent"] = intent
        return orig_plan(input_model)

    planner.plan = _spy
    return planner


def test_scenario60_second_heart_smaller():
    """§60：第二个爱心小一点 → PlanPatch 只更新那一项。"""
    plan = _base_plan()
    # 真实流程：IntentPatch 经 IntentStateManager 合并进当前意图——
    # merged intent 已包含 operation_02；patch 也传入用于 affected 指路
    merged = heart_intent()
    merged.explicit_operations = list(
        smaller_second_heart_patch().add_operations
    )
    patch = EditingPlanner().replan(
        plan, merged,
        intent_patch=smaller_second_heart_patch(),
        semantic_view=make_view(),
    )
    assert not patch.add_plan_items
    assert not patch.remove_plan_item_uids
    assert len(patch.update_plan_items) == 1
    updated = patch.update_plan_items[0]
    # 恰为 occurrence_index==2 的那一项
    assert updated.parameters.get("occurrence_index") == 2
    assert updated.parameters.get("scale_factor") == 0.8
    assert updated.spatial_spec.scale_policy["value"] < 0.16

    applied = EditingPlanner().apply_patch(plan, patch)
    assert applied.version == 2
    assert applied.plan_uid == plan.plan_uid
    scales = sorted(
        i.spatial_spec.scale_policy["value"]
        for i in applied.plan_items if i.spatial_spec
    )
    assert scales[0] < scales[1]  # 一个小一个大


def test_unchanged_requirements_not_touched():
    """需求未变 → patch 为空，不重规划（LLM 抖动被挡住）。"""
    plan = _base_plan()
    patch = EditingPlanner().replan(
        plan, heart_intent(), intent_patch=None, semantic_view=make_view(),
    )
    assert not patch.add_plan_items
    assert not patch.update_plan_items
    assert not patch.remove_plan_item_uids
    assert patch.requires_rematerialization is False


def test_removed_requirement_removes_items():
    """删掉 event_req_01 → 对应 plan_items 进 remove 列表。"""
    plan = _base_plan()
    empty = EditingIntent()
    patch = EditingPlanner().replan(
        plan, empty, intent_patch=None, semantic_view=make_view(),
    )
    assert set(patch.remove_plan_item_uids) == {
        i.plan_item_uid for i in plan.plan_items
    }
    applied = EditingPlanner().apply_patch(plan, patch)
    assert not applied.plan_items


def test_new_requirement_adds_items():
    """新增第二个事件需求 → add_plan_items 有新 key，旧 uid 保留。"""
    plan = _base_plan()
    intent2 = heart_intent()
    intent2.event_bound_requirements.append(
        event_req("event_req_02", "point_right", description="星星",
                  canonical_desc="star")
    )
    patch = EditingPlanner().replan(
        plan, intent2, intent_patch=None, semantic_view=make_view(),
    )
    old_uids = {i.plan_item_uid for i in plan.plan_items}
    added_keys = {i.plan_key for i in patch.add_plan_items}
    assert any("event_req_02" in k for k in added_keys)
    assert not (patch.remove_plan_item_uids)
    applied = EditingPlanner().apply_patch(plan, patch)
    assert old_uids <= {i.plan_item_uid for i in applied.plan_items}


def test_replan_scopes_intent_to_changed_requirements():
    """喂给 plan() 的意图只含 changed 需求——未变更需求不再走
    Creative 重创作（嵌套 plan 的上下文收缩）。"""
    plan = _base_plan()
    seen = {}
    planner = _spy_planner(seen)
    intent2 = heart_intent()
    intent2.event_bound_requirements.append(
        event_req("event_req_02", "point_right", description="星星",
                  canonical_desc="star")
    )
    patch = planner.replan(
        plan, intent2, intent_patch=None, semantic_view=make_view(),
    )

    scoped = seen["intent"]
    assert [r.id for r in scoped.event_bound_requirements] == ["event_req_02"]
    assert not scoped.object_requirements
    assert not scoped.explicit_operations
    # 约束全量随行（沿宿主传播到 scoped 项）
    assert [c.id for c in scoped.constraints] == ["constraint_01"]
    assert any("event_req_02" in i.plan_key for i in patch.add_plan_items)


def _music_req():
    from gesture_intent.models import ObjectRequirement

    return ObjectRequirement(
        id="req_music_01",
        object_type=ObjectType.music,
        action=ObjectAction.add,
        description=SemanticValue(raw="轻快音乐", canonical="upbeat"),
        source_text="加背景音乐",
    )


def test_replan_scope_keeps_music_requirement():
    """has_music 耦合：music 需求未变更也随 scope 进 plan()，
    避免改变已变更需求项的生成条件。"""
    intent = heart_intent()
    intent.object_requirements.append(_music_req())
    plan = EditingPlanner().plan({
        "editing_intent": intent,
        "semantic_view": make_view(),
    })
    seen = {}
    planner = _spy_planner(seen)
    intent2 = heart_intent()
    intent2.object_requirements.append(_music_req())
    intent2.event_bound_requirements.append(
        event_req("event_req_02", "point_right", description="星星",
                  canonical_desc="star")
    )
    planner.replan(
        plan, intent2, intent_patch=None, semantic_view=make_view(),
    )

    scoped = seen["intent"]
    assert [r.id for r in scoped.object_requirements] == ["req_music_01"]
    assert [r.id for r in scoped.event_bound_requirements] == ["event_req_02"]


def test_persistent_adjust_op_reapplied_on_replanned_items():
    """宿主需求重规划后，未变更的 scale op 仍落在新项上——
    scope 排除它会让调整随重发射回滚（回归）。"""
    plan = _base_plan()
    merged = heart_intent()
    merged.explicit_operations = list(
        smaller_second_heart_patch().add_operations
    )
    EditingPlanner().apply_patch(plan, EditingPlanner().replan(
        plan, merged, intent_patch=smaller_second_heart_patch(),
        semantic_view=make_view(),
    ))

    merged2 = heart_intent()
    merged2.explicit_operations = list(
        smaller_second_heart_patch().add_operations
    )
    merged2.event_bound_requirements[0].requirement.semantic_description.raw = (
        "红色爱心"
    )
    seen = {}
    planner = _spy_planner(seen)
    patch = planner.replan(
        plan, merged2, intent_patch=None, semantic_view=make_view(),
    )
    # 未变更的 op 因宿主在 scope 内而随行
    assert [o.id for o in seen["intent"].explicit_operations] == [
        "operation_02"]
    assert [r.id for r in seen["intent"].event_bound_requirements] == [
        "event_req_01"]
    # op 重落后新项与旧缩放项结构一致 → 不进补丁；apply 后缩放仍在
    applied = EditingPlanner().apply_patch(plan, patch)
    occ2 = [
        i for i in applied.plan_items
        if i.parameters.get("occurrence_index") == 2
    ]
    assert occ2 and occ2[0].parameters.get("scale_factor") == 0.8
    assert "adjusted_by:operation_02" in occ2[0].provenance.rules
