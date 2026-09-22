"""models.py 双版本兼容与字段校验（§11/§25-28/§45-52）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gesture_intent.models import model_dump, model_validate

from editing_planner.models import (
    AccessibilityPlanningProfile,
    DurationMode,
    DurationSpec,
    EventBoundary,
    LogicalEditingPlan,
    PlanItem,
    PlanItemProvenance,
    PlanItemStatus,
    PlanOperation,
    SpatialRelation,
    StrictModel,
    TemporalSpec,
    TimeAnchor,
    AnchorType,
    TimelineStructure,
    ValidationReport,
    ValidationStatus,
    PlanProvenance,
    _stable_uid,
)


def test_strict_model_forbids_extra_fields():
    with pytest.raises(ValidationError):
        model_validate(DurationSpec, {"mode": "exact", "value": 1.0, "bogus": 1})


def test_enum_wire_values():
    assert PlanOperation.add_overlay.value == "add_overlay"
    assert SpatialRelation.upper_left.value == "upper_left"
    assert EventBoundary.peak.value == "peak"
    assert PlanItemStatus.pending_dependency.value == "pending_dependency"
    assert ValidationStatus.needs_dependency.value == "needs_dependency"


def test_duration_spec_validators():
    with pytest.raises(ValidationError):
        DurationSpec(mode=DurationMode.exact)  # exact 必须给正数
    with pytest.raises(ValidationError):
        DurationSpec(mode=DurationMode.range, min=1.0, max=0.5)
    ok = DurationSpec(mode=DurationMode.range, min=0.6, value=0.8, max=1.0)
    assert ok.value == 0.8


def test_plan_item_roundtrip():
    item = PlanItem(
        plan_item_uid="pln_x",
        plan_key="event_req_01:evt_a:add_overlay",
        operation=PlanOperation.add_overlay,
        source_requirement_ids=["event_req_01"],
        temporal_spec=TemporalSpec(
            start_anchor=TimeAnchor(
                type=AnchorType.semantic_event, event_uid="evt_a",
                boundary=EventBoundary.peak,
            ),
            offset=-0.1,
        ),
        provenance=PlanItemProvenance(source_text="比心时出现爱心"),
    )
    dumped = model_dump(item)
    assert dumped["operation"] == "add_overlay"
    assert dumped["temporal_spec"]["start_anchor"]["event_uid"] == "evt_a"
    assert dumped["timeline_effect"] == "non_structural"
    back = model_validate(PlanItem, dumped)
    assert back.plan_key == item.plan_key
    assert back.temporal_spec.start_anchor.boundary == EventBoundary.peak


def test_plan_roundtrip_minimal():
    plan = LogicalEditingPlan(
        plan_uid="plan_x",
        provenance=PlanProvenance(video_id="v1"),
        timeline_structure=TimelineStructure(source_duration=12.0),
        validation=ValidationReport(status=ValidationStatus.valid),
    )
    back = model_validate(LogicalEditingPlan, model_dump(plan))
    assert back.plan_uid == "plan_x"
    assert back.version == 1


def test_stable_uid_deterministic():
    a = _stable_uid("pln", "event_req_01:evt_a:add_overlay")
    b = _stable_uid("pln", "event_req_01:evt_a:add_overlay")
    c = _stable_uid("pln", "event_req_01:evt_b:add_overlay")
    assert a == b and a.startswith("pln_") and a != c


def test_accessibility_profile_from_mobility():
    from video_understanding.models import MobilityProfile

    profile = AccessibilityPlanningProfile.from_mobility_profile(
        MobilityProfile(posture="seated", available_hands=["right"],
                        amplitude="low", amplitude_scale=0.6, tremor=True)
    )
    assert profile.posture == "seated"
    assert profile.active_hands == ["right"]  # posture 与 hands 正交（§7）
    assert profile.motion_amplitude == "low"
    assert profile.primary_action_region == "upper_body"
    assert profile.preserve_mobility_device is True
    assert profile.tremor is True
