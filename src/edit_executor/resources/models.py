"""执行资源模型（§29-§30）：MVP 仅定义返回结构。

ExecutionResource 本体在 edit_executor.models——技术执行资源
（freeze_frame_image / foreground_alpha / proxy / audio_extract）
来自原视频+分析结果，不是创作素材。
"""

from __future__ import annotations

from typing import List

from pydantic import Field

from ..models import ExecutionResource, StrictModel


class ResourceBuildResult(StrictModel):
    """ResourceBuilder 一次产出的结果。"""

    resources: List[ExecutionResource] = Field(default_factory=list)
    cache_hits: List[str] = Field(default_factory=list)  # resource_uid
    warnings: List[str] = Field(default_factory=list)
