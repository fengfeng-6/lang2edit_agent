"""LLM adapter and deterministic Chinese MVP extractor."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol
from urllib import error, request

from .canonicalizer import (
    canonicalize_gesture,
    event_from_text,
    first_match,
    semantic_value,
    split_clauses,
    tags_for,
)
from .models import (
    Autonomy,
    Constraint,
    ConstraintLevel,
    EditingIntent,
    EventBoundRequirement,
    EventReference,
    EventRequirement,
    EventTrigger,
    EventType,
    ExplicitOperation,
    GlobalIntent,
    IntentParserInput,
    IntentPatch,
    ObjectAction,
    ObjectRequirement,
    ObjectType,
    Occurrence,
    OccurrenceType,
    OperationType,
    RequestType,
    TargetReference,
    model_dump,
)


class StructuredIntentExtractor(Protocol):
    def extract(self, input: IntentParserInput, request_type: RequestType) -> dict[str, Any]:
        ...


@dataclass
class IdAllocator:
    counters: dict[str, int]

    @classmethod
    def from_input(cls, input: IntentParserInput) -> "IdAllocator":
        counters: dict[str, int] = defaultdict(int)
        intent = input.current_effective_intent
        if intent:
            for item in intent.object_requirements:
                counters["req_" + item.object_type.value] = max(
                    counters["req_" + item.object_type.value], _id_number(item.id)
                )
            counters["event_req"] = max([counters["event_req"]] + [_id_number(x.id) for x in intent.event_bound_requirements])
            counters["operation"] = max([counters["operation"]] + [_id_number(x.id) for x in intent.explicit_operations])
            counters["constraint"] = max([counters["constraint"]] + [_id_number(x.id) for x in intent.constraints])
        return cls(dict(counters))

    def next(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}_{self.counters[prefix]:02d}"


def _id_number(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 0


class RuleBasedExtractor:
    """Deterministic parser for the design document's first-stage language."""

    def extract(self, input: IntentParserInput, request_type: RequestType) -> dict[str, Any]:
        allocator = IdAllocator.from_input(input)
        if request_type == RequestType.initial_edit:
            intent = self._parse_intent(input.user_utterance, allocator)
            return {"request_type": request_type, "editing_intent": intent}
        patch = self._parse_patch(input.user_utterance, allocator)
        return {"request_type": request_type, "intent_patch": patch}

    def _parse_intent(self, text: str, allocator: IdAllocator) -> EditingIntent:
        global_intent = self._parse_global(text)
        objects: list[ObjectRequirement] = []
        event_requirements: list[EventBoundRequirement] = []
        operations: list[ExplicitOperation] = []
        constraints = self._parse_constraints(text, allocator)
        clauses = split_clauses(text)

        for clause in clauses:
            event_requirement = self._parse_event_requirement(clause, allocator)
            if event_requirement:
                event_requirements.append(event_requirement)
            object_requirements = self._parse_object_requirements(clause, allocator)
            objects.extend(object_requirements)
            operation = self._parse_explicit_operation(clause, allocator)
            if operation:
                operations.append(operation)

        if not objects:
            objects.extend(self._parse_object_requirements(text, allocator))
        if not operations:
            operation = self._parse_explicit_operation(text, allocator)
            if operation:
                operations.append(operation)

        if global_intent.autonomy and global_intent.autonomy.default == ConstraintLevel.open:
            protected = [item.id for item in objects if item.constraint_level == ConstraintLevel.hard]
            global_intent.autonomy.protected_requirements = protected

        return EditingIntent(
            global_intent=global_intent,
            object_requirements=_dedupe_by_id(objects),
            event_bound_requirements=_dedupe_by_id(event_requirements),
            explicit_operations=_dedupe_by_id(operations),
            constraints=_dedupe_by_id(constraints),
        )

    def _parse_patch(self, text: str, allocator: IdAllocator) -> IntentPatch:
        objects: list[ObjectRequirement] = []
        event_requirements: list[EventBoundRequirement] = []
        operations: list[ExplicitOperation] = []
        constraints = self._parse_constraints(text, allocator)
        for clause in split_clauses(text):
            event_requirement = self._parse_event_requirement(clause, allocator)
            if event_requirement:
                event_requirements.append(event_requirement)
            objects.extend(self._parse_object_requirements(clause, allocator))
            operation = self._parse_explicit_operation(clause, allocator)
            if operation:
                operations.append(operation)

        if not objects and not operations and not event_requirements:
            objects.extend(self._parse_object_requirements(text, allocator))
            operation = self._parse_explicit_operation(text, allocator)
            if operation:
                operations.append(operation)

        add_objects = [item for item in objects if item.action in {ObjectAction.add, ObjectAction.replace}]
        remove_ids: list[str] = []
        if any(item.action == ObjectAction.remove for item in objects):
            remove_ids = [item.target_ref for item in objects if item.target_ref and item.action == ObjectAction.remove]
            objects = [item for item in objects if item.action != ObjectAction.remove]

        global_updates: dict[str, Any] = {}
        if any(marker in text for marker in ("整体", "风格", "做成", "画面")):
            parsed_global = self._parse_global(text)
            for field in ("theme", "mood", "style", "pacing", "color_preference", "platform_style", "autonomy"):
                value = getattr(parsed_global, field)
                if value is not None:
                    global_updates[field] = model_dump(value) if hasattr(value, "dict") else value

        return IntentPatch(
            add_object_requirements=add_objects,
            add_event_bound_requirements=event_requirements,
            add_operations=operations,
            add_constraints=constraints,
            remove_object_requirement_ids=remove_ids,
            global_updates=global_updates,
        )

    def _parse_global(self, text: str) -> GlobalIntent:
        theme_words = [word for word in ("夏日", "夏天", "海边", "沙滩", "动漫") if word in text]
        mood_words = [word for word in ("青春", "有活力", "活泼", "欢快", "轻快") if word in text]
        style_words = [word for word in ("可爱", "小红书", "高级", "优雅", "梦幻") if word in text]
        color_words = [word for word in ("粉色", "蓝色") if word in text]
        pacing_words = [word for word in ("节奏快一点", "节奏慢一点", "整体快一点", "整体慢一点") if word in text]
        autonomy = None
        if any(value in text for value in ("自己发挥", "你来决定", "你自己挑", "其他你决定")):
            autonomy = Autonomy(default=ConstraintLevel.open)
        return GlobalIntent(
            theme=semantic_value("、".join(theme_words), confidence=0.93) if theme_words else None,
            mood=semantic_value("、".join(mood_words), confidence=0.88) if mood_words else None,
            style=semantic_value("、".join(style_words), confidence=0.88) if style_words else None,
            color_preference=semantic_value("、".join(color_words), confidence=0.93) if color_words else None,
            pacing=semantic_value("、".join(pacing_words), confidence=0.84) if pacing_words else None,
            platform_style="xiaohongshu" if "小红书" in text else None,
            autonomy=autonomy,
        )

    def _parse_object_requirements(self, clause: str, allocator: IdAllocator) -> list[ObjectRequirement]:
        result: list[ObjectRequirement] = []
        background = re.search(r"背景(?:一定要|最好要|换成|改成|用|设置为|是)\s*(.+)", clause)
        if background:
            raw = _clean_description(background.group(1))
            action = ObjectAction.replace if any(word in clause for word in ("换", "改")) else ObjectAction.add
            result.append(
                ObjectRequirement(
                    id=allocator.next("req_background"),
                    object_type=ObjectType.background,
                    action=action,
                    description=semantic_value(raw, confidence=0.96),
                    source_text=clause,
                    constraint_level=ConstraintLevel.hard if "一定" in clause else ConstraintLevel.soft if "最好" in clause else ConstraintLevel.hard,
                    confidence=0.95,
                )
            )

        if "音乐" in clause or "BGM" in clause.upper():
            music_preference = any(word in clause for word in ("欢快", "轻快", "节奏", "舒缓", "活泼"))
            if (any(word in clause for word in ("加", "添加", "换", "改成", "找")) or music_preference) and not any(word in clause for word in ("不要太响", "不要修改", "别改", "不要加", "别加", "不要添加", "不要换", "别换", "不加音乐", "不要音乐")):
                raw = _music_description(clause)
                action = ObjectAction.replace if any(word in clause for word in ("换", "改成")) else ObjectAction.add
                result.append(
                    ObjectRequirement(
                        id=allocator.next("req_music"),
                        object_type=ObjectType.music,
                        action=action,
                        description=semantic_value(raw, confidence=0.9),
                        source_text=clause,
                        constraint_level=ConstraintLevel.soft if "一点" in clause or "最好" in clause else ConstraintLevel.hard,
                        confidence=0.9,
                    )
                )

        text_req = _parse_text_object(clause, allocator)
        if text_req:
            result.append(text_req)

        # Standalone additions such as “再加一点星星” are object requirements.
        if not any(canonicalize_gesture(clause) for _ in [0]) and not _contains_event(clause):
            match = re.search(r"(?:再|另外|还)?加(?:上|一点|一个|一些)?\s*(星星|闪光|贝壳|海星|椰子树|爱心|皇冠)(?:素材|贴纸)?", clause)
            if match:
                raw = match.group(1)
                result.append(
                    ObjectRequirement(
                        id=allocator.next("req_sticker"),
                        object_type=ObjectType.sticker,
                        action=ObjectAction.add,
                        description=semantic_value(raw, confidence=0.9),
                        source_text=clause,
                        constraint_level=ConstraintLevel.hard,
                        confidence=0.9,
                    )
                )
        return result

    def _parse_event_requirement(self, clause: str, allocator: IdAllocator) -> Optional[EventBoundRequirement]:
        if not _contains_event(clause):
            return None
        has_action = any(word in clause for word in ("出现", "弹出", "跳出来", "添加", "加一个", "加上", "闪一下", "显示"))
        if not has_action:
            return None
        event_type, canonical, condition, event_confidence = event_from_text(clause)
        if not canonical and not condition:
            return None
        description = _event_asset_description(clause)
        object_type = _object_type_for_description(description, clause)
        action = ObjectAction.add
        operation = None
        if object_type == ObjectType.effect and "闪" in clause:
            operation = OperationType.replace_asset
        occurrence = _parse_occurrence(clause)
        temporal_relation = _parse_temporal_relation(clause)
        event = EventReference(
            type=event_type,
            raw=_event_raw(clause),
            canonical=canonical,
            condition=condition,
            confidence=event_confidence,
        )
        return EventBoundRequirement(
            id=allocator.next("event_req"),
            source_text=clause,
            trigger=EventTrigger(event=event, occurrence=occurrence, temporal_relation=temporal_relation),
            requirement=EventRequirement(
                object_type=object_type,
                action=action,
                semantic_description=semantic_value(description, confidence=0.92),
                content=_text_content(description) if object_type == ObjectType.text else None,
                operation=operation,
            ),
            constraint_level=ConstraintLevel.hard,
            confidence=min(event_confidence, 0.94),
        )

    def _parse_explicit_operation(self, clause: str, allocator: IdAllocator) -> Optional[ExplicitOperation]:
        if "定格" in clause:
            duration_match = re.search(r"([0-9一二两三四五六七八九十]+(?:\.\d+)?)\s*秒", clause)
            target = "ending_pose" if any(word in clause for word in ("最后", "结束", "结尾")) else "video_end"
            duration_value = 0.0
            if duration_match:
                raw_duration = duration_match.group(1)
                duration_value = float(raw_duration) if re.fullmatch(r"\d+\.\d+", raw_duration) else float(_number_from_text(raw_duration))
            return ExplicitOperation(
                id=allocator.next("operation"),
                operation=OperationType.freeze,
                target=TargetReference(type="semantic_event", value=target),
                parameters={"duration": {"value": duration_value, "unit": "second"}} if duration_match else {},
                source_text=clause,
                constraint_level=ConstraintLevel.hard,
                confidence=0.96,
            )
        if "音乐" not in clause and any(word in clause for word in ("小一点", "缩小", "大一点", "放大")):
            direction = "smaller" if any(word in clause for word in ("小一点", "缩小")) else "larger"
            target = _reference_phrase(clause)
            return ExplicitOperation(
                id=allocator.next("operation"),
                operation=OperationType.scale_adjust,
                target=TargetReference(type="object_reference", value=target),
                parameters={"direction": direction},
                source_text=clause,
                constraint_level=ConstraintLevel.hard,
                confidence=0.9,
            )
        if "文字" in clause and any(word in clause for word in ("改成", "换成", "替换")):
            value = _replacement_value(clause)
            return ExplicitOperation(
                id=allocator.next("operation"),
                operation=OperationType.replace_text,
                target=TargetReference(type="object_reference", value=_reference_phrase(clause) or "current text"),
                parameters={"value": value},
                source_text=clause,
                constraint_level=ConstraintLevel.hard,
                confidence=0.92,
            )
        if "音乐" in clause and any(word in clause for word in ("小一点", "小声", "调低", "降低", "不要太响")):
            return ExplicitOperation(
                id=allocator.next("operation"),
                operation=OperationType.volume_adjust,
                target=TargetReference(type="object_reference", value="current music"),
                parameters={"direction": "lower"},
                source_text=clause,
                constraint_level=ConstraintLevel.soft,
                confidence=0.94,
            )
        if any(word in clause for word in ("删掉", "删除", "去掉", "移除")):
            return ExplicitOperation(
                id=allocator.next("operation"),
                operation=OperationType.remove,
                target=TargetReference(type="object_reference", value=_reference_phrase(clause) or _removed_phrase(clause)),
                parameters={},
                source_text=clause,
                constraint_level=ConstraintLevel.hard,
                confidence=0.91,
            )
        return None

    def _parse_constraints(self, text: str, allocator: IdAllocator) -> list[Constraint]:
        result: list[Constraint] = []
        if any(word in text for word in ("不要挡脸", "不要遮住脸", "不能挡脸", "不要挡住脸")):
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "event_sticker"}, type="avoid_overlap", reference="face", raw=_matching_phrase(text, ("不要挡脸", "不要遮住脸", "不能挡脸", "不要挡住脸")), constraint_level=ConstraintLevel.hard, confidence=0.96))
        if "不要太花" in text:
            result.append(Constraint(id=allocator.next("constraint"), scope={}, type="avoid_visual_clutter", preference=semantic_value("不要太花", confidence=0.9), raw="不要太花", constraint_level=ConstraintLevel.soft, confidence=0.9))
        if "音乐不要太响" in text or "音乐别太响" in text:
            raw = "音乐不要太响" if "音乐不要太响" in text else "音乐别太响"
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "background_music"}, type="audio_volume", preference=semantic_value(raw, confidence=0.94), raw=raw, constraint_level=ConstraintLevel.soft, confidence=0.94))
        if "保持原视频长度" in text or "视频长度不变" in text:
            raw = "保持原视频长度" if "保持原视频长度" in text else "视频长度不变"
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "video"}, type="preserve_duration", raw=raw, constraint_level=ConstraintLevel.hard, confidence=0.98))
        if "不要裁掉人物" in text or "人物始终保持完整" in text:
            raw = "不要裁掉人物" if "不要裁掉人物" in text else "人物始终保持完整"
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "person"}, type="preserve_subject", raw=raw, constraint_level=ConstraintLevel.hard, confidence=0.96))
        if "不要修改原来的音乐" in text or "不要改原来的音乐" in text:
            raw = "不要修改原来的音乐" if "不要修改原来的音乐" in text else "不要改原来的音乐"
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "original_music"}, type="preserve_original_music", raw=raw, constraint_level=ConstraintLevel.hard, confidence=0.96))
        if "不要添加文字" in text or "不加文字" in text:
            raw = "不要添加文字" if "不要添加文字" in text else "不加文字"
            result.append(Constraint(id=allocator.next("constraint"), scope={"target": "text"}, type="prohibit_object", reference="text", raw=raw, constraint_level=ConstraintLevel.hard, confidence=0.97))
        return result


