"""执行资源缓存（§30）：execution/resources/ 下的内容寻址文件缓存。

缓存键 H(inputs, params, producer_version)——同一源视频+参数+生产版本
的产物可跨 revision 复用（reusable=True 的 ExecutionResource）。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..models import _stable_uid


def resource_cache_key(
    resource_type: str,
    inputs: Iterable[str],
    params: Dict[str, Any],
    producer_version: str,
) -> str:
    """res_<sha8>：内容与生产者共同决定（§30）。"""
    return _stable_uid(
        "res", resource_type, sorted(inputs), dict(params), producer_version
    )


class ResourceCache:
    """execution/resources/ 文件级缓存；MVP 只提供寻址与读写，5.4 才真正产出。"""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def path_for(self, key: str, suffix: str = ".bin") -> Path:
        return self.cache_dir / f"{key}{suffix}"

    def get(self, key: str, suffix: str = ".bin") -> Optional[Path]:
        path = self.path_for(key, suffix)
        return path if path.exists() else None

    def put(self, key: str, src_path: Path, suffix: str = ".bin") -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        dst = self.path_for(key, suffix)
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(src_path, tmp)
        import os

        os.replace(tmp, dst)
        return dst
