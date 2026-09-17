"""Temporal Event Aggregator 单测（§19-20）。"""

from __future__ import annotations

from video_understanding.events.aggregator import (
    TemporalConfig,
    aggregate,
    moving_average,
)


def _samples(spans, step=0.05, conf=0.9):
    """构造若干 [(t, conf)] 高置信段，其余为 0。"""
    out = []
    t = 0.0
    while t <= 12.0:
        c = conf if any(s <= t <= e for s, e in spans) else 0.0
        out.append((round(t, 4), c))
        t += step
    return out


def test_two_episodes_stay_separate():
    spans = aggregate(_samples([(4.0, 4.7), (9.8, 10.9)]), TemporalConfig(
        threshold=0.5, candidate_threshold=0.3, min_duration=0.15, merge_gap=0.2,
    ))
    assert len(spans) == 2
    assert abs(spans[0].temporal.start_time - 4.0) < 0.15
    assert abs(spans[1].temporal.start_time - 9.8) < 0.15


def test_gap_merging_within_merge_gap():
    # 两段间隔 0.15s < merge_gap 0.2 → 合并为一次动作
    spans = aggregate(_samples([(4.0, 4.4), (4.55, 5.0)]), TemporalConfig(
        threshold=0.5, candidate_threshold=0.3, min_duration=0.15, merge_gap=0.2,
        smooth_half_window=0,
    ))
    assert len(spans) == 1
    assert spans[0].temporal.start_time <= 4.05 and spans[0].temporal.end_time >= 4.95


def test_min_duration_filters_blips():
    spans = aggregate(_samples([(4.0, 4.05)], conf=0.9), TemporalConfig(
        threshold=0.5, candidate_threshold=0.3, min_duration=0.3, merge_gap=0.2,
        smooth_half_window=0,
    ))
    assert spans == []


def test_peak_is_argmax_of_raw_confidence():
    samples = _samples([(4.0, 5.0)])
    # 在 4.5s 放最高原始置信度（平滑后仍是该邻域最高）
    samples = [(t, 0.97 if abs(t - 4.5) < 1e-6 else c) for t, c in samples]
    spans = aggregate(samples, TemporalConfig(
        threshold=0.5, candidate_threshold=0.3, min_duration=0.1, merge_gap=0.2,
    ))
    assert len(spans) == 1
    assert abs(spans[0].temporal.peak_time - 4.5) < 1e-6
    assert spans[0].confidence == 0.97


def test_uncertain_band_yields_uncertain_status():
    # 峰值 0.45 ∈ [candidate 0.3, confirm 0.55) → uncertain（§44 不直接丢弃）
    spans = aggregate(_samples([(4.0, 5.0)], conf=0.45), TemporalConfig(
        threshold=0.55, candidate_threshold=0.3, min_duration=0.1, merge_gap=0.2,
        smooth_half_window=0,
    ))
    assert len(spans) == 1
    assert spans[0].status.value == "uncertain"


def test_moving_average_window():
    samples = [(float(i), v) for i, v in enumerate([0.0, 0.0, 1.0, 0.0, 0.0])]
    smoothed = moving_average(samples, 1)
    assert abs(smoothed[2][1] - 1 / 3) < 1e-6
    assert abs(smoothed[1][1] - 1 / 3) < 1e-6


def test_empty_samples():
    assert aggregate([], TemporalConfig()) == []
