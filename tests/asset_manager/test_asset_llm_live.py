"""LLMQueryRewriter 真实端点测试（§16）：大批量语义改写语料。

需要任一 LLM 环境变量组（ASSET_LLM_* / PLANNER_LLM_* /
INTENT_LLM_* / OPENAI_*），否则整个文件 skip。这些测试验证的是
真实 LLM 的两条不变量：

1. **白名单纪律**（§16 硬约束）：经 QueryCompiler 后，任何词不在
   词表也不在原文的一律不得出现；
2. **语义质量**（软约束）：canonical 词命中预期集合——LLM 非确定，
   断言写成"命中任一期望词"而不是精确相等。
"""

from __future__ import annotations

import os

import pytest

from asset_manager import AssetManager, ResolutionStatus
from asset_manager.query.compiler import QueryCompiler
from asset_manager.query.lexicon import ALIAS_TO_CANONICAL
from asset_manager.query.llm import OpenAICompatibleQueryRewriter
from asset_fixtures import lib_entry, make_png, request_dict, write_manifest
from editing_planner.models import AssetRequest

_VOCAB = set(ALIAS_TO_CANONICAL.values())


def _live_rewriter() -> OpenAICompatibleQueryRewriter | None:
    for prefix in ("ASSET_LLM", "PLANNER_LLM", "INTENT_LLM", "OPENAI"):
        key = os.getenv(f"{prefix}_API_KEY")
        if not key:
            continue
        return OpenAICompatibleQueryRewriter(
            os.getenv(f"{prefix}_BASE_URL")
            or OpenAICompatibleQueryRewriter.DEFAULT_BASE_URL,
            key,
            os.getenv(f"{prefix}_MODEL")
            or OpenAICompatibleQueryRewriter.DEFAULT_MODEL,
            timeout=float(os.getenv(f"{prefix}_TIMEOUT", "60")),
        )
    return None


_REWRITER = _live_rewriter()
pytestmark = pytest.mark.skipif(_REWRITER is None, reason="no LLM env configured")
_COMPILER = QueryCompiler(_REWRITER) if _REWRITER else None


def _parse(query: str, asset_type: str = "sticker", **kw):
    req = AssetRequest(**request_dict(
        semantic_query=query, asset_type=asset_type,
        media_type="audio" if asset_type in ("music", "sound_effect") else "image",
        **kw))
    return _COMPILER.parse(req)


def _whitelist_ok(parsed, raw: str) -> bool:
    raw_lower = raw.lower()
    for term in ([parsed.object] + parsed.attributes + parsed.style
                 + parsed.negative_terms):
        t = term.lower()
        if t and t not in _VOCAB and t not in raw_lower:
            return False
    return True


