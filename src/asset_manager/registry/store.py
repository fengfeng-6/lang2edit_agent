"""项目素材状态持久化（§60-61）。

布局::

    workspace/<project_id>/
    ├── asset_registry.json     # records + bindings + usage_index
    ├── search_history.jsonl    # 每次真实搜索追加一条
    └── assets/{imported,downloaded,cache}/

JSON 读写走模块一兼容层 ``model_dump`` / ``model_validate``。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from gesture_intent.models import model_dump, model_validate

from ..models import (
    AssetRecord,
    BindingRecord,
    ProjectAssetState,
    SearchRecord,
)


class ProjectStore:
    """单个项目的 Registry / Binding / UsageIndex / SearchHistory 读写。"""

    def __init__(self, workspace_root: str, project_id: str):
        self.project_id = project_id
        self.root = os.path.join(workspace_root, project_id)
        self.assets_dir = os.path.join(self.root, "assets")
        self.imported_dir = os.path.join(self.assets_dir, "imported")
        self.downloaded_dir = os.path.join(self.assets_dir, "downloaded")
        self.cache_dir = os.path.join(self.assets_dir, "cache")

    @property
    def registry_path(self) -> str:
        return os.path.join(self.root, "asset_registry.json")

    @property
    def history_path(self) -> str:
        return os.path.join(self.root, "search_history.jsonl")

    def ensure_dirs(self) -> None:
        for d in (self.imported_dir, self.downloaded_dir, self.cache_dir):
            os.makedirs(d, exist_ok=True)

    # -- state -----------------------------------------------------------

    def load_state(self) -> ProjectAssetState:
        if not os.path.isfile(self.registry_path):
            return ProjectAssetState(project_id=self.project_id)
        with open(self.registry_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return ProjectAssetState(
            project_id=self.project_id,
            registry={
                uid: model_validate(AssetRecord, rec)
                for uid, rec in (raw.get("registry") or {}).items()
            },
            bindings={
                req: [model_validate(BindingRecord, b) for b in items]
                for req, items in (raw.get("bindings") or {}).items()
            },
            usage_index={
                uid: list(items)
                for uid, items in (raw.get("usage_index") or {}).items()
            },
        )

    def save_state(self, state: ProjectAssetState) -> None:
        self.ensure_dirs()
        payload = {
            "project_id": self.project_id,
            "registry": {uid: model_dump(r) for uid, r in state.registry.items()},
            "bindings": {
                req: [model_dump(b) for b in items]
                for req, items in state.bindings.items()
            },
            "usage_index": state.usage_index,
        }
        tmp = self.registry_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.registry_path)

    def append_search(self, record: SearchRecord) -> None:
        self.ensure_dirs()
        with open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(model_dump(record), ensure_ascii=False) + "\n")

    def search_history(self) -> List[Dict]:
        if not os.path.isfile(self.history_path):
            return []
        with open(self.history_path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
