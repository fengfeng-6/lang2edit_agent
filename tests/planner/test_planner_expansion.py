"""Requirement Expander（§14-17）：occurrence 展开、缺失/不确定事件。"""

from __future__ import annotations

from planner_fixtures import _brief, event_req, make_view

from gesture_intent.models import ConstraintLevel, TemporalRelation
from video_understanding.events.router import query_id_for

from editing_planner.expansion.requirements import (
    EventIndex,
    expand_event_requirement,
    missing_to_dependency,
    required_query_for,
)


def _index(view):
    return EventIndex(view)


def test_expand_all_occurrence():
    view = make_view()
    req = event_req("event_req_01", "heart_gesture", occurrence="all")
    out = expand_event_requirement(req, _index(view))
    assert [b.event_uid for b in out.bindings] == ["evt_h1", "evt_h2"]


def test_expand_first_last_index_range():
    view = make_view()
    idx = _index(view)
    assert expand_event_requirement(
        event_req("r1", "heart_gesture", occurrence="first"), idx
    ).bindings[0].event_uid == "evt_h1"
    assert expand_event_requirement(
        event_req("r2", "heart_gesture", occurrence="last"), idx
    ).bindings[0].event_uid == "evt_h2"
    assert expand_event_requirement(
        event_req("r3", "heart_gesture", occurrence="index", occ_value=2), idx
    ).bindings[0].event_uid == "evt_h2"


def test_expand_index_out_of_range():
    req = event_req("r4", "heart_gesture", occurrence="index", occ_value=5)
    out = expand_event_requirement(req, _index(make_view()))
    assert out.missing_reason == "occurrence_out_of_range"
    assert not out.bindings


def test_missing_event_not_found_hard_blocks():
    """已查询但 not_found + hard → unfulfilled + blocked（§16）。"""
    req = event_req("r5", "v_sign", occurrence="all")
    query = required_query_for(req)
    view = make_view(query_statuses={query_id_for(query): "not_found"})
    out = expand_event_requirement(req, _index(view))
    assert out.missing_reason == "not_found"
    dep, unfulfilled, warn = missing_to_dependency(out, 1)
    assert dep is None
    assert unfulfilled is not None and unfulfilled.status == "blocked"


def test_missing_event_not_found_soft_skips():
    req = event_req("r6", "v_sign", level=ConstraintLevel.soft)
    query = required_query_for(req)
    view = make_view(query_statuses={query_id_for(query): "not_found"})
    out = expand_event_requirement(req, _index(view))
    dep, unfulfilled, warn = missing_to_dependency(out, 1)
    assert dep is None and unfulfilled is None and warn


def test_missing_event_never_queried_dependency():
    """无 query 记录 → PlannerDependencyRequest（§46），Planner 不自查。"""
    req = event_req("r7", "v_sign")
    view = make_view()  # query_statuses 空
    out = expand_event_requirement(req, _index(view))
    assert out.missing_reason == "not_analyzed"
    dep, unfulfilled, _ = missing_to_dependency(out, 1)
    assert dep is not None and dep.type.value == "video_analysis"
    assert dep.blocking is True
    assert dep.required_queries and dep.required_queries[0]["event"] == "v_sign"
    assert unfulfilled.status == "pending_dependency"


def test_all_uncertain_hard_needs_dependency():
    """§17：全 uncertain + hard → 依赖请求（重分析/用户确认）。"""
    events = [
        _brief("evt_u1", "gesture_heart_01", "heart_gesture", 1, 4.0, 4.35, 4.7,
               status="uncertain"),
        _brief("evt_u2", "gesture_heart_02", "heart_gesture", 2, 9.8, 10.35, 10.9,
               status="uncertain"),
    ]
    req = event_req("r8", "heart_gesture")
    out = expand_event_requirement(req, _index(make_view(events=events)))
    assert out.missing_reason == "all_uncertain"
    dep, unfulfilled, _ = missing_to_dependency(out, 1)
    assert dep is not None and dep.blocking
    assert unfulfilled.status == "pending_dependency"


def test_uncertain_soft_skips_uncertain_uses_confirmed():
    events = [
        _brief("evt_c1", "gesture_heart_01", "heart_gesture", 1, 4.0, 4.35, 4.7),
        _brief("evt_c2", "gesture_heart_02", "heart_gesture", 2, 9.8, 10.35, 10.9,
               status="uncertain"),
    ]
    req = event_req("r9", "heart_gesture", level=ConstraintLevel.soft)
    out = expand_event_requirement(req, _index(make_view(events=events)))
    assert [b.event_uid for b in out.bindings] == ["evt_c1"]
    assert out.skipped_uncertain == 1


def test_between_events_pairs():
    """between_events：index=i → event[i].end … event[i+1].start。"""
    req = event_req("r10", "heart_gesture", occurrence="index", occ_value=1,
                    relation=TemporalRelation.between_events)
    out = expand_event_requirement(req, _index(make_view()))
    assert len(out.bindings) == 1
    pair = out.bindings[0].pair
    assert pair[0]["event_uid"] == "evt_h1" and pair[1]["event_uid"] == "evt_h2"