# (query, asset_type, 期望 object 集合, attributes/style 任一命中, negative 必含)
CASES = [
    # —— 文档 §16 原例 ——
    ("少女一点的粉色爱心", "sticker", {"heart"}, {"pink", "pastel", "cute", "soft"}, set()),
    # —— 中文口语/同义词 ——
    ("给我来个卡哇伊的心形贴纸", "sticker", {"heart"}, {"cute"}, set()),
    ("比个心的那种素材", "sticker", {"heart"}, set(), set()),
    ("亮晶晶的星星，越小越好", "sticker", {"star"}, {"small", "glowing"}, set()),
    ("梦幻一点的蝴蝶", "sticker", {"butterfly"}, {"dreamy"}, set()),
    ("彩带纸屑那种感觉", "sticker", {"confetti"}, set(), set()),
    ("找个金色皇冠", "sticker", {"crown"}, {"gold"}, set()),
    ("透明的小泡泡", "sticker", {"bubble"}, {"transparent", "small"}, set()),
    # —— 英文与中英混合 ——
    ("cute pink heart sticker", "sticker", {"heart"}, {"pink", "cute"}, set()),
    ("a minimal rainbow", "sticker", {"rainbow"}, {"minimal"}, set()),
    ("retro 风格的 gift 盒子", "sticker", {"gift"}, {"retro"}, set()),
    ("snowflake, winter vibe", "sticker", {"snowflake"}, set(), set()),
    # —— 否定 ——
    ("爱心贴纸，不要红色", "sticker", {"heart"}, set(), {"red"}),
    ("star but without glitter", "sticker", {"star"}, set(), set()),
    ("花朵，无粉色", "sticker", {"flower"}, set(), {"pink"}),
    # —— 背景/场景 ——
    ("动漫风夏日海滩背景", "background", {"beach"}, {"anime", "summer"}, set()),
    ("竖屏城市夜景", "background", {"city"}, set(), set()),
    ("天空云朵背景，明亮一点", "background", {"sky", "cloud", "background"}, {"bright"}, set()),
    ("山和海边的极简背景", "background", {"mountain", "sea", "background"}, {"minimal"}, set()),
    # —— 音乐 ——
    ("轻快夏日流行音乐", "music", {"", "music_note"}, {"upbeat", "summer", "pop"}, set()),
    ("舒缓的钢琴曲", "music", {"", "music_note"}, {"calm", "piano"}, set()),
    ("电子动感bgm", "music", {"", "music_note"}, {"electronic", "energetic"}, set()),
    ("lo-fi 低保真背景音乐", "music", {"", "music_note"}, {"lofi"}, set()),
    # —— 词表外词：LLM 可回显原文或整词丢弃，但不得编造词表外新词 ——
    ("赛博朋克风的霓虹贴纸", "sticker", {"", "sparkle"}, set(), set()),
    ("像素风爱心", "sticker", {"heart", ""}, set(), set()),
    # —— 模糊/无关输入 ——
    ("随便来点好看的", "sticker", set(), set(), set()),
    ("今天天气真不错", "sticker", set(), set(), set()),
]

#: 年龄偏大/残障用户语料：口语化、儿化叠词、碎片句、ASR 语气词、
#: 感官诉求（怕晃眼/要大字/要亮堂）、方言自称、自我纠正、
#: 词表外的老年高频意象（福/寿/牡丹/戏曲/广场舞）。
ELDERLY_CASES = [
    # —— 儿化/叠词/称呼 ——
    ("给我弄个花儿，红的那种", "sticker", {"flower"}, {"red"}, set()),
    ("小星星，要多来点闪的", "sticker", {"star"}, {"small", "glowing"}, set()),
    ("孙女喜欢的卡哇伊贴画", "sticker", set(), {"cute"}, set()),
    ("俺想要个大红的气球", "sticker", {"balloon"}, {"red", "big"}, set()),
    # —— ASR 语气词/碎片句/自我纠正 ——
    ("那个……嗯……爱心，粉的", "sticker", {"heart"}, {"pink"}, set()),
    ("花。红的。大一点。", "sticker", {"flower"}, {"red", "big"}, set()),
    ("鸟……不对不对，蝴蝶，要蓝的", "sticker", {"butterfly", ""}, {"blue"}, set()),
    ("月亮……就是天上那个月亮", "sticker", {"moon"}, set(), set()),
    # —— 视力退化诉求（低视力/老花/怕晃） ——
    ("老花眼看不清，整大点的", "sticker", set(), {"big"}, set()),
    ("眼睛怕晃，别整太闪的", "sticker", set(), set(), {"glowing", "sparkle"}),
    ("亮堂点的背景，屋里暗", "background", {"", "background"}, {"bright"}, set()),
    ("颜色深点明显点，别太艳", "sticker", set(), set(), set()),
    # —— 听力/节奏诉求 ——
    ("慢悠悠的歌，别吵", "music", {"", "music_note"}, {"calm"}, set()),
    ("跳广场舞那种热闹曲子", "music", {"", "music_note"}, {"upbeat", "energetic"}, set()),
    ("抒情一点的二胡曲子", "music", {"", "music_note"}, {"emotional"}, set()),
    ("声音轻点的音乐，睡觉听", "music", {"", "music_note"}, {"calm"}, set()),
    # —— 老年高频意象（词表外 → 回显或丢弃都合规） ——
    ("过大寿用的，喜庆点", "sticker", set(), set(), set()),
    ("牡丹花那种富贵的", "sticker", {"flower", ""}, set(), set()),
    ("戏曲调调的背景音乐", "music", {"", "music_note"}, set(), set()),
    ("福字贴纸，过年贴", "sticker", set(), set(), set()),
    # —— 动作受限相关表述（与素材语义无关，须不编造） ——
    ("手抖得厉害，图案简单点", "sticker", set(), {"minimal", "flat"}, set()),
    ("轮椅上跳舞用的，素净点的", "sticker", set(), set(), set()),
]


