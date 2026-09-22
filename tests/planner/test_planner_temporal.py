"""Temporal Resolver / Timeline（§25-27/§33/§38-39）。"""

from __future__ import annotations

from planner_fixtures import event_req, make_view

from gesture_intent.models import TemporalRelation
from editing_planner.models import (
    AnchorType,
    DurationMode,
    EventBoundary,
    PlanItem,
    PlanOperation,
    TimelineEffect,
)
from editing_planner.temporal.resolver import (
    temporal_spec_for_event,
    temporal_spec_for_structure,
    temporal_spec_full_video,
)
from editing_planner.temporal.timeline import (
    compose_timeline,
    source_to_project,
    timeline_effect_for,
)
from editing_planner.models import TemporalSpec, TimeAnchor, DurationSpec


def _spec(relation: TemporalRelation):
    req = event_req("r", "heart_gesture", relation=relation)
    return temporal_spec_for_event(req, "evt_h1", PlanOperation.add_overlay,
                                   has_music=True)


def test_relation_boundary_map():
    assert _spec(TemporalRelation.at_event).start_anchor.boundary == EventBoundary.peak
    assert _spec(TemporalRelation.during_event).start_anchor.boundary == EventBoundary.start
    assert _spec(TemporalRelation.during_event).duration.mode == DurationMode.event_span
    assert _spec(TemporalRelation.after_event).start_anchor.boundary == EventBoundary.end


def test_before_event_anchors_end_at_start():
    spec = _spec(TemporalRelation.before_event)
    assert spec.start_anchor is None
    assert spec.end_anchor.boundary == EventBoundary.start


def test_beat_snap_marking_priority():
    """§33：heart_gesture=high(0.20)；无音乐需求不打标。"""
    req = event_req("r", "heart_gesture")
    with_music = temporal_spec_for_event(req, "e", PlanOperation.add_overlay, has_music=True)
    without = temporal_spec_for_event(req, "e", PlanOperation.add_overlay, has_music=False)
    assert with_music.beat_snap == {"priority": "high", "max_shift": 0.2}
    assert without.beat_snap is None
    lean = event_req("r2", "lean_body")
    assert temporal_spec_for_event(lean, "e", PlanOperation.add_overlay,
                                   has_music=True).beat_snap["max_shift"] == 0.06


def test_structure_anchor():
    spec = temporal_spec_for_structure("last_action")
    assert spec.start_anchor.type == AnchorType.video_structure
    assert spec.start_anchor.boundary == EventBoundary.peak  # 定格锚在 peak


def test_full_video_span():
    spec = temporal_spec_full_video()
    assert spec.mode == "structural"
    assert spec.start_anchor.structure_key == "video_start"
    assert spec.end_anchor.structure_key == "video_end"


def test_timeline_effect_and_tracks():
    assert timeline_effect_for(PlanOperation.freeze) == TimelineEffect.insert_duration
    assert timeline_effect_for(PlanOperation.add_overlay) == TimelineEffect.non_structural

    items = [
        PlanItem(plan_item_uid="a", plan_key="a", operation=PlanOperation.replace_background),
        PlanItem(plan_item_uid="b", plan_key="b", operation=PlanOperation.add_overlay),
        PlanItem(plan_item_uid="c", plan_key="c", operation=PlanOperation.add_text),
        PlanItem(plan_item_uid="d", plan_key="d", operation=PlanOperation.add_music),
    ]
    timeline = compose_timeline(items, 12.0)
    z = {t.name: t.z_order for t in timeline.tracks}
    assert z["background"] < z["overlay"] < z["text"]
    assert timeline.tracks[-1].name == "text"
    names = {t.name: t.item_uids for t in timeline.tracks}
    assert names["overlay"] == ["b"] and names["audio"] == ["d"]


def test_source_to_project_shifts():
    shifts = [{"from_source_time": 5.0, "delta": 1.0},
              {"from_source_time": 9.0, "delta": 0.5}]
    assert source_to_project(4.0, shifts) == 4.0
    assert source_to_project(5.0, shifts) == 5.0  # 严格在点后才平移
    assert source_to_project(6.0, shifts) == 7.0
    assert source_to_project(10.0, shifts) == 11.5
