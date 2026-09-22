"""PlanPatch / 局部重规划（§52/§60）。"""

from __future__ import annotations

from planner_fixtures import (
    event_req,
    heart_intent,
    make_view,
    smaller_second_heart_patch,
)
from gesture_intent.models import ConstraintLevel, EditingIntent

from editing_planner import EditingPlanner


def _base_plan():
    return EditingPlanner().plan({
        "editing_intent": heart_intent(),
        "semantic_view": make_view(),
    })


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
