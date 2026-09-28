"""DSL Operation DAG 调度（§38, §64）。

拓扑排序执行；ready 集按 operation_uid 排序保证确定性。
依赖失败时 dependent → skipped，而不是全部报 failed（§64）。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set

from .models import EditOperation, OperationStatus


def topo_order(operations: Iterable[EditOperation]) -> List[EditOperation]:
    """Kahn 拓扑排序；同一 ready 集内按 operation_uid 字典序，保证确定性。"""
    ops = list(operations)
    by_uid: Dict[str, EditOperation] = {op.operation_uid: op for op in ops}
    remaining_deps: Dict[str, Set[str]] = {
        op.operation_uid: {d for d in op.depends_on if d in by_uid}
        for op in ops
    }
    ordered: List[EditOperation] = []
    emitted: Set[str] = set()
    ready = sorted(uid for uid, deps in remaining_deps.items() if not deps)
    while ready:
        uid = ready.pop(0)
        if uid in emitted:
            continue
        emitted.add(uid)
        ordered.append(by_uid[uid])
        for other_uid, deps in remaining_deps.items():
            if uid in deps:
                deps.discard(uid)
                if not deps and other_uid not in emitted and other_uid not in ready:
                    ready.append(other_uid)
        ready.sort()
    # 环或未解析依赖：残余按 uid 序追加（确定性兜底）
    for uid in sorted(set(by_uid) - emitted):
        ordered.append(by_uid[uid])
    return ordered


def mark_dependents_skipped(
    operations: Iterable[EditOperation],
    failed_uids: Iterable[str],
) -> List[str]:
    """把（传递）依赖失败操作的所有 pending 下游标为 skipped。"""
    ops = {op.operation_uid: op for op in operations}
    blocked: Set[str] = set(failed_uids)
    blocked.update(
        uid
        for uid, op in ops.items()
        if op.status in (OperationStatus.failed.value, OperationStatus.skipped.value)
    )
    skipped: List[str] = []
    changed = True
    while changed:
        changed = False
        for uid, op in ops.items():
            if uid in blocked:
                continue
            if op.status in (
                OperationStatus.completed.value,
                OperationStatus.failed.value,
                OperationStatus.skipped.value,
            ):
                continue
            if any(d in blocked for d in op.depends_on):
                op.status = OperationStatus.skipped.value
                skipped.append(uid)
                blocked.add(uid)
                changed = True
    return skipped


def dependents_of(
    operations: Iterable[EditOperation], root_uids: Iterable[str]
) -> List[str]:
    """返回 root_uids 的全部传递下游（不含 root 本身）。"""
    ops = list(operations)
    blocked: Set[str] = set(root_uids)
    downstream: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for op in ops:
            if op.operation_uid in blocked:
                continue
            if any(d in blocked for d in op.depends_on):
                downstream.add(op.operation_uid)
                blocked.add(op.operation_uid)
                changed = True
    return sorted(downstream)
