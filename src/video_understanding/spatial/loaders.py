"""稠密轨道的外部来源：JSON artifact 加载与空间分析器协议（§48）。

Dense Spatial Tracks 属于 analysis artifact：可以来自
- JSON artifact（测试 / 外部管线 / 缓存复现）；
- ``SpatialAnalyzer`` 实现（如 MediaPipe 适配器，见 mediapipe_backend.py）。

JSON artifact 结构::

    {
      "video_id": "video_001",
      "fps": 30.0,
      "producer": "mediapipe_v1",
      "frames": [
        {"timestamp": 0.0,
         "person_bbox": [0.28, 0.10, 0.74, 0.93],
         "face_bbox": [0.43, 0.12, 0.59, 0.27],
         "keypoints": {"left_wrist": [0.46, 0.38], ...},
         "hands": {"left": {"center": [0.46, 0.38], "landmarks": {...}}}}
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, Union

from ..models import DenseSpatialTracks, VideoMetadata, model_validate


class SpatialAnalyzer(Protocol):
    """Base Spatial Analysis 生产者协议（§11）。

    实现负责输出 person/face/pose/hands 稠密轨道；轨道本身是
    Dense Data，由调用方决定如何落盘成 artifact。
    """

    name: str

    def available(self) -> bool: ...

    def analyze(self, video: "VideoSource") -> DenseSpatialTracks: ...


# analyze_video 的输入：路径 / 元数据+轨道字典 / 已构造的 VideoMetadata
VideoSource = Union[str, Path, dict, VideoMetadata]


def load_tracks(payload: Union[str, Path, dict, DenseSpatialTracks], *, video_id: str = "") -> DenseSpatialTracks:
    """从 JSON artifact / dict / 已构造对象加载 DenseSpatialTracks。"""
    if isinstance(payload, DenseSpatialTracks):
        return payload
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    if video_id and not payload.get("video_id"):
        payload = {**payload, "video_id": video_id}
    return model_validate(DenseSpatialTracks, payload)


class JsonTracksAnalyzer:
    """把随输入携带的 tracks（dict / JSON 路径 / DenseSpatialTracks）当作分析结果。

    用于测试、离线管线与缓存复现：调用方在 video 描述里给出
    ``tracks`` 字段或 ``tracks_path`` 即可。
    """

    name = "json_tracks_v1"

    def __init__(self, payload: Union[str, Path, dict, DenseSpatialTracks]):
        self.payload = payload

    def available(self) -> bool:
        return True

    def analyze(self, video: VideoSource) -> DenseSpatialTracks:
        video_id = video.video_id if isinstance(video, VideoMetadata) else ""
        tracks = load_tracks(self.payload, video_id=video_id)
        tracks.producer = self.name
        return tracks
