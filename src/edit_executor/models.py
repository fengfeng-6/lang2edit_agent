"""Module 5 剪辑工具执行模块：核心领域模型。

对应设计文档 docs/Module 5 剪辑工具执行模块设计文档.md 的 §4-§83。

规划与执行严格分离（§2.1）：本模块消费模块三的 ResolvedEditingPlan，
不重新理解语言、不重算事件时间、不做 Planner 语义降级。
``project_time`` / ``transform`` 直接复用模块三模型，保持 JSON 兼容。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

from asset_manager.models import ProjectAssetState
from editing_planner.models import ProjectTime, ResolvedEditingPlan, Transform

if TYPE_CHECKING:
    from .dsl.models import EditOperation


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


def _stable_uid(prefix: str, *parts: Any, length: int = 8) -> str:
    """确定性 uid：与模块三 pln_/plan_ 惯例一致（sha1 截断）。"""
    import hashlib
    import json

    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:length]}"


def canonical_json(value: Any) -> str:
    """canonical 序列化（§31 fingerprint 的依据）：排序键、UTF-8、非 ASCII 原样。"""
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 执行上下文输入（§4-§6）
# ---------------------------------------------------------------------------


class SourceMedia(StrictModel):
    """真实源媒体上下文（§5）：media_uid 是稳定身份，路径只是当前位置。"""

    media_uid: str
    video_id: str
    local_uri: str = ""
    duration: float = 0.0
    fps: float = 30.0
    width: int = 0
    height: int = 0
    rotation: int = 0
    has_audio: bool = False
    content_hash: str = ""
    version: str = "v1"


class AnalysisArtifact(StrictModel):
    """模块二已产出的执行级分析结果（§6）；模块五不主动调用模块二。"""

    artifact_uid: str
    artifact_type: str  # spatial_tracks / foreground_subject_mask / person_mask / ...
    video_id: str = ""
    video_version: str = ""
    semantic_state_version: int = 0
    target: Optional[str] = None  # head / left_hand / foreground_subject / ...
    local_uri: Optional[str] = None
    inline_data: Optional[Dict[str, Any]] = None
    coordinate_space: Optional[str] = None  # source_video_normalized
    time_space: str = "source_seconds"
    semantic_properties: Dict[str, Any] = Field(default_factory=dict)
    producer: str = ""
    producer_version: str = ""
    valid: bool = True


class AnalysisArtifactRegistry(StrictModel):
    """artifact_uid → AnalysisArtifact（§6）。"""

    artifacts: Dict[str, AnalysisArtifact] = Field(default_factory=dict)

    def find(
        self,
        artifact_type: str,
        target: Optional[str] = None,
    ) -> List[AnalysisArtifact]:
        out = [
            a
            for a in self.artifacts.values()
            if a.artifact_type == artifact_type and a.valid
        ]
        if target is not None:
            out = [a for a in out if a.target in (None, target)]
        return sorted(out, key=lambda a: a.artifact_uid)

    def require(
        self, artifact_type: str, target: Optional[str] = None
    ) -> Optional[AnalysisArtifact]:
        found = self.find(artifact_type, target)
        return found[0] if found else None


class BackendConfig(StrictModel):
    """Backend 选择与环境（§4/§40）。"""

    backend_id: str = "memory"
    materialization_mode: str = "incremental"  # incremental / rebuild / render_only
    capabilities_override: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)


class ExecutionOptions(StrictModel):
    dry_run: bool = False
    force: bool = False  # project.lock 占用时仍继续（§72，带 warning）
    run_label: Optional[str] = None


class ExportSpec(StrictModel):
    """导出规格（§74）；MVP 仅建模。"""

    output_uri: str = ""
    format: str = "mp4"
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None


class ExecutorInput(StrictModel):
    """模块五统一入口（§4）：ResolvedPlan 不是全部输入。"""

    project_id: str
    resolved_plan: ResolvedEditingPlan
    source_media: SourceMedia
    asset_registry: Optional[ProjectAssetState] = None
    analysis_artifacts: AnalysisArtifactRegistry = Field(
        default_factory=AnalysisArtifactRegistry
    )
    backend_config: BackendConfig = Field(default_factory=BackendConfig)
    current_state: Optional["ProjectExecutionState"] = None
    export_spec: Optional[ExportSpec] = None
    execution_options: ExecutionOptions = Field(default_factory=ExecutionOptions)


# ---------------------------------------------------------------------------
# Desired Project Graph（§7-§20）
# ---------------------------------------------------------------------------


class ProjectSpec(StrictModel):
    """工程输出规格（§8）：默认继承原始视频，模块五不自行决定平台格式。"""

    width: int = 0
    height: int = 0
    fps: float = 30.0
    aspect_ratio: float = 0.0
    audio_sample_rate: Optional[int] = None
    duration: float = 0.0


class TrackSpec(StrictModel):
    """逻辑轨道（§9-§10）：物理轨道分配属于 Backend。"""

    track_uid: str  # trk_<logical_name>
    logical_name: str
    track_type: str = "video"  # video / audio
    z_order: int = 0
    enabled: bool = True
    locked: bool = False
    object_uids: List[str] = Field(default_factory=list)


class MediaRef(StrictModel):
    """Graph 内统一媒体引用（§20）：对象不直接保存本地路径。"""

    media_ref_uid: str  # mref_source_video / mref_<asset_uid> / mref_res_<uid>
    source_type: str = ""  # source_video / asset / execution_resource
    source_uid: str = ""
    local_uri: str = ""
    media_type: str = ""  # video / image / audio
    content_hash: str = ""
    technical_metadata: Dict[str, Any] = Field(default_factory=dict)


class KeyframeGroup(StrictModel):
    """执行级关键帧（§18）：time 为工程秒；values 为归一化语义值。"""

    time: float
    values: Dict[str, Any] = Field(default_factory=dict)
    interpolation: str = "linear"


class SemanticAnimationSpec(StrictModel):
    """Core 层语义动画（§19）：不保存任何 Backend 动画 ID。"""

    semantic_type: str = "none"
    duration: Optional[float] = None
    intensity: Optional[float] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class TrackingBinding(StrictModel):
    """resolved_capability=tracking 时的跟随绑定（§25）；Backend 决定是否原生跟踪。"""

    target: str = ""
    trajectory_ref: str = ""  # AnalysisArtifact.artifact_uid
    smoothing: str = "source_smoothed"
    sensitivity: float = 1.0
    dead_zone: float = 0.01


class ObjectRelation(StrictModel):
    relation_type: str = ""
    target_uid: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


class TimelineObject(StrictModel):
    """剪辑工程中的可编辑实体（§11）；timeline_object_uid 长期稳定。"""

    timeline_object_uid: str
    object_key: str = ""  # <plan_item_uid>:<role>（§12）
    origin: str = "plan_item"  # system / plan_item / resource
    source_plan_item_uid: Optional[str] = None
    source_requirement_ids: List[str] = Field(default_factory=list)
    source_event_uids: List[str] = Field(default_factory=list)
    object_type: str = ""
    role: str = ""
    semantic_label: str = ""
    track_uid: str = ""
    media_ref: Optional[str] = None
    asset_uid: Optional[str] = None
    project_time: ProjectTime = Field(
        default_factory=lambda: ProjectTime(start=0.0, end=0.0)
    )
    transform: Optional[Transform] = None
    animation: Optional[SemanticAnimationSpec] = None
    keyframes: List[KeyframeGroup] = Field(default_factory=list)
    tracking: Optional[TrackingBinding] = None
    mask_ref: Optional[str] = None  # AnalysisArtifact.artifact_uid
    parameters: Dict[str, Any] = Field(default_factory=dict)
    relations: List[ObjectRelation] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = ""


class DesiredProjectGraph(StrictModel):
    """最新计划对应的完整目标工程状态（§7）。"""

    graph_uid: str
    project_id: str
    source_plan_uid: str = ""
    source_plan_version: int = 0
    project_spec: ProjectSpec = Field(default_factory=ProjectSpec)
    media_refs: Dict[str, MediaRef] = Field(default_factory=dict)
    tracks: Dict[str, TrackSpec] = Field(default_factory=dict)
    objects: Dict[str, TimelineObject] = Field(default_factory=dict)
    total_duration: float = 0.0
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff / Patch（§32-§35）
# ---------------------------------------------------------------------------


class DiffAction(str, Enum):
    create = "CREATE"
    update = "UPDATE"
    delete = "DELETE"
    noop = "NOOP"


class DiffSummary(StrictModel):
    created: int = 0
    updated: int = 0
    deleted: int = 0
    noop: int = 0
    object_changes: Dict[str, str] = Field(default_factory=dict)  # uid → action


class PatchValidation(StrictModel):
    valid: bool = True
    issues: List[str] = Field(default_factory=list)


class ExecutionPatch(StrictModel):
    """一次执行真正需要施加的变化（§35）。"""

    patch_uid: str
    project_id: str
    base_revision: int
    target_revision: int
    source_plan_uid: str = ""
    operations: List[EditOperation] = Field(default_factory=list)
    affected_plan_item_uids: List[str] = Field(default_factory=list)
    affected_timeline_object_uids: List[str] = Field(default_factory=list)
    summary: DiffSummary = Field(default_factory=DiffSummary)
    validation: PatchValidation = Field(default_factory=PatchValidation)


# ---------------------------------------------------------------------------
# 执行资源（§29-§30）
# ---------------------------------------------------------------------------


class ExecutionResource(StrictModel):
    """技术执行资源（§29）：来自原视频+分析结果，不是创作素材。"""

    resource_uid: str
    resource_type: str = ""  # freeze_frame_image / foreground_alpha / proxy / audio_extract
    source_refs: List[str] = Field(default_factory=list)
    local_uri: str = ""
    content_hash: str = ""
    producer: str = ""
    producer_version: str = ""
    reusable: bool = True


# ---------------------------------------------------------------------------
# 状态与历史（§56-§83）
# ---------------------------------------------------------------------------


class BackendProjectRef(StrictModel):
    """某次具体工程中的软件内部引用（§55）；随 revision 可变。"""

    backend_id: str = ""
    project_revision: int = 0
    local_uri: Optional[str] = None
    native_project_id: Optional[str] = None
    created_at: str = ""
    valid: bool = True


class ProjectExecutionState(StrictModel):
    """已成功执行到的工程状态（§57）；revision 只表示完整提交的版本。"""

    schema_version: int = 1
    project_id: str
    revision: int = 0
    backend_id: str = ""
    backend_version: str = ""
    app_version: str = ""
    project_ref: Optional[BackendProjectRef] = None
    source_plan_uid: str = ""
    source_plan_version: int = 0
    video_id: str = ""
    video_version: str = ""
    graph_fingerprint: str = ""
    tracks: Dict[str, Any] = Field(default_factory=dict)
    timeline_objects: Dict[str, Any] = Field(default_factory=dict)
    media_refs: Dict[str, Any] = Field(default_factory=dict)
    execution_resources: Dict[str, Any] = Field(default_factory=dict)
    plan_item_index: Dict[str, List[str]] = Field(default_factory=dict)
    requirement_index: Dict[str, List[str]] = Field(default_factory=dict)
    asset_usage_index: Dict[str, List[str]] = Field(default_factory=dict)
    total_duration: float = 0.0
    last_patch_uid: Optional[str] = None
    status: str = "active"


class RevisionManifest(StrictModel):
    """每个 revision 的清单（§69）；state=committed 才能成为 current。"""

    revision: int
    execution_uid: str = ""
    state: str = "candidate"  # candidate / committed
    graph_fingerprint: str = ""
    backend_project_ref: Optional[BackendProjectRef] = None
    source_plan_uid: str = ""
    patch_uid: str = ""
    created_at: str = ""


class HistoryEntry(StrictModel):
    """history.jsonl 一行：用户可观察的执行摘要（§61）。"""

    execution_uid: str
    patch_uid: str = ""
    base_revision: int = 0
    target_revision: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    noop: int = 0
    status: str = ""
    created_at: str = ""


class JournalEntry(StrictModel):
    """journal.jsonl 一行：执行级内部历史（§62）。"""

    execution_uid: str
    operation_uid: str = ""
    operation_type: str = ""
    target_uid: Optional[str] = None
    status: str = ""
    created_at: str = ""


class ExecutionPhase(str, Enum):
    """Execution 状态机（§63）。"""

    prepared = "prepared"
    preflight_passed = "preflight_passed"
    compiled = "compiled"
    executing = "executing"
    verifying = "verifying"
    committing = "committing"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"


class FailureCategory(str, Enum):
    """ExecutionFailure.category（§65）。"""

    preflight = "preflight"
    resource = "resource"
    backend = "backend"
    capability = "capability"
    validation = "validation"
    filesystem = "filesystem"
    timeout = "timeout"
    internal = "internal"


class ExecutionFailure(StrictModel):
    failure_uid: str
    execution_uid: str
    operation_uid: Optional[str] = None
    category: str = FailureCategory.internal.value
    code: str = ""
    message: str = ""
    retryable: bool = False
    affected_objects: List[str] = Field(default_factory=list)
    caused_by: Optional[str] = None


class ExecutorDependency(StrictModel):
    """缺资源时的返回（§84）；模块五不直接调其他模块。"""

    dependency_uid: str
    type: str  # analysis_artifact / asset / backend_capability / source_media
    target: str = ""
    required_resource: str = ""
    blocking: bool = True
    reason: str = ""


class VerificationIssue(StrictModel):
    code: str = ""
    severity: str = "hard"  # hard / warning / info
    message: str = ""
    object_uids: List[str] = Field(default_factory=list)


class VerificationReport(StrictModel):
    status: str = "pass"  # pass / warn / fail
    issues: List[VerificationIssue] = Field(default_factory=list)


class ExportStatus(str, Enum):
    """导出状态与工程状态分离（§74）。"""

    not_requested = "not_requested"
    pending = "pending"
    completed = "completed"
    failed = "failed"
    unsupported = "unsupported"
    requires_user_action = "requires_user_action"


class ExecutionStatus(str, Enum):
    completed = "completed"
    completed_noop = "completed_noop"
    dry_run = "dry_run"
    failed = "failed"
    preflight_failed = "preflight_failed"
    needs_dependency = "needs_dependency"
    capability_mismatch = "capability_mismatch"
    blocked = "blocked"
    stale_patch = "stale_patch"
    locked = "locked"
    needs_reconciliation = "needs_reconciliation"


class EditableObjectView(StrictModel):
    """Module 6 看到的可编辑对象（§76）。"""

    object_uid: str
    display_id: str = ""
    source_requirement_ids: List[str] = Field(default_factory=list)
    source_plan_item_uid: Optional[str] = None
    object_type: str = ""
    role: str = ""
    semantic_label: str = ""
    asset_uid: Optional[str] = None
    project_time: ProjectTime = Field(
        default_factory=lambda: ProjectTime(start=0.0, end=0.0)
    )
    editable_properties: List[str] = Field(default_factory=list)
    current_properties: Dict[str, Any] = Field(default_factory=dict)


class ProjectEditView(StrictModel):
    """模块五给模块六的最重要接口（§75）。"""

    project_id: str
    revision: int = 0
    objects: List[EditableObjectView] = Field(default_factory=list)


class ExecutionResult(StrictModel):
    """apply() 顶层输出（§83）。"""

    execution_uid: str = ""
    status: str = ExecutionStatus.completed.value
    base_revision: int = 0
    revision: int = 0
    project_ref: Optional[BackendProjectRef] = None
    patch: Optional[ExecutionPatch] = None  # dry_run / 诊断用（§83 微调）
    patch_summary: Optional[DiffSummary] = None
    object_changes: Dict[str, str] = Field(default_factory=dict)
    execution_state: Optional[ProjectExecutionState] = None
    edit_view: Optional[ProjectEditView] = None
    verification: Optional[VerificationReport] = None
    dependencies: List[ExecutorDependency] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[ExecutionFailure] = Field(default_factory=list)
    project_status: str = "unknown"
    export_status: str = ExportStatus.not_requested.value
    active_asset_usage: Dict[str, List[str]] = Field(default_factory=dict)


ExecutorInput.update_forward_refs()
