"""EditingPlanner 门面：dict 输入、状态路径、provenance、save/load。"""

from __future__ import annotations

from planner_fixtures import (
    _brief,
    event_req,
    heart_intent,
    make_view,
)
from gesture_intent.models import ConstraintLevel, EditingIntent

from editing_planner import EditingPlanner
from editing_planner.models import (
    LogicalEditingPlan,
    PlanItemStatus,
    ValidationStatus,
)


def test_plan_accepts_dict_and_model():
    planner = EditingPlanner()
    payload = {"editing_intent": heart_intent(), "semantic_view": make_view()}
    p1 = planner.plan(payload)
    p2 = planner.plan({
        "editing_intent": __import__("gesture_intent.models", fromlist=["model_dump"]).model_dump(heart_intent()),
        "semantic_view": make_view(),
    })
    assert p1.plan_items and p2.plan_items
    assert isinstance(p1, LogicalEditingPlan)


def test_provenance_fields():
    plan = EditingPlanner().plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    prov = plan.provenance
    assert prov.video_id == "vid_test"
    assert prov.semantic_state_version == 3
    assert prov.intent_hash and prov.semantic_view_hash
    assert prov.planner_mode in ("rules", "llm", "rules_fallback", "custom")


def test_blocked_status_for_missing_hard_event():
    intent = EditingIntent(event_bound_requirements=[
        event_req("r", "v_sign", level=ConstraintLevel.hard)
    ])
    # 视图里没这个事件，但已被查询过且 not_found → blocked（§16）
    from editing_planner.expansion.requirements import required_query_for
    from video_understanding.events.router import query_id_for
    q = required_query_for(intent.event_bound_requirements[0])
    view = make_view(query_statuses={query_id_for(q): "not_found"})
    plan = EditingPlanner().plan({"editing_intent": intent, "semantic_view": view})
    assert plan.validation.status == ValidationStatus.blocked
    assert plan.unresolved and plan.unresolved[0].status == "blocked"


def test_needs_dependency_status_for_unanalyzed_event():
    intent = EditingIntent(event_bound_requirements=[
        event_req("r", "v_sign", level=ConstraintLevel.hard)
    ])
    plan = EditingPlanner().plan({
        "editing_intent": intent, "semantic_view": make_view(),
    })
    assert plan.validation.status == ValidationStatus.needs_dependency
    assert plan.dependency_requests[0].type.value == "video_analysis"


def test_save_load_roundtrip(tmp_path):
    plan = EditingPlanner().plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    EditingPlanner().save(plan, tmp_path)
    back = EditingPlanner.load(tmp_path)
    assert back.plan_uid == plan.plan_uid
    assert len(back.plan_items) == len(plan.plan_items)


def test_plan_item_stable_uids_across_replan():
    """相同输入重跑 → plan_item_uid 稳定（§12 局部对齐前提）。"""
    payload = {"editing_intent": heart_intent(), "semantic_view": make_view()}
    p1 = EditingPlanner().plan(payload)
    p2 = EditingPlanner().plan(payload)
    assert [i.plan_item_uid for i in p1.plan_items] == [
        i.plan_item_uid for i in p2.plan_items
    ]


def test_planner_mode_rules_without_llm(monkeypatch):
    """无 API key → rules 模式（注入的 creative planner 是规则默认）。"""
    monkeypatch.delenv("PLANNER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    planner = EditingPlanner()
    plan = planner.plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    assert plan.provenance.planner_mode in ("rules", "rules_fallback")


def test_llm_output_only_touches_style():
    """LLM 返回越权字段 → 白名单吞掉，时间/坐标仍确定性。"""
    class RoguePlanner:
        name = "llm"
        def plan(self, context):
            return {
                "global_strategy": {"motion_language": ["evil_glow"]},
                "item_directives": [{
                    "requirement_id": "event_req_01",
                    "animation": "teleport_not_allowed",
                    "emphasis": "high",
                    "palette": ["pink"],
                    "duration_hint": 99.0,
                    "relation_preference": "nonsense",
                    "event_uid": "evt_forged",
                    "position": [0.0, 0.0],
                }],
            }

    planner = EditingPlanner(creative_planner=RoguePlanner())
    plan = planner.plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    assert plan.provenance.planner_mode == "llm"
    item = plan.plan_items[0]
    assert item.style_spec.animation == "pop"  # 非法枚举回默认
    assert item.style_spec.duration_hint <= 5.0  # 钳制
    assert item.style_spec.relation_preference is None
    assert "evt_forged" not in __import__("gesture_intent.models", fromlist=["model_dump"]).model_dump(plan)
