"""ExecutionStore：execution/ 目录布局、原子写、project.lock（§60/§68/§72）。

    workspace/<project_id>/execution/
    ├── state.json          # committed ProjectExecutionState
    ├── current.json        # {revision, manifest} 原子指针
    ├── current_graph.json  # committed DesiredProjectGraph
    ├── history.jsonl
    ├── journal.jsonl
    ├── runs/exe_<NNNNNN>/  # 每次执行的 patch/result 工件
    ├── revisions/rev_<NNNN>/  # manifest.json + graph.json + state.json
    ├── resources/          # 执行资源缓存（5.4）
    ├── backend/            # Backend 私有产物（memory sim.json 等）
    └── project.lock        # 存在即占用的文件锁
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _atomic_write(path: Path, content: str) -> None:
    """tmp → os.replace（沿用模块一/二/四约定）。"""
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


class ExecutionStore:
    def __init__(self, workspace_root: str, project_id: str) -> None:
        self.project_id = project_id
        self.root = Path(workspace_root) / project_id / "execution"
        self.state_path = self.root / "state.json"
        self.current_path = self.root / "current.json"
        self.current_graph_path = self.root / "current_graph.json"
        self.history_path = self.root / "history.jsonl"
        self.journal_path = self.root / "journal.jsonl"
        self.runs_dir = self.root / "runs"
        self.revisions_dir = self.root / "revisions"
        self.resources_dir = self.root / "resources"
        self.backend_dir = self.root / "backend"
        self.lock_path = self.root / "project.lock"

    def ensure_dirs(self) -> None:
        for path in (
            self.runs_dir,
            self.revisions_dir,
            self.resources_dir,
            self.backend_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # ---- 通用读写 ----

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            path, json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )

    def append_jsonl(self, path: Path, record: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        """容忍崩溃造成的坏尾行（同 gesture_intent.store）。"""
        if not path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # ---- 专用路径 ----

    def run_dir(self, execution_uid: str) -> Path:
        return self.runs_dir / execution_uid

    def revision_dir(self, revision: int) -> Path:
        return self.revisions_dir / f"rev_{revision:04d}"

    # ---- project.lock（§72）----

    def acquire_lock(self, execution_uid: str, force: bool = False) -> bool:
        """O_CREAT|O_EXCL 原子占用，win32/POSIX 通用。"""
        self.ensure_dirs()
        if force and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                return False
        try:
            fd = os.open(
                str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "execution_uid": execution_uid,
                    "pid": os.getpid(),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                fh,
            )
        return True

    def release_lock(self, execution_uid: str = "") -> None:
        if not self.lock_path.exists():
            return
        if execution_uid:
            info = self.read_json(self.lock_path) or {}
            if info.get("execution_uid") != execution_uid:
                return
        try:
            self.lock_path.unlink()
        except OSError:
            pass
