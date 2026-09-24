"""Candidate 去重（§51）：exact hash + perceptual hash + 来源指纹。"""

from __future__ import annotations

import hashlib
from typing import List, Tuple

from ..models import AssetCandidate, RejectedCandidate


def _phash_distance(a: str, b: str) -> int:
    """两个 hex dHash 的汉明距离；任一缺失返回一个大数。"""

    if not a or not b:
        return 1 << 30
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 1 << 30


def dedup_key(candidate: AssetCandidate) -> str:
    """有内容指纹用指纹，否则规范化的来源 uri。"""

    provenance = candidate.provenance or {}
    content = provenance.get("content_hash", "")
    if content:
        return f"sha256:{content}"
    ref = (candidate.original_uri or candidate.source_ref or candidate.candidate_uid)
    return "uri:" + hashlib.sha1(ref.encode()).hexdigest()


def dedup_candidates(
    candidates: List[AssetCandidate], phash_tolerance: int = 3
) -> Tuple[List[AssetCandidate], List[RejectedCandidate]]:
    """同内容只留首见（Provider 顺序即优先级）。"""

    seen_keys = {}
    seen_hashes: List[Tuple[str, AssetCandidate]] = []
    out: List[AssetCandidate] = []
    rejected: List[RejectedCandidate] = []
    for cand in candidates:
        key = dedup_key(cand)
        if key in seen_keys:
            rejected.append(RejectedCandidate(
                candidate_uid=cand.candidate_uid, reason="dedup_exact",
                detail=f"same as {seen_keys[key]}"))
            continue
        phash = (cand.provenance or {}).get("perceptual_hash", "")
        dup_of = None
        if phash:
            for kept_hash, kept in seen_hashes:
                if _phash_distance(phash, kept_hash) <= phash_tolerance:
                    dup_of = kept
                    break
        if dup_of is not None:
            rejected.append(RejectedCandidate(
                candidate_uid=cand.candidate_uid, reason="dedup_perceptual",
                detail=f"same as {dup_of.candidate_uid}"))
            continue
        seen_keys[key] = cand.candidate_uid
        if phash:
            seen_hashes.append((phash, cand))
        out.append(cand)
    return out, rejected
