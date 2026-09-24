"""Provider 基类（§17-18）：统一 search 接口 + 能力声明 + 状态区分。"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ..models import (
    AssetCandidate,
    ProviderCapability,
    ProviderQuery,
    ProviderStatus,
)


class SearchContext(BaseModel):
    """一次检索的上下文。"""

    class Config:
        extra = "forbid"

    project_id: str = "default"
    request_uid: str = ""
    asset_type: str = ""
    media_type: str = ""
    limit: int = 10


class ProviderResult(BaseModel):
    """search() 返回：状态与候选分离，异常归 ProviderStatus.failed（§66）。"""

    class Config:
        extra = "forbid"
        arbitrary_types_allowed = True

    status: ProviderStatus
    candidates: List[AssetCandidate] = Field(default_factory=list)
    message: str = ""


class AssetProvider:
    """素材 Provider 抽象（§17）。子类只需实现 capability/search。"""

    provider_id: str = "provider"
    version: str = "1"
    source_kind: str = "online"  # user / local / online——Compiler 据此整形 Query

    def capability(self) -> ProviderCapability:
        return ProviderCapability()

    def search(self, query: ProviderQuery, context: SearchContext) -> ProviderResult:
        raise NotImplementedError

    def resolve_reference(
        self, source_ref: str, context: SearchContext
    ) -> Optional[AssetCandidate]:
        """exact_reference 直查（§8.1）；不支持返回 None。"""
        return None

    def download(self, candidate: AssetCandidate, dest_dir: str) -> str:
        """取回原始文件到本地目录，返回可读路径。user/local 默认用已有文件。"""
        if candidate.local_uri:
            return candidate.local_uri
        if candidate.original_uri:
            return candidate.original_uri
        raise FileNotFoundError("candidate has no fetchable uri")

    def safe_search(
        self, query: ProviderQuery, context: SearchContext
    ) -> ProviderResult:
        """统一异常 → failed；返回 [] → no_result（§66）。"""

        try:
            result = self.search(query, context)
        except Exception as exc:  # noqa: BLE001 - Provider 故障必须收口
            return ProviderResult(
                status=ProviderStatus.failed,
                message=f"{type(exc).__name__}: {exc}",
            )
        if result.status == ProviderStatus.success and not result.candidates:
            result.status = ProviderStatus.no_result
        return result


def capability_allows(
    capability: ProviderCapability, asset_type: str, media_type: str
) -> bool:
    """Source Policy Resolver 的能力过滤（§18）。"""

    if capability.asset_types and asset_type not in capability.asset_types:
        return False
    if capability.media_types and media_type not in capability.media_types:
        return False
    return True


def term_overlap_score(terms: List[str], fields: List[str]) -> float:
    """canonical_terms 与候选语义字段的命中比，作 retrieval_score（§27 弱特征）。"""

    wanted = {t.lower() for t in terms if t}
    if not wanted:
        return 0.0
    hay = {str(f).lower() for f in fields if f}
    return len(wanted & hay) / len(wanted)

