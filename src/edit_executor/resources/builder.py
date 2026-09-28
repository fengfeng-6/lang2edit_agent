"""ResourceBuilder（§30）：由 Desired Graph 推导需要预生成的技术资源。

MVP（5.1/5.2）：没有任何资源必须预生成——freeze_frame_image、
foreground_alpha、proxy、audio_extract 均属 5.4 的 FFmpeg 资源管线。
这里保留接缝：签名固定，Backend 迭代时往里填。
"""

from __future__ import annotations

from ..models import DesiredProjectGraph, ExecutorInput
from ..state.store import ExecutionStore
from .models import ResourceBuildResult


def build_resources(
    executor_input: ExecutorInput,
    graph: DesiredProjectGraph,
    store: ExecutionStore,
) -> ResourceBuildResult:
    """MVP 恒空：资源需求推导将在 5.4 按 resource_type 分派实现。"""
    return ResourceBuildResult()