class OpenAICompatibleExtractor:
    """Minimal OpenAI-compatible Chat Completions adapter using stdlib HTTP."""

    DEFAULT_BASE_URL = "https://models.sjtu.edu.cn/api/v1"
    DEFAULT_MODEL = "deepseek-reasoner"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> Optional["OpenAICompatibleExtractor"]:
        # Keep the module-specific names while accepting the standard OpenAI
        # names used by the official SDK and documentation.
        api_key = os.getenv("INTENT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        base_url = os.getenv("INTENT_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or cls.DEFAULT_BASE_URL
        model = os.getenv("INTENT_LLM_MODEL") or os.getenv("OPENAI_MODEL") or cls.DEFAULT_MODEL
        try:
            timeout = float(os.getenv("INTENT_LLM_TIMEOUT", "60"))
        except ValueError:
            timeout = 60.0
        return cls(base_url, api_key, model, timeout=timeout)

    def extract(self, input: IntentParserInput, request_type: RequestType) -> dict[str, Any]:
        system = (
            "你是视频剪辑需求理解器。只提取用户明确表达的意图，不决定素材文件、位置、大小、"
            "动画参数、dB、BPM或具体时间戳。保留raw，同时提供canonical/tags。必须返回JSON。"
            "JSON顶层必须包含request_type和editing_intent或intent_patch。"
        )
        response_format = {"type": "json_object"}
        if os.getenv("INTENT_LLM_RESPONSE_FORMAT", "json_object").lower() == "json_schema":
            schema_type = EditingIntent if request_type == RequestType.initial_edit else IntentPatch
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "editing_intent" if request_type == RequestType.initial_edit else "intent_patch",
                    "strict": True,
                    "schema": _model_schema(schema_type),
                },
            }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"request_type": request_type.value, "input": model_dump(input)}, ensure_ascii=False)},
            ],
            "response_format": response_format,
        }
        # Reasoning models commonly reject sampling controls such as
        # temperature; deterministic post-validation still protects the
        # structured output contract.
        if "reasoner" not in self.model.lower() and "reasoning" not in self.model.lower():
            payload["temperature"] = 0
        endpoint = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        req = request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        if isinstance(content, str):
            content = content.strip()
            if content.startswith("```"):
                content = content.removeprefix("```").removeprefix("json").strip()
                if content.endswith("```"):
                    content = content[:-3].strip()
        return json.loads(content)


