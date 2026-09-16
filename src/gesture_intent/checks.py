"""Deterministic post-processing: queries, conflicts and normalized semantics."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .models import (
    Conflict,
    Constraint,
    EditingIntent,
    EventBoundRequirement,
    IntentPatch,
    ObjectAction,
    ObjectType,
    OperationType,
    RequiredVideoQuery,
    TemporalRelation,
)


def normalized_semantics(intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if intent:
        global_intent = intent.global_intent
        for field in ("theme", "mood", "style", "pacing", "color_preference"):
            value = getattr(global_intent, field)
            if value:
                values.append({"scope": "global", "field": field, "raw": value.raw, "canonical": value.canonical, "tags": value.tags})
        for requirement in intent.object_requirements:
            if requirement.description:
                values.append({"scope": "object", "id": requirement.id, "raw": requirement.description.raw, "canonical": requirement.description.canonical, "tags": requirement.description.tags})
        for requirement in intent.event_bound_requirements:
            event = requirement.trigger.event
            description = requirement.requirement.semantic_description
            values.append({"scope": "event", "id": requirement.id, "event": event.canonical, "event_raw": event.raw, "raw": description.raw if description else None, "tags": description.tags if description else []})
    if patch:
        for requirement in patch.add_object_requirements + patch.update_object_requirements:
            if requirement.description:
                values.append({"scope": "object_patch", "id": requirement.id, "raw": requirement.description.raw, "canonical": requirement.description.canonical, "tags": requirement.description.tags})
        for operation in patch.add_operations + patch.update_operations:
            values.append({"scope": "operation_patch", "id": operation.id, "operation": operation.operation.value, "target": operation.target.value})
        for requirement in patch.add_event_bound_requirements + patch.update_event_bound_requirements:
            event = requirement.trigger.event
            description = requirement.requirement.semantic_description
            values.append({"scope": "event_patch", "id": requirement.id, "event": event.canonical, "raw": description.raw if description else None, "tags": description.tags if description else []})
    return values


def required_video_queries(intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None) -> list[RequiredVideoQuery]:
    queries: list[RequiredVideoQuery] = []
    event_requirements: list[EventBoundRequirement] = list(intent.event_bound_requirements) if intent else []
    if patch:
        event_requirements += patch.add_event_bound_requirements + patch.update_event_bound_requirements
    for requirement in event_requirements:
        event = requirement.trigger.event
        if event.type.value == "pose_condition":
            queries.append(RequiredVideoQuery(type="pose_condition_detection", condition=event.condition))
        elif event.type.value == "audio_event":
            queries.append(RequiredVideoQuery(type="audio_event_detection", event=event.canonical, required_occurrence=requirement.trigger.occurrence))
        elif event.type.value == "video_structure":
            queries.append(RequiredVideoQuery(type="video_structure_detection", event=event.canonical, required_occurrence=requirement.trigger.occurrence))
        else:
            queries.append(RequiredVideoQuery(type="event_detection", event=event.canonical, required_occurrence=requirement.trigger.occurrence))
    operations = list(intent.explicit_operations) if intent else []
    if patch:
        operations += patch.add_operations + patch.update_operations
    for operation in operations:
        if operation.target.type == "semantic_event":
            queries.append(RequiredVideoQuery(type="video_structure_detection", event=operation.target.value))
    constraints = list(intent.constraints) if intent else []
    if patch:
        constraints += patch.add_constraints + patch.update_constraints
    for constraint in constraints:
        if constraint.type == "avoid_overlap" and constraint.reference == "face":
            queries.append(RequiredVideoQuery(type="person_face_tracking", reference="face"))
        elif constraint.type == "preserve_subject":
            queries.append(RequiredVideoQuery(type="person_tracking", reference="person"))
    unique: dict[tuple[Any, ...], RequiredVideoQuery] = {}
    for query in queries:
        key = (query.type, query.event, query.reference, str(query.condition), str(query.required_occurrence))
        unique[key] = query
    return list(unique.values())


def conflicts(intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None, object_types: Optional[dict[str, str]] = None) -> list[Conflict]:
    result: list[Conflict] = []
    constraints = intent.constraints if intent else []
    operations = intent.explicit_operations if intent else []
    object_requirements = intent.object_requirements if intent else []
    event_requirements = intent.event_bound_requirements if intent else []
    if patch:
        constraints = constraints + patch.add_constraints + patch.update_constraints
        operations = operations + patch.add_operations + patch.update_operations
        object_requirements = object_requirements + patch.add_object_requirements + patch.update_object_requirements
        event_requirements = event_requirements + patch.add_event_bound_requirements + patch.update_event_bound_requirements

    preserve_duration = next((item for item in constraints if item.type == "preserve_duration"), None)
    freeze = None
    for item in operations:
        if item.operation != OperationType.freeze:
            continue
        raw_duration = item.parameters.get("duration", {}).get("value", 0)
        try:
            duration = float(raw_duration or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration > 0:
            freeze = item
            break
    if preserve_duration and freeze:
        result.append(Conflict(requirements=[preserve_duration.id, freeze.id], type="duration_conflict", severity="medium", source_texts=[preserve_duration.raw, freeze.source_text]))

    preserve_music = next((item for item in constraints if item.type == "preserve_original_music"), None)
    changed_music = next((item for item in object_requirements if item.object_type == ObjectType.music and item.action in {ObjectAction.replace, ObjectAction.remove}), None)
    # Only replacing or removing a music track counts as "changing" it; a
    # volume_adjust merely changes loudness and must not be treated as editing
    # the original music. Accept either the reserved phrases or a resolved
    # music object_id (after resolve_references rewrites the target) — the id
    # may be a requirement id from the current intent or a project-view object
    # id, so check both namespaces via object_types.
    music_ids = {item.id for item in object_requirements if item.object_type == ObjectType.music}
    music_ids |= {object_id for object_id, kind in (object_types or {}).items() if kind == ObjectType.music.value}
    changed_music_operation = next((item for item in operations if item.target.value in ({"current music", "original_music"} | music_ids) and item.operation in {OperationType.replace_asset, OperationType.remove}), None)
    # A resolved remove op is consumed into the patch's remove lists and no
    # longer appears under operations — check those ids for music too.
    removed_music = None
    if patch:
        removed_ids = patch.remove_object_requirement_ids + patch.remove_event_bound_requirement_ids + patch.remove_operation_ids
        removed_music = next((rid for rid in removed_ids if rid in music_ids), None)
    if preserve_music and (changed_music or changed_music_operation or removed_music):
        other = changed_music or changed_music_operation
        other_id = other.id if other else removed_music
        source_texts = [preserve_music.raw, other.source_text] if other else [preserve_music.raw]
        result.append(Conflict(requirements=[preserve_music.id, other_id], type="original_music_conflict", severity="high", source_texts=source_texts))

    prohibit_text = next((item for item in constraints if item.type == "prohibit_object" and item.reference == "text"), None)
    added_text = next((item for item in object_requirements if item.object_type == ObjectType.text and item.action in {ObjectAction.add, ObjectAction.replace}), None)
    event_text = next((item for item in event_requirements if item.requirement.object_type == ObjectType.text), None)
    if prohibit_text and (added_text or event_text):
        other = added_text or event_text
        result.append(Conflict(requirements=[prohibit_text.id, other.id], type="text_prohibition_conflict", severity="high", source_texts=[prohibit_text.raw, other.source_text]))

    if patch:
        remove_ids = set(patch.remove_object_requirement_ids + patch.remove_event_bound_requirement_ids + patch.remove_operation_ids)
        modified_ids = {item.id for item in patch.update_object_requirements + patch.update_event_bound_requirements + patch.update_operations}
        for object_id in sorted(remove_ids & modified_ids):
            result.append(Conflict(requirements=[object_id], type="remove_and_modify_conflict", severity="high", source_texts=[]))
    return result
