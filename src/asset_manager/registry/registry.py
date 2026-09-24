"""Asset Registry（§52-58）：Candidate→Record 的唯一入口。

- ``asset_uid`` = ``ast_<sha256[:8]>``，内容哈希稳定（§55）；
- 同内容多来源共享 record，追加 ``origins[]``（§57-58）；
- Project Asset 入库后在项目生命周期内不自动回收（§70）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..models import (
    AssetCandidate,
    AssetOrigin,
    AssetRecord,
    Integrity,
    RegistryScope,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def asset_uid_for(content_hash: str, taken: Dict[str, AssetRecord]) -> str:
    """sha256 前 8 位定 uid；碰撞追加序号保唯一。"""

    base = f"ast_{content_hash[:8]}"
    uid = base
    seq = 2
    while uid in taken and taken[uid].integrity.content_hash != content_hash:
        uid = f"{base}_{seq}"
        seq += 1
    return uid


class AssetRegistry:
    """内存 Registry；持久化由 store.py 负责。"""

    def __init__(self, records: Optional[Dict[str, AssetRecord]] = None):
        self.records: Dict[str, AssetRecord] = dict(records or {})
        self._by_hash: Dict[str, str] = {
            r.integrity.content_hash: uid
            for uid, r in self.records.items() if r.integrity.content_hash
        }

    def get(self, asset_uid: str) -> Optional[AssetRecord]:
        return self.records.get(asset_uid)

    def find_by_hash(self, content_hash: str) -> Optional[AssetRecord]:
        uid = self._by_hash.get(content_hash)
        return self.records.get(uid) if uid else None

    def all(self) -> List[AssetRecord]:
        return list(self.records.values())

    def user_records(self) -> List[AssetRecord]:
        from ..models import AssetSource

        return [r for r in self.records.values() if r.source_type == AssetSource.user]

    def register_candidate(
        self,
        candidate: AssetCandidate,
        local_uri: str,
        integrity: Integrity,
        scope: RegistryScope = RegistryScope.project,
    ) -> AssetRecord:
        """Candidate → AssetRecord；同内容合并 origins（§52-57）。"""

        existing = self.find_by_hash(integrity.content_hash)
        origin = AssetOrigin(
            provider_id=candidate.provider_id,
            source_type=candidate.source_type,
            source_ref=candidate.source_ref,
            license=candidate.license_metadata,
            retrieved_at=_now(),
        )
        if existing is not None:
            known = {(o.provider_id, o.source_ref) for o in existing.origins}
            if (origin.provider_id, origin.source_ref) not in known:
                existing.origins.append(origin)
            existing.lifecycle["last_used_at"] = _now()
            return existing

        record = AssetRecord(
            asset_uid=asset_uid_for(integrity.content_hash, self.records),
            source_type=candidate.source_type,
            provider_id=candidate.provider_id,
            asset_type=candidate.asset_type,
            media_type=candidate.media_type,
            local_uri=local_uri,
            original_uri=candidate.original_uri,
            semantic_metadata=candidate.semantic_metadata,
            technical_metadata=candidate.technical_metadata,
            origins=[origin],
            integrity=integrity,
            registry_scope=scope,
            style_family=candidate.semantic_metadata.style_family,
            lifecycle={"created_at": _now(), "last_used_at": _now(), "state": "active"},
        )
        self.records[record.asset_uid] = record
        if record.integrity.content_hash:
            self._by_hash[record.integrity.content_hash] = record.asset_uid
        return record