def default_extractor() -> StructuredIntentExtractor:
    return OpenAICompatibleExtractor.from_environment() or RuleBasedExtractor()


def _model_schema(model_type: Any) -> dict[str, Any]:
    if hasattr(model_type, "model_json_schema"):
        return model_type.model_json_schema()
    return model_type.schema()


def _contains_event(text: str) -> bool:
    return bool(canonicalize_gesture(text) or any(word in text for word in ("转身", "蹲下", "跳跃", "最后一个动作", "最后动作", "结束动作", "手举到头顶", "双手交叉", "音乐重拍", "重拍", "鼓点")))


def _parse_occurrence(text: str) -> Occurrence:
    number = r"[0-9一二两三四五六七八九十百]+"
    range_match = re.search(rf"第\s*({number})\s*次\s*(?:到|至|—|-)\s*(?:第\s*)?({number})\s*次", text)
    if range_match:
        return Occurrence(type=OccurrenceType.range, start=_number_from_text(range_match.group(1)), end=_number_from_text(range_match.group(2)))
    if "每次" in text:
        return Occurrence(type=OccurrenceType.all)
    if "第一次" in text:
        return Occurrence(type=OccurrenceType.first)
    if "最后一次" in text:
        return Occurrence(type=OccurrenceType.last)
    index_match = re.search(rf"第\s*({number})\s*次", text)
    if index_match:
        return Occurrence(type=OccurrenceType.index, value=_number_from_text(index_match.group(1)))
    return Occurrence(type=OccurrenceType.all)


