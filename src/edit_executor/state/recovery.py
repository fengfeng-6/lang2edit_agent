"""Crash Recovery（§70-§71）。

启动检查：current.json → manifest 链 → revisions 目录。
未完成 candidate / rev_tmp_* → quarantine 记录（不删除，留给人工）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..models import RevisionManifest
from .store import ExecutionStore
from .revision import current_revision
from gesture_intent.models import model_validate


@dataclass
class RecoveryReport:
    ok: bool
    adopted_revision: int = 0
    quarantined: List[str] = field(default_factory=list)
    needs_reconciliation: bool = False
    notes: List[str] = field(default_factory=list)


def recover(store: ExecutionStore) -> RecoveryReport:
    report = RecoveryReport(ok=True)
    if not store.root.exists():
        return report
    pointer = store.read_json(store.current_path)
    if pointer is None:
        # 无指针：找最高 committed manifest 重建
        adopted = _highest_committed(store)
        if adopted is not None:
            store.write_json(
                store.current_path,
                {
                    "revision": adopted.revision,
                    "manifest": (
                        f"revisions/rev_{adopted.revision:04d}/manifest.json"
                    ),
                },
            )
            report.adopted_revision = adopted.revision
            report.notes.append(
                f"current.json 缺失/损坏，恢复至 rev_{adopted.revision:04d}"
            )
    else:
        manifest = _load_manifest(store, int(pointer.get("revision") or 0))
        if manifest is None or manifest.state != "committed":
            adopted = _highest_committed(store)
            if adopted is not None:
                store.write_json(
                    store.current_path,
                    {
                        "revision": adopted.revision,
                        "manifest": (
                            f"revisions/rev_{adopted.revision:04d}/manifest.json"
                        ),
                    },
                )
                report.adopted_revision = adopted.revision
                report.notes.append(
                    "current 指向的 manifest 非 committed，恢复至 "
                    f"rev_{adopted.revision:04d}"
                )
        else:
            report.adopted_revision = int(pointer.get("revision") or 0)

    for path in sorted(store.revisions_dir.glob("rev_*/manifest.json")):
        manifest = _load_manifest_path(path)
        if manifest is None:
            report.quarantined.append(str(path.parent))
            report.notes.append(f"{path.parent.name}: manifest 损坏，隔离")
        elif manifest.state != "committed":
            report.quarantined.append(str(path.parent))
            report.notes.append(
                f"{path.parent.name}: 未完成 candidate，隔离"
            )
    for path in sorted(store.revisions_dir.glob("rev_tmp_*")):
        report.quarantined.append(str(path))
        report.notes.append(f"{path.name}: 临时 revision 残留，隔离")
    return report


def _load_manifest(store: ExecutionStore, revision: int) -> Optional[RevisionManifest]:
    path = store.revision_dir(revision) / "manifest.json"
    return _load_manifest_path(path)


def _load_manifest_path(path) -> Optional[RevisionManifest]:
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        return model_validate(RevisionManifest, payload)
    except Exception:
        return None


def _highest_committed(store: ExecutionStore) -> Optional[RevisionManifest]:
    best: Optional[RevisionManifest] = None
    for path in sorted(store.revisions_dir.glob("rev_*/manifest.json")):
        manifest = _load_manifest_path(path)
        if manifest is not None and manifest.state == "committed":
            if best is None or manifest.revision > best.revision:
                best = manifest
    return best
