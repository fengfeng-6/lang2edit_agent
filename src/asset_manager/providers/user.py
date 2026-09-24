"""UserAssetProvider（§19）：检索用户已上传素材。

用户素材经 Import Pipeline 进项目 Registry（scope=project, source=user），
provider 直接在其上检索——caption / semantic tags / filename / 序号引用都支持。
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from ..models import (
    AssetCandidate,
    AssetRecord,
    AssetSource,
    LicenseMetadata,
    ProviderCapability,
    ProviderQuery,
    RetrievalMetadata,
)
from .base import AssetProvider, ProviderResult, SearchContext, term_overlap_score
from ..models import ProviderStatus


class UserAssetProvider(AssetProvider):
    provider_id = "user_assets"
    source_kind = "user"

    def __init__(self, records: Callable[[], List[AssetRecord]]):
        """records: 返回当前项目 source_type==user 的 AssetRecord 列表。"""

        self._records = records

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            asset_types=["sticker", "image", "background", "music", "sound_effect"],
            media_types=["image", "audio"],
            supports_semantic_search=True,
            supports_exact_lookup=True,
            supports_metadata_filter=True,
        )

    def _record_to_candidate(
        self, record: AssetRecord, score: float, seq: int
    ) -> AssetCandidate:
        return AssetCandidate(
            candidate_uid=f"u_{record.asset_uid}_{seq}",
            provider_id=self.provider_id,
            provider_version=self.version,
            source_type=AssetSource.user,
            source_ref=record.local_uri or record.original_uri,
            asset_type=record.asset_type,
            media_type=record.media_type,
            preview_uri=record.local_uri,
            original_uri=record.original_uri or record.local_uri,
            local_uri=record.local_uri,
            semantic_metadata=record.semantic_metadata,
            technical_metadata=record.technical_metadata,
            license_metadata=LicenseMetadata(type="user_provided"),
            retrieval_metadata=RetrievalMetadata(retrieval_score=score),
            provenance={"asset_uid": record.asset_uid},
        )

    def search(self, query: ProviderQuery, context: SearchContext) -> ProviderResult:
        filters = query.filters or {}
        scored = []
        for seq, record in enumerate(self._records()):
            if filters.get("asset_type") and record.asset_type != filters["asset_type"]:
                continue
            if filters.get("media_type") and record.media_type != filters["media_type"]:
                continue
            if filters.get("has_alpha") and not record.technical_metadata.has_alpha:
                continue
            meta = record.semantic_metadata
            fields = [meta.object] + meta.attributes + meta.style + meta.usage_tags
            score = term_overlap_score(query.canonical_terms, fields)
            text = f"{meta.caption} {os.path.basename(record.local_uri)}".lower()
            for term in query.canonical_terms:
                if term.lower() in text:
                    score += 0.15
            if query.text_query and query.text_query.lower() in text:
                score += 0.2
            if score <= 0 and query.canonical_terms:
                continue
            scored.append((score, seq, record))
        scored.sort(key=lambda t: (-t[0], t[1]))
        candidates = [
            self._record_to_candidate(record, score, seq)
            for score, seq, record in scored[: context.limit]
        ]
        return ProviderResult(status=ProviderStatus.success, candidates=candidates)

    def resolve_reference(
        self, source_ref: str, context: SearchContext
    ) -> Optional[AssetCandidate]:
        """支持 asset_uid / 文件名 / ``index:N`` 序号引用（§8.1）。"""

        records = self._records()
        ref = (source_ref or "").strip()
        if not ref:
            return None
        for seq, record in enumerate(records):
            names = {
                record.asset_uid,
                os.path.basename(record.local_uri),
                os.path.basename(record.original_uri),
                f"index:{seq}",
                f"index:{seq + 1}",  # 用户视角从 1 起数
            }
            if ref in names or ref == f"user:{seq + 1}":
                return self._record_to_candidate(record, 1.0, seq)
        return None
