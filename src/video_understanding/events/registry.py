"""Semantic Event Registry（§26）：系统认识哪些标准事件、如何检测。

每条记录是能力配置而非分析结果：canonical 名、事件类型、策略、
检测器、事件级 Temporal Config（§20）、依赖的底层轨道。

canonical 名以模块一 canonicalizer 的输出为准（heart_gesture / wave_hand /
open_both_hands …）；设计文档中的叫法（wave / hands_open …）收在 aliases。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models import EventType
from .aggregator import TemporalConfig


@dataclass(frozen=True)
class EventRegistryEntry:
    canonical: str
    event_type: EventType
    supported: bool
    strategy: str  # dedicated_detector / pose_motion_rule / audio_analyzer / structural / open_semantic
    detector: Optional[str] = None  # detectors.py 中的 scorer 名
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    dependencies: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)


def _gesture(canonical: str, detector: Optional[str], *, supported: bool = True,
             deps: Optional[List[str]] = None, aliases: Optional[List[str]] = None,
             **kw) -> EventRegistryEntry:
    return EventRegistryEntry(
        canonical=canonical, event_type=EventType.gesture, supported=supported,
        strategy="dedicated_detector", detector=detector,
        temporal=TemporalConfig(**kw),
        dependencies=deps or ["pose_track"], aliases=aliases or [],
    )


def _body(canonical: str, detector: Optional[str], *, supported: bool = True,
          aliases: Optional[List[str]] = None, **kw) -> EventRegistryEntry:
    return EventRegistryEntry(
        canonical=canonical, event_type=EventType.body_action, supported=supported,
        strategy="dedicated_detector", detector=detector,
        temporal=TemporalConfig(**kw),
        dependencies=["pose_track"], aliases=aliases or [],
    )


_REGISTRY: Dict[str, EventRegistryEntry] = {e.canonical: e for e in [
    # ---- Gesture（§15/§59 MVP 手势集）----
    _gesture("heart_gesture", "heart_gesture",
             min_duration=0.15, merge_gap=0.20, threshold=0.5, candidate_threshold=0.3,
             aliases=["heart"]),
    _gesture("point_left", "point_left", min_duration=0.12, merge_gap=0.15),
    _gesture("point_right", "point_right", min_duration=0.12, merge_gap=0.15),
    _gesture("wave_hand", "wave_hand", min_duration=0.30, merge_gap=0.10,
             aliases=["wave"]),
    _gesture("open_both_hands", "open_both_hands", min_duration=0.15, merge_gap=0.15,
             aliases=["hands_open"]),
    _gesture("close_both_hands", "close_both_hands", min_duration=0.15, merge_gap=0.15,
             aliases=["hands_close", "hands_together"]),
    # 需要 hand landmarks（§11 按需分析）：轨道缺失时 query 记 failed。
    _gesture("thumbs_up", "thumbs_up", deps=["hand_landmark_track"],
             min_duration=0.15, merge_gap=0.15, aliases=["thumb_up"]),
    _gesture("v_sign", "v_sign", deps=["hand_landmark_track"],
             min_duration=0.15, merge_gap=0.15, aliases=["victory"]),
    _gesture("ok_sign", "ok_sign", deps=["hand_landmark_track"],
             min_duration=0.15, merge_gap=0.15, aliases=["ok"]),
    # ---- Body Action（§15/§59）----
    _body("turn_body", "turn_body", min_duration=0.25, merge_gap=0.30),
    _body("move_left", "move_left", min_duration=0.20, merge_gap=0.20),
    _body("move_right", "move_right", min_duration=0.20, merge_gap=0.20),
    _body("jump", "jump", min_duration=0.10, merge_gap=0.25),
    _body("squat", "squat", min_duration=0.25, merge_gap=0.30),
    _body("stand_up", "stand_up", min_duration=0.20, merge_gap=0.30),
    _body("lean_body", "lean_body", min_duration=0.25, merge_gap=0.30),
    _body("approach_camera", "approach_camera", min_duration=0.30, merge_gap=0.30),
    _body("ending_pose", "ending_pose", min_duration=0.30, merge_gap=0.20,
          threshold=0.45, candidate_threshold=0.25),
]}


# 结构化事件（§39）：由 StructuralResolver 处理，不走逐帧检测。
_STRUCTURAL = {"video_start", "video_end", "first_action", "last_action"}

# 音频事件（§33-35）：由 AudioAnalyzer 产物物化。
_AUDIO = {
    "beat": TemporalConfig(min_duration=0.0, merge_gap=0.0),
    "downbeat": TemporalConfig(min_duration=0.0, merge_gap=0.0),
    "music_onset": TemporalConfig(min_duration=0.0, merge_gap=0.0),
}

# 设计文档叫法 → canonical（模块一输出优先，见上文 aliases）。
_ALIASES: Dict[str, str] = {
    alias: entry.canonical
    for entry in _REGISTRY.values()
    for alias in entry.aliases
}


def resolve_canonical(name: str) -> str:
    """别名归一：wave → wave_hand 等。"""
    return _ALIASES.get(name, name)


def lookup(canonical: str) -> Optional[EventRegistryEntry]:
    return _REGISTRY.get(resolve_canonical(canonical))


def is_structural(canonical: str) -> bool:
    return resolve_canonical(canonical) in _STRUCTURAL


def is_audio(canonical: str) -> bool:
    return resolve_canonical(canonical) in _AUDIO


def audio_config(canonical: str) -> TemporalConfig:
    return _AUDIO.get(resolve_canonical(canonical), TemporalConfig())


def registered_events() -> List[str]:
    return sorted(_REGISTRY)
