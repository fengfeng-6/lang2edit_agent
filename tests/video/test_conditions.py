"""Pose Condition Compiler 单测（§21-23）。"""

from __future__ import annotations

from video_understanding.models import FrameObservation
from video_understanding.pose.conditions import compile_condition


def _frame(lw=(0.42, 0.52), rw=(0.58, 0.52)):
    return FrameObservation(
        timestamp=0.0,
        person_bbox=(0.30, 0.10, 0.70, 0.98),
        face_bbox=(0.43, 0.12, 0.57, 0.26),
        keypoints={
            "nose": (0.5, 0.18),
            "left_ear": (0.46, 0.19), "right_ear": (0.54, 0.19),
            "left_shoulder": (0.42, 0.32), "right_shoulder": (0.58, 0.32),
            "left_wrist": lw, "right_wrist": rw,
            "left_hip": (0.45, 0.60), "right_hip": (0.55, 0.60),
        },
    )


def test_hand_above_head():
    pred = compile_condition({"subject": "hand", "relation": "above", "reference": "head"})
    assert pred(_frame(lw=(0.40, 0.10))) == 1.0   # 左手高过头
    assert pred(_frame()) == 0.0                  # 双手在身侧


def test_hand_near_face_uses_person_normalized_distance():
    pred = compile_condition({"subject": "hand", "relation": "near", "reference": "face"})
    near = pred(_frame(lw=(0.40, 0.20)))
    far = pred(_frame())
    assert near > far > 0.0 or (near > 0 and far == 0.0)
    assert near >= 0.5


def test_hands_crossed_requires_left_right_of_right():
    pred = compile_condition({
        "subject": ["left_hand", "right_hand"], "relation": "crossed", "reference": "chest",
    })
    crossed = _frame(lw=(0.56, 0.42), rw=(0.44, 0.42))   # 左手在画面右侧
    apart = _frame(lw=(0.30, 0.42), rw=(0.70, 0.42))     # 正常位置不交叉
    assert pred(crossed) > 0.5
    assert pred(apart) == 0.0


def test_left_of_and_right_of():
    left_of = compile_condition({"subject": "left_hand", "relation": "left_of", "reference": "head"})
    assert left_of(_frame(lw=(0.30, 0.20))) == 1.0
    assert left_of(_frame(lw=(0.60, 0.20))) == 0.0
    right_of = compile_condition({"subject": "right_hand", "relation": "right_of", "reference": "head"})
    assert right_of(_frame(rw=(0.70, 0.20))) == 1.0


def test_hands_subject_requires_both():
    pred = compile_condition({"subject": "hands", "relation": "above", "reference": "head"})
    one_up = _frame(lw=(0.40, 0.10))          # 只有左手过顶
    both_up = _frame(lw=(0.40, 0.10), rw=(0.60, 0.10))
    assert pred(one_up) == 0.0
    assert pred(both_up) == 1.0


def test_missing_reference_scores_zero():
    pred = compile_condition({"subject": "hand", "relation": "above", "reference": "head"})
    frame = FrameObservation(timestamp=0.0, keypoints={})
    assert pred(frame) == 0.0