def _parse_temporal_relation(text: str):
    from .models import TemporalRelation

    if "从" in text and "开始" in text and ("一直到" in text or "直到" in text):
        return TemporalRelation.between_events
    if "之前" in text or "以前" in text or "前面" in text:
        return TemporalRelation.before_event
    if "之后" in text or "以后" in text:
        return TemporalRelation.after_event
    if "过程中" in text or "全程" in text:
        return TemporalRelation.during_event
    if "从" in text and "开始" in text:
        return TemporalRelation.from_event
    if "一直到" in text or "直到" in text:
        return TemporalRelation.until_event
    return TemporalRelation.at_event


def _event_raw(text: str) -> str:
    for phrase in ("比心", "做爱心", "指向左侧", "往左指", "指向右侧", "往右指", "挥手", "转身", "跳跃", "手举到头顶", "双手交叉", "音乐重拍", "重拍", "最后一个动作", "最后动作", "结束动作"):
        if phrase in text:
            return phrase
    return text


def _event_asset_description(text: str) -> str:
    patterns = [
        r"(?:出现|弹出|跳出来|添加|加一个|加上|显示|闪一下)\s*(?:一个|一只|一些|一点)?\s*(.+)$",
        r"(?:的时候|时|过程中)\s*(?:出现|弹出|跳出来|添加|加上|闪一下)\s*(?:一个|一只|一些|一点)?\s*(.+)$",
    ]
    match = first_match(text, patterns)
    if match:
        return _clean_description(match.group(1))
    if "闪一下" in text:
        return "闪光效果"
    return _clean_description(text)


