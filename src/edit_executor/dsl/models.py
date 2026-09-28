"""Editing DSL 强类型结构（§36-§39, §64）。

DSL 不是字符串脚本：每条操作是结构化 EditOperation，通过
``depends_on`` 形成 DAG，``idempotency_key`` 支持崩溃后判断是否已执行。
这里故意没有 add_overlay/add_sticker——那是 Planner 语义（§37）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..models import StrictModel


class EditOpType(str, Enum):
    """第一阶段支持的 DSL 操作（§37）。"""

    create_project = "create_project"
    open_project = "open_project"
    import_media = "import_media"
    ensure_track = "ensure_track"
    create_object = "create_object"
    delete_object = "delete_object"
    replace_media = "replace_media"
    set_time_range = "set_time_range"
    set_transform = "set_transform"
    set_animation = "set_animation"
    set_keyframes = "set_keyframes"
    set_mask = "set_mask"
    set_text = "set_text"
    set_volume = "set_volume"
    freeze_frame = "freeze_frame"
    save_project = "save_project"
    export_video = "export_video"


class OperationStatus(str, Enum):
    """Operation 状态机（§64）：依赖失败时 dependent → skipped。"""

    pending = "pending"
    ready = "ready"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    rolled_back = "rolled_back"


class EditOperation(StrictModel):
    """DSL 操作（§36）。"""

    operation_uid: str
    op_type: str  # EditOpType value（字符串便于跨版本序列化）
    target_uid: Optional[str] = None  # timeline_object_uid / media_ref_uid / track_uid
    arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    idempotency_key: str = ""  # 如 create:tlobj_heart_02:v3（§39）
    status: str = OperationStatus.pending.value


class OperationResult(StrictModel):
    """Backend 单操作执行回报。"""

    operation_uid: str
    status: str = OperationStatus.pending.value
    error: str = ""
    backend_object_ref: Optional[str] = None


class BackendInfo(StrictModel):
    backend_id: str = ""
    backend_version: str = ""
    app_version: str = ""


class BackendProbeResult(StrictModel):
    available: bool = True
    details: Dict[str, Any] = Field(default_factory=dict)


class CapabilityStatus(str, Enum):
    """Backend 能力三态（§48）：unverified 对 Planner 保守映射为 false。"""

    supported = "supported"
    unsupported = "unsupported"
    unverified = "unverified"


class CapabilityEntry(StrictModel):
    status: str = CapabilityStatus.unverified.value
    source: str = ""
    backend_version: str = ""
    app_version: str = ""
    constraints: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class BackendCapabilityManifest(StrictModel):
    backend_id: str = ""
    capabilities: Dict[str, CapabilityEntry] = Field(default_factory=dict)

    def status_of(self, name: str) -> str:
        entry = self.capabilities.get(name)
        return entry.status if entry is not None else CapabilityStatus.unverified.value


class BackendSession(StrictModel):
    session_uid: str
    project_id: str = ""
    mode: str = "incremental"  # ProjectMaterializationMode（§40）


class BackendExecutionReport(StrictModel):
    op_results: List[OperationResult] = Field(default_factory=list)
    sim_uri: Optional[str] = None
    error: str = ""


class BackendVerificationReport(StrictModel):
    ok: bool = True
    issues: List[str] = Field(default_factory=list)


class ExportResult(StrictModel):
    status: str = "unsupported"  # ExportStatus value
    output_uri: Optional[str] = None
    message: str = ""


from ..models import ExecutionPatch  # noqa: E402

ExecutionPatch.update_forward_refs(EditOperation=EditOperation)
