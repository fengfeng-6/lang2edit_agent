"""视频预处理：metadata 提取与时间坐标统一（§6）。

上层统一使用秒级 timestamp；帧号与时间满足 t_i = i / f（§6.1）。
metadata 提取两级降级：调用方提供 → ffprobe（系统命令，不依赖第三方包）
→ 明确报错（failed，不静默猜测，§42）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Optional, Union

from ..models import VideoMetadata


def video_id_for(path: Union[str, Path]) -> str:
    """稳定 video_id：内容抽样哈希（首 256KB + 文件大小）。

    同一视频文件改名/移动后 id 不变，语义状态与缓存可跨路径复用。
    """
    p = Path(path)
    size = p.stat().st_size
    digest = hashlib.sha1()
    with p.open("rb") as handle:
        digest.update(handle.read(256 * 1024))
    digest.update(str(size).encode("utf-8"))
    return f"vid_{digest.hexdigest()[:10]}"


def _aspect_ratio(width: int, height: int) -> Optional[str]:
    if width <= 0 or height <= 0:
        return None
    from math import gcd

    g = gcd(width, height)
    w, h = width // g, height // g
    # 常见画幅归一（如 1080x1920 → 9:16）
    if (w, h) in {(9, 16), (16, 9), (1, 1), (3, 4), (4, 3)} or w * h < 400:
        return f"{w}:{h}"
    ratio = width / height
    for name, r in (("9:16", 9 / 16), ("16:9", 16 / 9), ("1:1", 1.0), ("3:4", 3 / 4), ("4:3", 4 / 3)):
        if abs(ratio - r) < 0.02:
            return name
    return f"{w}:{h}"


def metadata_from_dict(video_id: str, payload: dict) -> VideoMetadata:
    """调用方直接提供 metadata 字段（测试 / 外部管线注入）。

    接受设计文档示例里的 ``resolution: [w, h]`` 写法，归一为 width/height。
    """
    data = dict(payload)
    data.setdefault("video_id", video_id)
    resolution = data.pop("resolution", None)
    if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
        data.setdefault("width", resolution[0])
        data.setdefault("height", resolution[1])
    if data.get("width") and data.get("height") and not data.get("aspect_ratio"):
        data["aspect_ratio"] = _aspect_ratio(int(data["width"]), int(data["height"]))
    return VideoMetadata(**{k: v for k, v in data.items() if k in VideoMetadata.__fields__})


def metadata_from_ffprobe(path: Union[str, Path], *, video_id: Optional[str] = None) -> VideoMetadata:
    """用系统 ffprobe 提取 metadata（§6）。ffprobe 不在 PATH 时抛 RuntimeError。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH; install ffmpeg or pass metadata explicitly")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:200]}")
    info = json.loads(proc.stdout or "{}")

    video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError(f"no video stream in {path}")
    audio_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)

    fps = 0.0
    # 可变帧率用 avg_frame_rate（§6.2）
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    rotation = 0
    side_data = video_stream.get("side_data_list") or []
    for item in side_data:
        if "rotation" in item:
            rotation = int(item["rotation"]) % 360
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        rotation = int(tags["rotate"]) % 360

    duration = float(info.get("format", {}).get("duration") or video_stream.get("duration") or 0.0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if rotation in (90, 270):
        width, height = height, width  # 旋转后对外的有效分辨率

    return VideoMetadata(
        video_id=video_id or video_id_for(path),
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        aspect_ratio=_aspect_ratio(width, height),
        codec=video_stream.get("codec_name"),
        rotation=rotation,
        has_audio=audio_stream is not None,
        audio_sample_rate=int(audio_stream["sample_rate"]) if audio_stream and audio_stream.get("sample_rate") else None,
        source_uri=str(path),
    )


def resolve_metadata(video: Union[str, Path, dict, VideoMetadata]) -> VideoMetadata:
    """统一入口：dict/VideoMetadata 直接使用，路径走 ffprobe。"""
    if isinstance(video, VideoMetadata):
        return video
    if isinstance(video, dict):
        meta = dict(video.get("metadata") or {})
        if video.get("path") and not meta:
            extracted = metadata_from_ffprobe(video["path"], video_id=video.get("video_id"))
            return extracted
        if not meta:
            raise RuntimeError("video dict must include 'metadata' or a readable 'path'")
        vid = video.get("video_id") or meta.get("video_id")
        if not vid and video.get("path"):
            try:
                vid = video_id_for(video["path"])
            except OSError:
                vid = None
        return metadata_from_dict(vid or "video", meta)
    return metadata_from_ffprobe(video)
