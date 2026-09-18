"""检测器校准诊断：加载轨道 artifact，打印各打分器的分数分布。

    python scripts/diag_scores.py <tracks.json> [--profile auto|none]

tracks.json 是 analysis_artifacts/ 下的稠密轨道产物（DenseSpatialTracks
JSON），或在内存管线里 dump 出的同格式文件。对每个注册打分器输出
max / p95 / 超阈值时间段 + top-5 帧明细，供 §20 阈值校准与
真实视频检测分歧排查（不跑 mediapipe，纯标准库）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_understanding.events.detectors import (  # noqa: E402
    DependencyError,
    DetectContext,
    build_detector,
)
from video_understanding.events.registry import lookup, registered_events  # noqa: E402
from video_understanding.models import MobilityProfile  # noqa: E402
from video_understanding.pose.conditions import compile_condition  # noqa: E402
from video_understanding.spatial.loaders import load_tracks  # noqa: E402
from video_understanding.spatial.profile import infer_mobility_profile  # noqa: E402

_CONDITIONS = {
    "hand_above_head": {"subject": "hand", "relation": "above", "reference": "head"},
    "hand_near_face": {"subject": "hand", "relation": "near", "reference": "face"},
}


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def _segments(times, mask, limit=8):
    segs, i, n = [], 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        segs.append(f"{times[i]:.2f}-{times[j - 1]:.2f}")
        i = j
    return segs[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tracks", help="DenseSpatialTracks JSON artifact 路径")
    ap.add_argument("--profile", default="auto", choices=["auto", "none"],
                    help="auto=从轨道推断 MobilityProfile（默认），none=全正常人")
    args = ap.parse_args()

    tracks = load_tracks(args.tracks)
    times = [f.timestamp for f in tracks.frames]
    if not times:
        print("empty tracks")
        return 1
    profile = infer_mobility_profile(tracks) if args.profile == "auto" else MobilityProfile()
    ctx = DetectContext(duration=times[-1], profile=profile)
    print(f"{args.tracks}: {len(times)} frames, {times[0]:.2f}-{times[-1]:.2f}s "
          f"profile={profile.posture}/{profile.amplitude} hands={profile.available_hands}")

    scorers = {}
    for canonical in registered_events():
        entry = lookup(canonical)
        if entry and entry.strategy == "dedicated_detector" and entry.detector:
            try:
                det = build_detector(entry.detector)
            except KeyError:
                continue
            scorers[canonical] = lambda t, c, d=det: [s.conf for s in d.score(t, c)]
    for name, cond in _CONDITIONS.items():
        pred = compile_condition(cond)
        scorers[f"cond:{name}"] = (
            lambda t, c, p=pred: [p(f) for f in t.frames]
        )

    for name, fn in scorers.items():
        try:
            scores = [max(0.0, min(1.0, s or 0.0)) for s in fn(tracks, ctx)]
        except DependencyError as exc:
            print(f"{name:>22} deps-missing: {exc}")
            continue
        ordered = sorted(scores)
        p95 = _percentile(ordered, 0.95)
        line = f"{name:>22} max={ordered[-1]:.2f} p95={p95:.2f} mean={sum(scores) / len(scores):.2f}"
        for th in (0.3, 0.45, 0.6):
            segs = _segments(times, [s > th for s in scores])
            line += f" | >{th}: {len(segs)}s {segs[:4]}"
        print(line)
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[:5]
        detail = ", ".join(f"{times[i]:.2f}s={scores[i]:.2f}" for i in sorted(top))
        print(f"{'':>24} top: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
