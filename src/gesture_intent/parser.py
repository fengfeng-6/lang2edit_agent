"""Intent parser orchestration."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from .checks import conflicts, normalized_semantics, required_video_queries
from .extractors import RuleBasedExtractor, StructuredIntentExtractor, default_extractor
from .models import (
    EditingIntent,
    IntentParserInput,
    IntentParserOutput,
    IntentPatch,
    RequestType,
    model_validate,
)
from .resolver import resolve_references


class IntentParser:
    """Convert a user utterance into an Editing Intent or an Intent Patch."""

    def __init__(self, extractor: Optional[StructuredIntentExtractor] = None):
        self.extractor = extractor or default_extractor()
        self.rule_fallback = RuleBasedExtractor()

    def parse(self, input: IntentParserInput | dict[str, Any]) -> IntentParserOutput:
        parsed_input = input if isinstance(input, IntentParserInput) else model_validate(IntentParserInput, input)
        request_type = classify_request(parsed_input)
        raw_result, parser_mode, fallback_reason = self._extract_with_fallback(parsed_input, request_type)

        if request_type == RequestType.initial_edit:
            intent = model_validate(EditingIntent, raw_result.get("editing_intent", raw_result))
            resolution = resolve_references(parsed_input, intent=intent)
            unresolved = _unique_models(intent.unresolved + (resolution.unresolved or []))
            intent.unresolved = unresolved
            return IntentParserOutput(
                request_type=request_type,
                parser_mode=parser_mode,
                fallback_reason=fallback_reason,
                editing_intent=intent,
                normalized_semantics=normalized_semantics(intent=intent),
                resolved_references=resolution.resolved or [],
                required_video_queries=required_video_queries(intent=intent),
                affected_objects=resolution.affected_objects or [],
                conflicts=conflicts(intent=intent),
                unresolved=unresolved,
                confidence=_confidence_summary(intent=intent),
            )

        patch = model_validate(IntentPatch, raw_result.get("intent_patch", raw_result))
        resolution = resolve_references(parsed_input, patch=patch)
        unresolved = resolution.unresolved or []
        patch.affected_objects = list(dict.fromkeys(patch.affected_objects + (resolution.affected_objects or [])))
        return IntentParserOutput(
            request_type=request_type,
            parser_mode=parser_mode,
            fallback_reason=fallback_reason,
            intent_patch=patch,
            normalized_semantics=normalized_semantics(patch=patch),
            resolved_references=resolution.resolved or [],
            required_video_queries=required_video_queries(patch=patch),
            affected_objects=patch.affected_objects,
            # Pass the current intent so conflict checks can still map a
            # reference that resolve_references rewrote to an object_id (e.g. a
            # music target) back to its semantic type.
            conflicts=conflicts(patch=patch, intent=parsed_input.current_effective_intent),
            unresolved=unresolved,
            confidence=_confidence_summary(patch=patch),
        )

    def _extract_with_fallback(self, input: IntentParserInput, request_type: RequestType) -> tuple[dict[str, Any], str, Optional[str]]:
        attempted_llm = not isinstance(self.extractor, RuleBasedExtractor)
        try:
            result = self.extractor.extract(input, request_type)
            self._validate_extractor_shape(result, request_type)
            model_type = EditingIntent if request_type == RequestType.initial_edit else IntentPatch
            model_validate(model_type, result["editing_intent" if request_type == RequestType.initial_edit else "intent_patch"])
            return result, "llm" if attempted_llm else "rules", None
        except (Exception, ValidationError) as exc:
            # The deterministic extractor is deliberately the safe fallback:
            # it never invents assets or planner parameters. If the primary
            # extractor was already the deterministic one, re-running it cannot
            # help and would only raise the same error, so surface it directly.
            fallback_reason = f"{type(exc).__name__}" if attempted_llm else None
            if not attempted_llm:
                raise
            return self.rule_fallback.extract(input, request_type), "rules_fallback", fallback_reason

    @staticmethod
    def _validate_extractor_shape(result: Any, request_type: RequestType) -> None:
        if not isinstance(result, dict):
            raise ValueError("extractor must return a dict")
        key = "editing_intent" if request_type == RequestType.initial_edit else "intent_patch"
        if key not in result:
            raise ValueError(f"extractor result must include {key}")


def classify_request(input: IntentParserInput) -> RequestType:
    """Classify from conversation/project state plus explicit revision verbs."""
    if input.current_effective_intent is None and not input.semantic_project_view.objects:
        return RequestType.initial_edit
    text = input.user_utterance
    if any(word in text for word in ("删掉", "删除", "去掉", "移除")) and not any(word in text for word in ("再加", "添加", "加一个")):
        return RequestType.removal
    if any(word in text for word in ("再加", "另外加", "还要加", "添加一点", "添加一个", "再添加")):
        return RequestType.addition
    return RequestType.revision


def _confidence_summary(*, intent: Optional[EditingIntent] = None, patch: Optional[IntentPatch] = None) -> dict[str, float]:
    values: list[float] = []
    if intent:
        values.extend(item.confidence for item in intent.object_requirements)
        values.extend(item.confidence for item in intent.event_bound_requirements)
        values.extend(item.confidence for item in intent.explicit_operations)
        values.extend(item.confidence for item in intent.constraints)
    if patch:
        values.extend(item.confidence for item in patch.add_object_requirements + patch.update_object_requirements)
        values.extend(item.confidence for item in patch.add_event_bound_requirements + patch.update_event_bound_requirements)
        values.extend(item.confidence for item in patch.add_operations + patch.update_operations)
        values.extend(item.confidence for item in patch.add_constraints + patch.update_constraints)
    return {"overall": round(sum(values) / len(values), 4) if values else 0.0}


def _unique_models(items: list[Any]) -> list[Any]:
    seen: set[tuple[str, str]] = set()
    result = []
    for item in items:
        key = (getattr(item, "type", ""), getattr(item, "raw", ""))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
