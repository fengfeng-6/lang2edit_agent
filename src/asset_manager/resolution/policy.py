"""Source Policy Resolver（§12/§18/§46）：策略 → 有序 Provider 集 + 能力过滤。

- 归一化模块三词汇（``any``/``user_provided``）到文档策略枚举；
- ``user_first``/``local_first`` 改变优先级顺序，集合仍是 user+local+online；
- 离线配置（offline=True）收紧为 local_only；
- ``generated`` 是模块三预留值——模块四不支持生成式素材（§5）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from editing_planner.models import AssetRequest

from ..models import AssetSource, ProviderReport, ProviderStatus, SourcePolicy
from ..providers.base import AssetProvider, capability_allows

_POLICY_ORDER: Dict[SourcePolicy, List[AssetSource]] = {
    SourcePolicy.user_only: [AssetSource.user],
    SourcePolicy.local_only: [AssetSource.local],
    SourcePolicy.user_first: [AssetSource.user, AssetSource.local, AssetSource.online],
    SourcePolicy.local_first: [AssetSource.local, AssetSource.user, AssetSource.online],
    SourcePolicy.online_allowed: [AssetSource.user, AssetSource.local, AssetSource.online],
}

#: 模块三/别名 → 文档策略
_POLICY_ALIASES = {
    "any": SourcePolicy.online_allowed,
    "user_provided": SourcePolicy.user_only,
    "user": SourcePolicy.user_only,
    "local": SourcePolicy.local_only,
    "online": SourcePolicy.online_allowed,
}


def normalize_policy(request: AssetRequest, offline: bool = False) -> Tuple[Optional[SourcePolicy], str]:
    """返回 (policy, warning)；policy=None 表示策略本身不被支持。"""

    raw = (request.source_policy or "any").strip()
    if raw == "generated":
        return None, "generation_not_supported"  # §5：无生成式素材
    policy = _POLICY_ALIASES.get(raw)
    if policy is None and raw in {p.value for p in SourcePolicy}:
        policy = SourcePolicy(raw)
    warning = ""
    if policy is None:
        return None, f"unknown_source_policy:{raw}"
    if offline and policy not in (SourcePolicy.local_only,):
        policy = SourcePolicy.local_only
        warning = "offline_forced_local_only"
    return policy, warning


def provider_order(policy: SourcePolicy) -> List[AssetSource]:
    return list(_POLICY_ORDER[policy])


def search_strategy(request: AssetRequest) -> str:
    """显式字段优先；缺省按类型：sticker→first_satisfactory，其余 best_available（§13）。"""

    explicit = (request.search_strategy or "").strip()
    if explicit in ("first_satisfactory", "best_available"):
        return explicit
    return "first_satisfactory" if request.asset_type in ("sticker", "image") else "best_available"


def select_providers(
    providers: Dict[AssetSource, AssetProvider],
    order: List[AssetSource],
    request: AssetRequest,
) -> Tuple[List[AssetProvider], List[ProviderReport]]:
    """按策略顺序取可用 Provider，能力不符记 unavailable 报告（§18/§66）。"""

    allowed: List[AssetProvider] = []
    reports: List[ProviderReport] = []
    for source in order:
        provider = providers.get(source)
        if provider is None:
            reports.append(ProviderReport(
                provider_id=f"{source.value}_provider",
                status=ProviderStatus.unavailable,
                message="provider not configured",
            ))
            continue
        if not capability_allows(provider.capability(), request.asset_type, request.media_type):
            reports.append(ProviderReport(
                provider_id=provider.provider_id,
                status=ProviderStatus.unavailable,
                message=f"cannot handle {request.asset_type}/{request.media_type}",
            ))
            continue
        allowed.append(provider)
    return allowed, reports
