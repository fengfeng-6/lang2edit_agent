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
    object_types: dict[str, str] | None = None


def resolve_references(input: IntentParserInput, *, intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None) -> ResolutionResult:
    objects = list(input.semantic_project_view.objects)
    if not objects and input.current_effective_intent:
        objects = _objects_from_intent(input.current_effective_intent)
    object_types = {item.id: item.object_type.value for item in objects}
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
        return ResolutionResult(intent=intent, resolved=resolved, unresolved=unresolved, affected_objects=_unique(affected), object_types=object_types)

    if patch is None:
        return ResolutionResult(resolved=[], unresolved=[], affected_objects=[], object_types=object_types)

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
                    _route_remove_target(patch, reference.raw, target_id, objects, current_intent, affected, unresolved)
                    continue
            else:
                # A remove target that did not resolve to an object may still
                # refer to an existing requirement; route it to the matching
                # removal list so the delete is not a silent no-op. Multiple
                # matches are ambiguous and reported instead of guessed.
                if operation.operation.value == "remove" and current_intent is not None:
                    matches = _match_removable_targets(operation.target.value, current_intent)
                    if len(matches) == 1:
                        kind, removal_id = matches[0]
                        affected.append(removal_id)
                        _append_removal(patch, kind, removal_id)
                        continue
                    if len(matches) > 1:
                        reference = UnresolvedReference(
                            type="object_reference",
                            raw=operation.target.value,
                            candidates=[removal_id for _, removal_id in matches],
                            confidence=0.42,
                            reason="remove target matches multiple requirements",
                        )
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
    return ResolutionResult(patch=patch, resolved=resolved, unresolved=unresolved, affected_objects=_unique(affected), object_types=object_types)


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

# English aliases contribute their Chinese keyword so resolved phrases like
# "current music" still match requirements described in Chinese.
_REMOVAL_ALIASES = {
    "music": "音乐",
    "heart": "爱心",
    "star": "星星",
    "text": "文字",
    "background": "背景",
    "sticker": "贴纸",
    "freeze": "定格",
    "volume": "音量",
}


def _removal_tokens(text: str) -> set[str]:
    """Collect the domain keywords present in a phrase, used to match a remove
    target back to an existing requirement."""
    tokens = {keyword for keyword in _REMOVAL_KEYWORDS if keyword in text}
    lowered = text.lower()
    for english, chinese in _REMOVAL_ALIASES.items():
        if english in lowered:
            tokens.add(english)
            tokens.add(chinese)
    return tokens


def _match_removable_targets(raw: str, intent: EditingIntent) -> list[tuple[str, str]]:
    """Match a remove target phrase against the current intent's requirements.

    Returns ``(kind, id)`` pairs where kind is ``"object"``, ``"operation"`` or
    ``"event"``.  More than one match is an ambiguity the caller must surface
    as an unresolved reference instead of silently picking one.
    """
    target_tokens = _removal_tokens(raw)
    if not target_tokens:
        return []
    matches: list[tuple[str, str]] = []
    for requirement in intent.object_requirements:
        description = requirement.description
        haystack = " ".join(
            part
            for part in [
                description.raw if description else None,
                *(description.tags if description else []),
                requirement.content,
                requirement.source_text,
                requirement.object_type.value,
            ]
            if part
        )
        if _removal_tokens(haystack) & target_tokens:
            matches.append(("object", requirement.id))
    for operation in intent.explicit_operations:
        haystack = " ".join([operation.target.value, operation.source_text, operation.operation.value])
        if _removal_tokens(haystack) & target_tokens:
            matches.append(("operation", operation.id))
    for requirement in intent.event_bound_requirements:
        event = requirement.trigger.event
        description = requirement.requirement.semantic_description.raw if requirement.requirement.semantic_description else ""
        operation = requirement.requirement.operation.value if requirement.requirement.operation else ""
        haystack = " ".join([event.raw, event.canonical or "", description, requirement.source_text, operation])
        if _removal_tokens(haystack) & target_tokens:
            matches.append(("event", requirement.id))
    return matches


