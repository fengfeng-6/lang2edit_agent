"""LocalLibraryProvider（§20-21）：Manifest 是本地库唯一正式索引。

不运行时扫描目录猜内容；``manifest.json`` 每条目即一份候选描述
（asset_uid / path / asset_type / semantic_metadata / usage_tags /
license_metadata / style_family）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

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
from .base import AssetProvider, ProviderResult, SearchContext, term_overlap_score
from ..query.lexicon import canonicalize


class LocalLibraryProvider(AssetProvider):
    provider_id = "local_library"
    source_kind = "local"

    def __init__(self, library_root: str = "data/asset_library"):
        self.library_root = library_root
        self._entries: Optional[List[Dict[str, Any]]] = None

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.library_root, "manifest.json")

    def _load(self) -> List[Dict[str, Any]]:
        if self._entries is None:
            if not os.path.isfile(self.manifest_path):
                self._entries = []
            else:
                with open(self.manifest_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._entries = list(data.get("assets", data if isinstance(data, list) else []))
        return self._entries

    def available(self) -> bool:
        return os.path.isfile(self.manifest_path)

    def capability(self) -> ProviderCapability:
        asset_types = sorted({e.get("asset_type", "") for e in self._load()} - {""})
        media_types = sorted({e.get("media_type", "") for e in self._load()} - {""})
        return ProviderCapability(
            asset_types=asset_types or ["sticker", "image", "background", "music", "sound_effect"],
            media_types=media_types or ["image", "audio"],
            supports_semantic_search=True,
            supports_exact_lookup=True,
            supports_license_filter=True,
            supports_metadata_filter=True,
        )

    def _entry_to_candidate(
        self, entry: Dict[str, Any], score: float
    ) -> AssetCandidate:
        rel = entry.get("path", "")
        path = os.path.join(self.library_root, rel) if rel else ""
        sem = entry.get("semantic_metadata") or {}
        tech = entry.get("technical_metadata") or {}
        lic = entry.get("license_metadata") or {}
        semantic = SemanticMetadata(
            object=canonicalize(sem.get("object", "")) if sem.get("object") else "",
            attributes=[canonicalize(a) for a in sem.get("attributes", [])],
            style=[canonicalize(s) for s in sem.get("style", [])],
            usage_tags=list(sem.get("usage_tags", entry.get("usage_tags", []))),
            style_family=entry.get("style_family", sem.get("style_family", "")),
            caption=sem.get("caption", ""),
        )
        technical = TechnicalMetadata(
            width=int(tech.get("width", 0) or 0),
            height=int(tech.get("height", 0) or 0),
            has_alpha=tech.get("has_alpha"),
            duration=tech.get("duration"),
            bpm=tech.get("bpm"),
            format=tech.get("format", os.path.splitext(rel)[1].lstrip(".")),
            file_size=int(tech.get("file_size", 0) or 0),
        )
        if technical.width and technical.height:
            technical.aspect_ratio = technical.width / technical.height
        return AssetCandidate(
            candidate_uid=f"l_{entry.get('asset_uid', rel)}",
            provider_id=self.provider_id,
            provider_version=self.version,
            source_type=AssetSource.local,
            source_ref=rel,
            asset_type=entry.get("asset_type", ""),
            media_type=entry.get("media_type", ""),
            preview_uri=path,
            original_uri=path,
            local_uri=path,
            semantic_metadata=semantic,
            technical_metadata=technical,
            license_metadata=LicenseMetadata(
                type=lic.get("type", lic.get("status", "cleared")),
                attribution_required=bool(lic.get("attribution_required", False)),
                url=lic.get("url", ""),
                raw=lic,
            ),
            retrieval_metadata=RetrievalMetadata(retrieval_score=score),
            provenance={"manifest_uid": entry.get("asset_uid", "")},
        )

    def search(self, query: ProviderQuery, context: SearchContext) -> ProviderResult:
        if not self.available():
            return ProviderResult(
                status=ProviderStatus.unavailable,
                message=f"manifest missing: {self.manifest_path}",
            )
        filters = query.filters or {}
        scored = []
        for entry in self._load():
            if filters.get("asset_type") and entry.get("asset_type") != filters["asset_type"]:
                continue
            if filters.get("media_type") and entry.get("media_type") != filters["media_type"]:
                continue
            tech = entry.get("technical_metadata") or {}
            undersized = any(
                tech.get(key[4:], 0) and tech.get(key[4:], 0) < filters[key]
                for key in ("min_width", "min_height")
                if filters.get(key)
            )
            if filters.get("has_alpha") and not tech.get("has_alpha"):
                continue
            if undersized:
                continue
            sem = entry.get("semantic_metadata") or {}
            fields = (
                [canonicalize(sem.get("object", ""))]
                + [canonicalize(a) for a in sem.get("attributes", [])]
                + [canonicalize(s) for s in sem.get("style", [])]
                + list(sem.get("usage_tags", entry.get("usage_tags", [])))
            )
            if {t.lower() for t in query.negative_terms} & {f.lower() for f in fields}:
                continue
            score = term_overlap_score(query.canonical_terms, fields)
            scored.append((score, entry))
        scored.sort(key=lambda t: -t[0])
        limit = context.limit or 10
        candidates = [self._entry_to_candidate(e, s) for s, e in scored[:limit]]
        return ProviderResult(status=ProviderStatus.success, candidates=candidates)

    def resolve_reference(
        self, source_ref: str, context: SearchContext
    ) -> Optional[AssetCandidate]:
        for entry in self._load():
            if source_ref in (entry.get("asset_uid"), entry.get("path")):
                return self._entry_to_candidate(entry, 1.0)
        return None
