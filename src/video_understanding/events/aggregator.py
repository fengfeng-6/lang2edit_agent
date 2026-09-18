"""Temporal Event Aggregator（§19-20）。

把逐帧 / 逐窗口的原始置信度序列 p_e(t) 聚合成独立语义事件：

    smoothing（滑动平均 p̄ = 1/(2k+1) Σ p）
      → thresholding（候选分割）
      → gap merging（小缺口合并）
      → minimum duration filtering
      → peak localization（peak = argmax p）

双阈值：峰值 ≥ confirm_threshold → confirmed；
[candidate_threshold, confirm_threshold) → uncertain（§44 低置信度事件
不直接删除）。参数属于 Event Registry 的事件级配置（§20），不同事件
允许不同的 min_duration / merge_gap / threshold。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..models import ConfidenceStatus, TemporalSpan

ConfidenceSample = Tuple[float, float]  # (timestamp, confidence)


@dataclass
class TemporalConfig:
    """事件级聚合参数（§20）。"""

    threshold: float = 0.55  # 确认阈值 θ
    candidate_threshold: float = 0.35  # 候选阈值（低于 θ 但仍有价值）
    min_duration: float = 0.15  # 秒
    merge_gap: float = 0.20  # 秒，候选段之间小于此缺口则合并
    smooth_half_window: int = 1  # 滑动平均半窗 k（单位：采样点）
    max_duration: Optional[float] = None  # 超过则按峰拆分的上限（暂只截断置信）


@dataclass
class AggregatedSpan:
    """聚合后的候选事件区间。"""

    temporal: TemporalSpan
    confidence: float  # peak 处的原始置信度
    status: ConfidenceStatus
    sample_count: int = 0


def moving_average(samples: Sequence[ConfidenceSample], k: int) -> List[ConfidenceSample]:
    """p̄_t = 1/(2k+1) Σ p_{t+i}（§19 平滑）。"""
    if k <= 0 or len(samples) < 3:
        return list(samples)
    values = [c for _, c in samples]
    out: List[ConfidenceSample] = []
    for i, (t, _) in enumerate(samples):
        lo = max(0, i - k)
        hi = min(len(values), i + k + 1)
        out.append((t, sum(values[lo:hi]) / (hi - lo)))
    return out


def _segments(samples: Sequence[ConfidenceSample], threshold: float) -> List[Tuple[int, int]]:
    """p̄ > θ 的连续段（返回样本下标区间 [lo, hi)）。"""
    spans: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for i, (_, conf) in enumerate(samples):
        if conf > threshold:
            if start is None:
                start = i
        elif start is not None:
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, len(samples)))
    return spans


def _merge_gaps(spans: List[Tuple[int, int]], samples: Sequence[ConfidenceSample], merge_gap: float) -> List[Tuple[int, int]]:
    """相邻候选段时间缺口 ≤ merge_gap 时合并为同一次动作（§19）。"""
    if not spans:
        return spans
    merged = [spans[0]]
    for lo, hi in spans[1:]:
        prev_lo, prev_hi = merged[-1]
        gap = samples[lo][0] - samples[prev_hi - 1][0]
        if gap <= merge_gap:
            merged[-1] = (prev_lo, hi)
        else:
            merged.append((lo, hi))
    return merged


def aggregate(samples: Sequence[ConfidenceSample], config: TemporalConfig) -> List[AggregatedSpan]:
    """原始置信度序列 → 语义事件候选区间。

    samples 需按时间升序（检测器输出保证）。返回按 start_time 排序的区间，
    自然区分同一动作的多次发生（occurrence）。
    """
    if not samples:
        return []
    ordered = sorted(samples, key=lambda s: s[0])
    smoothed = moving_average(ordered, config.smooth_half_window)

    # 候选段以 candidate_threshold 切分，保证 uncertain 区间也能成形；
    # 段内若峰达到 confirm_threshold 则 confirmed。
    spans = _merge_gaps(_segments(smoothed, config.candidate_threshold), ordered, config.merge_gap)

    raw_conf = [c for _, c in ordered]
    result: List[AggregatedSpan] = []
    for lo, hi in spans:
        seg_start = ordered[lo][0]
        seg_end = ordered[hi - 1][0]
        if seg_end - seg_start < config.min_duration:
            continue
        # peak = argmax_t p_t（原始置信度，而非平滑值）
        peak_i = max(range(lo, hi), key=lambda i: raw_conf[i])
        peak_time, peak_conf = ordered[peak_i]
        status = ConfidenceStatus.confirmed if peak_conf >= config.threshold else ConfidenceStatus.uncertain
        result.append(AggregatedSpan(
            temporal=TemporalSpan(start_time=seg_start, peak_time=peak_time, end_time=seg_end),
            confidence=round(peak_conf, 4),
            status=status,
            sample_count=hi - lo,
        ))
    return result
