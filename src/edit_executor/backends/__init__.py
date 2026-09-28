"""Backend 层（§40-§50）：ExecutorBackend 协议 + Memory/FFmpeg 实现。"""

from .base import BackendOperationError, ExecutorBackend, capability_manifest
from .ffmpeg import FFmpegBackend
from .memory import MemoryBackend

__all__ = [
    "BackendOperationError",
    "ExecutorBackend",
    "FFmpegBackend",
    "MemoryBackend",
    "capability_manifest",
]
