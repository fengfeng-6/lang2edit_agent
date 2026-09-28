"""DiffEngine（§32）：CREATE / UPDATE / DELETE / NOOP。

    CREATE:  o ∈ G* ∧ o ∉ G
    DELETE:  o ∉ G* ∧ o ∈ G
    UPDATE:  F_o* ≠ F_o
    NOOP:    F_o* = F_o
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models import DesiredProjectGraph, DiffAction, DiffSummary
from .properties import PropertyChange, compute_property_diff


@dataclass
class ObjectDiff:
    uid: str
    action: str  # DiffAction value
    changed_properties: List[PropertyChange] = field(default_factory=list)


@dataclass
class GraphDiff:
    first_run: bool
    diffs: List[ObjectDiff]
    summary: DiffSummary

    def objects_by_action(self, action: str) -> List[ObjectDiff]:
        return [d for d in self.diffs if d.action == action]


def diff_graphs(
    base: Optional[DesiredProjectGraph],
    desired: DesiredProjectGraph,
) -> GraphDiff:
    base_objects = base.objects if base is not None else {}
    diffs: List[ObjectDiff] = []

    for uid in sorted(desired.objects):
        new_obj = desired.objects[uid]
        old_obj = base_objects.get(uid)
        if old_obj is None:
            diffs.append(ObjectDiff(uid=uid, action=DiffAction.create.value))
            continue
        new_fp = new_obj.fingerprint
        old_fp = old_obj.fingerprint
        if new_fp == old_fp:
            diffs.append(ObjectDiff(uid=uid, action=DiffAction.noop.value))
            continue
        changes = compute_property_diff(old_obj, new_obj)
        diffs.append(
            ObjectDiff(
                uid=uid,
                action=DiffAction.update.value,
                changed_properties=changes,
            )
        )
    for uid in sorted(base_objects):
        if uid not in desired.objects:
            diffs.append(ObjectDiff(uid=uid, action=DiffAction.delete.value))

    summary = DiffSummary(
        created=sum(1 for d in diffs if d.action == DiffAction.create.value),
        updated=sum(1 for d in diffs if d.action == DiffAction.update.value),
        deleted=sum(1 for d in diffs if d.action == DiffAction.delete.value),
        noop=sum(1 for d in diffs if d.action == DiffAction.noop.value),
        object_changes={d.uid: d.action for d in diffs},
    )
    return GraphDiff(first_run=base is None, diffs=diffs, summary=summary)
