"""生成 video-understand CLI 的演示输入（无需真实视频/CV 依赖）。

    python examples/make_demo_input.py > examples/demo_input.json
    python -m video_understanding run --input examples/demo_input.json --pretty

脚本化合成 12s 稠密轨道（idle → 手举过头顶 → 两次比心 → 指向右 →
指向左 → 末段静止），模拟 analysis_artifacts 注入路径（§48）。
"""

from __future__ import annotations

import json
import sys

FPS = 15
DURATION = 12.0

IDLE_L, IDLE_R = (0.42, 0.52), (0.58, 0.52)
HEART_L, HEART_R = (0.48, 0.24), (0.52, 0.24)
UP_L, UP_R = (0.36, 0.08), (0.64, 0.08)


def _lerp(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)


def _keypoints(lw, rw):
    return {
        "nose": (0.5, 0.18), "left_ear": (0.46, 0.19), "right_ear": (0.54, 0.19),
        "left_shoulder": (0.42, 0.32), "right_shoulder": (0.58, 0.32),
        "left_elbow": ((0.42 + lw[0]) / 2, (0.32 + lw[1]) / 2),
        "right_elbow": ((0.58 + rw[0]) / 2, (0.32 + rw[1]) / 2),
        "left_wrist": lw, "right_wrist": rw,
        "left_hip": (0.45, 0.60), "right_hip": (0.55, 0.60),
        "left_knee": (0.46, 0.78), "right_knee": (0.54, 0.78),
        "left_ankle": (0.46, 0.95), "right_ankle": (0.54, 0.95),
    }


def frames():
    out = []
    for i in range(int(DURATION * FPS)):
        t = round(i / FPS, 4)
        if 2.0 <= t <= 2.6:
            lw, rw = UP_L, UP_R
        elif 4.0 <= t <= 4.7 or 9.8 <= t <= 10.9:
            u = (t - (4.0 if t < 7 else 9.8)) / (0.7 if t < 7 else 1.1)
            approach = min(u / 0.4, 1.0)
            lw, rw = _lerp(IDLE_L, HEART_L, approach), _lerp(IDLE_R, HEART_R, approach)
        elif 6.5 <= t <= 7.2:
            lw, rw = (0.44, 0.55), (0.86, 0.33)
        elif 7.6 <= t <= 8.3:
            lw, rw = (0.14, 0.33), (0.56, 0.55)
        else:
            lw, rw = IDLE_L, IDLE_R
        out.append({
            "timestamp": t,
            "person_bbox": [0.30, 0.10, 0.70, 0.98],
            "face_bbox": [0.43, 0.12, 0.57, 0.26],
            "keypoints": _keypoints(lw, rw),
        })
    return out


def main():
    payload = {
        "video": {
            "video_id": "v_demo",
            "metadata": {"duration": DURATION, "fps": 30.0, "resolution": [1080, 1920]},
            "tracks": {"video_id": "v_demo", "fps": FPS, "frames": frames()},
            "audio": {"original_audio": {
                "bpm": 120.0,
                "beats": [0.5 + 0.5 * k for k in range(24)],
                "downbeats": [0.5 + 2.0 * k for k in range(6)],
                "onsets": [0.5, 2.5, 6.5],
            }},
        },
        "queries": [
            {"type": "event_detection", "event": "heart_gesture",
             "required_occurrence": {"type": "index", "value": 2}},
            {"type": "event_detection", "event": "point_right"},
            {"type": "pose_condition_detection",
             "condition": {"subject": "hand", "relation": "above", "reference": "head"}},
            {"type": "audio_event_detection", "event": "downbeat"},
            {"type": "video_structure_detection", "event": "last_action"},
        ],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
