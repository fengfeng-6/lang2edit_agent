"""ExecutionStateManager：执行号分配、history/journal、索引、EditView（§57-§83）。"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

from gesture_intent.models import model_dump

from ..models import (
    DesiredProjectGraph,
    EditableObjectView,
    ExecutionPatch,
    HistoryEntry,
    JournalEntry,
    ProjectEditView,
    ProjectExecutionState,
)
from .revision import (
    current_revision,
    load_committed_graph,
    load_committed_state,
)
from .recovery import RecoveryReport, recover
from .store import ExecutionStore

_EXE_RE = re.compile(r"^exe_(\d+)$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class ExecutionStateManager:
    def __init__(self, store: ExecutionStore) -> None:
        self.store = store

    # ---- 执行号（§59）：扫 runs/ 目录，崩溃安全 ----

    def next_execution_uid(self) -> str:
        highest = 0
        if self.store.runs_dir.exists():
            for path in self.store.runs_dir.iterdir():
                match = _EXE_RE.match(path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return f"exe_{highest + 1:06d}"

    def recover(self) -> RecoveryReport:
        return recover(self.store)

    # ---- current ----

    def current(self) -> Optional[ProjectExecutionState]:
        return load_committed_state(self.store)

    def current_graph(self) -> Optional[DesiredProjectGraph]:
        return load_committed_graph(self.store)

    def current_revision(self) -> int:
        return current_revision(self.store)

    def lock(self, execution_uid: str, force: bool = False) -> bool:
        return self.store.acquire_lock(execution_uid, force=force)

    def unlock(self, execution_uid: str) -> None:
        self.store.release_lock(execution_uid)

    # ---- history / journal ----

    def append_history(self, entry: HistoryEntry) -> None:
        self.store.append_jsonl(
            self.store.history_path, model_dump(entry)
        )

    def append_journal(self, entry: JournalEntry) -> None:
        self.store.append_jsonl(
            self.store.journal_path, model_dump(entry)
        )

    def history(self) -> List[HistoryEntry]:
        return [
            _model(HistoryEntry, r)
            for r in self.store.read_jsonl(self.store.history_path)
        ]

    def journal(self, execution_uid: Optional[str] = None) -> List[JournalEntry]:
        rows = [
            _model(JournalEntry, r)
            for r in self.store.read_jsonl(self.store.journal_path)
        ]
        if execution_uid is not None:
            rows = [r for r in rows if r.execution_uid == execution_uid]
        return rows

    # ---- state 构建（§57 + §79-§81 索引） ----

    def build_state(
        self,
        graph: DesiredProjectGraph,
        patch: Optional[ExecutionPatch],
        revision: int,
        backend_id: str,
        backend_version: str,
        app_version: str,
        project_ref,
        graph_fingerprint: str,
    ) -> ProjectExecutionState:
        plan_item_index: Dict[str, List[str]] = {}
        requirement_index: Dict[str, List[str]] = {}
        asset_usage_index: Dict[str, List[str]] = {}
        for uid, obj in graph.objects.items():
            if obj.source_plan_item_uid:
                plan_item_index.setdefault(obj.source_plan_item_uid, []).append(uid)
            for req_id in obj.source_requirement_ids:
                requirement_index.setdefault(req_id, []).append(uid)
            if obj.asset_uid:
                asset_usage_index.setdefault(obj.asset_uid, []).append(uid)
        for index in (plan_item_index, requirement_index, asset_usage_index):
            for key in index:
                index[key] = sorted(set(index[key]))
        return ProjectExecutionState(
            schema_version=1,
            project_id=graph.project_id,
            revision=revision,
            backend_id=backend_id,
            backend_version=backend_version,
            app_version=app_version,
            project_ref=project_ref,
            source_plan_uid=graph.source_plan_uid,
            source_plan_version=graph.source_plan_version,
            video_id="",
            video_version="",
            graph_fingerprint=graph_fingerprint,
            tracks={uid: model_dump(t) for uid, t in graph.tracks.items()},
            timeline_objects={
                uid: model_dump(o) for uid, o in graph.objects.items()
            },
            media_refs={uid: model_dump(m) for uid, m in graph.media_refs.items()},
            execution_resources={},
            plan_item_index=plan_item_index,
            requirement_index=requirement_index,
            asset_usage_index=asset_usage_index,
            total_duration=graph.total_duration,
            last_patch_uid=patch.patch_uid if patch else None,
            status="active",
        )

    # ---- ProjectEditView（§75-§76） ----

    def build_edit_view(
        self,
        graph: Optional[DesiredProjectGraph] = None,
        revision: Optional[int] = None,
    ) -> ProjectEditView:
        if graph is None:
            graph = self.current_graph()
        if revision is None:
            revision = self.current_revision()
        objects: List[EditableObjectView] = []
        if graph is not None:
            for uid in sorted(graph.objects):
                objects.append(_editable_view(graph.objects[uid]))
        return ProjectEditView(
            project_id=self.store.project_id,
            revision=revision,
            objects=objects,
        )


_EDITABLE_BY_TYPE = {
    "overlay": ["scale", "position", "asset", "animation"],
    "effect": ["scale", "position", "animation"],
    "text": ["content", "position", "scale", "animation"],
    "audio": ["asset", "volume", "time_range"],
    "foreground_subject": ["mask"],
    "freeze": ["duration"],
    "source_slice": ["time_range"],
    "background": ["asset", "time_range"],
}


def _editable_view(obj) -> EditableObjectView:
    occurrence = int(obj.provenance.get("occurrence_index") or 0)
    label = obj.semantic_label or obj.role
    display_id = (
        f"{obj.role}_{label}_{occurrence:02d}"
        if occurrence
        else f"{obj.role}_{label}"
    )
    current: Dict[str, object] = {}
    if obj.transform is not None:
        current["scale"] = obj.transform.scale
        current["position"] = list(obj.transform.position)
        current["rotation"] = obj.transform.rotation
    if obj.asset_uid:
        current["asset_uid"] = obj.asset_uid
    for key in ("content", "volume_db", "source_time", "freeze_duration"):
        if key in obj.parameters:
            current[key] = obj.parameters[key]
    if obj.animation is not None:
        current["animation"] = obj.animation.semantic_type
    editable = (
        [] if obj.origin == "system" else _EDITABLE_BY_TYPE.get(obj.object_type, [])
    )
    return EditableObjectView(
        object_uid=obj.timeline_object_uid,
        display_id=display_id,
        source_requirement_ids=list(obj.source_requirement_ids),
        source_plan_item_uid=obj.source_plan_item_uid,
        object_type=obj.object_type,
        role=obj.role,
        semantic_label=obj.semantic_label,
        asset_uid=obj.asset_uid,
        project_time=obj.project_time,
        editable_properties=editable,
        current_properties=current,
    )


def _model(cls, payload):
    from gesture_intent.models import model_validate

    return model_validate(cls, payload)
