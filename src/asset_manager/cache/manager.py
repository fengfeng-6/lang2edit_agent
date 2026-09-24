"""缓存管理（§69）：Search / Preview / Asset 三类分开。

- Search Cache：``sha256(provider_id + canonical query)`` → 候选 metadata JSON；
- Preview Cache：在线候选缩略图按 uri 哈希落盘；
- Asset Cache：选中素材原文件（``downloaded/`` 的实际落点由 Importer 负责，
  这里只管路径分配）。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from typing import Any, Dict, List, Optional


def search_cache_key(provider_id: str, query: Dict[str, Any]) -> str:
    blob = json.dumps({"p": provider_id, "q": query}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class CacheManager:
    def __init__(self, cache_dir: str):
        self.root = cache_dir
        self.search_dir = os.path.join(cache_dir, "search")
        self.preview_dir = os.path.join(cache_dir, "previews")
        self.asset_dir = os.path.join(cache_dir, "assets")

    def ensure(self) -> None:
        for d in (self.search_dir, self.preview_dir, self.asset_dir):
            os.makedirs(d, exist_ok=True)

    # -- search ----------------------------------------------------------

    def search_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        path = os.path.join(self.search_dir, f"{key}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def search_put(self, key: str, candidates: List[Dict[str, Any]]) -> None:
        self.ensure()
        path = os.path.join(self.search_dir, f"{key}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(candidates, fh, ensure_ascii=False)

    # -- preview ----------------------------------------------------------

    def preview_path(self, uri: str) -> str:
        name = os.path.basename(urllib.parse.urlparse(uri).path) or "preview"
        digest = hashlib.sha1(uri.encode()).hexdigest()[:10]
        return os.path.join(self.preview_dir, f"{digest}_{name}")

    def preview_exists(self, uri: str) -> bool:
        return os.path.isfile(self.preview_path(uri))

    # -- asset ------------------------------------------------------------

    def asset_path(self, asset_uid: str, ext: str = "") -> str:
        return os.path.join(self.asset_dir, f"{asset_uid}{ext}")
