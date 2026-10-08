"""llm_sanitize + OpenAICompatibleExtractor 清洗管线测试（无网络，mock urlopen）。"""

from __future__ import annotations

import json

import pytest

from gesture_intent.extractors import OpenAICompatibleExtractor
from gesture_intent.llm_sanitize import (
    sanitize_intent_payload,
    sanitize_patch_payload,
)
from gesture_intent.models import (
    EditingIntent,
    IntentParserInput,
    IntentPatch,
    RequestType,
    model_validate,
)
from gesture_intent.parser import IntentParser


# ---------------------------------------------------------------------------
# sanitize_*_payload 纯函数
# ---------------------------------------------------------------------------


def test_sanitize_intent_coerces_messy_output():
    """多余键丢弃、非法枚举归位、裸字符串 description 包成 SemanticValue。"""
    raw = {
        "editing_intent": {"should": "be ignored at payload level"},
        "object_requirements": [
            {
                "id": "req_01",
                "object_type": "背景",  # 非法枚举 → sticker
                "action": "add",
                "description": "海边沙滩",  # 裸 str → {"raw": ...}
                "source_text": "用海边沙滩背景",
                "confidence": 5,  # 越界 → clamp 1.0
                "invented_key": "drop me",
            },
            {"object_type": "music", "action": "replace"},  # 缺 id/source_text
            "not-a-dict",  # 非 dict 条目跳过
        ],
        "constraints": [
            {"type": "keep", "raw": "保留人物", "weird": True},
        ],
        "unresolved": "oops",  # 非 list → []
        "totally_alien": [1, 2, 3],
    }

    cleaned = sanitize_intent_payload(raw)
    intent = model_validate(EditingIntent, cleaned)

    assert len(intent.object_requirements) == 2
    first = intent.object_requirements[0]
    assert first.object_type.value == "sticker"
    assert first.description is not None and first.description.raw == "海边沙滩"
    assert first.confidence == 1.0
    second = intent.object_requirements[1]
    assert second.object_type.value == "music"
    assert second.action.value == "replace"
    assert second.id  # 自动补齐
    assert len(intent.constraints) == 1
    assert intent.constraints[0].raw == "保留人物"


def test_sanitize_intent_rejects_alien_shape():
    with pytest.raises(ValueError):
        sanitize_intent_payload({"raw": "比心", "canonical": "heart", "tags": []})
    with pytest.raises(ValueError):
        sanitize_intent_payload(["not", "a", "dict"])
    # 空 dict 是合法"什么都没抽到"，不抛
    assert sanitize_intent_payload({})["object_requirements"] == []


def test_sanitize_global_intent_platform_style_and_autonomy():
    raw = {
        "global_intent": {
            "platform_style": {"raw": "小红书"},  # dict → 取 raw
            "autonomy": {"default": "creative", "extra": 1},  # 非法 level → soft
            "theme": "旅行",
        }
    }
    intent = model_validate(EditingIntent, sanitize_intent_payload(raw))
    assert intent.global_intent.platform_style == "小红书"
    assert intent.global_intent.autonomy is not None
    assert intent.global_intent.autonomy.default.value == "soft"
    assert intent.global_intent.theme.raw == "旅行"


def test_sanitize_event_req_nested_cleanup():
    raw = {
        "event_bound_requirements": [{
            "id": "event_req_01",
            "source_text": "每次比心出现爱心",
            "trigger": {
                "event": {"type": "手势", "raw": "比心",
                          "canonical": "heart_gesture", "junk": 1},
                "occurrence": {"type": "每次", "value": "2"},  # 非法 type → all
                "temporal_relation": "同时",  # 非法 → at_event
            },
            "requirement": {"object_type": "sticker",
                            "semantic_description": "粉色爱心"},
        }],
    }
    intent = model_validate(EditingIntent, sanitize_intent_payload(raw))
    req = intent.event_bound_requirements[0]
    assert req.trigger.event.type.value == "gesture"  # 非法事件类型归位
    assert req.trigger.event.canonical == "heart_gesture"
    assert req.trigger.occurrence.type.value == "all"
    assert req.trigger.temporal_relation.value == "at_event"
    assert req.requirement.semantic_description.raw == "粉色爱心"


