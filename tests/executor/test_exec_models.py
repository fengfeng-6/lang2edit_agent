"""模型层：round-trip、fingerprint 排除项、forward-ref patch（§31/§35）。"""

from __future__ import annotations

import copy

from gesture_intent.models import model_dump, model_validate

from edit_executor.compiler.fingerprint import object_fingerprint
from edit_executor.dsl.models import EditOperation, EditOpType
from edit_executor.models import (
    ExecutionPatch,
    ExecutionResult,
    ExecutionStatus,
    TimelineObject,
)

from executor_fixtures import overlay_item


def _obj(uid: str = "tlobj_x") -> TimelineObject:
    item = overlay_item(uid="item_a")
    return TimelineObject(
        timeline_object_uid=uid,
        object_key="item_a:overlay",
        source_plan_item_uid="item_a",
        object_type="heart",
        role="overlay",
        track_uid="trk_overlay",
        media_ref="mref_ast_heart",
        asset_uid="ast_heart",
        project_time=item.project_time,
        transform=item.transform,
        parameters={"object_type": "heart"},
    )


def test_timeline_object_roundtrip():
    obj = _obj()
    again = model_validate(TimelineObject, model_dump(obj))
    assert again == obj


def test_fingerprint_excludes_identity_fields():
    obj = _obj()
    base = object_fingerprint(obj)
    variant = copy.deepcopy(obj)
    variant.timeline_object_uid = "tlobj_other"
    variant.origin = "system"
    variant.provenance = {"anything": 1}
    variant.semantic_label = "renamed"
    variant.fingerprint = "garbage"
    assert object_fingerprint(variant) == base


def test_fingerprint_changes_on_semantics():
    obj = _obj()
    base = object_fingerprint(obj)
    for mutate in (
        lambda o: setattr(o.transform, "scale", 0.4),
        lambda o: setattr(o, "asset_uid", "ast_other"),
        lambda o: o.parameters.update({"extra": 1}),
        lambda o: setattr(o, "mask_ref", "art_m1"),
    ):
        variant = copy.deepcopy(obj)
        mutate(variant)
        assert object_fingerprint(variant) != base


def test_patch_operations_forward_ref():
    op = EditOperation(
        operation_uid="op_0001",
        op_type=EditOpType.create_object.value,
        target_uid="tlobj_x",
        arguments={"k": 1},
        idempotency_key="create_object:tlobj_x:v1",
    )
    patch = model_validate(
        ExecutionPatch,
        {
            "patch_uid": "patch_1",
            "project_id": "p",
            "base_revision": 0,
            "target_revision": 1,
            "operations": [model_dump(op)],
        },
    )
    assert isinstance(patch.operations[0], EditOperation)
    dumped = model_validate(ExecutionPatch, model_dump(patch))
    assert dumped.operations[0].target_uid == "tlobj_x"


def test_execution_result_defaults():
    result = ExecutionResult()
    assert result.status == ExecutionStatus.completed.value
    assert result.export_status == "not_requested"
    assert result.dependencies == []
