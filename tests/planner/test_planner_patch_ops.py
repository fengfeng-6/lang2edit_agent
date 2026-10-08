"""模块六依赖的模块三最小扩展回归（remove/volume/replace_music/持久 op）。

- remove op 在 §60 克隆路径 → remove_plan_item_uids（实例级删除）
- volume_adjust 不走克隆路径（volume_offset 是死参数），全量重规划
  产独立 volume item（delta_db + audio_track target）
- object 需求产出的 mutation 项（replace_music）不挂 video target——
  否则 executor mutation 把 asset_uid 覆写到主视频
- 已消费/无目标的持久 op 不再每轮强制全量重规划
"""

from __future__ import annotations

from typing import Any, Dict

from planner_fixtures import (
    heart_intent,
    make_view,
    smaller_second_heart_patch,
)
from gesture_intent.models import (
    EditingIntent,
    ExplicitOperation,
    IntentPatch,
    ObjectAction,
    ObjectRequirement,
    ObjectType,
    OperationType,
    SemanticValue,
    TargetReference,
)

from editing_planner import EditingPlanner
from editing_planner.models import PlanOperation


class _SpyPlanner:
    """记录 creative planner 调用次数——replan 触发全量规划即 >0。"""

    name = "spy"

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return {}


def _base_plan():
    return EditingPlanner().plan({
        "editing_intent": heart_intent(),
        "semantic_view": make_view(),
    })


def _remove_op(uid: str) -> ExplicitOperation:
    return ExplicitOperation(
        id="operation_09",
        operation=OperationType.remove,
        target=TargetReference(type="object_id", value=uid),
        source_text="把第二个爱心删掉",
    )


def test_remove_op_maps_to_remove_plan_items():
    """把第二个爱心删掉 → 该实例进 remove_plan_item_uids，而非 clone+update。"""
    plan = _base_plan()
    target = next(
        i for i in plan.plan_items if i.parameters.get("occurrence_index") == 2
    )
    op = _remove_op(target.plan_item_uid)
    merged = heart_intent()
    merged.explicit_operations = [op]

    patch = EditingPlanner().replan(
        plan, merged,
        intent_patch=IntentPatch(add_operations=[op]),
        semantic_view=make_view(),
    )
    assert target.plan_item_uid in patch.remove_plan_item_uids
    assert not patch.update_plan_items
    # op 被 §60 消费后不再要求全量重规划
    assert all(
        i.source_requirement_ids[0] != op.id for i in patch.add_plan_items
    )

    applied = EditingPlanner().apply_patch(plan, patch)
    assert target.plan_item_uid not in {
        i.plan_item_uid for i in applied.plan_items
    }


def test_persistent_remove_op_does_not_resurrect():
    """remove op 沉淀在 intent 中：下一轮 replan 目标项保持缺席且不再变更。"""
    plan = _base_plan()
    target = next(
        i for i in plan.plan_items if i.parameters.get("occurrence_index") == 2
    )
    op = _remove_op(target.plan_item_uid)
    merged = heart_intent()
    merged.explicit_operations = [op]

    planner = EditingPlanner()
    patch = planner.replan(
        plan, merged,
        intent_patch=IntentPatch(add_operations=[op]),
        semantic_view=make_view(),
    )
    applied = planner.apply_patch(plan, patch)

    spy = _SpyPlanner()
    planner2 = EditingPlanner(creative_planner=spy)
    patch2 = planner2.replan(
        applied, merged, intent_patch=None, semantic_view=make_view(),
    )
    assert spy.calls == 0  # 目标已不在计划中 → op 无可解析目标 → 不重规划
    assert not patch2.requires_rematerialization
    assert target.plan_item_uid not in {
        i.plan_item_uid for i in applied.plan_items
    }


def test_volume_adjust_produces_standalone_item():
    """音乐再小一点 → 独立 volume_adjust 项（delta_db），不克隆出死参数。"""
    plan = _base_plan()
    op = ExplicitOperation(
        id="operation_vol",
        operation=OperationType.volume_adjust,
        target=TargetReference(type="object_reference", value="current music"),
        parameters={"direction": "quieter"},
        source_text="音乐再小一点",
    )
    merged = heart_intent()
    merged.explicit_operations = [op]

    patch = EditingPlanner().replan(
        plan, merged,
        intent_patch=IntentPatch(add_operations=[op]),
        semantic_view=make_view(),
    )
    volume_items = [
        i for i in patch.add_plan_items
        if i.operation == PlanOperation.volume_adjust
    ]
    assert len(volume_items) == 1
    item = volume_items[0]
    assert item.parameters.get("delta_db") == -3.0
    assert item.target.get("type") == "audio_track"
    # 克隆路径不得再写死参数
    assert not any(
        "volume_offset" in i.parameters for i in patch.update_plan_items
    )


def test_replace_music_item_has_no_video_target():
    """replace_music 是 mutation 项：不得携带 {"type":"video"} target。"""
    intent = EditingIntent(
        object_requirements=[
            ObjectRequirement(
                id="req_music_01",
                object_type=ObjectType.music,
                action=ObjectAction.replace,
                description=SemanticValue(raw="欢快音乐", canonical="happy_music"),
                source_text="音乐换成欢快的",
            )
        ]
    )
    plan = EditingPlanner().plan({
        "editing_intent": intent,
        "semantic_view": make_view(),
    })
    item = next(
        i for i in plan.plan_items
        if i.operation == PlanOperation.replace_music
    )
    assert item.target.get("type") != "video"
    assert not item.target.get("value")


def test_consumed_adjust_op_does_not_force_replan():
    """§60 已消费的 scale op 常驻 intent → 下轮 replan 不触发全量规划。"""
    planner = EditingPlanner()
    plan = planner.plan({
        "editing_intent": heart_intent(),
        "semantic_view": make_view(),
    })
    merged = heart_intent()
    merged.explicit_operations = list(
        smaller_second_heart_patch().add_operations
    )
    patch = planner.replan(
        plan, merged,
        intent_patch=smaller_second_heart_patch(),
        semantic_view=make_view(),
    )
    applied = planner.apply_patch(plan, patch)

    spy = _SpyPlanner()
    planner2 = EditingPlanner(creative_planner=spy)
    patch2 = planner2.replan(
        applied, merged, intent_patch=None, semantic_view=make_view(),
    )
    assert spy.calls == 0
    assert not patch2.requires_rematerialization
