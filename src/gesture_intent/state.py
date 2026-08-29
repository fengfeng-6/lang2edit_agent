"""Apply intent patches while preserving untouched requirements."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import Autonomy, EditingIntent, IntentPatch, SemanticValue, model_dump, model_validate


class IntentStateManager:
    def apply_patch(self, current: EditingIntent, patch: IntentPatch) -> EditingIntent:
        updated = model_validate(EditingIntent, model_dump(current))
        updated.object_requirements = _apply_collection(updated.object_requirements, patch.add_object_requirements, patch.update_object_requirements, patch.remove_object_requirement_ids)
        updated.event_bound_requirements = _apply_collection(updated.event_bound_requirements, patch.add_event_bound_requirements, patch.update_event_bound_requirements, patch.remove_event_bound_requirement_ids)
        updated.explicit_operations = _apply_collection(updated.explicit_operations, patch.add_operations, patch.update_operations, patch.remove_operation_ids)
        updated.constraints = _apply_collection(updated.constraints, patch.add_constraints, patch.update_constraints, patch.remove_constraint_ids)

        for field, value in patch.global_updates.items():
            if not hasattr(updated.global_intent, field):
                continue
            current_value = getattr(updated.global_intent, field)
            if isinstance(value, dict):
                if field == "autonomy":
                    value = model_validate(Autonomy, value)
                elif field in {"theme", "mood", "style", "pacing", "color_preference"} or current_value is not None:
                    value = model_validate(SemanticValue, value)
            setattr(updated.global_intent, field, value)
        return updated


def _apply_collection(current: list[Any], additions: list[Any], updates: list[Any], removals: list[str]) -> list[Any]:
    by_id = {item.id: item for item in current}
    for item in additions:
        by_id[item.id] = deepcopy(item)
    for item in updates:
        by_id[item.id] = deepcopy(item)
    for item_id in removals:
        by_id.pop(item_id, None)
    # Keep original order for existing objects and append new IDs in patch order.
    result: list[Any] = []
    seen: set[str] = set()
    for item in current + additions + updates:
        if item.id in by_id and item.id not in seen:
            result.append(by_id[item.id])
            seen.add(item.id)
    return result
