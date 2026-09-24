"""Hard Requirement 过滤（§29-31）：required 是闸门，不进 Ranking。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from editing_planner.models import AssetRequest

from ..models import AssetCandidate, RejectedCandidate
from ..query.compiler import split_technical

_LICENSE_BLOCK = {"restricted", "proprietary", "unlicensed"}
_LICENSE_CLEAR = {"cleared", "cc0", "cc_by", "cc_by_sa", "royalty_free", "user_provided"}


def _license_ok(license_type: str, licensing_policy: str) -> bool:
    lt = (license_type or "unknown").lower()
    if lt in _LICENSE_BLOCK:
        return False
    if licensing_policy in ("cleared_only", "cc0_only"):
        return lt in _LICENSE_CLEAR
    return True


def _violations(candidate: AssetCandidate, required: Dict[str, Any],
                request: AssetRequest) -> List[str]:
    tech = candidate.technical_metadata
    rules: List[str] = []

    # media_type 对齐（§30-31：background 第一阶段只收静态图）
    if request.media_type and candidate.media_type and candidate.media_type != request.media_type:
        rules.append("media_type_mismatch")
    if request.asset_type == "background" and candidate.media_type not in ("", "image"):
        rules.append("background_not_static_image")

    # 可解码：已检过的必须过；在线未下载候选（partial）暂时放行由 Import 复核
    if candidate.provenance.get("inspected") and not candidate.provenance.get("decodable", True):
        rules.append("not_decodable")

    if required.get("min_width") and tech.width and tech.width < int(required["min_width"]):
        rules.append("min_width")
    if required.get("min_height") and tech.height and tech.height < int(required["min_height"]):
        rules.append("min_height")
    if required.get("min_resolution"):
        need = int(required["min_resolution"])
        if tech.width and tech.height and tech.width * tech.height < need:
            rules.append("min_resolution")
    if required.get("has_alpha") and tech.has_alpha is False:
        rules.append("alpha_required")
    if required.get("max_duration") and tech.duration and tech.duration > float(required["max_duration"]):
        rules.append("max_duration")
    if required.get("min_duration") and tech.duration and tech.duration < float(required["min_duration"]):
        rules.append("min_duration")
    if required.get("bpm_range") and tech.bpm:
        lo, hi = required["bpm_range"]
        if not (float(lo) <= tech.bpm <= float(hi)):
            rules.append("bpm_range")

    if not _license_ok(candidate.license_metadata.type, request.licensing_policy):
        rules.append("license")

    # 语义负词硬剔除
    query = request.semantic_query or ""
    meta = candidate.semantic_metadata
    fields = " ".join([meta.object] + meta.attributes + meta.style + [meta.caption]).lower()
    for marker in ("不要", "无", "without"):
        if marker in query:
            idx = query.find(marker)
            term = query[idx + len(marker):idx + len(marker) + 8].strip().split()[0:1]
            if term and term[0].lower() in fields:
                rules.append("negative_term")
    return rules


def hard_filter(
    candidates: List[AssetCandidate], request: AssetRequest
) -> Tuple[List[AssetCandidate], List[RejectedCandidate]]:
    """C_valid = {a ∈ C | H(a)=1}（§29）。"""

    required, _preferred = split_technical(request.technical_requirements)
    passed: List[AssetCandidate] = []
    rejected: List[RejectedCandidate] = []
    for cand in candidates:
        rules = _violations(cand, required, request)
        if rules:
            rejected.append(RejectedCandidate(
                candidate_uid=cand.candidate_uid,
                reason="hard_filter",
                detail=",".join(rules),
            ))
        else:
            passed.append(cand)
    return passed, rejected
