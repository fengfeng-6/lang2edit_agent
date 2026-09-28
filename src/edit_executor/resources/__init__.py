"""执行资源（§29-§30）：MVP 接缝包，5.4 填充实现。"""

from .builder import build_resources
from .cache import ResourceCache, resource_cache_key
from .models import ResourceBuildResult

__all__ = [
    "ResourceBuildResult",
    "ResourceCache",
    "build_resources",
    "resource_cache_key",
]
