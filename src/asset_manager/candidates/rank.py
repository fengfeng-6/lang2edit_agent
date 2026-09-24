"""Candidate Ranking（§32-44）：透明加权排序，不做学习模型。

S = ws·semantic + wt·technical + wv·visual + wl·license + wu·usage

- semantic 拆 object/attribute/style/text 四项（§33）；
- technical 只评 preferred 项与规格贴合度，hard 项已在 Filter 处理（§37）；
- visual 评可用性质量（§38 → quality.py）；
- usage 评"是否适合当前剪辑用途"（§39/§43-44/§72）——坐姿画像只改
  usage 偏好（紧凑、避让、低杂度），不改素材主题（§40/§71）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from editing_planner.models import AssetRequest

from ..models import (
    AssetCandidate,
    RankedCandidate,
    SemanticQuery,
)
from ..query.compiler import split_technical
from ..query.lexicon import canonicalize
from .quality import visual_score

#: asset_type → 五维权重
WEIGHTS: Dict[str, Dict[str, float]] = {
    "sticker": {"semantic": 0.45, "technical": 0.15, "visual": 0.15, "license": 0.05, "usage": 0.20},
    "image": {"semantic": 0.50, "technical": 0.15, "visual": 0.20, "license": 0.05, "usage": 0.10},
    "background": {"semantic": 0.40, "technical": 0.20, "visual": 0.15, "license": 0.05, "usage": 0.20},
    "music": {"semantic": 0.40, "technical": 0.25, "visual": 0.0, "license": 0.15, "usage": 0.20},
    "sound_effect": {"semantic": 0.40, "technical": 0.25, "visual": 0.0, "license": 0.15, "usage": 0.20},
    "_default": {"semantic": 0.45, "technical": 0.20, "visual": 0.15, "license": 0.05, "usage": 0.15},
}

_LICENSE_SCORE = {
    "cleared": 1.0, "cc0": 1.0, "user_provided": 1.0, "royalty_free": 0.9,
    "cc_by": 0.8, "cc_by_sa": 0.75, "unknown": 0.4, "restricted": 0.0,
}

_OVERLAY_TAGS = {"gesture_overlay", "compact_overlay", "hand_follow", "overlay_friendly"}
_LOW_CLUTTER_TAGS = {"low_clutter", "clean", "minimal", "solid", "gradient", "simple_background"}


def _overlap(want: List[str], have: List[str]) -> float:
    w = {t.lower() for t in want if t}
    if not w:
        return 0.0
    h = {t.lower() for t in have if t}
    return len(w & h) / len(w)


def semantic_score(query: SemanticQuery, candidate: AssetCandidate) -> float:
    """α·object + β·attribute + γ·style + δ·text（§33）。"""

    meta = candidate.semantic_metadata
    cand_obj = meta.object.lower()
    s_object = 0.0
    if query.object:
        q = query.object.lower()
        if cand_obj == q:
            s_object = 1.0
        elif q and (q in cand_obj or cand_obj in q):
            s_object = 0.5
        elif not cand_obj:
            s_object = 0.2  # 未标注 object 的候选不给死 0，但显著低于命中
    else:
        s_object = 0.3
    s_attr = _overlap(query.attributes, meta.attributes)
    s_style = _overlap(query.style, meta.style)
    text_fields = " ".join(
        [meta.caption] + meta.attributes + meta.style + meta.usage_tags
    ).lower()
    raw_terms = [t for t in query.raw.lower().split() if len(t) > 1]
    s_text = (
        sum(1 for t in raw_terms if t in text_fields) / len(raw_terms)
        if raw_terms else 0.0
    )
    return 0.5 * s_object + 0.2 * s_attr + 0.15 * s_style + 0.15 * s_text


def _aspect_fit(asset_ratio: float, canvas_ratio: float) -> float:
    """比例贴合度（§43）：对数比例差越大分越低。"""

    if not asset_ratio or not canvas_ratio:
        return 0.5
    diff = abs(math.log(asset_ratio / canvas_ratio))
    return max(0.0, 1.0 - diff / math.log(4.0))


def technical_score(request: AssetRequest, candidate: AssetCandidate) -> float:
    """preferred 项满足度 + 规格贴合（§37）。"""

    _required, preferred = split_technical(request.technical_requirements)
    tech = candidate.technical_metadata
    checks: List[float] = []
    if preferred.get("aspect_ratio"):
        checks.append(_aspect_fit(tech.aspect_ratio, float(preferred["aspect_ratio"])))
    if preferred.get("min_width") and tech.width:
        checks.append(min(1.0, tech.width / float(preferred["min_width"])))
    if preferred.get("min_height") and tech.height:
        checks.append(min(1.0, tech.height / float(preferred["min_height"])))
    canvas = request.usage_context.get("canvas_aspect_ratio")
    if canvas and tech.aspect_ratio:
        checks.append(_aspect_fit(tech.aspect_ratio, float(canvas)))
    if preferred.get("bpm_range") and tech.bpm:
        lo, hi = preferred["bpm_range"]
        checks.append(1.0 if float(lo) <= tech.bpm <= float(hi) else 0.3)
    if not checks:
        # 无 preferred 要求时按基础规格充足度给分
        if tech.width and tech.height:
            return 0.8 if tech.width * tech.height >= 256 * 256 else 0.5
        if tech.duration:
            return 0.7
        return 0.5
    return sum(checks) / len(checks)


def license_score(candidate: AssetCandidate) -> float:
    return _LICENSE_SCORE.get(candidate.license_metadata.type.lower(), 0.4)


def usage_score(request: AssetRequest, candidate: AssetCandidate) -> float:
    """用途贴合（§39/§44/§72）：usage_context 只调偏好，不改主题。"""

    ctx = request.usage_context or {}
    tech = candidate.technical_metadata
    meta = candidate.semantic_metadata
    score = 0.5

    if request.asset_type in ("sticker", "image"):
        if ctx.get("preferred_compact_shape") or ctx.get("compact_shape"):
            if tech.aspect_ratio and 0.6 <= tech.aspect_ratio <= 1.6:
                score += 0.2
            if tech.foreground_occupancy is not None:
                score += 0.2 * min(1.0, tech.foreground_occupancy / 0.4)
        elif tech.foreground_occupancy is not None:
            score += 0.1 * min(1.0, tech.foreground_occupancy / 0.4)
        if _OVERLAY_TAGS & {t.lower() for t in meta.usage_tags}:
            score += 0.15
    elif request.asset_type == "background":
        canvas = ctx.get("canvas_aspect_ratio")
        if canvas and tech.aspect_ratio:
            score += 0.3 * _aspect_fit(tech.aspect_ratio, float(canvas))
        clutter_pref = str(ctx.get("preferred_background_clutter", "")).lower()
        if clutter_pref == "low" and _LOW_CLUTTER_TAGS & {t.lower() for t in meta.usage_tags + meta.style}:
            score += 0.2
        if ctx.get("subject_region") and "subject_friendly" in {t.lower() for t in meta.usage_tags}:
            score += 0.1
    elif request.asset_type in ("music", "sound_effect"):
        mood = [t for t in meta.style]
        wanted = [canonicalize(s) for s in ctx.get("mood", [])] if ctx.get("mood") else []
        if wanted and _overlap(wanted, mood):
            score += 0.25
        if tech.duration and request.technical_requirements.get("min_duration"):
            score += 0.1
    return max(0.0, min(1.0, score))


def rank_candidates(
    candidates: List[AssetCandidate],
    request: AssetRequest,
    query: Optional[SemanticQuery] = None,
    weights: Optional[Dict[str, float]] = None,
) -> List[RankedCandidate]:
    """按 S(a) 降序；breakdown 保留五维明细供 Binding/SearchRecord 溯源。"""

    query = query or SemanticQuery(raw=request.semantic_query)
    w = dict(WEIGHTS.get(request.asset_type, WEIGHTS["_default"]))
    if weights:
        w.update(weights)
    ranked: List[RankedCandidate] = []
    for cand in candidates:
        parts = {
            "semantic": semantic_score(query, cand),
            "technical": technical_score(request, cand),
            "visual": visual_score(cand),
            "license": license_score(cand),
            "usage": usage_score(request, cand),
        }
        total = sum(w[k] * parts[k] for k in w)
        # Provider retrieval_score 是弱特征（§27）：最多 ±0.02 微调
        total += 0.02 * (cand.retrieval_metadata.retrieval_score - 0.5)
        ranked.append(RankedCandidate(
            candidate_uid=cand.candidate_uid, score=round(total, 6),
            breakdown={k: round(v, 6) for k, v in parts.items()},
        ))
    ranked.sort(key=lambda r: -r.score)
    return ranked