def _check(parsed, query, objects, any_terms, negatives):
    assert _whitelist_ok(parsed, query), (
        f"白名单外泄: {parsed} raw={query!r}")
    if objects:
        assert parsed.object in objects, f"object={parsed.object!r} 期望 {objects}"
    if any_terms:
        hit = (set(parsed.attributes) | set(parsed.style)) & any_terms
        assert hit, f"{parsed} 未命中任一期望词 {any_terms}"
    for neg in negatives:
        assert neg in parsed.negative_terms, (
            f"negative_terms={parsed.negative_terms} 缺少 {neg}")


@pytest.mark.parametrize(
    "query,asset_type,objects,any_terms,negatives", CASES,
    ids=[c[0] for c in CASES],
)
def test_live_rewrite(query, asset_type, objects, any_terms, negatives):
    _check(_parse(query, asset_type), query, objects, any_terms, negatives)


@pytest.mark.parametrize(
    "query,asset_type,objects,any_terms,negatives", ELDERLY_CASES,
    ids=[c[0] for c in ELDERLY_CASES],
)
def test_live_rewrite_elderly(query, asset_type, objects, any_terms, negatives):
    _check(_parse(query, asset_type), query, objects, any_terms, negatives)


def test_live_consistency_same_query_twice():
    """temperature=0 下同一条查询两次改写应一致（object 强约束）。"""

    a = _parse("梦幻的粉色爱心贴纸")
    b = _parse("梦幻的粉色爱心贴纸")
    assert _whitelist_ok(a, "梦幻的粉色爱心贴纸")
    assert _whitelist_ok(b, "梦幻的粉色爱心贴纸")
    assert a.object == b.object
    assert set(a.attributes) == set(b.attributes)
    assert set(a.style) == set(b.style)


def test_live_e2e_resolve_with_llm_rewrite(tmp_path):
    """真实 LLM 改写进完整解析链路：本地库命中并绑定。"""

    lib = tmp_path / "library"
    make_png(str(lib / "pink_heart.png"), size=(128, 128), shape="ellipse")
    make_png(str(lib / "red_star.png"), size=(128, 128), shape="cross",
             color=(255, 0, 0, 255))
    write_manifest(str(lib), [
        lib_entry("lib_heart", "pink_heart.png", obj="heart",
                  attributes=["pink"], style=["cute", "pastel"],
                  technical={"has_alpha": True}),
        lib_entry("lib_star", "red_star.png", obj="star",
                  attributes=["red"], style=["bright"],
                  technical={"has_alpha": True}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="live", query_rewriter=_REWRITER)
    req = request_dict(semantic_query="少女一点的粉色爱心")
    result = mgr.resolve_assets([req])
    assert result.status in (
        ResolutionStatus.resolved, ResolutionStatus.resolved_with_warnings)
    record = mgr.registry.get(result.bindings[0].asset_uid)
    assert record.semantic_metadata.object == "heart"
    # 编译进 ProviderQuery 的词必须过白名单
    for pq in result.search_records[-1].queries.values():
        terms = pq["canonical_terms"] if isinstance(pq, dict) else pq.canonical_terms
        for term in terms:
            assert term.lower() in _VOCAB or term.lower() in "少女一点的粉色爱心"
