"""模块四：素材搜索与管理（Asset Search & Management）。

消费模块三 ``AssetRequest``，在 User / Local / Online 三类来源中检索、
筛选、排序、下载、注册并绑定素材（模块四设计文档 §1/§14/§76）::

    from asset_manager import AssetManager
    mgr = AssetManager(workspace_root="workspace", project_id="p1")
    result = mgr.resolve_assets(plan.asset_requests)

对外契约：模块三简版 ``AssetBinding``（``AssetResolutionResult.bindings``），
内部富绑定 ``BindingRecord`` 持久化在项目 Registry（§63-64）。
"""

from .api import AssetManager
from .models import (
    AssetCandidate,
    AssetDependencyRequest,
    AssetOrigin,
    AssetRecord,
    AssetResolutionResult,
    AssetSource,
    BindingRecord,
    Integrity,
    LicenseMetadata,
    ProviderCapability,
    ProviderQuery,
    ProviderReport,
    ProviderStatus,
    RegistryScope,
    RequestResolution,
    RequestStatus,
    ResolutionMode,
    ResolutionStatus,
    SearchRecord,
    SemanticMetadata,
    SemanticQuery,
    TechnicalMetadata,
)
from .providers.base import AssetProvider, ProviderResult, SearchContext

__all__ = [
    "AssetManager",
    "AssetCandidate",
    "AssetDependencyRequest",
    "AssetOrigin",
    "AssetProvider",
    "AssetRecord",
    "AssetResolutionResult",
    "AssetSource",
    "BindingRecord",
    "Integrity",
    "LicenseMetadata",
    "ProviderCapability",
    "ProviderQuery",
    "ProviderReport",
    "ProviderResult",
    "ProviderStatus",
    "RegistryScope",
    "RequestResolution",
    "RequestStatus",
    "ResolutionMode",
    "ResolutionStatus",
    "SearchContext",
    "SearchRecord",
    "SemanticMetadata",
    "SemanticQuery",
    "TechnicalMetadata",
]
