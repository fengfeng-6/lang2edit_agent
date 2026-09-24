"""Query Compiler（§15-16）：AssetRequest → 各 Provider 的 ProviderQuery。

- 输入仅限 ``AssetRequest.semantic_query`` + ``style_context`` +
  ``technical_requirements``（§16），不重新阅读聊天历史自由创作；
- 默认定性词表解析（``LexiconQueryRewriter``），LLM 改写走
  ``QueryRewriter`` 接缝且输出过白名单校验，不允许无依据加词。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Protocol, Tuple

from editing_planner.models import AssetRequest

from ..models import ProviderQuery, SemanticQuery
from .lexicon import (
    ALIAS_TO_CANONICAL,
    ALL_ALIASES_LONGEST,
    ATTRIBUTE_CANONICAL,
    MUSIC_TAG_CANONICAL,
    NEGATIVE_MARKERS,
    OBJECT_CANONICAL,
    STYLE_CANONICAL,
    canonicalize,
)

_STYLE_LIKE = frozenset(STYLE_CANONICAL.values()) | frozenset(MUSIC_TAG_CANONICAL.values())


def split_technical(requirements: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """技术需求拆 required / preferred（§11）。

    模块三目前下发扁平 dict（如 ``{"has_alpha": true}``）——按 required
    处理；若已显式分桶则直接采用。不得混淆：required 进 Hard Filter，
    preferred 只进 Ranking。
    """

    if "required" in requirements or "preferred" in requirements:
        return (
            dict(requirements.get("required") or {}),
            dict(requirements.get("preferred") or {}),
        )
    return dict(requirements), {}


class QueryRewriter(Protocol):
    """LLM/外部改写接缝（§16）；只许语义改写与搜索词补充。"""

    def rewrite(
        self, semantic_query: str, context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """返回 {"object","attributes","style","negative_terms"} 或 None。"""


class LexiconQueryRewriter:
    """确定性词表改写：扫描 semantic_query 命中的 canonical 术语。"""

    name = "lexicon"

    def rewrite(
        self, semantic_query: str, context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return parse_semantic_terms(semantic_query)


def parse_semantic_terms(text: str) -> Dict[str, Any]:
    """自由文本 → 结构化术语（最长别名优先，中英混合）。"""

    lowered = f" {text.strip().lower()} "
    obj = ""
    attrs: List[str] = []
    styles: List[str] = []
    consumed: List[str] = []

    for alias in ALL_ALIASES_LONGEST:
        needle = alias.lower()
        if needle not in lowered:
            continue
        canonical = ALIAS_TO_CANONICAL[needle]
        if canonical in OBJECT_CANONICAL.values() and not obj:
            obj = canonical
            consumed.append(needle)
        elif canonical in ATTRIBUTE_CANONICAL.values() and canonical not in attrs:
            attrs.append(canonical)
            consumed.append(needle)
        elif canonical in _STYLE_LIKE and canonical not in styles:
            styles.append(canonical)
            consumed.append(needle)

    negatives: List[str] = []
    for marker in NEGATIVE_MARKERS:
        for match in re.finditer(re.escape(marker) + r"([\w一-鿿]+)", lowered):
            term = match.group(1).strip()
            if term:
                negatives.append(canonicalize(term))

    leftover = lowered
    for alias in consumed:
        leftover = leftover.replace(alias, " ")
    extra_tokens = [t for t in re.split(r"\s+", leftover) if len(t) > 1]
    return {
        "object": obj,
        "attributes": attrs,
        "style": styles,
        "negative_terms": negatives,
        "extra_tokens": extra_tokens,
    }


def _validate_rewritten(
    parsed: Dict[str, Any], raw: str
) -> Dict[str, Any]:
    """§16 白名单：改写结果只能是词表术语或原文出现的词。"""

    allowed = set(ALIAS_TO_CANONICAL.values())
    raw_lower = raw.lower()

    def keep(term: str) -> bool:
        t = str(term).strip().lower()
        return bool(t) and (t in allowed or t in raw_lower or canonicalize(t) in allowed)

    out = dict(parsed)
    for key in ("attributes", "style", "negative_terms"):
        out[key] = [t for t in parsed.get(key, []) if keep(t)]
    obj = parsed.get("object") or ""
    out["object"] = obj if keep(obj) else ""
    return out


class QueryCompiler:
    """按 Provider 能力把同一需求编译成不同 ProviderQuery（§15）。"""

    def __init__(self, rewriter: Optional[QueryRewriter] = None):
        self.rewriter = rewriter or LexiconQueryRewriter()

    def parse(self, request: AssetRequest) -> SemanticQuery:
        raw = request.semantic_query or ""
        context = {
            "style_context": dict(request.style_context),
            "technical_requirements": dict(request.technical_requirements),
            "asset_type": request.asset_type,
        }
        parsed = self.rewriter.rewrite(raw, context) if self.rewriter else None
        if not parsed:
            parsed = parse_semantic_terms(raw)
        parsed = _validate_rewritten(parsed, raw)
        # 全局风格标签（visual_language）补进 style——§22 style family 对齐
        style = list(parsed.get("style", []))
        for tag in request.style_context.get("visual_language", []) or []:
            canonical = canonicalize(str(tag))
            if canonical not in style:
                style.append(canonical)
        return SemanticQuery(
            raw=raw,
            object=parsed.get("object", ""),
            attributes=list(parsed.get("attributes", [])),
            style=style,
            negative_terms=list(parsed.get("negative_terms", [])),
        )

    def compile(self, request: AssetRequest, provider_id: str,
                provider_kind: str) -> ProviderQuery:
        """provider_kind: user / local / online——输出各自消费的字段形态。"""

        query = self.parse(request)
        required, _preferred = split_technical(request.technical_requirements)
        canonical_terms = [t for t in [query.object] + query.attributes + query.style if t]

        filters: Dict[str, Any] = {
            "asset_type": request.asset_type,
            "media_type": request.media_type,
        }
        filters.update(required)

        if provider_kind == "online":
            hints = {
                "sticker": "sticker transparent png",
                "image": "image",
                "background": "background wallpaper",
                "music": "music track",
                "sound_effect": "sound effect",
            }
            text = " ".join(canonical_terms + [hints.get(request.asset_type, "")]).strip()
            return ProviderQuery(
                provider_id=provider_id,
                canonical_terms=canonical_terms,
                text_query=text or request.semantic_query,
                filters={},
                negative_terms=list(query.negative_terms),
            )
        if provider_kind == "user":
            # 用户素材索引包含 caption/filename，保留 raw 提高自然语言引用命中
            text = " ".join([request.semantic_query] + canonical_terms).strip()
            return ProviderQuery(
                provider_id=provider_id,
                canonical_terms=canonical_terms,
                text_query=text,
                filters=filters,
                negative_terms=list(query.negative_terms),
            )
        # local：manifest 结构化检索，canonical_terms + filters 为主
        return ProviderQuery(
            provider_id=provider_id,
            canonical_terms=canonical_terms,
            text_query=request.semantic_query,
            filters=filters,
            negative_terms=list(query.negative_terms),
        )
