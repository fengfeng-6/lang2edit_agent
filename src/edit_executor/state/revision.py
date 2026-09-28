"""Revision-Level Atomicity（§66-§69）：Candidate Revision → verify → commit。

Promote 顺序：
    rev_<n>/manifest.json{state:candidate} + graph.json + state.json
        → core/backend verify
        → manifest 原子重写为 committed
        → current_graph.json 重写
        → current.json 原子翻转（永不指向未 committed 的 manifest）
        → state.json 重写
任何一步失败：candidate 丢弃，current 不变（§67）。
"""

from __future__ import annotations

import time
from typing import Optional

from gesture_intent.models import model_dump

from ..models import (
    BackendProjectRef,
    DesiredProjectGraph,
    ProjectExecutionState,
    RevisionManifest,
)
from .store import ExecutionStore


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_candidate(
    store: ExecutionStore,
    revision: int,
    execution_uid: str,
    patch_uid: str,
    graph: DesiredProjectGraph,
    state: ProjectExecutionState,
) -> RevisionManifest:
    """写入 candidate revision 工件（尚未可成为 current）。"""
    rev_dir = store.revision_dir(revision)
    rev_dir.mkdir(parents=True, exist_ok=True)
    manifest = RevisionManifest(
        revision=revision,
        execution_uid=execution_uid,
        state="candidate",
        graph_fingerprint=state.graph_fingerprint,
        backend_project_ref=state.project_ref,
        source_plan_uid=state.source_plan_uid,
        patch_uid=patch_uid,
        created_at=_now(),
    )
    store.write_json(rev_dir / "graph.json", model_dump(graph))
    store.write_json(rev_dir / "state.json", model_dump(state))
    store.write_json(rev_dir / "manifest.json", model_dump(manifest))
    return manifest


def commit_candidate(
    store: ExecutionStore,
    manifest: RevisionManifest,
    graph: DesiredProjectGraph,
    state: ProjectExecutionState,
) -> None:
    """把已验证的 candidate 翻转为 current（§68-§69）。"""
    rev_dir = store.revision_dir(manifest.revision)
    manifest.state = "committed"
    store.write_json(rev_dir / "manifest.json", model_dump(manifest))
    if manifest.backend_project_ref is not None:
        store.write_json(
            rev_dir / "backend_ref.json",
            model_dump(manifest.backend_project_ref),
        )
    store.write_json(store.current_graph_path, model_dump(graph))
    store.write_json(
        store.current_path,
        {
            "revision": manifest.revision,
            "manifest": f"revisions/rev_{manifest.revision:04d}/manifest.json",
        },
    )
    store.write_json(store.state_path, model_dump(state))


def load_committed_graph(
    store: ExecutionStore,
) -> Optional[DesiredProjectGraph]:
    from gesture_intent.models import model_validate

    payload = store.read_json(store.current_graph_path)
    if payload is None:
        return None
    return model_validate(DesiredProjectGraph, payload)


def load_committed_state(
    store: ExecutionStore,
) -> Optional[ProjectExecutionState]:
    from gesture_intent.models import model_validate

    payload = store.read_json(store.state_path)
    if payload is None:
        return None
    return model_validate(ProjectExecutionState, payload)


def current_revision(store: ExecutionStore) -> int:
    pointer = store.read_json(store.current_path) or {}
    return int(pointer.get("revision") or 0)
