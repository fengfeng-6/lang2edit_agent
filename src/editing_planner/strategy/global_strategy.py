"""Global Strategy Planner（§18）：总体剪辑策略的确定性部分。

输入 global_intent + AccessibilityPlanningProfile + video summary，
输出 ``GlobalStrategy``。创作类细节（视觉语言措辞、动画语义）由
``strategy/creative.py`` 的 CreativePlanner 补充——本文件只放确定性映射。
"""

from __future__ import annotations

from typing import Any, Dict, List

from gesture_intent.models import EditingIntent, ObjectAction, ObjectType

from ..models import AccessibilityPlanningProfile, AccessibilityStrategy, GlobalStrategy


def _tags_of(value: Any) -> List[str]:
    """SemanticValue → 规范标签列表（canonical + tags）。"""
    if value is None:
        return []
    out: List[str] = []
    canonical = getattr(value, "canonical", None)
    if canonical:
        out.append(canonical)
    out.extend(t for t in getattr(value, "tags", []) or [] if t not in out)
    if not out and getattr(value, "raw", None):
        out.append(value.raw)
    return out


def build_global_strategy(
    intent: EditingIntent,
    profile: AccessibilityPlanningProfile,
    video_summary: Dict[str, Any],
) -> GlobalStrategy:
    """global_intent + profile + video summary → GlobalStrategy（§18）。"""
    g = intent.global_intent

    visual_language: List[str] = []
    for field_name in ("theme", "mood", "style", "color_preference"):
        for tag in _tags_of(getattr(g, field_name)):
            if tag not in visual_language:
                visual_language.append(tag)

    pacing_strategy = "neutral"
    pacing = g.pacing
    if pacing:
        raw = pacing.raw or ""
        canonical = pacing.canonical or ""
        if any(k in raw for k in ("快", "节奏", "卡点")) or canonical in ("fast", "beat"):
            pacing_strategy = "beat_driven" if "拍" in raw or "卡" in raw else "gesture_reactive"
        elif any(k in raw for k in ("舒缓", "慢", "柔和")):
            pacing_strategy = "gesture_reactive"

    music_req = next(
        (
            r
            for r in intent.object_requirements
            if r.object_type == ObjectType.music
        ),
        None,
    )
    music_strategy: Dict[str, Any] = {}
    if music_req is not None:
        music_strategy = {
            "mode": "replace" if music_req.action == ObjectAction.replace else "add",
            "beat_sync": True,
            "volume": "bed",
        }
        if pacing_strategy == "neutral":
            pacing_strategy = "gesture_reactive"

    background_strategy = "keep"
    if any(
        r.object_type == ObjectType.background
        and r.action in (ObjectAction.add, ObjectAction.replace, ObjectAction.update)
        for r in intent.object_requirements
    ):
        background_strategy = "replacement"

    seated = profile.posture == "seated"
    low_amp = profile.motion_amplitude in ("low", "very_low")

    accessibility_strategy = AccessibilityStrategy(
        subject_mode=profile.posture,
        primary_action_region=profile.primary_action_region,
        gesture_emphasis="high" if (seated or low_amp) else "normal",
        camera_motion_intensity="low" if low_amp or profile.tremor else "low_to_medium",
        protect_active_hands=profile.layout_preferences.protect_active_hands,
        preserve_mobility_device=profile.preserve_mobility_device,
        motion_amplitude_independent_energy=True,
    )

    return GlobalStrategy(
        visual_language=visual_language,
        motion_language=[],
        pacing_strategy=pacing_strategy,
        background_strategy=background_strategy,
        music_strategy=music_strategy,
        layout_strategy="upper_body_aware" if seated else "full_body_aware",
        consistency_strategy={"palette": [t for t in _tags_of(g.color_preference)]},
        accessibility_strategy=accessibility_strategy,
    )
