"""Open Semantic Detector（§24）：两阶段低成本候选 + 语义验证。

    C = Proposal(V)        低成本候选动作区间（运动能量峰值）
    E = SemanticVerify(C, q)   开放语义模型确认

第一阶段不内置 VLM：``OpenSemanticVerifier`` 是可插拔协议，宿主可接入
任意多模态模型。未配置 verifier 时 query 记 ``failed``（技术错误，
区别于 ``not_found``，§42），并携带已产出的候选区间供上层参考。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, Tuple

from ..models import DenseSpatialTracks, TemporalSpan
from ..pose.motion import motion_energy
from .aggregator import AggregatedSpan, TemporalConfig, aggregate


class OpenSemanticVerifier(Protocol):
    """语义验证器协议：确认候选区间是否匹配自然语言描述。"""

    name: str

    def verify(self, candidates: Sequence[TemporalSpan], description: str) -> List[Tuple[TemporalSpan, float]]:
        """返回 [(span, confidence)]，空列表表示都不匹配。"""
        ...


class MotionEnergyProposer:
    """Cheap Candidate Proposal：运动能量显著区间作为动作候选。"""

    name = "motion_energy_v1"

    def __init__(self, *, threshold_ratio: float = 0.45, max_candidates: int = 8):
        self.threshold_ratio = threshold_ratio
        self.max_candidates = max_candidates

    def propose(self, tracks: DenseSpatialTracks) -> List[AggregatedSpan]:
        energies = motion_energy(tracks)
        if not energies:
            return []
        peak = max(v for _, v in energies)
        if peak <= 1e-6:
            return []
        # 能量转置信度：相对峰值归一后走统一聚合器。
        samples = [(t, min(1.0, v / peak)) for t, v in energies]
        config = TemporalConfig(
            threshold=self.threshold_ratio,
            candidate_threshold=self.threshold_ratio * 0.6,
            min_duration=0.2,
            merge_gap=0.35,
            smooth_half_window=2,
        )
        spans = aggregate(samples, config)
        spans.sort(key=lambda s: s.confidence, reverse=True)
        return spans[: self.max_candidates]


@dataclass
class OpenSemanticResult:
    candidates: List[TemporalSpan]
    verified: List[Tuple[TemporalSpan, float]]
    verifier_name: Optional[str]


def detect_open_semantic(
    tracks: DenseSpatialTracks,
    description: str,
    *,
    verifier: Optional[OpenSemanticVerifier],
    proposer: Optional[MotionEnergyProposer] = None,
) -> OpenSemanticResult:
    proposer = proposer or MotionEnergyProposer()
    candidates = proposer.propose(tracks)
    spans = [c.temporal for c in candidates]
    if verifier is None:
        return OpenSemanticResult(candidates=spans, verified=[], verifier_name=None)
    verified = verifier.verify(spans, description)
    return OpenSemanticResult(candidates=spans, verified=verified, verifier_name=verifier.name)
