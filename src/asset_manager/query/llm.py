"""LLM Query Rewriter（§16）：OpenAI-compatible 适配器。

LLM 在模块四的唯一职责：把 ``semantic_query`` 改写/补充为词表内
canonical 检索词。输入仅限 semantic_query + style_context +
technical_requirements；输出经 ``compiler._validate_rewritten``
白名单校验——LLM 提的词不在词表也不在原文里就丢弃。

适配器形态与模块三 ``editing_planner.llm.planner`` 一致：stdlib
urllib → ``{base_url}/chat/completions``，``ASSET_LLM_*`` 环境变量
优先、``OPENAI_*`` 兜底。任何错误返回 None，由 compiler 回退到
``LexiconQueryRewriter``。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib import error, request

from .lexicon import (
    ATTRIBUTE_ALIASES,
    MUSIC_TAG_ALIASES,
    OBJECT_ALIASES,
    STYLE_ALIASES,
)


def _vocab_lines() -> str:
    """把词表灌进 prompt——LLM 只能从这里挑词，白名单才有意义。"""

    def fmt(table: Dict[str, List[str]]) -> str:
        return "\n".join(
            f"  {canonical}: {' / '.join(aliases)}"
            for canonical, aliases in table.items()
        )

    return (
        "object 可用词:\n" + fmt(OBJECT_ALIASES)
        + "\nattributes 可用词:\n" + fmt(ATTRIBUTE_ALIASES)
        + "\nstyle 可用词:\n" + fmt(STYLE_ALIASES)
        + "\nstyle 音乐标签也可选:\n" + fmt(MUSIC_TAG_ALIASES)
    )


_SYSTEM = (
    "你是素材检索查询改写器。把用户的素材描述改写为结构化检索词。\n"
    "规则：\n"
    "1. 只许使用下方词表里的 canonical 词（冒号左边那个英文词），"
    "或用户原文里出现过的词；禁止无依据添加 3D/glitter/animated 这类词。\n"
    "2. object 至多一个——描述里最核心的实体；没有明确实体就留空字符串。\n"
    "3. attributes 放颜色/尺寸/形态诉求；style 放风格/氛围/音乐标签。\n"
    "4. “不要/无/without”这类否定后面的词放进 negative_terms。\n"
    "5. 只返回 JSON："
    "{\"object\": str, \"attributes\": [str], \"style\": [str], "
    "\"negative_terms\": [str]}，不要输出任何别的内容。\n\n"
) + _vocab_lines()


class OpenAICompatibleQueryRewriter:
    """OpenAI-compatible Chat Completions 查询改写器（stdlib HTTP）。"""

    DEFAULT_BASE_URL = "https://models.sjtu.edu.cn/api/v1"
    DEFAULT_MODEL = "deepseek-reasoner"
    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> Optional["OpenAICompatibleQueryRewriter"]:
        api_key = os.getenv("ASSET_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        base_url = (
            os.getenv("ASSET_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or cls.DEFAULT_BASE_URL
        )
        model = os.getenv("ASSET_LLM_MODEL") or os.getenv("OPENAI_MODEL") or cls.DEFAULT_MODEL
        try:
            timeout = float(os.getenv("ASSET_LLM_TIMEOUT", "60"))
        except ValueError:
            timeout = 60.0
        return cls(base_url, api_key, model, timeout=timeout)

    def rewrite(
        self, semantic_query: str, context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not (semantic_query or "").strip():
            return None
        user = {
            "semantic_query": semantic_query,
            "asset_type": context.get("asset_type", ""),
            "style_context": context.get("style_context") or {},
            "technical_requirements": context.get("technical_requirements") or {},
        }
        try:
            content = self._call(json.dumps(user, ensure_ascii=False))
            parsed = self._parse(content)
        except (error.URLError, TimeoutError, OSError, RuntimeError,
                json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return parsed

    # -- internals ---------------------------------------------------------

    def _call(self, user_content: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            "response_format": self._response_format(),
        }
        if "reasoner" not in self.model.lower() and "reasoning" not in self.model.lower():
            payload["temperature"] = 0
        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        req = request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("empty llm content")
        return content

    @staticmethod
    def _parse(content: str) -> Optional[Dict[str, Any]]:
        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```").removeprefix("json").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return None

        def _list(value: Any) -> List[str]:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                return []
            return [str(v).strip() for v in value if str(v).strip()]

        obj = data.get("object") or ""
        return {
            "object": str(obj).strip() if obj else "",
            "attributes": _list(data.get("attributes")),
            "style": _list(data.get("style")),
            "negative_terms": _list(data.get("negative_terms")),
        }

    @staticmethod
    def _response_format() -> Dict[str, Any]:
        """ASSET_LLM_RESPONSE_FORMAT=json_object|json_schema（同模块一/三惯例）。"""
        if os.getenv("ASSET_LLM_RESPONSE_FORMAT", "json_object").lower() == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "rewritten_query",
                    "strict": True,
                    "schema": _REWRITE_SCHEMA,
                },
            }
        return {"type": "json_object"}


_REWRITE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "object": {"type": "string"},
        "attributes": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "array", "items": {"type": "string"}},
        "negative_terms": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["object", "attributes", "style", "negative_terms"],
    "additionalProperties": False,
}


def default_query_rewriter():
    """有 ASSET_LLM_* 配置走 LLM，否则词表改写。"""

    return OpenAICompatibleQueryRewriter.from_environment()