def _object_type_for_description(description: str, clause: str) -> ObjectType:
    if "文字" in description or "文案" in description:
        return ObjectType.text
    if "音乐" in description or "歌曲" in description:
        return ObjectType.music
    if "音效" in description:
        return ObjectType.sound_effect
    if "效果" in description or "特效" in description or "闪一下" in clause:
        return ObjectType.effect
    if "图片" in description or "照片" in description:
        return ObjectType.image
    return ObjectType.sticker


def _parse_text_object(clause: str, allocator: IdAllocator) -> Optional[ObjectRequirement]:
    if "文字" not in clause and "文案" not in clause:
        # “最后加一个 Summer” is an explicit Latin text addition.
        match = re.search(r"(?:最后)?\s*加(?:一个|上)?\s*([A-Za-z][A-Za-z0-9 !?._-]*)$", clause)
        if not match:
            return None
        raw = match.group(1).strip()
    else:
        if not any(word in clause for word in ("加", "添加", "出现", "放上")):
            return None
        match = re.search(r"(?:文字|文案)\s*[：:]?\s*[“\"']?(.+?)[”\"']?$", clause)
        if match:
            raw = match.group(1).strip()
        else:
            match = re.search(r"(?:加|添加|出现|放上)(?:一个|一段)?\s*[“\"']?(.+?)[”\"']?\s*(?:文字|文案)", clause)
            if not match:
                return None
            raw = match.group(1).strip()
    raw = raw.strip("“”\"' ")
    return ObjectRequirement(
        id=allocator.next("req_text"),
        object_type=ObjectType.text,
        action=ObjectAction.add,
        description=semantic_value(raw, confidence=0.97),
        content=raw,
        source_text=clause,
        constraint_level=ConstraintLevel.hard,
        confidence=0.97,
    )


