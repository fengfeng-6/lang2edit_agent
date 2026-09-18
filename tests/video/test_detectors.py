"""Dedicated Detector + 事件物化单测（§15-17、§27-28、§37-38）。"""

from __future__ import annotations

import pytest

from video_understanding.events.detectors import DependencyError, DetectContext, build_detector
from video_understanding.spatial.loaders import load_tracks

from conftest import DURATION, make_tracks_payload


@pytest.fixture()
def tracks():
    return load_tracks(make_tracks_payload())


def test_heart_gesture_two_occurrences(tracks):
    det = build_detector("heart_gesture")
    scores = det.score(tracks, DetectContext(duration=DURATION))
    assert len(scores) == len(tracks.frames)  # 零分帧保留，时间轴完整
    peak_times = [s.t for s in scores if s.conf > 0.6]
    assert any(4.0 <= t <= 4.7 for t in peak_times)
    assert any(9.8 <= t <= 10.9 for t in peak_times)


def test_point_right_and_left_direction(tracks):
    right = build_detector("point_right").score(tracks, DetectContext())
    left = build_detector("point_left").score(tracks, DetectContext())
    r_peaks = [s for s in right if s.conf > 0.5]
    l_peaks = [s for s in left if s.conf > 0.5]
    assert r_peaks and all(6.4 <= s.t <= 7.3 for s in r_peaks)
    assert l_peaks and all(7.5 <= s.t <= 8.4 for s in l_peaks)
    assert r_peaks[0].direction.label.value == "right"
    assert l_peaks[0].direction.label.value == "left"
    assert r_peaks[0].properties["pointing_hand"] == "right"
    assert l_peaks[0].properties["pointing_hand"] == "left"


def test_ending_pose_in_final_still_segment(tracks):
    from video_understanding.events.aggregator import aggregate
    from video_understanding.events import registry

    det = build_detector("ending_pose")
    ctx = DetectContext(duration=DURATION)
    scores = det.score(tracks, ctx)
    # 纯静止度打分：所有静止段都会高分，事件级"延伸到结尾"约束由 post_filter 完成
    spans = det.post_filter(aggregate([(s.t, s.conf) for s in scores],
                                    registry.lookup("ending_pose").temporal), ctx)
    assert len(spans) == 1
    assert spans[0].temporal.end_time >= DURATION - 0.75
    # 比心 hold（约 10.2s 起）延续到结尾，整体构成最后的静止姿势
    assert spans[0].temporal.start_time >= 9.9


def test_landmark_detector_requires_hand_landmarks(tracks):
    det = build_detector("thumbs_up")
    with pytest.raises(DependencyError):
        det.score(tracks, DetectContext())


def test_landmark_thumbs_up_scores_when_present(tracks):
    from video_understanding.models import HandObservation

    hand_size = 0.05
    up = {
        "wrist": (0.5, 0.5), "middle_mcp": (0.5, 0.5 - hand_size),
        "thumb_cmc": (0.52, 0.52), "thumb_mcp": (0.53, 0.50),
        "thumb_ip": (0.54, 0.48), "thumb_tip": (0.55, 0.40),  # 拇指伸直向上
        "index_mcp": (0.50, 0.45), "index_pip": (0.51, 0.47), "index_dip": (0.51, 0.48), "index_tip": (0.51, 0.49),
        "middle_pip": (0.50, 0.47), "middle_dip": (0.50, 0.48), "middle_tip": (0.50, 0.49),
        "ring_mcp": (0.49, 0.45), "ring_pip": (0.49, 0.47), "ring_dip": (0.49, 0.48), "ring_tip": (0.49, 0.49),
        "pinky_mcp": (0.48, 0.45), "pinky_pip": (0.48, 0.47), "pinky_dip": (0.48, 0.48), "pinky_tip": (0.48, 0.49),
    }
    for frame in tracks.frames:
        if 5.0 <= frame.timestamp <= 5.8:
            frame.hands["right"] = HandObservation(center=(0.5, 0.5), landmarks=up)
    det = build_detector("thumbs_up")
    scores = det.score(tracks, DetectContext(duration=DURATION))
    assert any(s.conf > 0.6 and 5.0 <= s.t <= 5.8 for s in scores)
