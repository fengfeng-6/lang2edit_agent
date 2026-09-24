"""Exact Reference Resolver（§8.1）：用户指定素材 → 直查，不走 Ranking。"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..models import AssetCandidate, ProviderStatus
from ..providers.base import AssetProvider, SearchContext


class ExactResolver:
    """按序问 Provider 的 ``resolve_reference``；命中即返回候选。"""

    def resolve(
        self,
        source_ref: str,
        providers: List[AssetProvider],
        context: SearchContext,
    ) -> Tuple[Optional[AssetCandidate], Optional[str], List[str]]:
        """返回 (candidate, provider_id, misses)。全部 miss 返回 (None, None, misses)。"""

        misses: List[str] = []
        for provider in providers:
            try:
                candidate = provider.resolve_reference(source_ref, context)
            except Exception as exc:  # noqa: BLE001
                misses.append(f"{provider.provider_id}: {type(exc).__name__}")
                continue
            if candidate is not None:
                return candidate, provider.provider_id, misses
            misses.append(provider.provider_id)
        return None, None, misses
