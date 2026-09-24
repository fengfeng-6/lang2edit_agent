"""Candidate 标准化（§28）：Provider 返回统一归一成 canonical 字段。

Provider 侧已尽量给结构，本步做兜底：语义字段走词表 canonical、
aspect_ratio 补齐、license type 归一、negative 词剔除。Filter/Rank
不再接触 Provider 原始响应。
"""

from __future__ import annotations

from typing import List, Tuple

from ..models import AssetCandidate, RejectedCandidate
from ..query.lexicon import canonicalize

_LICENSE_ALIASES = {
    "cleared": "cleared",
    "cc0": "cc0",
    "cc-0": "cc0",
    "public domain": "cc0",
    "cc_by": "cc_by",
    "cc-by": "cc_by",
    "cc_by_sa": "cc_by_sa",
    "cc-by-sa": "cc_by_sa",
    "royalty_free": "royalty_free",
    "royalty-free": "royalty_free",
    "user_provided": "user_provided",
    "restricted": "restricted",
    "proprietary": "restricted",
}


def normalize_license(raw: str) -> str:
    return _LICENSE_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower() or "unknown")


def normalize_candidate(candidate: AssetCandidate) -> AssetCandidate:
    meta = candidate.semantic_metadata
    if meta.object:
        meta.object = canonicalize(meta.object)
    meta.attributes = [canonicalize(a) for a in meta.attributes]
    meta.style = [canonicalize(s) for s in meta.style]
    tech = candidate.technical_metadata
    if tech.width and tech.height and not tech.aspect_ratio:
        tech.aspect_ratio = tech.width / tech.height
    candidate.license_metadata.type = normalize_license(candidate.license_metadata.type)
    return candidate


def normalize_batch(
    candidates: List[AssetCandidate],
) -> Tuple[List[AssetCandidate], List[RejectedCandidate]]:
    """逐个标准化；异常单条记 rejected 不拖垮整批。"""

    out: List[AssetCandidate] = []
    rejected: List[RejectedCandidate] = []
    for cand in candidates:
        try:
            out.append(normalize_candidate(cand))
        except Exception as exc:  # noqa: BLE001
            rejected.append(RejectedCandidate(
                candidate_uid=cand.candidate_uid,
                reason="normalize_failed",
                detail=f"{type(exc).__name__}: {exc}",
            ))
    return out, rejected
