"""Resolution Router（§8/§14）：exact_reference vs semantic_search。"""

from __future__ import annotations

from editing_planner.models import AssetRequest

from ..models import ResolutionMode


def resolution_mode(request: AssetRequest) -> ResolutionMode:
    """显式字段优先；source_ref 或 source_policy=user_provided 暗示明确素材。"""

    explicit = (request.resolution_mode or "").strip()
    if explicit == ResolutionMode.exact_reference.value:
        return ResolutionMode.exact_reference
    if explicit == ResolutionMode.semantic_search.value:
        return ResolutionMode.semantic_search
    if request.source_ref:
        return ResolutionMode.exact_reference
    if request.source_policy == "user_provided":
        return ResolutionMode.exact_reference
    return ResolutionMode.semantic_search
