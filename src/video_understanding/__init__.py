"""模块二：视频理解（Video Understanding）。

需求驱动（task-oriented）的视频分析：消费模块一输出的 ``required_video_queries``，
将用户语言中的动作、手势、姿态、视频结构和音乐节点等语义概念，映射为带
What / When / Where（事件内容 / start-peak-end 时间 / 空间位置）的语义事件。

门面::

    from video_understanding import VideoUnderstanding

    vu = VideoUnderstanding()
    vu.analyze_video({"video_id": "v1", "metadata": {...}, "tracks": {...}})
    results = vu.resolve_queries([{"type": "event_detection", "event": "heart_gesture",
                                   "required_occurrence": {"type": "index", "value": 2}}])
    view = vu.build_semantic_view()

工程设计文档：docs/模块二-视频理解.md
"""

from .api import VideoUnderstanding
from .models import (
    AnalysisRecord,
    AudioAnalysis,
    Confidence,
    ConfidenceStatus,
    DenseSpatialTracks,
    Direction,
    DirectionLabel,
    FrameObservation,
    HandObservation,
    QueryResult,
    QueryStatus,
    Region,
    SemanticEvent,
    SemanticVideoState,
    SemanticView,
    SpatialSnapshot,
    TemporalSpan,
    Trajectory,
    TrajectoryPoint,
    VideoAnalysisResult,
    VideoMetadata,
)

__all__ = [
    "VideoUnderstanding",
    "AnalysisRecord",
    "AudioAnalysis",
    "Confidence",
    "ConfidenceStatus",
    "DenseSpatialTracks",
    "Direction",
    "DirectionLabel",
    "FrameObservation",
    "HandObservation",
    "QueryResult",
    "QueryStatus",
    "Region",
    "SemanticEvent",
    "SemanticVideoState",
    "SemanticView",
    "SpatialSnapshot",
    "TemporalSpan",
    "Trajectory",
    "TrajectoryPoint",
    "VideoAnalysisResult",
    "VideoMetadata",
]
