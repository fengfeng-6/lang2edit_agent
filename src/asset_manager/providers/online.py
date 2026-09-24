"""OnlineAssetProvider（§23-24）：统一管理在线素材源 Adapter。

- 具体站点逻辑不写进核心：各源实现 ``OnlineSourceAdapter``；
- 搜索阶段只取 metadata / preview_uri / original_uri，不下载原文件（§24），
  原文件只给最终选中的 Candidate 下载；
- ``HttpJsonAdapter`` 是通用实现：endpoint 返回 JSON 数组（字段见
  ``_ROW_KEYS``），适配多数素材站点的简单检索 API；
- 音乐检索仅当 adapter 声明 ``authorized_for_music``（§46）。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Protocol

from ..models import (
    AssetCandidate,
    AssetSource,
    LicenseMetadata,
    ProviderCapability,
    ProviderQuery,
    ProviderStatus,
    RetrievalMetadata,
    SemanticMetadata,
    TechnicalMetadata,
)
from .base import AssetProvider, ProviderResult, SearchContext
from ..query.lexicon import canonicalize


class OnlineSourceAdapter(Protocol):
    """在线素材源适配协议（§23）。"""

    adapter_id: str
    asset_types: List[str]
    authorized_for_music: bool

    def search(
        self, text_query: str, negative_terms: List[str], limit: int
    ) -> List[Dict[str, Any]]:
        """返回原始行：{id,title,tags,preview_url,original_url,width,height,license,asset_type}"""

    def download(self, original_uri: str, dest_dir: str) -> str:
        """下载原文件到 dest_dir，返回本地路径。"""


class HttpJsonAdapter:
    """通用 HTTP JSON 源：GET <endpoint>?q=...&limit=... → JSON 数组。

    配置::

        HttpJsonAdapter({
            "adapter_id": "source_a",
            "endpoint": "https://example.com/search",
            "asset_types": ["sticker", "image", "background"],
            "authorized_for_music": False,
            "timeout": 10,
            "headers": {"Authorization": "..."},
        })

    每行 JSON 至少给 ``original_url``；其余字段尽力映射。
    """

    def __init__(self, config: Dict[str, Any]):
        self.adapter_id = config.get("adapter_id", "http_json")
        self.endpoint = config.get("endpoint", "")
        self.asset_types = list(config.get("asset_types", ["sticker", "image", "background"]))
        self.authorized_for_music = bool(config.get("authorized_for_music", False))
        self.timeout = float(config.get("timeout", 10))
        self.headers = dict(config.get("headers", {}))
        self.query_param = config.get("query_param", "q")

    def search(
        self, text_query: str, negative_terms: List[str], limit: int
    ) -> List[Dict[str, Any]]:
        if not self.endpoint:
            return []
        sep = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{sep}{self.query_param}={urllib.parse.quote(text_query)}&limit={limit}"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - endpoint 由部署方配置
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload.get("results", payload) if isinstance(payload, dict) else payload
        return list(rows)[:limit]

    def download(self, original_uri: str, dest_dir: str) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        name = os.path.basename(urllib.parse.urlparse(original_uri).path) or "asset"
        dest = os.path.join(dest_dir, name)
        req = urllib.request.Request(original_uri, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            data = resp.read()
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest


class OnlineAssetProvider(AssetProvider):
    """聚合若干 OnlineSourceAdapter 为一个 Provider（§23）。"""

    provider_id = "online_assets"
    source_kind = "online"

    def __init__(self, adapters: Optional[List[OnlineSourceAdapter]] = None):
        self._adapters = list(adapters or [])

    def capability(self) -> ProviderCapability:
        asset_types = sorted({t for a in self._adapters for t in a.asset_types})
        media_types = sorted({
            m for a in self._adapters for m in
            (["audio"] if "music" in a.asset_types or "sound_effect" in a.asset_types else ["image"])
        })
        return ProviderCapability(
            asset_types=asset_types,
            media_types=media_types,
            supports_semantic_search=True,
            supports_exact_lookup=False,
            supports_license_filter=True,
            supports_metadata_filter=True,
        )

    def _row_to_candidate(
        self, adapter: OnlineSourceAdapter, row: Dict[str, Any], seq: int, score: float
    ) -> AssetCandidate:
        row_id = str(row.get("id") or row.get("uid") or hashlib.sha1(
            json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:10])
        tags = [canonicalize(t) for t in row.get("tags", []) if t]
        lic = row.get("license") or {}
        if isinstance(lic, str):
            lic = {"type": lic}
        width = int(row.get("width", 0) or 0)
        height = int(row.get("height", 0) or 0)
        technical = TechnicalMetadata(
            width=width,
            height=height,
            aspect_ratio=width / height if width and height else 0.0,
            has_alpha=row.get("has_alpha"),
            duration=row.get("duration"),
            bpm=row.get("bpm"),
            format=str(row.get("format", "")).lstrip("."),
            file_size=int(row.get("file_size", 0) or 0),
            partial=True,  # 在线候选未下载，像素级字段待 Import 复核（§24/§42）
        )
        return AssetCandidate(
            candidate_uid=f"o_{adapter.adapter_id}_{row_id}_{seq}",
            provider_id=f"{self.provider_id}:{adapter.adapter_id}",
            provider_version=self.version,
            source_type=AssetSource.online,
            source_ref=str(row.get("id", row.get("original_url", ""))),
            asset_type=row.get("asset_type", ""),
            media_type=row.get("media_type", "audio" if row.get("asset_type") in ("music", "sound_effect") else "image"),
            preview_uri=row.get("preview_url", ""),
            original_uri=row.get("original_url", ""),
            semantic_metadata=SemanticMetadata(
                object=canonicalize(row.get("object", "")) if row.get("object") else "",
                attributes=tags,
                style=tags,
                caption=row.get("title", ""),
                usage_tags=list(row.get("usage_tags", [])),
            ),
            technical_metadata=technical,
            license_metadata=LicenseMetadata(
                type=lic.get("type", "unknown"),
                attribution_required=bool(lic.get("attribution_required", False)),
                url=lic.get("url", ""),
                raw=lic,
            ),
            retrieval_metadata=RetrievalMetadata(
                retrieval_score=float(row.get("score", score) or 0.0)),
            provenance={"adapter": adapter.adapter_id, "row_id": row_id},
        )

    def search(self, query: ProviderQuery, context: SearchContext) -> ProviderResult:
        if not self._adapters:
            return ProviderResult(
                status=ProviderStatus.unavailable, message="no adapters configured")
        want_music = context.asset_type in ("music", "sound_effect")
        candidates: List[AssetCandidate] = []
        failures: List[str] = []
        for adapter in self._adapters:
            if context.asset_type and context.asset_type not in adapter.asset_types:
                continue
            if want_music and not adapter.authorized_for_music:
                continue  # §46：在线音乐只走授权源
            try:
                rows = adapter.search(query.text_query, query.negative_terms, context.limit)
            except Exception as exc:  # noqa: BLE001 - 单 adapter 故障不拖垮整次检索
                failures.append(f"{adapter.adapter_id}: {type(exc).__name__}: {exc}")
                continue
            candidates += [
                self._row_to_candidate(adapter, row, i, 1.0 - i * 0.01)
                for i, row in enumerate(rows)
            ]
        if failures and not candidates:
            return ProviderResult(
                status=ProviderStatus.failed, message="; ".join(failures))
        result = ProviderResult(status=ProviderStatus.success, candidates=candidates)
        if failures:
            result.message = "; ".join(failures)
        return result

    def download(self, candidate: AssetCandidate, dest_dir: str) -> str:
        adapter_id = candidate.provenance.get("adapter", "")
        for adapter in self._adapters:
            if adapter.adapter_id == adapter_id:
                return adapter.download(candidate.original_uri, dest_dir)
        raise LookupError(f"no adapter {adapter_id!r} for candidate {candidate.candidate_uid}")


def adapters_from_env(env: Optional[Dict[str, str]] = None) -> List[HttpJsonAdapter]:
    """从环境变量装配通用源：``ASSET_ONLINE_SOURCES`` = JSON 数组配置。"""

    env = env if env is not None else os.environ
    raw = env.get("ASSET_ONLINE_SOURCES", "")
    if not raw.strip():
        return []
    try:
        configs = json.loads(raw)
    except ValueError:
        return []
    if isinstance(configs, dict):
        configs = [configs]
    return [HttpJsonAdapter(c) for c in configs if isinstance(c, dict)]
