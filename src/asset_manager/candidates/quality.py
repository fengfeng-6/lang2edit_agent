"""Visual Score（§38）：可用性质量，不是主观审美。

评价清晰度下限、透明边缘质量、主体占比合理性、水印/异常信号；
缺像素统计（在线未下载）给中性分，让 Import 复核兜底。
"""

from __future__ import annotations

from ..models import AssetCandidate


def visual_score(candidate: AssetCandidate) -> float:
    tech = candidate.technical_metadata
    score = 0.7  # 中性基线
    if tech.width and tech.height:
        pixels = tech.width * tech.height
        if pixels >= 512 * 512:
            score += 0.2
        elif pixels >= 256 * 256:
            score += 0.1
        elif pixels < 64 * 64:
            score -= 0.4
    if tech.has_alpha and tech.alpha_ratio is not None:
        # 透明贴纸：alpha 占比极端（≈0 或 ≈1）说明透明度名不副实
        if 0.02 < tech.alpha_ratio < 0.98:
            score += 0.1
        else:
            score -= 0.2
    tags = {t.lower() for t in candidate.semantic_metadata.usage_tags}
    text = candidate.semantic_metadata.caption.lower()
    if {"watermark", "低清", "模糊"} & tags or "watermark" in text:
        score -= 0.4
    return max(0.0, min(1.0, score))
