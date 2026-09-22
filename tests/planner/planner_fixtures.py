"""tests/planner 公共 fixtures：合成 SemanticView / EditingIntent / profile。

不走 CV 与 LLM——SemanticView 直接构造 dict（对应模块二
build_semantic_view 的输出形态），EditingIntent 直接构造模型
（不跑中文解析）。场景脚本：

    12s / 9:16 坐姿手势舞：
      2.0s  clap           (occ 1)
      4.0s  heart_gesture  (occ 1) —— anchor(0.5, 0.62) 胸前，脸在 0.06-0.26
      6.0s  point_right    (occ 1, direction=right)
      9.8s  heart_gesture  (occ 2)
      11.0s ending_pose    (occ 1)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from gesture_intent.models import (
    Autonomy,
    Constraint,
    ConstraintLevel,
    EditingIntent,
    EventBoundRequirement,
    EventReference,
    EventRequirement,
    EventTrigger,
    EventType,
    ExplicitOperation,
    GlobalIntent,
    ObjectAction,
    ObjectType,
    Occurrence,
    OccurrenceType,
    OperationType,
    SemanticValue,
    TargetReference,
    TemporalRelation,
)

VIDEO_DURATION = 12.0


def _brief(uid, display, canonical, occ, start, peak, end, status="confirmed",
           etype="gesture", **props):
    return {
        "event_uid": uid,
        "display_id": display,
        "canonical": canonical,
        "event_type": etype,
        "occurrence_index": occ,
        "start_time": start,
        "peak_time": peak,
        "end_time": end,
        "confidence": 0.9,
        "status": status,
        "invalidated": False,
        "properties": props,
    }


def _spatial(anchor=(0.5, 0.62), face=(0.4, 0.08, 0.6, 0.24), direction=None):
    out = {
        "event_anchor": list(anchor),
        "face_bbox": list(face),
        "person_bbox": [0.3, 0.06, 0.7, 0.95],
        "head_center": [0.5, 0.16],
        "protected_regions": {"face": [face[0] - 0.02, face[1] - 0.02,
                                       face[2] + 0.02, face[3] + 0.02]},
        "hands": {"left": [0.42, 0.58], "right": [0.58, 0.58]},
        "timestamp": 4.35,
    }
    if direction:
        out["direction"] = {"vector": [1.0, 0.0], "label": direction}
    return out


def make_view(
    events: Optional[List[Dict[str, Any]]] = None,
    spatial: Optional[Dict[str, Any]] = None,
    subject_profile: Optional[Dict[str, Any]] = None,
    query_statuses: Optional[Dict[str, str]] = None,
    audio: bool = True,
) -> Dict[str, Any]:
    events = events if events is not None else [
        _brief("evt_clap", "gesture_clap_01", "clap", 1, 2.0, 2.2, 2.4),
        _brief("evt_h1", "gesture_heart_01", "heart_gesture", 1, 4.0, 4.35, 4.7),
        _brief("evt_p1", "gesture_point_01", "point_right", 1, 6.0, 6.3, 6.6),
        _brief("evt_h2", "gesture_heart_02", "heart_gesture", 2, 9.8, 10.35, 10.9),
        _brief("evt_end", "body_ending_01", "ending_pose", 1, 11.0, 11.4, 11.8,
               etype="body_action"),
    ]
    spatial = spatial if spatial is not None else {
        "gesture_clap_01": _spatial(anchor=(0.5, 0.5)),
        "gesture_heart_01": _spatial(),
        "gesture_point_01": _spatial(direction="right"),
        "gesture_heart_02": _spatial(),
        "body_ending_01": _spatial(anchor=(0.5, 0.4)),
    }
    return {
        "video": {
            "video_id": "vid_test",
            "duration": VIDEO_DURATION,
            "fps": 15.0,
            "resolution": [720, 1280],
            "aspect_ratio": "9:16",
            "has_audio": True,
            "version": "v1",
        },
        "relevant_events": events,
        "spatial_summaries": spatial,
        "audio_summary": (
            {"original_audio": {"bpm": 120.0, "beat_count": 24,
                                "downbeat_count": 6,
                                "downbeats": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]}}
            if audio else {}
        ),
        "structural": {
            "video_start": 0.0,
            "video_end": VIDEO_DURATION,
            "first_action": {"event_ref": "evt_clap"},
            "last_action": {"event_ref": "evt_end"},
        },
        "query_statuses": query_statuses or {},
        "subject_profile": subject_profile if subject_profile is not None else {
            "posture": "seated",
            "available_hands": ["left", "right"],
            "amplitude": "low",
            "amplitude_scale": 0.6,
            "tremor": False,
            "mirrored": False,
            "inferred": True,
        },
        "state_version": 3,
    }


# ---------------------------------------------------------------------------
# EditingIntent 构造 helper
# ---------------------------------------------------------------------------


def event_req(
    req_id: str,
    canonical: str,
    object_type: ObjectType = ObjectType.sticker,
    occurrence: str = "all",
    occ_value: Optional[int] = None,
    relation: TemporalRelation = TemporalRelation.at_event,
    description: str = "粉色爱心",
    canonical_desc: Optional[str] = "heart",
    level: ConstraintLevel = ConstraintLevel.hard,
    source_text: str = "",
    event_type: EventType = EventType.gesture,
) -> EventBoundRequirement:
    occ = Occurrence(type=OccurrenceType(occurrence), value=occ_value)
    return EventBoundRequirement(
        id=req_id,
        source_text=source_text or f"{description}",
        trigger=EventTrigger(
            event=EventReference(type=event_type, raw=canonical, canonical=canonical),
            occurrence=occ,
            temporal_relation=relation,
        ),
        requirement=EventRequirement(
            object_type=object_type,
            action=ObjectAction.add,
            semantic_description=SemanticValue(
                raw=description, canonical=canonical_desc, tags=["pink", "cute"]
            ),
        ),
        constraint_level=level,
    )


def heart_intent(level: ConstraintLevel = ConstraintLevel.hard) -> EditingIntent:
    """§56：每次比心的时候出现粉色爱心，不要挡脸。"""
    return EditingIntent(
        global_intent=GlobalIntent(
            theme=SemanticValue(raw="夏日可爱", canonical="summer", tags=["cute", "pink"])
        ),
        event_bound_requirements=[
            event_req("event_req_01", "heart_gesture", level=level,
                      source_text="每次比心的时候出现粉色爱心"),
        ],
        constraints=[
            Constraint(
                id="constraint_01", type="avoid_overlap", reference="face",
                raw="不要挡脸", constraint_level=ConstraintLevel.hard,
            ),
        ],
    )


def follow_intent() -> EditingIntent:
    """§58：皇冠一直跟着头。"""
    from gesture_intent.models import ObjectRequirement

    return EditingIntent(
        object_requirements=[
            ObjectRequirement(
                id="req_sticker_01",
                object_type=ObjectType.sticker,
                action=ObjectAction.add,
                description=SemanticValue(raw="皇冠", canonical="crown"),
                source_text="皇冠一直跟着头",
                constraint_level=ConstraintLevel.hard,
            )
        ]
    )


def background_intent() -> EditingIntent:
    """§59：背景换成动漫海滩。"""
    from gesture_intent.models import ObjectRequirement

    return EditingIntent(
        object_requirements=[
            ObjectRequirement(
                id="req_background_01",
                object_type=ObjectType.background,
                action=ObjectAction.replace,
                description=SemanticValue(raw="动漫海滩", canonical="anime_beach"),
                source_text="背景换成动漫海滩",
                constraint_level=ConstraintLevel.hard,
            )
        ]
    )


def freeze_intent() -> EditingIntent:
    """最后动作定格一秒。"""
    return EditingIntent(
        explicit_operations=[
            ExplicitOperation(
                id="operation_01",
                operation=OperationType.freeze,
                target=TargetReference(type="semantic_event", value="last_action"),
                parameters={"duration": {"value": 1.0, "unit": "second"}},
                source_text="最后动作定格一秒",
            )
        ]
    )


def smaller_second_heart_patch() -> "Any":
    """§60：第二个爱心小一点 → IntentPatch.add_operations(scale_adjust)。"""
    from gesture_intent.models import IntentPatch

    return IntentPatch(
        add_operations=[
            ExplicitOperation(
                id="operation_02",
                operation=OperationType.scale_adjust,
                target=TargetReference(type="object_id", value="gesture_heart_02"),
                parameters={"direction": "smaller"},
                source_text="第二个爱心小一点",
            )
        ],
        affected_objects=["gesture_heart_02"],
    )