def test_sanitize_patch_drops_bad_ops_keeps_good():
    raw = {
        "add_operations": [
            {"id": "op_01", "operation": "scale_adjust",
             "target": {"type": "object_reference", "value": "爱心"},
             "parameters": {"direction": "smaller"},
             "source_text": "爱心小一点"},
            {"operation": "explode",  # 非法 operation → 整条丢弃
             "target": {"type": "object_reference", "value": "星星"}},
            {"operation": "remove"},  # 无 target → 丢弃
        ],
        "remove_object_requirement_ids": ["req_01", 7],
        "unknown_patch_key": {"x": 1},
    }
    cleaned = sanitize_patch_payload(raw)
    patch = model_validate(IntentPatch, cleaned)

    assert len(patch.add_operations) == 1
    assert patch.add_operations[0].operation.value == "scale_adjust"
    assert patch.remove_object_requirement_ids == ["req_01", "7"]


# ---------------------------------------------------------------------------
# extract() 全链（mock HTTP）
# ---------------------------------------------------------------------------


def _fake_urlopen(captured, content):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode("utf-8")

    def fake(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    return fake


def _llm_env(monkeypatch):
    monkeypatch.setenv("INTENT_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("INTENT_LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")
    monkeypatch.setenv("INTENT_LLM_MODEL", "unit-test-model")
    monkeypatch.delenv("INTENT_LLM_RESPONSE_FORMAT", raising=False)


def test_extract_sanitizes_and_wraps(monkeypatch):
    captured = {}
    _llm_env(monkeypatch)
    monkeypatch.setattr(
        "gesture_intent.extractors.request.urlopen",
        _fake_urlopen(captured, json.dumps({
            "editing_intent": {
                "object_requirements": [{
                    "id": "req_01", "object_type": "background",
                    "action": "add", "description": "海边沙滩",
                    "source_text": "背景用海边沙滩", "bogus": "x",
                }],
            }
        }, ensure_ascii=False)),
    )
    adapter = OpenAICompatibleExtractor.from_environment()
    result = adapter.extract(
        IntentParserInput(user_utterance="背景用海边沙滩"),
        RequestType.initial_edit)
    assert result["editing_intent"]["object_requirements"][0]["description"]["raw"] == "海边沙滩"
    # 引导提示词确实进了请求
    system = captured["payload"]["messages"][0]["content"]
    assert "editing_intent" in system and "intent_patch" in system


def test_parser_uses_llm_mode_when_sanitize_succeeds(monkeypatch):
    _llm_env(monkeypatch)
    monkeypatch.setattr(
        "gesture_intent.extractors.request.urlopen",
        _fake_urlopen({}, json.dumps({
            "event_bound_requirements": [{
                "id": "event_req_01", "source_text": "比心出现爱心",
                "trigger": {
                    "event": {"type": "gesture", "raw": "比心",
                              "canonical": "heart_gesture"},
                    "occurrence": {"type": "all"},
                    "temporal_relation": "at_event"},
                "requirement": {"object_type": "sticker",
                                "semantic_description": "粉色爱心"},
            }],
        }, ensure_ascii=False)),
    )
    output = IntentParser().parse(
        IntentParserInput(user_utterance="每次比心出现粉色爱心"))
    assert output.parser_mode == "llm"
    assert output.editing_intent.event_bound_requirements[0].trigger.event.canonical == "heart_gesture"


def test_parser_falls_back_when_llm_output_alien(monkeypatch):
    _llm_env(monkeypatch)
    monkeypatch.setattr(
        "gesture_intent.extractors.request.urlopen",
        _fake_urlopen({}, json.dumps(
            {"raw": "比心", "canonical": "heart_gesture", "tags": ["gesture"]},
            ensure_ascii=False)),
    )
    output = IntentParser().parse(
        IntentParserInput(user_utterance="每次比心出现粉色爱心"))
    assert output.parser_mode == "rules_fallback"
    assert output.fallback_reason.startswith("ValueError")
    # 规则兜底仍然抽到比心
    assert output.editing_intent.event_bound_requirements
