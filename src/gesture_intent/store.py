"""File-backed intent state and append-only history."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import EditingIntent, IntentParserOutput, model_dump, model_validate


class IntentStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.state_path = self.directory / "state.json"
        self.history_path = self.directory / "history.jsonl"

    def save_state(
        self,
        current_intent: EditingIntent,
        *,
        utterances: Optional[list[str]] = None,
        intent_history: Optional[list[dict[str, Any]]] = None,
        patch_history: Optional[list[dict[str, Any]]] = None,
        resolved_references: Optional[list[dict[str, Any]]] = None,
        version: int = 1,
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version,
            "current_intent": model_dump(current_intent),
            "utterances": utterances or [],
            "intent_history": intent_history or [],
            "patch_history": patch_history or [],
            "resolved_references": resolved_references or [],
            "requirement_ids": _requirement_ids(current_intent),
            "constraint_ids": [item.id for item in current_intent.constraints],
        }
        _atomic_write(self.state_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise FileNotFoundError(self.state_path)
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def load_current_intent(self) -> EditingIntent:
        return model_validate(EditingIntent, self.load_state()["current_intent"])

    def append_history(self, utterance: str, output: IntentParserOutput) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "utterance": utterance,
            "output": model_dump(output),
        }
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn trailing line after a crash
        return records


def _requirement_ids(intent: EditingIntent) -> list[str]:
    return [item.id for item in intent.object_requirements + intent.event_bound_requirements + intent.explicit_operations]


def _atomic_write(path: Path, content: str) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)
