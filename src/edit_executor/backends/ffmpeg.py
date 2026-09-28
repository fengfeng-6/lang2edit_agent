"""FFmpeg Backend 存根（5.4 接缝，§91）。

设计定位：render_only 渲染管线 Backend（非交互式工程），
本里程碑只提供 backend_info / describe_capabilities 形状，
execute/save 明确报 unsupported——绝不静默吞掉操作（§2.5）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..dsl.models import (
    BackendCapabilityManifest,
    BackendExecutionReport,
    BackendInfo,
    BackendSession,
    BackendVerificationReport,
    CapabilityStatus,
    ExportResult,
)
from ..models import BackendConfig, BackendProjectRef, DesiredProjectGraph, ExecutionPatch
from .base import ExecutorBackend, capability_manifest
from .memory import _ALL_CAPS


class FFmpegBackend(ExecutorBackend):
    def __init__(self, *, config: Optional[BackendConfig] = None) -> None:
        self.config = config or BackendConfig(backend_id="ffmpeg")

    def backend_info(self) -> BackendInfo:
        from .. import __version__

        return BackendInfo(
            backend_id="ffmpeg", backend_version="stub", app_version=__version__
        )

    def describe_capabilities(self) -> BackendCapabilityManifest:
        return capability_manifest(
            "ffmpeg",
            {name: CapabilityStatus.unverified.value for name in _ALL_CAPS},
        )

    def begin_session(self, project_context: Dict[str, Any]) -> BackendSession:
        return BackendSession(
            session_uid="sess_ffmpeg_stub",
            project_id=str(project_context.get("project_id", "")),
            mode="render_only",
        )

    def execute(
        self,
        session: BackendSession,
        patch: Optional[ExecutionPatch],
        desired_graph: DesiredProjectGraph,
    ) -> BackendExecutionReport:
        return BackendExecutionReport(
            error="FFmpegBackend 未实现（里程碑 5.4）：不执行任何操作"
        )

    def verify(
        self, session: BackendSession, desired_graph: DesiredProjectGraph
    ) -> BackendVerificationReport:
        report = BackendVerificationReport(ok=False)
        report.issues.append("FFmpegBackend 未实现（里程碑 5.4）")
        return report

    def save(self, session: BackendSession) -> BackendProjectRef:
        return BackendProjectRef(backend_id="ffmpeg", valid=False)

    def export(self, session: BackendSession, export_spec: Any) -> ExportResult:
        return ExportResult(status="unsupported", message="导出属里程碑 5.4")
