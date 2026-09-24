"""LLMQueryRewriter 单元测试（§16）：契约、解析、白名单、回退。

HTTP 层全部 monkeypatch——这些测试不联网。真实端点行为见
test_asset_llm_live.py（需要 ASSET_LLM_*/INTENT_LLM_* 环境变量）。
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from asset_manager.query.compiler import QueryCompiler
from asset_manager.query.llm import OpenAICompatibleQueryRewriter
from asset_fixtures import request_dict


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body if isinstance(self._body, bytes) else json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, body=None, capture=None, error=None):
    def fake(req, timeout=None):
        if capture is not None:
            capture["payload"] = json.loads(req.data.decode("utf-8"))
            capture["url"] = req.full_url
            capture["auth"] = req.headers.get("Authorization")
        if error is not None:
            raise error
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def _msg(content):
    return {"choices": [{"message": {"content": content}}]}


def _rewriter(model="qwen-test"):
    return OpenAICompatibleQueryRewriter(
        "https://llm.example.com/v1", "sk-test", model, timeout=5)


def test_rewrite_happy_path(monkeypatch):
    _patch_urlopen(monkeypatch, _msg(
        '{"object": "heart", "attributes": ["pink"], '
        '"style": ["cute", "pastel"], "negative_terms": []}'))
    out = _rewriter().rewrite("少女一点的粉色爱心", {})
    assert out == {
        "object": "heart",
        "attributes": ["pink"],
        "style": ["cute", "pastel"],
        "negative_terms": [],
    }


def test_rewrite_strips_code_fence(monkeypatch):
    _patch_urlopen(monkeypatch, _msg(
        '```json\n{"object": "star", "attributes": [], "style": [],'
        ' "negative_terms": []}\n```'))
    out = _rewriter().rewrite("星星", {})
    assert out["object"] == "star"


def test_rewrite_content_list_joined(monkeypatch):
    _patch_urlopen(monkeypatch, _msg(
        [{"type": "text", "text": '{"object": "moon", '},
         {"type": "text", "text": '"attributes": [], "style": [], "negative_terms": []}'}]))
    assert _rewriter().rewrite("月亮", {})["object"] == "moon"


def test_rewrite_string_fields_coerced(monkeypatch):
    _patch_urlopen(monkeypatch, _msg(
        '{"object": "beach", "attributes": "blue", "style": null, "negative_terms": "red"}'))
    out = _rewriter().rewrite("beach", {})
    assert out["attributes"] == ["blue"]
    assert out["style"] == []
    assert out["negative_terms"] == ["red"]


def test_rewrite_bad_json_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, _msg("not json at all"))
    assert _rewriter().rewrite("爱心", {}) is None


def test_rewrite_non_dict_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, _msg('["heart", "pink"]'))
    assert _rewriter().rewrite("爱心", {}) is None


def test_rewrite_http_error_returns_none(monkeypatch):
    _patch_urlopen(monkeypatch, error=TimeoutError("boom"))
    assert _rewriter().rewrite("爱心", {}) is None


def test_rewrite_empty_query_short_circuits(monkeypatch):
    captured = {}
    _patch_urlopen(monkeypatch, _msg("{}"), capture=captured)
    assert _rewriter().rewrite("   ", {}) is None
    assert "payload" not in captured  # 空查询不发请求


def test_compiler_whitelist_drops_unfounded_llm_terms(monkeypatch):
    """LLM 提了词表外且原文没有的词 → 编译后被 §16 白名单丢弃。"""

    _patch_urlopen(monkeypatch, _msg(
        '{"object": "heart", "attributes": ["pink", "3d", "glitter"],'
        ' "style": ["cute", "metallic"], "negative_terms": []}'))
    from editing_planner.models import AssetRequest
    compiler = QueryCompiler(_rewriter())
    query = compiler.parse(AssetRequest(**request_dict(semantic_query="粉色爱心")))
    assert query.object == "heart"
    assert query.attributes == ["pink"]
    assert query.style == ["cute"]


def test_compiler_keeps_terms_from_raw_text(monkeypatch):
    """原文出现的词不在词表也允许保留（§16 白名单第二支）。"""

    _patch_urlopen(monkeypatch, _msg(
        '{"object": "heart", "attributes": [], "style": ["赛博朋克"],'
        ' "negative_terms": []}'))
    from editing_planner.models import AssetRequest
    compiler = QueryCompiler(_rewriter())
    query = compiler.parse(AssetRequest(**request_dict(semantic_query="赛博朋克爱心")))
    assert "赛博朋克" in query.style


def test_compiler_falls_back_to_lexicon_on_none(monkeypatch):
    _patch_urlopen(monkeypatch, error=TimeoutError("down"))
    from editing_planner.models import AssetRequest
    compiler = QueryCompiler(_rewriter())
    query = compiler.parse(AssetRequest(**request_dict(semantic_query="可爱的粉色爱心")))
    # rewriter 返回 None → parse_semantic_terms 接管
    assert query.object == "heart"
    assert "pink" in query.attributes
    assert "cute" in query.style


def test_payload_shape_and_temperature(monkeypatch):
    captured = {}
    _patch_urlopen(monkeypatch, _msg(
        '{"object": "", "attributes": [], "style": [], "negative_terms": []}'),
        capture=captured)
    _rewriter(model="qwen-plus").rewrite("爱心", {"asset_type": "sticker"})
    payload = captured["payload"]
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    user = json.loads(payload["messages"][1]["content"])
    assert user["semantic_query"] == "爱心"
    assert user["asset_type"] == "sticker"
    # §16：词表灌进 system prompt，user 消息只带请求上下文
    assert "object 可用词" in payload["messages"][0]["content"]


def test_reasoner_model_omits_temperature(monkeypatch):
    captured = {}
    _patch_urlopen(monkeypatch, _msg(
        '{"object": "", "attributes": [], "style": [], "negative_terms": []}'),
        capture=captured)
    _rewriter(model="deepseek-reasoner").rewrite("爱心", {})
    assert "temperature" not in captured["payload"]


def test_from_environment_and_response_format(monkeypatch):
    for var in ("ASSET_LLM_API_KEY", "ASSET_LLM_BASE_URL", "ASSET_LLM_MODEL",
                "ASSET_LLM_TIMEOUT", "ASSET_LLM_RESPONSE_FORMAT",
                "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert OpenAICompatibleQueryRewriter.from_environment() is None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    rw = OpenAICompatibleQueryRewriter.from_environment()
    assert rw.api_key == "sk-openai"
    assert rw.base_url == OpenAICompatibleQueryRewriter.DEFAULT_BASE_URL

    monkeypatch.setenv("ASSET_LLM_API_KEY", "sk-asset")
    monkeypatch.setenv("ASSET_LLM_BASE_URL", "https://asset.example.com/")
    monkeypatch.setenv("ASSET_LLM_MODEL", "asset-model")
    monkeypatch.setenv("ASSET_LLM_TIMEOUT", "7")
    rw = OpenAICompatibleQueryRewriter.from_environment()
    assert rw.api_key == "sk-asset"
    assert rw.base_url == "https://asset.example.com"
    assert rw.model == "asset-model"
    assert rw.timeout == 7.0

    monkeypatch.setenv("ASSET_LLM_RESPONSE_FORMAT", "json_schema")
    fmt = rw._response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "rewritten_query"