def _append_removal(patch: IntentPatch, kind: str, removal_id: str) -> None:
    if kind == "operation":
        patch.remove_operation_ids.append(removal_id)
    elif kind == "event":
        patch.remove_event_bound_requirement_ids.append(removal_id)
    else:
        patch.remove_object_requirement_ids.append(removal_id)


def _route_remove_target(
    patch: IntentPatch,
    raw: str,
    target_id: str,
    objects: list[SemanticProjectObject],
    current_intent: Optional[EditingIntent],
    affected: list[str],
    unresolved: list[UnresolvedReference],
) -> None:
    """Route a resolved remove target into the correct removal list(s).

    The resolved id may itself be an event-bound requirement or explicit
    operation id (objects derived from the current intent carry requirement
    ids), so membership is checked first.  Otherwise the project-object id is
    kept on ``remove_object_requirement_ids`` for the host, and the backing
    requirement is routed too so ``apply_patch`` is not a silent no-op: an
    unnumbered reference to an event-produced object removes the whole
    binding, while an ordinal ("第二个爱心") removes only that instance.
    """
    if current_intent is not None:
        if any(item.id == target_id for item in current_intent.event_bound_requirements):
            patch.remove_event_bound_requirement_ids.append(target_id)
            return
        if any(item.id == target_id for item in current_intent.explicit_operations):
            patch.remove_operation_ids.append(target_id)
            return
    patch.remove_object_requirement_ids.append(target_id)
    if current_intent is None:
        return
    resolved_obj = next((item for item in objects if item.id == target_id), None)
    if resolved_obj is not None and resolved_obj.event_ref:
        if _ordinal(raw) is not None:
            return
        matches = [
            ("event", item.id)
            for item in current_intent.event_bound_requirements
            if resolved_obj.event_ref in {item.trigger.event.canonical, item.trigger.event.raw}
        ]
        if len(matches) == 1:
            affected.append(matches[0][1])
            patch.remove_event_bound_requirement_ids.append(matches[0][1])
        elif len(matches) > 1:
            unresolved.append(
                UnresolvedReference(
                    type="object_reference",
                    raw=raw,
                    candidates=[removal_id for _, removal_id in matches],
                    confidence=0.42,
                    reason="remove target matches multiple event requirements",
                )
            )
        return
    phrase = resolved_obj.description if resolved_obj is not None and resolved_obj.description else raw
    matches = [match for match in _match_removable_targets(phrase, current_intent) if match[1] != target_id]
    if len(matches) == 1:
        kind, removal_id = matches[0]
        affected.append(removal_id)
        _append_removal(patch, kind, removal_id)
    elif len(matches) > 1:
        unresolved.append(
            UnresolvedReference(
                type="object_reference",
                raw=raw,
                candidates=[removal_id for _, removal_id in matches],
                confidence=0.42,
                reason="remove target matches multiple requirements",
            )
        )


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
    if "最后" in raw:
        return -1
    return None


def _objects_from_intent(intent: EditingIntent) -> list[SemanticProjectObject]:
    objects: list[SemanticProjectObject] = []
    for index, requirement in enumerate(intent.object_requirements, start=1):
        description = requirement.description.raw if requirement.description else requirement.content
        aliases = requirement.description.tags if requirement.description else []
        objects.append(SemanticProjectObject(id=requirement.id, object_type=requirement.object_type, description=description, order=index, aliases=aliases))
    # Event-bound requirements also produce user-addressable objects ("那个
    # 爱心"), and carry the requirement id so a resolved remove lands on the
    # right removal list.
    for requirement in intent.event_bound_requirements:
        event = requirement.trigger.event
        description = requirement.requirement.semantic_description
        objects.append(
            SemanticProjectObject(
                id=requirement.id,
                object_type=requirement.requirement.object_type,
                description=description.raw if description else requirement.requirement.content,
                event_ref=event.canonical or event.raw,
                order=len(objects) + 1,
                aliases=[tag for tag in [event.raw, *(description.tags if description else [])] if tag],
            )
        )
    return objects


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
