"""GraphDiff → Editing DSL Operation DAG → ExecutionPatch（§35-§39）。

发射顺序：
    create_project → import_media → ensure_track
    → 每个对象的 create / recreate(delete+create) / update(set_*) / delete
    → save_project（依赖全部操作）
    → export_video（可选）
idempotency_key = "<op_type>:<target>:v<target_revision>"（§39）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from gesture_intent.models import model_dump

from ..diff.engine import GraphDiff
from ..diff.properties import RECREATE
from ..models import (
    DesiredProjectGraph,
    DiffAction,
    ExecutionPatch,
    ExecutorInput,
    PatchValidation,
    _stable_uid,
)
from .models import EditOperation, EditOpType, OperationStatus

_UPDATE_OP_ORDER = (
    EditOpType.replace_media.value,
    EditOpType.set_time_range.value,
    EditOpType.set_transform.value,
    EditOpType.set_animation.value,
    EditOpType.set_keyframes.value,
    EditOpType.set_mask.value,
    EditOpType.set_text.value,
    EditOpType.set_volume.value,
    EditOpType.freeze_frame.value,
)


def build_patch(
    graph_diff: GraphDiff,
    desired: DesiredProjectGraph,
    executor_input: ExecutorInput,
    base_revision: int,
) -> ExecutionPatch:
    target_revision = base_revision + 1
    ops: List[EditOperation] = []
    seq = {"n": 0}

    def emit(
        op_type: str,
        target: Optional[str],
        arguments: dict,
        depends_on: Optional[List[str]] = None,
    ) -> EditOperation:
        seq["n"] += 1
        op = EditOperation(
            operation_uid=f"op_{seq['n']:04d}",
            op_type=op_type,
            target_uid=target,
            arguments=dict(arguments),
            depends_on=list(depends_on or []),
            idempotency_key=f"{op_type}:{target or '-'}:v{target_revision}",
            status=OperationStatus.pending.value,
        )
        ops.append(op)
        return op

    if graph_diff.first_run:
        emit(
            EditOpType.create_project.value,
            executor_input.project_id,
            {"project_spec": model_dump(desired.project_spec)},
        )

    # ---- 轨道与媒体（只覆盖受影响对象涉及的） ----
    changed_uids = {
        d.uid
        for d in graph_diff.diffs
        if d.action != DiffAction.noop.value
    }
    track_uids = sorted(
        {
            desired.objects[u].track_uid
            for u in changed_uids
            if u in desired.objects
        }
    )
    ensure_ops: Dict[str, str] = {}
    for trk in track_uids:
        spec = desired.tracks.get(trk)
        ensure_ops[trk] = emit(
            EditOpType.ensure_track.value,
            trk,
            model_dump(spec) if spec is not None else {},
        ).operation_uid

    media_uids = sorted(
        {
            desired.objects[u].media_ref
            for u in changed_uids
            if u in desired.objects and desired.objects[u].media_ref
        }
    )
    import_ops: Dict[str, str] = {}
    for mref in media_uids:
        ref = desired.media_refs.get(mref)
        import_ops[mref] = emit(
            EditOpType.import_media.value,
            mref,
            model_dump(ref) if ref is not None else {},
        ).operation_uid

    def obj_deps(obj_uid: str) -> List[str]:
        obj = desired.objects.get(obj_uid)
        deps = []
        if obj is not None:
            if obj.track_uid in ensure_ops:
                deps.append(ensure_ops[obj.track_uid])
            if obj.media_ref in import_ops:
                deps.append(import_ops[obj.media_ref])
        return deps

    last_op_of: Dict[str, str] = {}
    all_op_uids: List[str] = []

    for d in graph_diff.diffs:
        uid = d.uid
        if d.action == DiffAction.noop.value:
            continue
        if d.action == DiffAction.create.value:
            obj = desired.objects[uid]
            created = emit(
                EditOpType.create_object.value,
                uid,
                model_dump(obj),
                depends_on=obj_deps(uid),
            )
            last_op_of[uid] = created.operation_uid
            all_op_uids.append(created.operation_uid)
            continue
        if d.action == DiffAction.delete.value:
            deleted = emit(EditOpType.delete_object.value, uid, {})
            last_op_of[uid] = deleted.operation_uid
            all_op_uids.append(deleted.operation_uid)
            continue
        if d.action == DiffAction.update.value:
            recreate = any(c.op_type == RECREATE for c in d.changed_properties)
            if recreate:
                deleted = emit(EditOpType.delete_object.value, uid, {})
                obj = desired.objects[uid]
                created = emit(
                    EditOpType.create_object.value,
                    uid,
                    model_dump(obj),
                    depends_on=obj_deps(uid) + [deleted.operation_uid],
                )
                last_op_of[uid] = created.operation_uid
                all_op_uids += [deleted.operation_uid, created.operation_uid]
                continue
            changes = sorted(
                d.changed_properties,
                key=lambda c: _UPDATE_OP_ORDER.index(c.op_type)
                if c.op_type in _UPDATE_OP_ORDER
                else len(_UPDATE_OP_ORDER),
            )
            previous: List[str] = list(obj_deps(uid))
            for change in changes:
                op = emit(
                    change.op_type,
                    uid,
                    change.arguments,
                    depends_on=previous,
                )
                previous = [op.operation_uid]
                all_op_uids.append(op.operation_uid)
            if previous:
                last_op_of[uid] = previous[-1]

    if ops:
        emit(
            EditOpType.save_project.value,
            executor_input.project_id,
            {},
            depends_on=sorted(set(all_op_uids)),
        )
    if executor_input.export_spec is not None:
        emit(
            EditOpType.export_video.value,
            executor_input.project_id,
            model_dump(executor_input.export_spec),
            depends_on=[ops[-1].operation_uid] if ops else [],
        )

    patch = ExecutionPatch(
        patch_uid=_stable_uid(
            "patch",
            executor_input.project_id,
            base_revision,
            desired.source_plan_uid,
            [op.idempotency_key for op in ops],
        ),
        project_id=executor_input.project_id,
        base_revision=base_revision,
        target_revision=target_revision,
        source_plan_uid=desired.source_plan_uid,
        operations=ops,
        affected_plan_item_uids=sorted(
            {
                desired.objects[u].source_plan_item_uid
                for u in changed_uids
                if u in desired.objects
                and desired.objects[u].source_plan_item_uid
            }
        ),
        affected_timeline_object_uids=sorted(changed_uids),
        summary=graph_diff.summary,
        validation=_validate(ops, desired, changed_uids),
    )
    return patch


def _validate(
    ops: List[EditOperation],
    desired: DesiredProjectGraph,
    changed_uids: set,
) -> PatchValidation:
    issues: List[str] = []
    known = set(desired.objects) | {executor for executor in changed_uids}
    for op in ops:
        if op.op_type in (
            EditOpType.create_object.value,
            EditOpType.delete_object.value,
            EditOpType.replace_media.value,
            EditOpType.set_time_range.value,
            EditOpType.set_transform.value,
            EditOpType.set_animation.value,
            EditOpType.set_keyframes.value,
            EditOpType.set_mask.value,
            EditOpType.set_text.value,
            EditOpType.set_volume.value,
            EditOpType.freeze_frame.value,
        ) and op.target_uid not in known:
            issues.append(f"{op.operation_uid}: 目标 {op.target_uid} 不在受影响集合")
    return PatchValidation(valid=not issues, issues=issues)