def _clean_description(value: str) -> str:
    value = re.split(r"(?:，|。|；|但是|但不要|并且不要|不要)", value, maxsplit=1)[0]
    return value.strip(" \t,，。；;:：\"“”'") or value.strip()


def _music_description(clause: str) -> str:
    match = re.search(r"音乐(?:换成|改成|要|用|找)?\s*(.+)$", clause)
    if match:
        return _clean_description(match.group(1).replace("一点", "").strip())
    match = re.search(r"(?:加一首|添加一首|换一首|找一首)\s*(.+)$", clause)
    return _clean_description(match.group(1)) if match else "用户指定的音乐"


def _text_content(description: str) -> str:
    return description.replace("文字", "").replace("文案", "").strip()


def _reference_phrase(text: str) -> str:
    for pattern in (
        r"(第\s*[0-9一二两三四五六七八九十]+\s*个(?:爱心|星星|文字|文案|音乐|闪光|效果|贴纸|背景))",
        r"(最后(?:那个|一个)?\s*(?:文字|文案|爱心|星星|闪光|效果|音乐|背景))",
        r"(当前\s*(?:文字|文案|音乐|背景|爱心|效果))",
        r"(那个\s*(?:爱心|星星|文字|文案|音乐|闪光|效果|背景))",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    if "音乐" in text:
        return "current music"
    if "文字" in text or "文案" in text:
        return "current text"
    # Bare object noun as the direct subject of an adjust/remove verb,
    # e.g. "把星星放大" / "背景放大一点".
    for noun in ("爱心", "星星", "闪光", "效果", "背景", "文字", "文案", "音乐", "贴纸", "皇冠"):
        if noun in text:
            return noun
    return ""


def _replacement_value(text: str) -> str:
    match = re.search(r"(?:改成|换成|替换为|换掉为)\s*[“\"']?(.+?)[”\"']?$", text)
    return match.group(1).strip("“”\"' ") if match else ""


def _removed_phrase(text: str) -> str:
    match = re.search(r"(?:把)?(.+?)(?:删掉|删除|去掉|移除)", text)
    return match.group(1).strip() if match else text


def _matching_phrase(text: str, candidates: tuple[str, ...]) -> str:
    return next((candidate for candidate in candidates if candidate in text), text)


def _number_from_text(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value in digits:
        return digits[value]
    if "百" in value:
        parts = value.split("百")
        hundreds = digits.get(parts[0], 1) if parts[0] else 1
        rest = parts[1] if len(parts) > 1 else ""
        remainder = _number_from_text(rest.lstrip("零")) if rest.lstrip("零") else 0
        return hundreds * 100 + remainder
    if "十" in value:
        parts = value.split("十")
        tens = digits.get(parts[0], 1) if parts[0] else 1
        ones = digits.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return 0


def _dedupe_by_id(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return result
