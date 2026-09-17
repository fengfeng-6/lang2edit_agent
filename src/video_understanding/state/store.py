"""语义状态与 Dense Data 的 JSON 持久化（§48）。

项目目录布局::

    workspace/
    ├── semantic_video_state.json        # SemanticVideoState（只含引用）
    └── analysis_artifacts/
        └── <artifact_id>.json           # DenseSpatialTracks

语义状态只保存 artifact_id；轨道本体独立落盘，体量再大也不进 state。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from gesture_intent.models import model_dump, model_validate

from ..models import DenseSpatialTracks, SemanticVideoState
from .manager import SemanticStateManager


class SemanticStateStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.state_path = self.directory / "semantic_video_state.json"
        self.artifacts_dir = self.directory / "analysis_artifacts"

    def save(self, manager: SemanticStateManager) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.state_path, json.dumps(model_dump(manager.state), ensure_ascii=False, indent=2))
        if manager.tracks is not None:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.artifacts_dir / f"{manager.tracks.artifact_id or 'tracks'}.json"
            _atomic_write(artifact, json.dumps(model_dump(manager.tracks), ensure_ascii=False))

    def load(self) -> SemanticStateManager:
        if not self.state_path.exists():
            raise FileNotFoundError(self.state_path)
        state = model_validate(SemanticVideoState, json.loads(self.state_path.read_text(encoding="utf-8")))
        manager = SemanticStateManager(state.video)
        manager.state = state
        manager._analysis_seq = len(state.analysis_history)
        if state.track_artifact_ids:
            artifact = self.artifacts_dir / f"{state.track_artifact_ids[0]}.json"
            if artifact.exists():
                manager.tracks = model_validate(
                    DenseSpatialTracks, json.loads(artifact.read_text(encoding="utf-8"))
                )
        return manager


def _atomic_write(path: Path, content: str) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)
