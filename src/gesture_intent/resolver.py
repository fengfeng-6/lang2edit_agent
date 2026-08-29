"""Resolve references against simplified project state without resolving timestamps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .extractors import _number_from_text
from .models import (
    EditingIntent,
    ExplicitOperation,
    IntentParserInput,
    IntentPatch,
    ObjectType,
    ResolvedReference,
    SemanticProjectObject,
    TargetReference,
    UnresolvedReference,
    model_dump,
)


@dataclass
class ResolutionResult:
    intent: Optional[EditingIntent] = None
    patch: Optional[IntentPatch] = None
    resolved: list[ResolvedReference] | None = None
    unresolved: list[UnresolvedReference] | None = None
    affected_objects: list[str] | None = None


def resolve_references(input: IntentParserInput, *, intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None) -> ResolutionResult:
    objects = list(input.semantic_project_view.objects)
    if not objects and input.current_effective_intent:
        objects = _objects_from_intent(input.current_effective_intent)
    resolved: list[ResolvedReference] = []
    unresolved: list[UnresolvedReference] = []
    affected: list[str] = []

    if intent is not None:
        # Initial requests normally do not reference existing objects, but keep
        # the same resolver for explicit future extensions.
        for operation in intent.explicit_operations:
            operation_target, reference = _resolve_target(operation.target, objects)
            if reference:
                target_id = getattr(reference, "target_id", None)
                if target_id:
                    resolved.append(reference)
                    operation.target = operation_target
                    affected.append(target_id)
                else:
                    unresolved.append(reference)  # type: ignore[arg-type]
        intent.unresolved.extend(unresolved)
        return ResolutionResult(intent=intent, resolved=resolved, unresolved=unresolved, affected_objects=_unique(affected))

    if patch is None:
        return ResolutionResult(resolved=[], unresolved=[], affected_objects=[])

    retained_add_operations = []
    retained_update_operations = []
    current_intent = input.current_effective_intent
    for operation in patch.add_operations + patch.update_operations:
        operation_target, reference = _resolve_target(operation.target, objects)
        if reference:
            target_id = getattr(reference, "target_id", None)
            if target_id:
                resolved.append(reference)
                operation.target = operation_target
                affected.append(target_id)
                if operation.operation.value == "remove":
                    patch.remove_object_requirement_ids.append(target_id)
                    continue
            else:
                # A remove target that did not resolve to an object requirement
                # may instead refer to an existing explicit operation or an
                # event-bound requirement. Route it to the matching removal list
                # so the delete is not a silent no-op.
                if operation.operation.value == "remove" and current_intent is not None:
                    matched = _match_removable_target(operation.target.value, current_intent)
                    if matched is not None:
                        kind, removal_id = matched
                        affected.append(removal_id)
                        if kind == "operation":
                            patch.remove_operation_ids.append(removal_id)
                        else:
                            patch.remove_event_bound_requirement_ids.append(removal_id)
                        continue
                unresolved.append(reference)  # type: ignore[arg-type]
        if operation in patch.add_operations:
            retained_add_operations.append(operation)
        else:
            retained_update_operations.append(operation)
    patch.add_operations = retained_add_operations
    patch.update_operations = retained_update_operations
    patch.remove_operation_ids = _unique(patch.remove_operation_ids)
    patch.remove_event_bound_requirement_ids = _unique(patch.remove_event_bound_requirement_ids)

    remove_ids: list[str] = []
    for raw_target in patch.remove_object_requirement_ids:
        target, reference = _resolve_raw_target(raw_target, objects)
        if reference:
            target_id = getattr(reference, "target_id", None)
            if target_id:
                resolved.append(reference)
                remove_ids.append(target_id)
                affected.append(target_id)
            else:
                unresolved.append(reference)  # type: ignore[arg-type]
        else:
            remove_ids.append(raw_target)
            affected.append(raw_target)
    patch.remove_object_requirement_ids = _unique(remove_ids)

    patch.affected_objects = _unique(patch.affected_objects + affected)
    return ResolutionResult(patch=patch, resolved=resolved, unresolved=unresolved, affected_objects=_unique(affected))


def _resolve_target(target: TargetReference, objects: list[SemanticProjectObject]):
    if target.type != "object_reference":
        return target, None
    return _resolve_raw_target(target.value, objects)


def _resolve_raw_target(raw: str, objects: list[SemanticProjectObject]):
    exact = next((item for item in objects if item.id == raw), None)
    if exact:
        return TargetReference(type="object_id", value=exact.id), ResolvedReference(type="object_reference", raw=raw, target_id=exact.id, confidence=1.0)
    candidates = _candidate_objects(raw, objects)
    if not candidates:
        return TargetReference(type="object_reference", value=raw), UnresolvedReference(type="object_reference", raw=raw, candidates=[], confidence=0.0, reason="no matching project object")
    ordinal = _ordinal(raw)
    if ordinal is not None:
        ordered = sorted(candidates, key=lambda item: (item.order if item.order is not None else 10**9, item.id))
        if ordinal == -1:
            chosen = ordered[-1]
            return TargetReference(type="object_id", value=chosen.id), ResolvedReference(type="object_reference", raw=raw, target_id=chosen.id, confidence=0.98)
        if ordinal <= len(ordered):
            chosen = ordered[ordinal - 1]
            return TargetReference(type="object_id", value=chosen.id), ResolvedReference(type="object_reference", raw=raw, target_id=chosen.id, confidence=0.98)
        return TargetReference(type="object_reference", value=raw), UnresolvedReference(type="object_reference", raw=raw, candidates=[item.id for item in ordered], confidence=0.25, reason="ordinal is outside candidate range")
    if len(candidates) == 1:
        chosen = candidates[0]
        return TargetReference(type="object_id", value=chosen.id), ResolvedReference(type="object_reference", raw=raw, target_id=chosen.id, confidence=0.96)
    return TargetReference(type="object_reference", value=raw), UnresolvedReference(type="object_reference", raw=raw, candidates=[item.id for item in candidates], confidence=0.42, reason="multiple matching project objects")


_REMOVAL_KEYWORDS = ("爱心", "星星", "闪光", "效果", "文字", "文案", "背景", "音乐", "贴纸", "比心", "皇冠", "海星", "放大", "缩小", "定格", "音量")


def _removal_tokens(text: str) -> set[str]:
    """Collect the domain keywords present in a phrase, used to match a remove
    target back to an existing operation or event-bound requirement."""
    return {keyword for keyword in _REMOVAL_KEYWORDS if keyword in text}


def _match_removable_target(raw: str, intent: EditingIntent) -> Optional[tuple[str, str]]:
    """Match a remove target phrase against the current intent's explicit
    operations and event-bound requirements.

    Returns ``(kind, id)`` where kind is ``"operation"`` or ``"event"``, or
    ``None`` when there is no confident match. Used when a remove operation's
    target could not be resolved to an object requirement.
    """
    target_tokens = _removal_tokens(raw)
    if not target_tokens:
        return None
    for operation in intent.explicit_operations:
        haystack = " ".join([operation.target.value, operation.source_text, operation.operation.value])
        if _removal_tokens(haystack) & target_tokens:
            return ("operation", operation.id)
    for requirement in intent.event_bound_requirements:
        event = requirement.trigger.event
        description = requirement.requirement.semantic_description.raw if requirement.requirement.semantic_description else ""
        operation = requirement.requirement.operation.value if requirement.requirement.operation else ""
        haystack = " ".join([event.raw, event.canonical or "", description, requirement.source_text, operation])
        if _removal_tokens(haystack) & target_tokens:
            return ("event", requirement.id)
    return None


_REFERENCE_KEYWORD_TOKENS = {
    "heart": ("heart", "爱心"),
    "star": ("star", "星星"),
    "spark": ("star", "spark", "sparkle", "闪光", "星光"),
    "palm": ("palm", "椰子树", "椰子"),
    "starfish": ("starfish", "海星"),
}


def _keyword_matches(haystack: str, keyword: Optional[str]) -> bool:
    if keyword is None:
        return True
    for token in _REFERENCE_KEYWORD_TOKENS.get(keyword, (keyword,)):
        if token.isascii():
            if re.search(rf"\b{re.escape(token)}\b", haystack):
                return True
        elif token in haystack:
            return True
    return False


def _candidate_objects(raw: str, objects: list[SemanticProjectObject]) -> list[SemanticProjectObject]:
    lowered = raw.lower()
    object_type = _object_type_from_reference(lowered)
    keyword = _keyword_from_reference(lowered)
    result: list[SemanticProjectObject] = []
    for item in objects:
        if object_type and item.object_type != object_type:
            continue
        haystack = " ".join([item.description or "", item.event_ref or "", *item.aliases]).lower()
        if not _keyword_matches(haystack, keyword):
            continue
        result.append(item)
    return result


def _object_type_from_reference(raw: str) -> Optional[ObjectType]:
    if "音乐" in raw or "music" in raw or "bgm" in raw:
        return ObjectType.music
    if "文字" in raw or "文案" in raw or "text" in raw:
        return ObjectType.text
    if any(word in raw for word in ("爱心", "星星", "闪光", "贴纸", "sticker")):
        return ObjectType.sticker if "闪光" not in raw else None
    if "背景" in raw or "background" in raw:
        return ObjectType.background
    if "图片" in raw or "image" in raw:
        return ObjectType.image
    return None


def _keyword_from_reference(raw: str) -> Optional[str]:
    if "爱心" in raw or "heart" in raw:
        return "heart"
    if "星星" in raw or "star" in raw:
        return "star"
    if "闪光" in raw or "sparkle" in raw:
        return "spark"
    if "椰子" in raw or "palm" in raw:
        return "palm"
    if "海星" in raw or "starfish" in raw:
        return "starfish"
    return None


def _ordinal(raw: str) -> Optional[int]:
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百]+)\s*个(?:爱心|星星|文字|文案|音乐|闪光|效果|贴纸|背景)?", raw)
    if match:
        return _number_from_text(match.group(1))
    if "第一个" in raw:
        return 1
    if "第二个" in raw:
        return 2
    if "第三个" in raw:
        return 3
    if "最后" in raw:
        return -1
    return None


def _objects_from_intent(intent: EditingIntent) -> list[SemanticProjectObject]:
    objects: list[SemanticProjectObject] = []
    for index, requirement in enumerate(intent.object_requirements, start=1):
        description = requirement.description.raw if requirement.description else requirement.content
        aliases = requirement.description.tags if requirement.description else []
        objects.append(SemanticProjectObject(id=requirement.id, object_type=requirement.object_type, description=description, order=index, aliases=aliases))
    return objects


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
