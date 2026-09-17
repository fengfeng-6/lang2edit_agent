"""模块二数据模型。

对应设计文档（docs/模块二-视频理解.md）的核心 Schema：

- 空间原语（§9）：``Region`` / ``Point`` / ``Direction`` / ``Trajectory``，
  全部绑定 ``source_video_normalized`` 坐标系（左上角原点，x 向右 y 向下，[0,1]）。
- 稠密轨道（§10/§48）：``DenseSpatialTracks`` 为高频底层数据，属于
  analysis artifact，不直接写入 Semantic Video State。
- 语义事件（§17/§37-38）：``SemanticEvent`` 带 start/peak/end 三元时间、
  双 ID（稳定 ``event_uid`` + 时序 ``display_id``）、来源与置信度。
- 查询与状态（§40-44）：``AnalysisRecord`` / ``QueryStatus`` 严格区分
  ``not_found``（正常分析但没有目标）与 ``failed``（技术错误）。
- 语义状态（§36/§39/§50）：``SemanticVideoState`` 只存引用与摘要，
  Dense Data 以 artifact 形式单独管理。

兼容 Pydantic 1.10 与 2.x（复用模块一的 model_dump/model_validate 约定）。
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, validator

# 模块二作为下游可以 import 模块一的数据模型（README：模块间单向依赖）。
from gesture_intent.models import (
    EventType,
    Occurrence,
    OccurrenceType,
    RequiredVideoQuery,
    model_dump,
    model_validate,
)

__all__ = [
    "EventType",
    "Occurrence",
    "OccurrenceType",
    "RequiredVideoQuery",
    "model_dump",
    "model_validate",
]


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


Point2 = Tuple[float, float]
BBox = Tuple[float, float, float, float]


def normalize_vector(dx: float, dy: float) -> Point2:
    norm = math.hypot(dx, dy)
    if norm < 1e-9:
        return (0.0, 0.0)
    return (dx / norm, dy / norm)


def direction_label(dx: float, dy: float) -> "DirectionLabel":
    """连续方向向量 → 8 向离散标签（§9.3）。y 向下为正。"""
    ux, uy = normalize_vector(dx, dy)
    if ux == 0.0 and uy == 0.0:
        return DirectionLabel.down  # 无方向时的占位，调用方应自行判空
    angle = math.degrees(math.atan2(uy, ux))  # -180..180，0=右，90=下
    octants = [
        DirectionLabel.right,
        DirectionLabel.lower_right,
        DirectionLabel.down,
        DirectionLabel.lower_left,
        DirectionLabel.left,
        DirectionLabel.upper_left,
        DirectionLabel.up,
        DirectionLabel.upper_right,
    ]
    index = int((angle + 22.5) // 45) % 8
    return octants[index]


class DirectionLabel(str, Enum):
    left = "left"
    right = "right"
    up = "up"
    down = "down"
    upper_left = "upper_left"
    upper_right = "upper_right"
    lower_left = "lower_left"
    lower_right = "lower_right"


# ---------------------------------------------------------------------------
# 空间原语（§9）
# ---------------------------------------------------------------------------


class Region(StrictModel):
    """需要占据、引用或避让的矩形区域（x1,y1,x2,y2）。"""

    type: str = "region"
    bbox: BBox

    @validator("bbox")
    def bbox_ordered(cls, value: BBox) -> BBox:
        x1, y1, x2, y2 = value
        if x2 < x1 or y2 < y1:
            raise ValueError("bbox must satisfy x2>=x1 and y2>=y1")
        return value

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def center(self) -> Point2:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    def expand(self, margin: float) -> "Region":
        """Protected Region 构造：R_protected = Expand(R_face, m)（§29）。"""
        x1, y1, x2, y2 = self.bbox
        return Region(
            bbox=(
                _clamp01(x1 - margin),
                _clamp01(y1 - margin),
                _clamp01(x2 + margin),
                _clamp01(y2 + margin),
            )
        )

    def intersects(self, other: "Region") -> bool:
        ax1, ay1, ax2, ay2 = self.bbox
        bx1, by1, bx2, by2 = other.bbox
        return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


class Point(StrictModel):
    type: str = "point"
    position: Point2


class Direction(StrictModel):
    type: str = "direction"
    vector: Point2 = (0.0, 0.0)
    label: DirectionLabel = DirectionLabel.right

    @validator("vector")
    def vector_unit(cls, value: Point2) -> Point2:
        return normalize_vector(*value)

    @classmethod
    def from_delta(cls, dx: float, dy: float) -> "Direction":
        return cls(vector=normalize_vector(dx, dy), label=direction_label(dx, dy))


class TrajectoryPoint(StrictModel):
    t: float
    x: float
    y: float


class Trajectory(StrictModel):
    """持续性空间轨迹（§9.4）。对上层优先暴露 smoothed=True 的轨迹。"""

    type: str = "trajectory"
    target: str
    points: List[TrajectoryPoint] = Field(default_factory=list)
    smoothed: bool = False


# ---------------------------------------------------------------------------
# 稠密轨道（§10，analysis artifact）
# ---------------------------------------------------------------------------

# 身体关键点名与 MediaPipe PoseLandmarker 对齐；左右以人物自身左右为准
# （镜像自拍视频中由上游保证约定一致）。
BODY_KEYPOINTS = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


class HandObservation(StrictModel):
    """单手观测：中心点、包围盒，按需带 21 关键点 landmarks（§11 按需分析）。"""

    center: Optional[Point2] = None
    bbox: Optional[BBox] = None
    landmarks: Optional[Dict[str, Point2]] = None
    confidence: float = 1.0


class FrameObservation(StrictModel):
    """单时刻的稠密空间观测（§10 示例结构）。"""

    timestamp: float
    person_bbox: Optional[BBox] = None
    face_bbox: Optional[BBox] = None
    keypoints: Dict[str, Point2] = Field(default_factory=dict)
    hands: Dict[str, HandObservation] = Field(default_factory=dict)  # "left"/"right"

    def keypoint(self, name: str) -> Optional[Point2]:
        return self.keypoints.get(name)

    def head_center(self) -> Optional[Point2]:
        """头部中心：优先双耳中点，退化为 nose。"""
        left_ear = self.keypoints.get("left_ear")
        right_ear = self.keypoints.get("right_ear")
        if left_ear and right_ear:
            return ((left_ear[0] + right_ear[0]) / 2, (left_ear[1] + right_ear[1]) / 2)
        return self.keypoints.get("nose")

    def body_center(self) -> Optional[Point2]:
        """躯干中心：双肩双髋四点均值。"""
        names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
        pts = [self.keypoints[n] for n in names if n in self.keypoints]
        if not pts:
            return self.person_center()
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def person_center(self) -> Optional[Point2]:
        if self.person_bbox:
            return Region(bbox=self.person_bbox).center
        return None

    def hand_center(self, side: str) -> Optional[Point2]:
        """手部中心：优先 hand track，退化到对应手腕关键点。"""
        hand = self.hands.get(side)
        if hand and hand.center:
            return hand.center
        return self.keypoints.get(f"{side}_wrist")


class DenseSpatialTracks(StrictModel):
    """Person / Face / Pose / Hand 高频轨道（§10）。

    属于 Dense Analysis Data：作为 artifact 独立存储，Semantic Video State
    只保存其 artifact_id（§48）。
    """

    video_id: str
    fps: float = 30.0
    frames: List[FrameObservation] = Field(default_factory=list)
    artifact_id: Optional[str] = None
    producer: str = ""  # 产生轨道的分析器与版本，如 mediapipe_v1 / json_tracks_v1

    @validator("frames")
    def frames_sorted(cls, value: List[FrameObservation]) -> List[FrameObservation]:
        return sorted(value, key=lambda f: f.timestamp)

    def at(self, timestamp: float) -> Optional[FrameObservation]:
        """取最接近 timestamp 的帧（稠密采样下足够；不做线性插值以免虚构关键点）。"""
        if not self.frames:
            return None
        best = min(self.frames, key=lambda f: abs(f.timestamp - timestamp))
        return best

    def between(self, start: float, end: float) -> List[FrameObservation]:
        return [f for f in self.frames if start <= f.timestamp <= end]

    def series(self, accessor: str) -> List[Tuple[float, Point2]]:
        """提取某个空间目标的 (t, (x,y)) 序列。

        accessor: "head" / "body" / "person" / "left_hand" / "right_hand" /
        "left_wrist" 等关键点名。
        """
        out: List[Tuple[float, Point2]] = []
        for frame in self.frames:
            pt = self._resolve_point(frame, accessor)
            if pt is not None:
                out.append((frame.timestamp, pt))
        return out

    @staticmethod
    def _resolve_point(frame: FrameObservation, accessor: str) -> Optional[Point2]:
        if accessor == "head":
            return frame.head_center()
        if accessor == "body":
            return frame.body_center()
        if accessor == "person":
            return frame.person_center()
        if accessor == "face":
            return Region(bbox=frame.face_bbox).center if frame.face_bbox else None
        if accessor in ("left_hand", "right_hand"):
            return frame.hand_center(accessor.split("_")[0])
        return frame.keypoints.get(accessor)

    def trajectory(self, accessor: str, *, smooth_half_window: int = 3) -> Trajectory:
        """提取轨迹并做滑动平均平滑（§9.4：对上层暴露 Smooth(T)）。"""
        raw = self.series(accessor)
        if len(raw) < 2 * smooth_half_window + 1:
            points = [TrajectoryPoint(t=t, x=p[0], y=p[1]) for t, p in raw]
            return Trajectory(target=accessor, points=points, smoothed=False)
        smoothed: List[TrajectoryPoint] = []
        for i, (t, _) in enumerate(raw):
            lo = max(0, i - smooth_half_window)
            hi = min(len(raw), i + smooth_half_window + 1)
            xs = [raw[j][1][0] for j in range(lo, hi)]
            ys = [raw[j][1][1] for j in range(lo, hi)]
            smoothed.append(TrajectoryPoint(t=t, x=sum(xs) / len(xs), y=sum(ys) / len(ys)))
        return Trajectory(target=accessor, points=smoothed, smoothed=True)


# ---------------------------------------------------------------------------
# 视频元数据与预处理（§6）
# ---------------------------------------------------------------------------


class VideoMetadata(StrictModel):
    video_id: str
    duration: float
    fps: float
    width: int
    height: int
    aspect_ratio: Optional[str] = None
    codec: Optional[str] = None
    rotation: int = 0
    has_audio: bool = False
    audio_sample_rate: Optional[int] = None
    source_uri: Optional[str] = None
    version: str = "v1"

    @property
    def frame_count(self) -> int:
        return int(round(self.duration * self.fps))

    def time_of_frame(self, frame_index: int) -> float:
        """t_i = i / f（§6.1）。"""
        return frame_index / self.fps if self.fps else 0.0


# ---------------------------------------------------------------------------
# 语义事件（§17/§37）
# ---------------------------------------------------------------------------


class TemporalSpan(StrictModel):
    """E = (t_s, t_p, t_e)：开始 / 最典型 / 结束（§17）。"""

    start_time: float
    peak_time: float
    end_time: float

    @validator("peak_time")
    def peak_within(cls, value: float, values: Dict[str, Any]) -> float:
        start = values.get("start_time")
        if start is not None and value < start:
            raise ValueError("peak_time must be >= start_time")
        return value

    @validator("end_time")
    def end_after_peak(cls, value: float, values: Dict[str, Any]) -> float:
        peak = values.get("peak_time")
        if peak is not None and value < peak:
            raise ValueError("end_time must be >= peak_time")
        return value

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class ConfidenceStatus(str, Enum):
    confirmed = "confirmed"
    candidate = "candidate"
    uncertain = "uncertain"


class Confidence(StrictModel):
    """总体置信度 + 分来源置信度（§43），支持后续调试和重新验证。"""

    overall: float
    status: ConfidenceStatus = ConfidenceStatus.confirmed
    sources: Dict[str, float] = Field(default_factory=dict)

    @validator("overall")
    def overall_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence overall must be between 0 and 1")
        return value


class DetectorInfo(StrictModel):
    strategy: str  # dedicated_detector / pose_motion_rule / open_semantic / structural / audio_analyzer
    version: str = "unknown"


class SemanticEvent(StrictModel):
    """统一语义事件：视觉与音频事件同协议（§34/§37）。"""

    event_uid: str
    display_id: str = ""
    event_type: EventType
    canonical: str
    occurrence_index: Optional[int] = None
    source_query_ids: List[str] = Field(default_factory=list)
    temporal: TemporalSpan
    spatial_ref: Optional[str] = None  # SpatialSnapshot.spatial_id
    confidence: Confidence
    detector: DetectorInfo
    properties: Dict[str, Any] = Field(default_factory=dict)  # 如 direction_label
    invalidated: bool = False  # §47：失效不删除，只标记

    def anchor_time(self, relation: str) -> float:
        """Intent 时态关系 → 事件时间锚点（§18）。

        at→peak / during→start（区间由调用方取两端）/ before→start / after→end。
        """
        if relation in ("after_event",):
            return self.temporal.end_time
        if relation in ("before_event", "during_event", "from_event", "until_event"):
            return self.temporal.start_time
        return self.temporal.peak_time


# ---------------------------------------------------------------------------
# 事件级空间快照（§27-29）
# ---------------------------------------------------------------------------


class SpatialSnapshot(StrictModel):
    spatial_id: str
    timestamp: float
    person_bbox: Optional[BBox] = None
    face_bbox: Optional[BBox] = None
    head_center: Optional[Point2] = None
    left_hand: Optional[Point2] = None
    right_hand: Optional[Point2] = None
    event_anchor: Optional[Point2] = None
    direction: Optional[Direction] = None
    protected_regions: Dict[str, BBox] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 音频分析（§33-35）
# ---------------------------------------------------------------------------


class AudioAnalysis(StrictModel):
    """analyze_audio(asset_id) 的结果；原视频音频与外部音乐同协议（§35）。"""

    asset_id: str
    bpm: Optional[float] = None
    beats: List[float] = Field(default_factory=list)
    downbeats: List[float] = Field(default_factory=list)
    onsets: List[float] = Field(default_factory=list)
    analyzer: str = "unknown"
    confidence: float = 0.9


# ---------------------------------------------------------------------------
# 查询执行与状态（§40-44/§51）
# ---------------------------------------------------------------------------


class QueryStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    not_found = "not_found"  # 正常分析过，但视频中没有目标事件（§42）
    low_confidence = "low_confidence"  # 有候选但可信度不足
    failed = "failed"  # 模型/输入/依赖技术错误
    invalidated = "invalidated"  # 依赖变化导致历史结果失效


class AnalysisRecord(StrictModel):
    """required_video_query 的分析执行记录（§40）。"""

    query_id: str
    query: Dict[str, Any]
    status: QueryStatus = QueryStatus.pending
    result_refs: List[str] = Field(default_factory=list)  # event_uid / artifact_id
    strategy: Optional[str] = None
    model_version: Optional[str] = None
    source_video_version: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class AnalysisHistoryEntry(StrictModel):
    """分析历史（§51）：debug / 评估 / 缓存追踪 / 事件来源追踪。"""

    analysis_id: str
    query_id: str
    strategy: str
    outputs: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None


class StructuralRef(StrictModel):
    event_ref: Optional[str] = None
    confidence: float = 0.0


class StructuralState(StrictModel):
    """视频结构信息（§39）：video_start/end 为确定性信息，

    first_action/last_action 依赖动作分析结果。
    """

    video_start: float = 0.0
    video_end: float = 0.0
    first_action: Optional[StructuralRef] = None
    last_action: Optional[StructuralRef] = None


# ---------------------------------------------------------------------------
# Semantic Video State（§36/§50）
# ---------------------------------------------------------------------------


class SemanticVideoState(StrictModel):
    """视频语义状态：只存事件、引用与摘要，Dense Data 走 artifact（§48）。

    ``version`` 随每次分析演化递增，Planner 记录 based_on 版本以判断
    是否需要 Partial Replanning（§50）。
    """

    state_id: str
    version: int = 1
    video: VideoMetadata
    semantic_events: List[SemanticEvent] = Field(default_factory=list)
    spatial_snapshots: Dict[str, SpatialSnapshot] = Field(default_factory=dict)
    track_artifact_ids: List[str] = Field(default_factory=list)
    spatial_asset_refs: List[Dict[str, Any]] = Field(default_factory=list)  # §32 segmentation 等
    audio_state: Dict[str, AudioAnalysis] = Field(default_factory=dict)  # asset_id -> 分析
    structural_state: StructuralState = Field(default_factory=StructuralState)
    analysis_registry: List[AnalysisRecord] = Field(default_factory=list)
    analysis_history: List[AnalysisHistoryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 对外结果（§52-53）
# ---------------------------------------------------------------------------


class QueryResult(StrictModel):
    """单个 required_video_query 的解析结果。"""

    query_id: str
    status: QueryStatus
    strategy: Optional[str] = None
    events: List[SemanticEvent] = Field(default_factory=list)  # 该 canonical 的全部事件
    selected_event_uids: List[str] = Field(default_factory=list)  # occurrence 过滤后
    cache_hit: bool = False
    error: Optional[str] = None


class VideoAnalysisResult(StrictModel):
    """analyze_video() 的结果：基础 Metadata + Base Spatial State（§52）。"""

    video: VideoMetadata
    base_track_ref: Optional[str] = None
    state_version: int = 1
    warnings: List[str] = Field(default_factory=list)


class SemanticView(StrictModel):
    """为 Planner / LLM 构建的精简语义视图（§49）。"""

    video: Dict[str, Any] = Field(default_factory=dict)
    relevant_events: List[Dict[str, Any]] = Field(default_factory=list)
    spatial_summaries: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    audio_summary: Dict[str, Any] = Field(default_factory=dict)
    structural: Dict[str, Any] = Field(default_factory=dict)
    query_statuses: Dict[str, str] = Field(default_factory=dict)
