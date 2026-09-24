"""Query Compiler（§15-16）：词表解析、provider 差异、白名单校验。"""

from __future__ import annotations

from editing_planner.models import AssetRequest

from asset_manager.query.compiler import (
    LexiconQueryRewriter,
    QueryCompiler,
    parse_semantic_terms,
    split_technical,
)


def _req(**kw):
    base = dict(request_uid="r1", asset_type="sticker", media_type="image")
    base.update(kw)
    return AssetRequest(**base)


def test_parse_chinese_query():
    parsed = parse_semantic_terms("可爱的粉色爱心")
    assert parsed["object"] == "heart"
    assert "pink" in parsed["attributes"]
    assert "cute" in parsed["style"]


def test_parse_negative_terms():
    parsed = parse_semantic_terms("粉色爱心 不要红色")
    assert "red" in parsed["negative_terms"]


def test_local_query_has_filters_and_terms():
    compiler = QueryCompiler()
    req = _req(semantic_query="可爱的粉色爱心",
               technical_requirements={"has_alpha": True, "min_width": 512})
    q = compiler.compile(req, "local_library", "local")
    assert q.filters["has_alpha"] is True
    assert q.filters["min_width"] == 512
    assert q.filters["asset_type"] == "sticker"
    assert "heart" in q.canonical_terms and "pink" in q.canonical_terms


def test_online_query_text_hint():
    compiler = QueryCompiler()
    req = _req(semantic_query="可爱的粉色爱心")
    q = compiler.compile(req, "online_assets", "online")
    assert "heart" in q.text_query and "transparent" in q.text_query


def test_split_technical_flat_is_required():
    required, preferred = split_technical({"has_alpha": True})
    assert required == {"has_alpha": True} and preferred == {}
    required, preferred = split_technical(
        {"required": {"min_width": 512}, "preferred": {"aspect_ratio": 1.0}})
    assert required["min_width"] == 512 and preferred["aspect_ratio"] == 1.0


class InventingRewriter:
    """模拟 LLM 加无依据词（§16：不许自由创作）。"""

    def rewrite(self, semantic_query, context):
        return {
            "object": "heart",
            "attributes": ["pink", "metallic"],  # metallic 不在词表也不在原文
            "style": ["cute", "3d"],
            "negative_terms": [],
        }


def test_rewriter_whitelist_drops_unfounded_terms():
    compiler = QueryCompiler(rewriter=InventingRewriter())
    req = _req(semantic_query="可爱的粉色爱心")
    query = compiler.parse(req)
    assert query.object == "heart"
    assert "pink" in query.attributes and "metallic" not in query.attributes
    assert "cute" in query.style and "3d" not in query.style


def test_style_context_merges_into_style():
    compiler = QueryCompiler()
    req = _req(semantic_query="爱心",
               style_context={"visual_language": ["夏日", "pastel"]})
    query = compiler.parse(req)
    assert "summer" in query.style and "pastel" in query.style
