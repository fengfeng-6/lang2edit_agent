"""Small, deterministic canonicalization library for the first MVP."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import EventType, SemanticValue


GESTURE_SYNONYMS = {
    "比心": "heart_gesture",
    "做爱心": "heart_gesture",
    "摆成爱心": "heart_gesture",
    "手摆成一个心": "heart_gesture",
    "手摆成心": "heart_gesture",
    "爱心动作": "heart_gesture",
    "指向左": "point_left",
    "指向左侧": "point_left",
    "往左指": "point_left",
    "向左指": "point_left",
    "指向右": "point_right",
    "指向右侧": "point_right",
    "往右指": "point_right",
    "向右指": "point_right",
    "挥手": "wave_hand",
    "摆手": "wave_hand",
    "点赞": "thumbs_up",
    "比赞": "thumbs_up",
    "v手势": "v_sign",
    "v字手势": "v_sign",
    "ok手势": "ok_sign",
    "双手张开": "open_both_hands",
    "双手合拢": "close_both_hands",
}

BODY_SYNONYMS = {
    "转身": "turn_body",
    "转过去": "turn_body",
    "蹲下": "squat",
    "起身": "stand_up",
    "跳跃": "jump",
    "跳起来": "jump",
    "向左移动": "move_left",
    "向右移动": "move_right",
    "身体倾斜": "lean_body",
    "靠近镜头": "approach_camera",
    "最后一个动作": "last_action",
    "最后动作": "last_action",
    "结束动作": "ending_pose",
}

AUDIO_SYNONYMS = {
    "重拍": "downbeat",
    "音乐重拍": "downbeat",
    "鼓点": "beat",
    "节拍": "beat",
    "音乐开始": "music_onset",
    "副歌开始": "chorus_start",
}

TAG_SYNONYMS = {
    "夏日": "summer",
    "夏天": "summer",
    "海边": "beach",
    "沙滩": "beach",
    "动漫": "anime",
    "可爱": "cute",
    "萌": "cute",
    "青春": "youthful",
    "有活力": "energetic",
    "活泼": "lively",
    "欢快": "upbeat",
    "轻快": "light",
    "高级": "premium",
    "优雅": "elegant",
    "粉色": "pink",
    "蓝色": "blue",
    "小红书": "xiaohongshu",
    "简洁": "minimal",
    "梦幻": "dreamy",
}


def _find_canonical(text: str, mapping: dict[str, str]) -> Optional[str]:
    for source, canonical in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        if source.lower() in text.lower():
            return canonical
    return None


def canonicalize_gesture(text: str) -> Optional[str]:
    return _find_canonical(text, GESTURE_SYNONYMS)


def canonicalize_body_action(text: str) -> Optional[str]:
    return _find_canonical(text, BODY_SYNONYMS)


def canonicalize_audio_event(text: str) -> Optional[str]:
    return _find_canonical(text, AUDIO_SYNONYMS)


def tags_for(text: str) -> list[str]:
    tags: list[str] = []
    for source, tag in sorted(TAG_SYNONYMS.items(), key=lambda item: len(item[0]), reverse=True):
        if source.lower() in text.lower() and tag not in tags:
            tags.append(tag)
    if "爱心" in text or "心形" in text:
        tags.append("heart")
    if "星星" in text or "闪光" in text:
        tags.append("star")
    if "海星" in text:
        tags.append("starfish")
    if "贝壳" in text:
        tags.append("seashell")
    if "椰子树" in text or "椰子" in text:
        tags.append("palm_tree")
    return list(dict.fromkeys(tags))


def semantic_value(raw: str, *, kind: str = "general", confidence: Optional[float] = None) -> SemanticValue:
    canonical = None
    if kind == "gesture":
        canonical = canonicalize_gesture(raw)
    elif kind == "body_action":
        canonical = canonicalize_body_action(raw)
    elif kind == "audio_event":
        canonical = canonicalize_audio_event(raw)
    return SemanticValue(raw=raw.strip(), canonical=canonical, tags=tags_for(raw), confidence=confidence)


def event_from_text(raw: str) -> tuple[EventType, str | None, dict | None, float]:
    """Return event type, canonical name, pose condition and confidence."""
    gesture = canonicalize_gesture(raw)
    if gesture:
        return EventType.gesture, gesture, None, 0.97
    body = canonicalize_body_action(raw)
    if body:
        return EventType.body_action, body, None, 0.95
    audio = canonicalize_audio_event(raw)
    if audio:
        return EventType.audio_event, audio, None, 0.95
    if "手举到头顶" in raw or ("手" in raw and "头顶" in raw):
        return (
            EventType.pose_condition,
            None,
            {"subject": "hand", "relation": "above", "reference": "head"},
            0.9,
        )
    if "双手交叉" in raw and "胸前" in raw:
        return (
            EventType.pose_condition,
            None,
            {"subject": ["left_hand", "right_hand"], "relation": "crossed", "reference": "chest"},
            0.9,
        )
    return EventType.video_structure, _find_canonical(raw, BODY_SYNONYMS), None, 0.72


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_CLAUSE_SEPARATORS = frozenset("，。；;！？!?\n")

_QUOTE_CLOSERS = {
    '"': '"',
    "“": "”",
    "'": "'",
    "‘": "’",
    "「": "」",
    "『": "』",
    "《": "》",
}

# Action words marking that a clause carries a requirement, not just a trigger.
EVENT_ACTION_WORDS = ("出现", "弹出", "跳出来", "添加", "加一个", "加上", "加", "闪一下", "显示")

# A clause ending in one of these is an event trigger whose requirement lives
# in the next clause ("每次比心的时候，出现爱心" must stay one clause).
_TRIGGER_ENDINGS = (
    "的时候",
    "之前",
    "以前",
    "之后",
    "以后",
    "过程中",
    "全程",
    "开始",
    "结束",
    "时",
    "前",
    "后",
)

_EVENT_SOURCES = tuple(GESTURE_SYNONYMS) + tuple(BODY_SYNONYMS) + tuple(AUDIO_SYNONYMS)

# Words showing a clause already carries a requirement (event-bound action or
# explicit operation), so it is not a lone trigger waiting for the next clause.
_REQUIREMENT_WORDS = EVENT_ACTION_WORDS + (
    "定格", "剪掉", "删掉", "删除", "去掉", "移除",
    "缩小", "放大", "调低", "调小", "调大", "降低",
    "加快", "减慢", "替换", "换成", "改成", "换掉",
    "放上", "贴上",
)

# Bare trigger clauses like "每次比心" / "第二次比心" open a merge only when
# they start with a quantifier/temporal prefix; otherwise the trailing event
# word is more likely the object of a verb ("想做爱心" must not absorb "加星星").
_TRIGGER_PREFIXES = ("每", "第", "当", "从", "在", "到", "等")


def contains_event_trigger(text: str) -> bool:
    """Whether the text mentions a detectable event trigger."""
    return bool(
        canonicalize_gesture(text)
        or canonicalize_body_action(text)
        or canonicalize_audio_event(text)
        or "手举到头顶" in text
        or "双手交叉" in text
    )


def split_clauses(text: str) -> list[str]:
    """Split a request into clauses without breaking quotes or trigger pairs.

    Separators inside quoted spans ("..." 「...」 etc.) do not split, so quoted
    payloads like 文字："你好，世界" stay intact.  A clause that ends with an
    event trigger ("每次比心的时候" / "第二次比心") absorbs the next clause so
    the requirement stays attached to its trigger — unless the next clause is
    itself a complete event clause (trigger + action), which keeps its own
    trigger.
    """
    spans = quoted_spans(text)
    raw_parts: list[str] = []
    buffer: list[str] = []
    for index, char in enumerate(text):
        if char in _CLAUSE_SEPARATORS and not inside_spans(index, spans):
            raw_parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    raw_parts.append("".join(buffer))

    clauses: list[str] = []
    for part in raw_parts:
        part = normalize_spaces(part)
        if not part:
            continue
        if clauses and _needs_following_clause(clauses[-1]) and not _is_complete_event_clause(part):
            clauses[-1] += part
        else:
            clauses.append(part)
    return clauses


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) index spans covered by quote pairs."""
    spans: list[tuple[int, int]] = []
    start: Optional[int] = None
    closing: Optional[str] = None
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                spans.append((start, index + 1))
                start = None
                closing = None
            continue
        if char in _QUOTE_CLOSERS and not _is_apostrophe(text, index):
            start = index
            closing = _QUOTE_CLOSERS[char]
    return spans


def inside_spans(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < index < end - 1 for start, end in spans)


def _is_apostrophe(text: str, index: int) -> bool:
    """An ASCII ' between two letters is an apostrophe (don't), not a quote."""
    return (
        text[index] == "'"
        and 0 < index < len(text) - 1
        and text[index - 1].isalpha()
        and text[index + 1].isalpha()
    )


def _needs_following_clause(clause: str) -> bool:
    if any(word in clause for word in _REQUIREMENT_WORDS):
        return False
    if clause.endswith(_TRIGGER_ENDINGS):
        return True
    if not clause.startswith(_TRIGGER_PREFIXES):
        return False
    return clause.endswith(_EVENT_SOURCES) or contains_event_trigger(clause)


def _is_complete_event_clause(clause: str) -> bool:
    return contains_event_trigger(clause) and any(word in clause for word in _REQUIREMENT_WORDS)


def first_match(text: str, patterns: Iterable[str]) -> Optional[re.Match[str]]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match
    return None
