"""edit_executor — 模块五：剪辑工具执行模块。

把模块三 ``ResolvedEditingPlan`` 转换为真实可编辑工程状态（§1-§2）：

    ExecutorInput
        → compile()  → DesiredProjectGraph   （目标工程图）
        → diff       → GraphDiff → ExecutionPatch（Editing DSL DAG）
        → apply()    → Backend execute → verify → Revision Commit
        → inspect/edit_view → ProjectEditView（模块六接口）

规划与执行严格分离：不重算语义、不自动降级；缺资源走
``ExecutorDependency``，能力不匹配走 ``capability_mismatch``。
"""

# 必须先于子模块导入赋值——backends/api 以 from . import __version__ 读它
__version__ = "0.1.0"

from .api import EditingExecutor
from .models import (
    AnalysisArtifact,
    AnalysisArtifactRegistry,
    BackendConfig,
    BackendProjectRef,
    DesiredProjectGraph,
    DiffAction,
    DiffSummary,
    EditableObjectView,
    ExecutionFailure,
    ExecutionOptions,
    ExecutionPatch,
    ExecutionPhase,
    ExecutionResource,
    ExecutionResult,
    ExecutionStatus,
    ExecutorDependency,
    ExecutorInput,
    ExportSpec,
    ExportStatus,
    FailureCategory,
    HistoryEntry,
    JournalEntry,
    KeyframeGroup,
    MediaRef,
    ObjectRelation,
    PatchValidation,
    ProjectEditView,
    ProjectExecutionState,
    ProjectSpec,
    RevisionManifest,
    SemanticAnimationSpec,
    SourceMedia,
    TimelineObject,
    TrackingBinding,
    TrackSpec,
    VerificationIssue,
    VerificationReport,
)

__all__ = [
    "EditingExecutor",
    "AnalysisArtifact",
    "AnalysisArtifactRegistry",
    "BackendConfig",
    "BackendProjectRef",
    "DesiredProjectGraph",
    "DiffAction",
    "DiffSummary",
    "EditableObjectView",
    "ExecutionFailure",
    "ExecutionOptions",
    "ExecutionPatch",
    "ExecutionPhase",
    "ExecutionResource",
    "ExecutionResult",
    "ExecutionStatus",
    "ExecutorDependency",
    "ExecutorInput",
    "ExportSpec",
    "ExportStatus",
    "FailureCategory",
    "HistoryEntry",
    "JournalEntry",
    "KeyframeGroup",
    "MediaRef",
    "ObjectRelation",
    "PatchValidation",
    "ProjectEditView",
    "ProjectExecutionState",
    "ProjectSpec",
    "RevisionManifest",
    "SemanticAnimationSpec",
    "SourceMedia",
    "TimelineObject",
    "TrackingBinding",
    "TrackSpec",
    "VerificationIssue",
    "VerificationReport",
    "__version__",
]
