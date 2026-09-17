"""tests/video 共享 fixture：脚本化合成稠密轨道（~12s @15fps）。

相位脚本（source 时间）：
    0.0–1.9    idle        双手垂在身侧
    2.0–2.6   hands_up     双手举过头顶（验证 pose_condition above head）
    4.0–4.7   heart        比心：双手从两侧收到头前合拢再放开（峰值在 ~4.35）
    6.5–7.2   point_right  右臂水平指向画面右侧
    7.6–8.3   point_left   左臂水平指向画面左侧
    9.8–10.9  heart        第二次比心
    11.3–12.0 still        末段静止（ending_pose）
"""

from __future__ import annotations

import pytest

FPS = 15
DURATION = 12.0

IDLE_L, IDLE_R = (0.42, 0.52), (0.58, 0.52)
HEART_L, HEART_R = (0.48, 0.24), (0.52, 0.24)
UP_L, UP_R = (0.36, 0.08), (0.64, 0.08)


def _lerp(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def _phase(t: float):
    if 2.0 <= t <= 2.6:
        return "hands_up", 0.5
    if 4.0 <= t <= 4.7:
        return "heart", (t - 4.0) / 0.7
    if 6.5 <= t <= 7.2:
        return "point_right", 0.5
    if 7.6 <= t <= 8.3:
        return "point_left", 0.5
    if 9.8 <= t <= 10.9:
        return "heart", (t - 9.8) / 1.1
    if t >= 11.3:
        return "still", 1.0
    return "idle", 0.0


def _keypoints(lw, rw, dx=0.0, shoulder_half=0.08):
    return {
        "nose": (0.5 + dx, 0.18),
        "left_ear": (0.46 + dx, 0.19),
        "right_ear": (0.54 + dx, 0.19),
        "left_shoulder": (0.5 - shoulder_half + dx, 0.32),
        "right_shoulder": (0.5 + shoulder_half + dx, 0.32),
        "left_elbow": ((0.5 - shoulder_half + dx + lw[0]) / 2, (0.32 + lw[1]) / 2),
        "right_elbow": ((0.5 + shoulder_half + dx + rw[0]) / 2, (0.32 + rw[1]) / 2),
        "left_wrist": lw,
        "right_wrist": rw,
        "left_hip": (0.45 + dx, 0.60),
        "right_hip": (0.55 + dx, 0.60),
        "left_knee": (0.46 + dx, 0.78),
        "right_knee": (0.54 + dx, 0.78),
        "left_ankle": (0.46 + dx, 0.95),
        "right_ankle": (0.54 + dx, 0.95),
    }


def make_frames():
    frames = []
    for i in range(int(DURATION * FPS)):
        t = round(i / FPS, 4)
        phase, u = _phase(t)
        if phase == "hands_up":
            lw, rw = UP_L, UP_R
        elif phase == "heart":
            # 双手先收拢抬高（u<0.4）再保持（峰值置信在中后段）
            approach = min(u / 0.4, 1.0)
            lw = _lerp(IDLE_L, HEART_L, approach)
            rw = _lerp(IDLE_R, HEART_R, approach)
        elif phase == "point_right":
            lw, rw = (0.44, 0.55), (0.86, 0.33)
        elif phase == "point_left":
            lw, rw = (0.14, 0.33), (0.56, 0.55)
        else:
            lw, rw = IDLE_L, IDLE_R
        frames.append({
            "timestamp": t,
            "person_bbox": [0.30, 0.10, 0.70, 0.98],
            "face_bbox": [0.43, 0.12, 0.57, 0.26],
            "keypoints": _keypoints(lw, rw),
        })
    return frames


def make_tracks_payload():
    return {"video_id": "v_dance", "fps": FPS, "frames": make_frames()}


def make_video(**overrides):
    video = {
        "video_id": "v_dance",
        "metadata": {"duration": DURATION, "fps": 30.0, "resolution": [1080, 1920]},
        "tracks": make_tracks_payload(),
        "audio": {"original_audio": {
            "bpm": 120.0,
            "beats": [0.5 + 0.5 * k for k in range(24)],
            "downbeats": [0.5 + 2.0 * k for k in range(6)],
            "onsets": [0.5, 2.5, 6.5],
        }},
    }
    video.update(overrides)
    return video


@pytest.fixture()
def video_input():
    return make_video()


@pytest.fixture()
def analyzed(video_input):
    from video_understanding import VideoUnderstanding

    vu = VideoUnderstanding()
    vu.analyze_video(video_input)
    return vu
