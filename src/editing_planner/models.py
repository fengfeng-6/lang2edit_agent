"""Public schema for module three (Editing Planner).

模块三把模块一的 ``EditingIntent`` 与模块二的 ``SemanticView`` 转换为
软件无关的两阶段剪辑计划（模块三设计文档 §10）：

- ``LogicalEditingPlan``：想实现什么（事件相对时间 + 语义空间 + 素材需求）；
- ``ResolvedEditingPlan``：素材返回后用当前素材与工具具体怎么实现。

所有模型兼容 Pydantic 1.10 与 2.x，序列化统一走模块一的
``model_dump`` / ``model_validate`` 兼容层。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, validator

from gesture_intent.models import (
    ConstraintLevel,
    EditingIntent,
    ObjectType,
    SemanticValue,
)
from video_understanding.models import MobilityProfile, SemanticView


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


Point2 = Tuple[float, float]
BBox = Tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# 枚举（§11-13/§25-30/§39-46）
# ---------------------------------------------------------------------------


class PlanOperation(str, Enum):
    """第一阶段 Operation 范围（§13）。

    MVP 重点支持前十二个；trim/split/remove_segment/speed_adjust 保留枚举
    但展开为 ``status=unsupported``，减少 Timeline Mapping 复杂度。
    """

    replace_background = "replace_background"
    add_overlay = "add_overlay"
    add_text = "add_text"
    add_effect = "add_effect"
    add_music = "add_music"
    replace_music = "replace_music"
    add_sound_effect = "add_sound_effect"
    freeze = "freeze"
    scale_adjust = "scale_adjust"
    position_adjust = "position_adjust"
    volume_adjust = "volume_adjust"
    track_overlay = "track_overlay"
    # 保留但不重点支持
    trim = "trim"
    split = "split"
    remove_segment = "remove_segment"
    speed_adjust = "speed_adjust"


RESERVED_OPERATIONS = {
    PlanOperation.trim,
    PlanOperation.split,
    PlanOperation.remove_segment,
    PlanOperation.speed_adjust,
}


class AnchorType(str, Enum):
    """时间锚点类型（§26）。"""

    semantic_event = "semantic_event"
    video_structure = "video_structure"
    source_time = "source_time"


class EventBoundary(str, Enum):
    start = "start"
    peak = "peak"
    end = "end"


class DurationMode(str, Enum):
    """Duration 支持模式（§27）。"""

    exact = "exact"
    preferred = "preferred"
    range = "range"
    event_span = "event_span"  # 跟随事件 [start,end] 跨度


class SpatialRelation(str, Enum):
    """第一阶段 Spatial Relation（§29），不实现复杂自由布局。"""

    centered_on = "centered_on"
    above = "above"
    below = "below"
    left_of = "left_of"
    right_of = "right_of"
    upper_left = "upper_left"
    upper_right = "upper_right"
    screen_left = "screen_left"
    screen_right = "screen_right"
    follow = "follow"


class SpatialAnchorType(str, Enum):
    event_anchor = "event_anchor"
    spatial_track = "spatial_track"
    structural = "structural"
    absolute = "absolute"


class TimelineEffect(str, Enum):
    """PlanItem 对时间线结构的影响（§39）。"""

    non_structural = "non_structural"
    insert_duration = "insert_duration"  # freeze 等：插入时长，后续源时间平移


class DegradationPolicy(str, Enum):
    """能力降级策略（§42）。"""

    strict = "strict"
    allow_equivalent = "allow_equivalent"
    allow_simplification = "allow_simplification"


class PlanItemStatus(str, Enum):
    planned = "planned"
    degraded = "degraded"  # 能力降级后仍可执行
    blocked = "blocked"  # hard 需求无法满足
    skipped = "skipped"  # soft 需求主动跳过
    unfulfilled = "unfulfilled"  # 无法产出可执行项（缺内容/缺素材等）
    pending_dependency = "pending_dependency"  # 等上游补数据（§46）
    unsupported = "unsupported"  # 超出 MVP Operation 范围


class ValidationStatus(str, Enum):
    """ValidationReport 总体状态（§45）。"""

    valid = "valid"
    valid_with_warnings = "valid_with_warnings"
    blocked = "blocked"
    needs_dependency = "needs_dependency"


class DependencyType(str, Enum):
    """PlannerDependencyRequest 类型（§46）：Planner 不直接调其他模块。"""

    video_analysis = "video_analysis"
    user_confirmation = "user_confirmation"
    asset = "asset"
    capability = "capability"


class ReusePolicy(str, Enum):
    """素材复用策略（§36）：默认每次同类事件用同一素材。"""

    reuse_same_asset = "reuse_same_asset"
    allow_variants = "allow_variants"


class PlannerMode(str, Enum):
    """创意规划来源（对应模块一 parser_mode 惯例）。"""

    rules = "rules"
    llm = "llm"
    rules_fallback = "rules_fallback"
    custom = "custom"


# ---------------------------------------------------------------------------
# 输入侧（§6-8/§40）
# ---------------------------------------------------------------------------


class LayoutPreferences(StrictModel):
    protect_face: bool = True
    protect_active_hands: bool = True
    protect_gesture_region: bool = True


class AccessibilityPlanningProfile(StrictModel):
    """Planner 使用的无障碍画像（§7）。

    由模块二 ``MobilityProfile`` 经 ``from_mobility_profile`` 转换，
    或由上层显式声明。``posture`` 与 ``active_hands`` 必须正交：
    坐姿不等于单手，单手也不等于坐姿。
    """

    posture: str = "standing"  # standing / seated / lying
    active_hands: List[str] = Field(default_factory=lambda: ["left", "right"])
    motion_amplitude: str = "normal"  # normal / low / very_low
    amplitude_scale: float = 1.0
    tremor: bool = False
    mirrored: bool = False
    inferred: bool = False
    primary_action_region: str = "full_body"  # upper_body / full_body
    preserve_mobility_device: bool = False
    layout_preferences: LayoutPreferences = Field(default_factory=LayoutPreferences)

    @validator("active_hands")
    def hands_valid(cls, value: List[str]) -> List[str]:
        return [h for h in value if h in ("left", "right")]

    @classmethod
    def from_mobility_profile(cls, profile: MobilityProfile) -> "AccessibilityPlanningProfile":
        """MobilityProfile → AccessibilityPlanningProfile（§7/§8）。

        seated 是保守默认：主体主要动作区域转为上半身，并默认保留
        轮椅等辅助设备区域——宁多保不少保（§23-24）。
        """
        seated = profile.posture == "seated"
        return cls(
            posture=profile.posture,
            active_hands=list(profile.available_hands),
            motion_amplitude=profile.amplitude,
            amplitude_scale=profile.amplitude_scale,
            tremor=profile.tremor,
            mirrored=profile.mirrored,
            inferred=profile.inferred,
            primary_action_region="upper_body" if seated else "full_body",
            preserve_mobility_device=seated,
        )


class ToolCapabilityProfile(StrictModel):
    """当前剪辑后端支持的能力集合（§40）。

    保守默认：tracking / mask / camera_motion 未声明即视为不支持，
    宁可降级也不虚构能力。
    """

    background_replacement: bool = True
    overlay: bool = True
    text: bool = True
    audio: bool = True
    freeze: bool = True
    keyframes: bool = True
    tracking: bool = False
    opacity: bool = True
    scale_animation: bool = True
    position_animation: bool = True
    # §24 数据前提：执行端能否消费前景主体分割结果
    person_mask: bool = False
    foreground_subject_mask: bool = False
    # 执行端镜头运动能力；accessibility validator 据此判定"不必要镜头运动"
    camera_motion: bool = False


class PlannerContext(StrictModel):
    """运行上下文：可覆盖的调优参数集中在 ``options``。"""

    run_label: Optional[str] = None
    debug: bool = False
    options: Dict[str, Any] = Field(default_factory=dict)
    # 常用 options 键（默认值见各消费处）：
    #   default_overlay_duration=0.8, avoid_iou_threshold=0.05,
    #   beat_snap_max_shift=0.20, follow_keyframe_density=1.0,
    #   tremor_keyframe_density=0.3, max_concurrent_overlays=3


class EditingPlannerInput(StrictModel):
    """模块总体输入（§6）。"""

    editing_intent: EditingIntent
    semantic_view: SemanticView
    accessibility_profile: Optional[
        Union[AccessibilityPlanningProfile, MobilityProfile]
    ] = None
    tool_capabilities: ToolCapabilityProfile = Field(default_factory=ToolCapabilityProfile)
    existing_plan: Optional["LogicalEditingPlan"] = None
    planner_context: PlannerContext = Field(default_factory=PlannerContext)


# ---------------------------------------------------------------------------
# 全局策略（§18）
# ---------------------------------------------------------------------------


class AccessibilityStrategy(StrictModel):
    """GlobalStrategy 内嵌的无障碍策略块（§18 示例结构）。"""

    subject_mode: str = "standing"
    primary_action_region: str = "full_body"
    gesture_emphasis: str = "normal"  # normal / high
    camera_motion_intensity: str = "low"  # low / low_to_medium / medium / high
    protect_active_hands: bool = True
    preserve_mobility_device: bool = False
    motion_amplitude_independent_energy: bool = True  # §3 不变量


class GlobalStrategy(StrictModel):
    """总体剪辑策略（§18）：global_intent + profile + video summary 的产物。"""

    visual_language: List[str] = Field(default_factory=list)
    motion_language: List[str] = Field(default_factory=list)
    pacing_strategy: str = "neutral"  # neutral / gesture_reactive / beat_driven
    background_strategy: str = "keep"  # keep / replacement
    music_strategy: Dict[str, Any] = Field(default_factory=dict)
    layout_strategy: str = "upper_body_aware"  # upper_body_aware / full_body_aware
    consistency_strategy: Dict[str, Any] = Field(default_factory=dict)
    accessibility_strategy: AccessibilityStrategy = Field(default_factory=AccessibilityStrategy)


# ---------------------------------------------------------------------------
# PlanItem 核心（§11-12/§25-31）
# ---------------------------------------------------------------------------


class TimeAnchor(StrictModel):
    """时间锚点（§26）：逻辑阶段保留事件相对引用，materialize 才落到秒。"""

    type: AnchorType
    event_uid: Optional[str] = None  # type=semantic_event
    structure_key: Optional[str] = None  # video_start / video_end / first_action / last_action
    boundary: EventBoundary = EventBoundary.peak
    time: Optional[float] = None  # type=source_time


class DurationSpec(StrictModel):
    """时长声明（§27）：exact / preferred / range / event_span。"""

    mode: DurationMode = DurationMode.preferred
    value: Optional[float] = None  # exact / preferred 的取值
    min: Optional[float] = None
    max: Optional[float] = None

    @validator("value", always=True)
    def value_positive(cls, value: Optional[float], values: Dict[str, Any]) -> Optional[float]:
        if values.get("mode") == DurationMode.exact and (value is None or value <= 0):
            raise ValueError("exact duration requires positive value")
        return value

    @validator("max")
    def range_ordered(cls, value: Optional[float], values: Dict[str, Any]) -> Optional[float]:
        minimum = values.get("min")
        if value is not None and minimum is not None and value < minimum:
            raise ValueError("duration max must be >= min")
        return value


class TemporalSpec(StrictModel):
    """事件相对时间规格（§25）。"""

    mode: str = "event_relative"  # event_relative / structural / absolute
    start_anchor: Optional[TimeAnchor] = None
    end_anchor: Optional[TimeAnchor] = None
    offset: float = 0.0  # 作用在解析出的 start 上（§25 示例 -0.1）
    end_offset: float = 0.0
    duration: DurationSpec = Field(default_factory=DurationSpec)
    source_time_hint: Optional[float] = None
    beat_snap: Optional[Dict[str, Any]] = None  # {priority, max_shift} §33


class SpatialAnchor(StrictModel):
    """空间锚点（§28/§30）：引用而非坐标，materialize 才解析。"""

    type: SpatialAnchorType
    event_uid: Optional[str] = None  # type=event_anchor
    target: Optional[str] = None  # type=spatial_track: head/face/person/left_hand/right_hand/…
    structure_key: Optional[str] = None  # type=structural: screen_center 等
    point: Optional[Point2] = None  # type=absolute


class FollowSpec(StrictModel):
    """跟随规格（§30-31）：Planner 声明策略，关键帧在 materialize 生成。"""

    enabled: bool = False
    mode: str = "trajectory"  # trajectory / tracking / keyframes（capability 决定）
    smoothing: str = "source_smoothed"  # none / source_smoothed / strong
    sensitivity: float = 1.0  # α：T_asset = F(T_subject, α)
    dead_zone: float = 0.01  # 归一化单位
    keyframe_density: float = 1.0  # 关键帧/秒 提示


class SpatialSpec(StrictModel):
    """空间规格（§28-29）。"""

    coordinate_space: str = "source_video_normalized"
    anchor: Optional[SpatialAnchor] = None
    relation: SpatialRelation = SpatialRelation.above
    offset_policy: Dict[str, Any] = Field(
        default_factory=lambda: {"mode": "relative", "distance": 0.08}
    )
    scale_policy: Dict[str, Any] = Field(
        default_factory=lambda: {"mode": "relative", "value": 0.16}
    )
    avoid_regions: List[str] = Field(default_factory=list)
    # face / active_gesture / active_hands / upper_torso / mobility_device / person
    follow: FollowSpec = Field(default_factory=FollowSpec)
    direction_policy: Dict[str, Any] = Field(default_factory=lambda: {"mode": "none"})
    fallback_positions: List[str] = Field(
        default_factory=lambda: ["upper_right", "upper_left", "right_of", "left_of", "below"]
    )


class StyleSpec(StrictModel):
    """创作决策输出（§19）：LLM 唯一可写的字段组。"""

    animation: Optional[str] = "pop"  # pop/soft_pop/fade/glow/flash/particle/float/none
    animation_params: Dict[str, Any] = Field(default_factory=dict)
    emphasis: str = "medium"  # low / medium / high
    palette: List[str] = Field(default_factory=list)
    duration_hint: Optional[float] = None
    relation_preference: Optional[str] = None  # SpatialRelation 值
    notes: List[str] = Field(default_factory=list)


class PlanItemProvenance(StrictModel):
    source_text: str = ""
    planner_stage: str = ""  # 最后触碰它的流水线阶段
    rules: List[str] = Field(default_factory=list)  # 确定性规则 id，如 "occurrence:index:2"


class PlanItem(StrictModel):
    """完整、原子、可独立修改的剪辑决策（§11）。

    ``plan_key`` = (requirement_id, event_uid, operation) 三元组字符串（§12），
    ``plan_item_uid`` = pln_ + sha1(plan_key)[:8]，局部重规划时保持稳定。
    """

    plan_item_uid: str
    plan_key: str
    operation: PlanOperation
    source_requirement_ids: List[str] = Field(default_factory=list)
    constraint_refs: List[str] = Field(default_factory=list)
    target: Dict[str, Any] = Field(default_factory=dict)
    # {type: event|track|video|audio_track|plan_item_ref, value: ...}
    temporal_spec: Optional[TemporalSpec] = None
    spatial_spec: Optional[SpatialSpec] = None
    asset_request_ref: Optional[str] = None
    style_spec: StyleSpec = Field(default_factory=StyleSpec)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeline_effect: TimelineEffect = TimelineEffect.non_structural
    capability_requirements: List[str] = Field(default_factory=list)
    resolved_capability: Optional[str] = None
    degradation_policy: DegradationPolicy = DegradationPolicy.allow_equivalent
    degradation_applied: List[str] = Field(default_factory=list)  # ["tracking→keyframes"]
    provenance: PlanItemProvenance = Field(default_factory=PlanItemProvenance)
    status: PlanItemStatus = PlanItemStatus.planned


# ---------------------------------------------------------------------------
# 素材需求（§34-37）
# ---------------------------------------------------------------------------


class AssetRequest(StrictModel):
    """模块三 → 模块四的素材需求（§34-36）。

    Planner 只产生语义需求，不产生具体素材文件；去重后同一需求只搜一次。
    """

    request_uid: str  # asset_req_<asset_type>_<NN>
    version: int = 1  # 需求修改升版本；模块四按 (uid, version) 管 Binding
    asset_type: str  # background / sticker / image / music / sound_effect（text 不外求 §35）
    media_type: str  # image / audio / video
    resolution_mode: Optional[str] = None  # exact_reference / semantic_search；None 由模块四推导
    source_ref: Optional[str] = None  # exact_reference 时的用户素材引用（文件名/序号/asset_uid）
    semantic_query: str = ""  # SemanticValue raw + canonical + tags 组装
    style_context: Dict[str, Any] = Field(default_factory=dict)
    technical_requirements: Dict[str, Any] = Field(default_factory=dict)
    usage_context: Dict[str, Any] = Field(default_factory=dict)
    reuse_policy: ReusePolicy = ReusePolicy.reuse_same_asset
    source_policy: str = "any"  # any / user_provided / generated（预留）
    licensing_policy: str = "default"
    search_strategy: Optional[str] = None  # first_satisfactory / best_available；None 按类型默认
    fallback_policy: str = "nearest_semantic"  # 给模块四的提示
    constraint_level: ConstraintLevel = ConstraintLevel.hard
    dedup_key: str = ""


class AssetBinding(StrictModel):
    """模块四返回的素材绑定（§37）；模块四不直接修改 Plan。"""

    asset_request_uid: str
    asset_uid: str
    uri: str = ""
    media_type: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    match_score: float = 0.0


# ---------------------------------------------------------------------------
# 校验与依赖（§43-46）
# ---------------------------------------------------------------------------


class ValidationIssue(StrictModel):
    code: str
    severity: str  # hard / warning / info
    message: str = ""
    item_uids: List[str] = Field(default_factory=list)
    requirement_ids: List[str] = Field(default_factory=list)


class SubReport(StrictModel):
    status: str = "pass"  # pass / warn / fail / skipped
    issues: List[ValidationIssue] = Field(default_factory=list)


class ValidationReport(StrictModel):
    """校验报告（§45）：矛盾 → blocked；纯数据缺口 → needs_dependency。"""

    status: ValidationStatus = ValidationStatus.valid
    semantic: SubReport = Field(default_factory=SubReport)
    temporal: SubReport = Field(default_factory=SubReport)
    spatial: SubReport = Field(default_factory=SubReport)
    capability: SubReport = Field(default_factory=SubReport)
    asset: SubReport = Field(default_factory=SubReport)
    accessibility: SubReport = Field(default_factory=SubReport)
    hard_violations: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)
    unresolved_dependencies: List[str] = Field(default_factory=list)  # dep uids


class PlannerDependencyRequest(StrictModel):
    """缺少上游信息时输出给 Agent Controller 的请求（§46）。

    Planner 本身不调用其他模块——Controller 据此再调 Module 2/4 或问用户。
    """

    request_uid: str  # dep_<NN>
    type: DependencyType
    required_queries: List[Dict[str, Any]] = Field(default_factory=list)
    reason: str = ""
    blocking: bool = True
    requirement_ids: List[str] = Field(default_factory=list)


class UnfulfilledRequirement(StrictModel):
    requirement_id: str
    reason: str = ""  # event_not_found / unsupported_operation / missing_content / …
    severity: str = "hard"
    status: str = "blocked"  # blocked / skipped / pending_dependency


# ---------------------------------------------------------------------------
# 计划实体（§38/§48-52）
# ---------------------------------------------------------------------------


class PlanProvenance(StrictModel):
    """缓存判断 / 局部重规划 / stale 检测 / debug 用（§49）。"""

    video_id: str = ""
    video_version: str = ""
    semantic_state_version: Optional[int] = None
    semantic_view_hash: str = ""
    intent_hash: str = ""
    planner_version: str = ""
    planner_mode: str = "rules"  # PlannerMode 值
    fallback_reason: Optional[str] = None


class TimelineTrack(StrictModel):
    name: str
    z_order: int
    item_uids: List[str] = Field(default_factory=list)


class TimelineStructure(StrictModel):
    """逻辑轨道（§38）：具体剪映轨道由模块五决定。"""

    tracks: List[TimelineTrack] = Field(default_factory=list)
    source_duration: float = 0.0


class LogicalEditingPlan(StrictModel):
    """想实现什么（§10/§48）。"""

    plan_uid: str
    version: int = 1
    provenance: PlanProvenance = Field(default_factory=PlanProvenance)
    global_strategy: GlobalStrategy = Field(default_factory=GlobalStrategy)
    accessibility_profile: AccessibilityPlanningProfile = Field(
        default_factory=AccessibilityPlanningProfile
    )
    plan_items: List[PlanItem] = Field(default_factory=list)
    asset_requests: List[AssetRequest] = Field(default_factory=list)
    timeline_structure: TimelineStructure = Field(default_factory=TimelineStructure)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    dependency_requests: List[PlannerDependencyRequest] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unresolved: List[UnfulfilledRequirement] = Field(default_factory=list)


class ProjectTime(StrictModel):
    start: float
    end: float


class Transform(StrictModel):
    position: Point2 = (0.5, 0.3)
    scale: float = 0.16
    rotation: float = 0.0


class AnimationSpec(StrictModel):
    type: str = "none"
    params: Dict[str, Any] = Field(default_factory=dict)


class ResolvedPlanItem(StrictModel):
    """素材返回后的可执行剪辑项（§51）：仍是软件无关表示。

    不出现 CapCut animation ID / Premiere API / FFmpeg 命令——那是模块五。
    """

    plan_item_uid: str
    plan_key: str = ""
    operation: PlanOperation
    asset_uid: Optional[str] = None
    project_time: ProjectTime
    transform: Optional[Transform] = None
    animation: AnimationSpec = Field(default_factory=AnimationSpec)
    follow: Optional[Dict[str, Any]] = None
    # {target, mode, smoothing, keyframes:[{t,x,y}] 或 trajectory_ref}
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source_requirement_ids: List[str] = Field(default_factory=list)
    degradation_applied: List[str] = Field(default_factory=list)
    status: PlanItemStatus = PlanItemStatus.planned
    warnings: List[str] = Field(default_factory=list)


class TimelineMapping(StrictModel):
    """源时间 → 工程时间的映射（freeze insert_duration 移位表）。"""

    tracks: List[TimelineTrack] = Field(default_factory=list)
    shifts: List[Dict[str, float]] = Field(default_factory=list)
    # [{from_source_time, delta}]：严格在其后的源时间整体平移
    total_duration: float = 0.0


class ResolvedEditingPlan(StrictModel):
    """用当前素材和当前工具具体怎么实现（§50）。"""

    plan_uid: str
    source_logical_plan_version: int = 1
    resolved_items: List[ResolvedPlanItem] = Field(default_factory=list)
    asset_bindings: List[AssetBinding] = Field(default_factory=list)
    timeline_mapping: TimelineMapping = Field(default_factory=TimelineMapping)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    warnings: List[str] = Field(default_factory=list)


class PlanPatch(StrictModel):
    """局部重规划补丁（§52）：requirement_id ↔ plan_item_uid 映射常驻。"""

    add_plan_items: List[PlanItem] = Field(default_factory=list)
    update_plan_items: List[PlanItem] = Field(default_factory=list)
    remove_plan_item_uids: List[str] = Field(default_factory=list)
    add_asset_requests: List[AssetRequest] = Field(default_factory=list)
    update_asset_requests: List[AssetRequest] = Field(default_factory=list)
    remove_asset_request_uids: List[str] = Field(default_factory=list)
    global_strategy_updates: Dict[str, Any] = Field(default_factory=dict)
    requires_rematerialization: bool = True


EditingPlannerInput.update_forward_refs()


def _stable_uid(prefix: str, *parts: Any, length: int = 8) -> str:
    """确定性 uid：与模块二 evt_/query_ 惯例一致（sha1 截断）。"""
    import hashlib
    import json

    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:length]}"


def short_hash(value: Any, length: int = 10) -> str:
    """模型/字典内容的 sha1 短哈希：replan 的需求级 diff 依据。"""
    import hashlib
    import json

    if isinstance(value, BaseModel):
        from gesture_intent.models import model_dump

        value = model_dump(value)
    return hashlib.sha1(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:length]
