"""模块四 schema：Candidate / Record / Binding / SearchRecord / ResolutionResult。

对应模块四设计文档 §8-§13、§26、§54-§68：

- ``AssetCandidate``：搜索候选，只要求在当前 SearchRecord 内稳定（§26）；
- ``AssetRecord``：正式入库素材，稳定 ``asset_uid`` + 完整 SHA-256 + origins（§54-57）；
- ``BindingRecord``：模块四内部富绑定（§63-64），对外映射成模块三的 ``AssetBinding``；
- ``SearchRecord``：每次真实搜索留痕，用于定位 Query/召回/Filter/Ranking 问题（§65）；
- ``AssetResolutionResult``：顶层结果，区分 resolved / warnings / partial / failed（§67）。

兼容 Pydantic 1.10 与 2.x，序列化走模块一的 ``model_dump`` / ``model_validate``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from editing_planner.models import AssetBinding  # noqa: F401  # re-export 契约类型


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


# ---------------------------------------------------------------------------
# 枚举（§4/§8/§12-13/§59/§66-67）
# ---------------------------------------------------------------------------


class AssetSource(str, Enum):
    """素材来源（§4）。"""

    user = "user"
    local = "local"
    online = "online"


class ResolutionMode(str, Enum):
    """解析模式（§8）：明确素材直接解析 vs 语义检索。"""

    exact_reference = "exact_reference"
    semantic_search = "semantic_search"


class SourcePolicy(str, Enum):
    """来源策略（§12）；``any``/``user_provided`` 由 policy.py 归一化进来。"""

    user_only = "user_only"
    local_only = "local_only"
    user_first = "user_first"
    local_first = "local_first"
    online_allowed = "online_allowed"


class SearchStrategy(str, Enum):
    """检索策略（§13）。"""

    first_satisfactory = "first_satisfactory"
    best_available = "best_available"


class ProviderStatus(str, Enum):
    """Provider 状态（§66）：三者不可混为一个错误。"""

    success = "success"
    no_result = "no_result"
    unavailable = "unavailable"
    failed = "failed"


class ResolutionStatus(str, Enum):
    """顶层状态（§67）。"""

    resolved = "resolved"
    resolved_with_warnings = "resolved_with_warnings"
    partial = "partial"
    failed = "failed"


class RequestStatus(str, Enum):
    """单个 AssetRequest 的结局。"""

    resolved = "resolved"
    unresolved = "unresolved"


class RegistryScope(str, Enum):
    """Registry 范围（§59）；user_library 第一阶段仅保留 schema。"""

    project = "project"
    global_ = "global"
    user_library = "user_library"


# ---------------------------------------------------------------------------
# Query（§10/§15）
# ---------------------------------------------------------------------------


class SemanticQuery(StrictModel):
    """结构化语义查询（§10）；``raw`` 必须始终保留。"""

    raw: str = ""
    object: str = ""
    attributes: List[str] = Field(default_factory=list)
    style: List[str] = Field(default_factory=list)
    negative_terms: List[str] = Field(default_factory=list)


class ProviderQuery(StrictModel):
    """Query Compiler 输出（§15）；不同 Provider 不共享唯一字符串 Query。"""

    provider_id: str = ""
    canonical_terms: List[str] = Field(default_factory=list)
    text_query: str = ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    negative_terms: List[str] = Field(default_factory=list)


class ProviderCapability(StrictModel):
    """Provider 能力声明（§18）；Source Policy Resolver 先按它过滤。"""

    asset_types: List[str] = Field(default_factory=list)
    media_types: List[str] = Field(default_factory=list)
    supports_semantic_search: bool = True
    supports_exact_lookup: bool = True
    supports_license_filter: bool = False
    supports_metadata_filter: bool = True


# ---------------------------------------------------------------------------
# Candidate（§26-28）
# ---------------------------------------------------------------------------


class LicenseMetadata(StrictModel):
    """许可信息；与 ``origin`` 绑定（§58）。"""

    type: str = "unknown"  # cleared / cc0 / cc_by / cc_by_sa / royalty_free / unknown / restricted
    attribution_required: bool = False
    url: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)


class AssetOrigin(StrictModel):
    """同一内容的来源之一（§57-58）。"""

    provider_id: str
    source_type: AssetSource
    source_ref: str = ""
    license: LicenseMetadata = Field(default_factory=LicenseMetadata)
    retrieved_at: str = ""


class SemanticMetadata(StrictModel):
    """统一语义字段（§28/§34-35）。"""

    object: str = ""
    attributes: List[str] = Field(default_factory=list)
    style: List[str] = Field(default_factory=list)
    usage_tags: List[str] = Field(default_factory=list)
    style_family: str = ""
    caption: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)


class TechnicalMetadata(StrictModel):
    """统一技术字段（§28/§41-42）；``partial=True`` 表示像素级统计缺失。"""

    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    has_alpha: Optional[bool] = None
    alpha_ratio: Optional[float] = None  # N(alpha<255)/N（§42）
    foreground_occupancy: Optional[float] = None  # N(alpha>tau)/N（§41）
    duration: Optional[float] = None
    bpm: Optional[float] = None
    format: str = ""
    file_size: int = 0
    mime_type: str = ""
    partial: bool = False


class RetrievalMetadata(StrictModel):
    """Provider 召回信息；retrieval_score 仅是弱特征（§27）。"""

    retrieval_score: float = 0.0
    retrieved_at: str = ""


class AssetCandidate(StrictModel):
    """搜索候选（§26）；≠ AssetRecord，只有被选中才进 Registry。"""

    candidate_uid: str
    provider_id: str
    provider_version: str = "1"
    source_type: AssetSource
    source_ref: str = ""
    asset_type: str = ""
    media_type: str = ""
    preview_uri: str = ""
    original_uri: str = ""
    local_uri: str = ""  # user/local 候选已有的本地可读路径
    semantic_metadata: SemanticMetadata = Field(default_factory=SemanticMetadata)
    technical_metadata: TechnicalMetadata = Field(default_factory=TechnicalMetadata)
    license_metadata: LicenseMetadata = Field(default_factory=LicenseMetadata)
    retrieval_metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)
    provenance: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AssetRecord / Integrity（§52-58）
# ---------------------------------------------------------------------------


class Integrity(StrictModel):
    """完整性校验（§56）；Executor 只消费通过 Integrity 的 AssetRecord。"""

    content_hash: str = ""  # 完整 SHA-256 hex
    perceptual_hash: str = ""
    file_size: int = 0
    mime_type: str = ""
    decodable: bool = False
    validated_at: str = ""


class AssetRecord(StrictModel):
    """正式入库素材（§54）；Plan 引用 ``asset_uid`` 而非文件路径（§55）。"""

    asset_uid: str  # ast_<sha256[:8]>，冲突时追加序号
    source_type: AssetSource
    provider_id: str = ""
    asset_type: str = ""
    media_type: str = ""
    local_uri: str = ""  # 项目内可读路径（§75：Executor 只认它）
    original_uri: str = ""
    semantic_metadata: SemanticMetadata = Field(default_factory=SemanticMetadata)
    technical_metadata: TechnicalMetadata = Field(default_factory=TechnicalMetadata)
    origins: List[AssetOrigin] = Field(default_factory=list)
    integrity: Integrity = Field(default_factory=Integrity)
    registry_scope: RegistryScope = RegistryScope.project
    style_family: str = ""
    lifecycle: Dict[str, Any] = Field(default_factory=dict)  # created_at / last_used_at / state


# ---------------------------------------------------------------------------
# Binding / SearchRecord（§63-65）
# ---------------------------------------------------------------------------


class BindingRecord(StrictModel):
    """模块四内部富绑定（§63）；``active`` 标记当前版本（§64）。"""

    binding_uid: str
    asset_request_uid: str
    request_version: int = 1
    asset_uid: str = ""
    selected_origin: Optional[AssetOrigin] = None
    selection_score: float = 0.0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    alternatives: List[str] = Field(default_factory=list)  # candidate_uids（§50）
    fallback_used: bool = False
    active: bool = True
    warnings: List[str] = Field(default_factory=list)
    created_at: str = ""


class RejectedCandidate(StrictModel):
    candidate_uid: str
    reason: str = ""  # filter 规则 id / dedup / inspect_failed / import_failed
    detail: str = ""


class RankedCandidate(StrictModel):
    candidate_uid: str
    score: float = 0.0
    breakdown: Dict[str, float] = Field(default_factory=dict)


class SearchRecord(StrictModel):
    """一次真实搜索的留痕（§65）；candidates 保留候选池供 alternative 切换。"""

    search_uid: str  # srch_<NN>
    request_uid: str
    request_version: int = 1
    provider_ids: List[str] = Field(default_factory=list)
    queries: Dict[str, Any] = Field(default_factory=dict)  # provider_id → ProviderQuery dump
    candidates: List[AssetCandidate] = Field(default_factory=list)
    rejected_candidates: List[RejectedCandidate] = Field(default_factory=list)
    ranked_candidates: List[RankedCandidate] = Field(default_factory=list)
    selected_candidate: str = ""
    status: str = "success"  # success / empty / failed
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider 报告 / 依赖请求 / 结果（§66-68/§74）
# ---------------------------------------------------------------------------


class ProviderReport(StrictModel):
    provider_id: str
    status: ProviderStatus
    candidate_count: int = 0
    message: str = ""


class AssetDependencyRequest(StrictModel):
    """模块四 → Controller 的依赖请求（§47/§74），如 Module 2 音频分析。"""

    request_uid: str  # adep_<NN>
    type: str = "audio_analysis"
    asset_ref: str = ""  # 待分析素材（candidate source_ref / uri）
    required_analyses: List[str] = Field(default_factory=list)  # ["bpm", "duration", "beats"]
    reason: str = ""
    blocking: bool = False
    request_ref: str = ""  # 来源 AssetRequest uid


class RequestResolution(StrictModel):
    """单个 AssetRequest 的解析结局。"""

    request_uid: str
    request_version: int = 1
    status: RequestStatus = RequestStatus.unresolved
    binding: Optional[BindingRecord] = None
    warnings: List[str] = Field(default_factory=list)
    search_uid: str = ""
    reason: str = ""


class AssetResolutionResult(StrictModel):
    """顶层结果（§67）。"""

    status: ResolutionStatus = ResolutionStatus.failed
    bindings: List[AssetBinding] = Field(default_factory=list)  # 模块三简版契约
    binding_records: List[BindingRecord] = Field(default_factory=list)  # 模块四富版
    resolutions: List[RequestResolution] = Field(default_factory=list)
    unresolved_requests: List[str] = Field(default_factory=list)  # request_uids
    provider_warnings: List[ProviderReport] = Field(default_factory=list)
    search_records: List[SearchRecord] = Field(default_factory=list)
    dependency_requests: List[AssetDependencyRequest] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class ProjectAssetState(StrictModel):
    """项目素材状态（§61）：registry + bindings + usage_index。"""

    project_id: str = "default"
    registry: Dict[str, AssetRecord] = Field(default_factory=dict)  # asset_uid → record
    bindings: Dict[str, List[BindingRecord]] = Field(default_factory=dict)  # request_uid → 版本链
    usage_index: Dict[str, List[str]] = Field(default_factory=dict)  # asset_uid → plan_item_uids
