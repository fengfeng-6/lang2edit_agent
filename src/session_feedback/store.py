"""workspace/<pid>/session/ 的读写（与 IntentStore / planner.save 同惯例）。

布局::

    workspace/<pid>/session/
    ├── session_state.json                  # SessionState
    ├── resolved_plan.json                  # 最近一次成功 apply 的 ResolvedEditingPlan
    ├── turns.jsonl                         # TurnRecord 追加日志
    ├── intent/                             # IntentStore（state.json + history.jsonl）
    ├── plan/logical_editing_plan.json      # planner.save 落点
    └── snapshots/snap_<NNNN>/              # undo 快照（全部 model_dump 深拷贝）
        ├── intent.json
        ├── plan.json
        └── resolved.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from editing_planner.models import LogicalEditingPlan, ResolvedEditingPlan
from gesture_intent.models import (
    EditingIntent,
    model_dump,
    model_validate,
)

from .models import SessionState, TurnRecord


class SessionStore:
    def __init__(self, workspace_root: Any, project_id: str):
        self.root = Path(workspace_root) / project_id / "session"
        self.state_path = self.root / "session_state.json"
        self.resolved_path = self.root / "resolved_plan.json"
        self.turns_path = self.root / "turns.jsonl"
        self.intent_dir = self.root / "intent"
        self.plan_dir = self.root / "plan"
        self.snapshots_dir = self.root / "snapshots"

    # -- session_state -----------------------------------------------------

    def load_state(self) -> Optional[SessionState]:
        if not self.state_path.exists():
            return None
        return model_validate(
            SessionState,
            json.loads(self.state_path.read_text(encoding="utf-8")),
        )

    def save_state(self, state: SessionState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.state_path,
            json.dumps(model_dump(state), ensure_ascii=False, indent=2),
        )

    # -- resolved plan -------------------------------------------------------

    def save_resolved(self, resolved: ResolvedEditingPlan) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.resolved_path,
            json.dumps(model_dump(resolved), ensure_ascii=False, indent=2),
        )

    def load_resolved(self) -> ResolvedEditingPlan:
        return model_validate(
            ResolvedEditingPlan,
            json.loads(self.resolved_path.read_text(encoding="utf-8")),
        )

    # -- snapshots ----------------------------------------------------------

    def save_snapshot(
        self,
        name: str,
        intent: EditingIntent,
        plan: LogicalEditingPlan,
        resolved: ResolvedEditingPlan,
    ) -> None:
        directory = self.snapshots_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        for filename, payload in (
            ("intent.json", model_dump(intent)),
            ("plan.json", model_dump(plan)),
            ("resolved.json", model_dump(resolved)),
        ):
            _atomic_write(
                directory / filename,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

    def load_snapshot(
        self, name: str
    ) -> Tuple[EditingIntent, LogicalEditingPlan, ResolvedEditingPlan]:
        directory = self.snapshots_dir / name
        intent = model_validate(
            EditingIntent,
            json.loads((directory / "intent.json").read_text(encoding="utf-8")),
        )
        plan = model_validate(
            LogicalEditingPlan,
            json.loads((directory / "plan.json").read_text(encoding="utf-8")),
        )
        resolved = model_validate(
            ResolvedEditingPlan,
            json.loads((directory / "resolved.json").read_text(encoding="utf-8")),
        )
        return intent, plan, resolved

    # -- turns.jsonl ----------------------------------------------------------

    def append_turn(self, record: TurnRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        row = dict(model_dump(record))
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
        with self.turns_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)
