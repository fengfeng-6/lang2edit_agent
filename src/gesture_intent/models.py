"""Public schema for module one.

The models intentionally keep creative language in ``raw`` while exposing
canonical names and tags for downstream modules.  The code is compatible
with both Pydantic 1.10 and Pydantic 2.x so the package can run in the
workspace without requiring a network install.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        validate_assignment = True


class RequestType(str, Enum):
    initial_edit = "initial_edit"
    revision = "revision"
    addition = "addition"
    removal = "removal"


class ConstraintLevel(str, Enum):
    hard = "hard"
    soft = "soft"
    open = "open"


class ObjectType(str, Enum):
    background = "background"
    music = "music"
    image = "image"
    sticker = "sticker"
    text = "text"
    effect = "effect"
    sound_effect = "sound_effect"
    overlay = "overlay"


class ObjectAction(str, Enum):
    add = "add"
    replace = "replace"
    update = "update"
    remove = "remove"


class EventType(str, Enum):
    gesture = "gesture"
    body_action = "body_action"
    pose_condition = "pose_condition"
    video_structure = "video_structure"
    audio_event = "audio_event"


class OccurrenceType(str, Enum):
    first = "first"
    last = "last"
    all = "all"
    index = "index"
    range = "range"


class TemporalRelation(str, Enum):
    before_event = "before_event"
    at_event = "at_event"
    during_event = "during_event"
    after_event = "after_event"
    from_event = "from_event"
    until_event = "until_event"
    between_events = "between_events"


class OperationType(str, Enum):
    freeze = "freeze"
    trim = "trim"
    split = "split"
    remove = "remove"
    scale_adjust = "scale_adjust"
    position_adjust = "position_adjust"
    volume_adjust = "volume_adjust"
    speed_adjust = "speed_adjust"
    replace_text = "replace_text"
    replace_asset = "replace_asset"


class SemanticValue(StrictModel):
    raw: str
    canonical: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None

    @validator("confidence")
    def confidence_in_range(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class Autonomy(StrictModel):
    default: ConstraintLevel = ConstraintLevel.soft
    protected_requirements: List[str] = Field(default_factory=list)


class GlobalIntent(StrictModel):
    theme: Optional[SemanticValue] = None
    mood: Optional[SemanticValue] = None
    style: Optional[SemanticValue] = None
    pacing: Optional[SemanticValue] = None
    color_preference: Optional[SemanticValue] = None
    platform_style: Optional[str] = None
    autonomy: Optional[Autonomy] = None


class ObjectRequirement(StrictModel):
    id: str
    object_type: ObjectType
    action: ObjectAction
    description: Optional[SemanticValue] = None
    content: Optional[str] = None
    target_ref: Optional[str] = None
    source_text: str
    constraint_level: ConstraintLevel = ConstraintLevel.hard
    confidence: float = 0.9
    field_confidence: Dict[str, float] = Field(default_factory=dict)

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class EventReference(StrictModel):
    type: EventType
    raw: str
    canonical: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    confidence: float = 0.9

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class Occurrence(StrictModel):
    type: OccurrenceType
    value: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None

    @validator("value", "start", "end")
    def occurrence_numbers_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("occurrence numbers must be positive")
        return value


class EventTrigger(StrictModel):
    event: EventReference
    occurrence: Occurrence = Field(default_factory=lambda: Occurrence(type=OccurrenceType.all))
    temporal_relation: TemporalRelation = TemporalRelation.at_event


class EventRequirement(StrictModel):
    object_type: ObjectType
    action: ObjectAction = ObjectAction.add
    semantic_description: Optional[SemanticValue] = None
    content: Optional[str] = None
    operation: Optional[OperationType] = None


class EventBoundRequirement(StrictModel):
    id: str
    source_text: str
    trigger: EventTrigger
    requirement: EventRequirement
    constraint_level: ConstraintLevel = ConstraintLevel.hard
    confidence: float = 0.9
    field_confidence: Dict[str, float] = Field(default_factory=dict)

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class TargetReference(StrictModel):
    type: str
    value: str


class ExplicitOperation(StrictModel):
    id: str
    operation: OperationType
    target: TargetReference
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source_text: str
    constraint_level: ConstraintLevel = ConstraintLevel.hard
    confidence: float = 0.9

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class Constraint(StrictModel):
    id: str
    scope: Dict[str, Any] = Field(default_factory=dict)
    type: str
    reference: Optional[str] = None
    preference: Optional[SemanticValue] = None
    raw: str
    constraint_level: ConstraintLevel = ConstraintLevel.hard
    confidence: float = 0.9

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @property
    def source_text(self) -> str:
        """Uniform traceability accessor shared by all requirement types."""
        return self.raw


class EditingIntent(StrictModel):
    global_intent: GlobalIntent = Field(default_factory=GlobalIntent)
    object_requirements: List[ObjectRequirement] = Field(default_factory=list)
    event_bound_requirements: List[EventBoundRequirement] = Field(default_factory=list)
    explicit_operations: List[ExplicitOperation] = Field(default_factory=list)
    constraints: List[Constraint] = Field(default_factory=list)
    unresolved: List["UnresolvedReference"] = Field(default_factory=list)


class PatchAction(str, Enum):
    add = "add"
    update = "update"
    remove = "remove"


class IntentPatch(StrictModel):
    add_object_requirements: List[ObjectRequirement] = Field(default_factory=list)
    update_object_requirements: List[ObjectRequirement] = Field(default_factory=list)
    remove_object_requirement_ids: List[str] = Field(default_factory=list)
    add_event_bound_requirements: List[EventBoundRequirement] = Field(default_factory=list)
    update_event_bound_requirements: List[EventBoundRequirement] = Field(default_factory=list)
    remove_event_bound_requirement_ids: List[str] = Field(default_factory=list)
    add_operations: List[ExplicitOperation] = Field(default_factory=list)
    update_operations: List[ExplicitOperation] = Field(default_factory=list)
    remove_operation_ids: List[str] = Field(default_factory=list)
    add_constraints: List[Constraint] = Field(default_factory=list)
    update_constraints: List[Constraint] = Field(default_factory=list)
    remove_constraint_ids: List[str] = Field(default_factory=list)
    global_updates: Dict[str, Any] = Field(default_factory=dict)
    affected_objects: List[str] = Field(default_factory=list)


class UnresolvedReference(StrictModel):
    type: str
    raw: str
    candidates: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: Optional[str] = None

    @validator("confidence")
    def confidence_in_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class ResolvedReference(StrictModel):
    type: str
    raw: str
    target_id: str
    confidence: float = 1.0


class Conflict(StrictModel):
    requirements: List[str]
    type: str
    severity: str
    source_texts: List[str] = Field(default_factory=list)


class RequiredVideoQuery(StrictModel):
    type: str
    event: Optional[str] = None
    required_occurrence: Optional[Occurrence] = None
    condition: Optional[Dict[str, Any]] = None
    reference: Optional[str] = None


class RequestContext(StrictModel):
    request_stage: Optional[str] = None
    conversation_context: List[Dict[str, Any]] = Field(default_factory=list)


class SemanticProjectObject(StrictModel):
    id: str
    object_type: ObjectType
    description: Optional[str] = None
    event_ref: Optional[str] = None
    order: Optional[int] = None
    aliases: List[str] = Field(default_factory=list)


class SemanticProjectView(StrictModel):
    objects: List[SemanticProjectObject] = Field(default_factory=list)


class SemanticVideoEvent(StrictModel):
    event_id: str
    event_type: EventType
    canonical: Optional[str] = None
    start_time: Optional[float] = None
    peak_time: Optional[float] = None
    end_time: Optional[float] = None
    confidence: float = 0.9


class SemanticVideoView(StrictModel):
    events: List[SemanticVideoEvent] = Field(default_factory=list)


class IntentParserInput(StrictModel):
    user_utterance: str
    request_context: RequestContext = Field(default_factory=RequestContext)
    current_effective_intent: Optional[EditingIntent] = None
    semantic_project_view: SemanticProjectView = Field(default_factory=SemanticProjectView)
    semantic_video_view: SemanticVideoView = Field(default_factory=SemanticVideoView)

    @validator("user_utterance")
    def utterance_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("user_utterance must not be empty")
        return value.strip()


class IntentParserOutput(StrictModel):
    request_type: RequestType
    parser_mode: str = "rules"
    fallback_reason: Optional[str] = None
    editing_intent: Optional[EditingIntent] = None
    intent_patch: Optional[IntentPatch] = None
    normalized_semantics: List[Dict[str, Any]] = Field(default_factory=list)
    resolved_references: List[ResolvedReference] = Field(default_factory=list)
    required_video_queries: List[RequiredVideoQuery] = Field(default_factory=list)
    affected_objects: List[str] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    unresolved: List[UnresolvedReference] = Field(default_factory=list)
    confidence: Dict[str, float] = Field(default_factory=dict)


EditingIntent.update_forward_refs()


def model_dump(model: BaseModel) -> Dict[str, Any]:
    """Return a JSON-compatible dict across Pydantic major versions."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")  # type: ignore[attr-defined]
    # Pydantic 1.x leaves Enum instances in ``dict()``; its JSON serializer
    # already knows how to turn them into the wire representation.
    import json

    return json.loads(model.json())


def model_validate(model_type: Any, value: Any) -> Any:
    """Validate a model across Pydantic major versions."""
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(value)
    return model_type.parse_obj(value)
