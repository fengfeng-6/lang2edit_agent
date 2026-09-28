"""ExecutorBackend 协议（§47-§50, §55）。

incremental Backend 主要消费 ExecutionPatch；
rebuild Backend 主要消费 DesiredProjectGraph。
Backend 只能做 Implementation Lowering（等语义实现），
不得做 Semantic Degradation（§2.5）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..dsl.models import (
    BackendCapabilityManifest,
    BackendExecutionReport,
    BackendInfo,
    BackendProbeResult,
    BackendSession,
    BackendVerificationReport,
    CapabilityEntry,
    CapabilityStatus,
    ExportResult,
)
from ..models import BackendProjectRef, DesiredProjectGraph, ExecutionPatch


class BackendOperationError(Exception):
    """Backend 单操作失败（可重试与否由调用方判断）。"""


class ExecutorBackend:
    """Backend 接口（§47）。"""

    def backend_info(self) -> BackendInfo:
        raise NotImplementedError

    def probe_environment(self) -> BackendProbeResult:
        return BackendProbeResult(available=True)

    def describe_capabilities(self) -> BackendCapabilityManifest:
        raise NotImplementedError

    def planner_capabilities(self) -> Dict[str, bool]:
        """§49 闭环：manifest → Planner 的保守布尔能力表。"""
        manifest = self.describe_capabilities()
        return {
            name: entry.status == CapabilityStatus.supported.value
            for name, entry in manifest.capabilities.items()
        }

    def begin_session(self, project_context: Dict[str, Any]) -> BackendSession:
        raise NotImplementedError

    def execute(
        self,
        session: BackendSession,
        patch: Optional[ExecutionPatch],
        desired_graph: DesiredProjectGraph,
    ) -> BackendExecutionReport:
        raise NotImplementedError

    def verify(
        self,
        session: BackendSession,
        desired_graph: DesiredProjectGraph,
    ) -> BackendVerificationReport:
        raise NotImplementedError

    def save(self, session: BackendSession) -> BackendProjectRef:
        raise NotImplementedError

    def export(self, session: BackendSession, export_spec: Any) -> ExportResult:
        return ExportResult(status="unsupported")


def capability_manifest(
    backend_id: str, supported: Dict[str, str]
) -> BackendCapabilityManifest:
    """便捷构造：{cap: status} → manifest。"""
    return BackendCapabilityManifest(
        backend_id=backend_id,
        capabilities={
            name: CapabilityEntry(status=status, source="backend_declared")
            for name, status in supported.items()
        },
    )
