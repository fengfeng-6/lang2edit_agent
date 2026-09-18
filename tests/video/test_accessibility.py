"""无障碍适配测试：可动性画像推断 + 单手/坐姿/低幅度/震颤/镜像。

面向残障用户群体的手势舞识别适配——覆盖：
- infer_mobility_profile：单手覆盖、坐姿（无脚踝+宽框）、震颤、低幅度；
- 单手降级：heart_gesture → single_hand_heart；无变体的双手事件 not_found+note；
- 坐姿归一化：pose condition 用肩宽（person bbox 含轮椅会失真）；
- 低幅度阈值缩放：正常阈值漏检的移动在低幅度画像下检出；
- 震颤轨迹：median 预滤波降低抖动；
- 镜像修正：左右手标签互换，画面方向语义不变；
- 数据覆盖度：缺肢体帧多时 confirmed 降为 uncertain。
"""

from __future__ import annotations

import math

from video_understanding import QueryStatus, VideoUnderstanding
from video_understanding.spatial.loaders import load_tracks
from video_understanding.spatial.profile import infer_mobility_profile, mirror_tracks

FPS = 15
DURATION = 8.0


def _base_kps(lw=None, rw=None, *, dx=0.0, ankles=True, shoulder_half=0.08):
    """构造关键点；lw/rw=None 表示该侧腕点缺失。"""
    kp = {
        "nose": (0.5 + dx, 0.18),
        "left_ear": (0.46 + dx, 0.19), "right_ear": (0.54 + dx, 0.19),
        "left_shoulder": (0.5 - shoulder_half + dx, 0.32),
        "right_shoulder": (0.5 + shoulder_half + dx, 0.32),
        "left_hip": (0.45 + dx, 0.60), "right_hip": (0.55 + dx, 0.60),
    }
    if ankles:
        kp.update({
            "left_knee": (0.46 + dx, 0.78), "right_knee": (0.54 + dx, 0.78),
            "left_ankle": (0.46 + dx, 0.95), "right_ankle": (0.54 + dx, 0.95),
        })
    if lw is not None:
        kp["left_elbow"] = ((0.42 + dx + lw[0]) / 2, (0.32 + lw[1]) / 2)
        kp["left_wrist"] = lw
    if rw is not None:
        kp["right_elbow"] = ((0.58 + dx + rw[0]) / 2, (0.32 + rw[1]) / 2)
        kp["right_wrist"] = rw
    return kp


def _frames_single_hand():
    """单侧（左手）上肢用户：右手全程无观测（腕点也缺失）。

    相位：0-2s idle → 2.5-3.5 单手抱心（左手到胸前）→ 5-6s 左手指左。
    """
    frames = []
    for i in range(int(DURATION * FPS)):
        t = round(i / FPS, 4)
        if 2.5 <= t <= 3.5:
            lw = (0.5, 0.44)      # 左手贴胸前（抱心）
        elif 5.0 <= t <= 6.0:
            lw = (0.16, 0.33)     # 左臂水平指向画面左
        else:
            lw = (0.42, 0.55)
        frames.append({
            "timestamp": t,
            "person_bbox": [0.32, 0.10, 0.68, 0.98],
            "face_bbox": [0.43, 0.12, 0.57, 0.26],
            "keypoints": _base_kps(lw=lw, rw=None),
        })
    return frames


def _video(frames, **extra):
    video = {
        "video_id": "v_access",
        "metadata": {"duration": DURATION, "fps": 30.0, "resolution": [1080, 1920]},
        "tracks": {"video_id": "v_access", "fps": FPS, "frames": frames},
    }
    video.update(extra)
    return video


# ---------------------------------------------------------------------------
# profile 推断
# ---------------------------------------------------------------------------


def test_infer_single_hand_from_coverage():
    tracks = load_tracks({"video_id": "v", "fps": FPS, "frames": _frames_single_hand()})
    profile = infer_mobility_profile(tracks)
    assert profile.available_hands == ["left"]
    assert profile.posture == "standing"  # 脚踝可见
    assert profile.inferred is True


def test_infer_seated_when_ankles_missing_and_wide_bbox():
    frames = [{
        "timestamp": i / FPS,
        "person_bbox": [0.20, 0.15, 0.80, 0.85],   # 含轮椅 → 高宽比 ~1.17
        "face_bbox": [0.43, 0.16, 0.57, 0.30],
        "keypoints": _base_kps(lw=(0.42, 0.5), rw=(0.58, 0.5), ankles=False),
    } for i in range(int(4 * FPS))]
    tracks = load_tracks({"video_id": "v", "fps": FPS, "frames": frames})
    profile = infer_mobility_profile(tracks)
    assert profile.posture == "seated"


def test_infer_tremor_from_reversals():
    frames = []
    for i in range(int(4 * FPS)):
        jitter = 0.012 if i % 2 == 0 else -0.012  # 每帧往返 → ~7.5/s 反转
        frames.append({
            "timestamp": i / FPS,
            "keypoints": _base_kps(lw=(0.42 + jitter, 0.5), rw=(0.58, 0.5)),
        })
    tracks = load_tracks({"video_id": "v", "fps": FPS, "frames": frames})
    assert infer_mobility_profile(tracks).tremor is True


# ---------------------------------------------------------------------------
# 单手降级（heart_gesture → single_hand_heart）
# ---------------------------------------------------------------------------


def test_heart_gesture_adapts_to_single_hand_variant():
    vu = VideoUnderstanding()
    vu.analyze_video(_video(_frames_single_hand()))
    assert vu.state.subject_profile.available_hands == ["left"]

    [r] = vu.resolve_queries([{"type": "event_detection", "event": "heart_gesture"}])
    assert r.status == QueryStatus.completed
    assert r.note and "single_hand_heart" in r.note
    assert r.events and r.events[0].canonical == "single_hand_heart"
    assert r.events[0].properties.get("adapted_from") == "heart_gesture"
    assert abs(r.events[0].temporal.peak_time - 3.0) < 0.5


def test_two_hand_event_without_variant_is_not_applicable():
    vu = VideoUnderstanding()
    vu.analyze_video(_video(_frames_single_hand()))
    [r] = vu.resolve_queries([{"type": "event_detection", "event": "close_both_hands"}])
    assert r.status == QueryStatus.not_found
    assert r.note and "2 只" in r.note


def test_single_hand_point_left_still_detected():
    vu = VideoUnderstanding()
    vu.analyze_video(_video(_frames_single_hand()))
    [r] = vu.resolve_queries([{"type": "event_detection", "event": "point_left"}])
    assert r.status == QueryStatus.completed
    assert r.events[0].properties.get("pointing_hand") == "left"


# ---------------------------------------------------------------------------
# 坐姿：不适用事件 + 肩宽归一化
# ---------------------------------------------------------------------------


def _seated_video():
    frames = [{
        "timestamp": i / FPS,
        "person_bbox": [0.20, 0.15, 0.80, 0.85],
        "face_bbox": [0.43, 0.16, 0.57, 0.30],
        "keypoints": _base_kps(lw=(0.45, 0.20), rw=(0.58, 0.5), ankles=False),
    } for i in range(int(4 * FPS))]
    return _video(frames, profile={"posture": "seated", "available_hands": ["left", "right"]})


def test_seated_profile_marks_not_applicable_events():
    vu = VideoUnderstanding()
    vu.analyze_video(_seated_video())
    assert vu.state.subject_profile.posture == "seated"
    [r] = vu.resolve_queries([{"type": "event_detection", "event": "jump"}])
    assert r.status == QueryStatus.not_found
    assert r.note and "坐姿" in r.note


def test_seated_pose_condition_uses_shoulder_normalization():
    vu = VideoUnderstanding()
    vu.analyze_video(_seated_video())
    [r] = vu.resolve_queries([{
        "type": "pose_condition_detection",
        "condition": {"subject": "left_hand", "relation": "near", "reference": "face"},
    }])
    assert r.status == QueryStatus.completed
    assert r.events[0].properties.get("norm_scale") == "shoulder"


# ---------------------------------------------------------------------------
# 低幅度阈值缩放
# ---------------------------------------------------------------------------


def _low_amplitude_video(declared=True):
    """person center 1.5s 内右移 0.09（vx≈0.06/s）：正常阈值漏检。"""
    frames = []
    for i in range(int(DURATION * FPS)):
        t = i / FPS
        dx = min(max(t - 3.0, 0.0) / 1.5, 1.0) * 0.09  # 3.0-4.5s 缓慢右移
        frames.append({
            "timestamp": round(t, 4),
            "person_bbox": [0.30 + dx, 0.10, 0.70 + dx, 0.98],
            "face_bbox": [0.43 + dx, 0.12, 0.57 + dx, 0.26],
            "keypoints": _base_kps(lw=(0.42 + dx, 0.5), rw=(0.58 + dx, 0.5), dx=dx),
        })
    profile = {"amplitude": "very_low", "amplitude_scale": 0.35} if declared else None
    return _video(frames, profile=profile)


def test_low_amplitude_move_detected_only_with_profile():
    """无需声明的自动推断已能部分适配（inferred very_low → uncertain 检出）；
    显式声明更强幅度缩放后完整检出——两条适配路径都有效。"""
    vu = VideoUnderstanding()
    vu.analyze_video(_low_amplitude_video(declared=False))
    assert vu.state.subject_profile.inferred is True
    assert vu.state.subject_profile.amplitude_scale < 1.0
    [r0] = vu.resolve_queries([{"type": "event_detection", "event": "move_right"}])
    assert r0.status == QueryStatus.low_confidence  # 推断阈值部分适配

    vu2 = VideoUnderstanding()
    vu2.analyze_video(_video(
        _low_amplitude_video(declared=False)["tracks"]["frames"],
        profile={"amplitude": "very_low", "amplitude_scale": 0.2}))
    [r1] = vu2.resolve_queries([{"type": "event_detection", "event": "move_right"}])
    assert r1.status == QueryStatus.completed
    assert any(3.0 <= e.temporal.peak_time <= 4.8 for e in r1.events)


# ---------------------------------------------------------------------------
# 震颤轨迹 + 镜像
# ---------------------------------------------------------------------------


def test_tremor_profile_smooths_trajectory():
    frames = []
    for i in range(int(4 * FPS)):
        jitter = 0.012 if i % 2 == 0 else -0.012
        frames.append({
            "timestamp": i / FPS,
            "keypoints": _base_kps(lw=(0.42 + jitter, 0.5), rw=(0.58, 0.5)),
        })
    vu = VideoUnderstanding()
    vu.analyze_video(_video(frames, profile={"tremor": True}))
    assert vu.state.subject_profile.tremor is True

    tremor_traj = vu.get_spatial_track("left_wrist")
    raw_series = vu.tracks.series("left_wrist")
    raw_diffs = sum(abs(b[1][0] - a[1][0]) for a, b in zip(raw_series, raw_series[1:]))
    smooth_diffs = sum(abs(b.x - a.x) for a, b in
                       zip(tremor_traj.points, tremor_traj.points[1:]))
    # 中值滤波 + 宽窗平滑后，x 方向抖动幅度应大幅下降（对比原始序列）
    assert smooth_diffs < raw_diffs * 0.2


def test_mirrored_profile_swaps_hand_labels():
    """前置镜像：画面左侧的手实际是人物的右手。"""
    frames = []
    for i in range(int(4 * FPS)):
        lw = (0.16, 0.33) if 1.5 <= i / FPS <= 2.5 else (0.42, 0.55)
        frames.append({
            "timestamp": i / FPS,
            "person_bbox": [0.30, 0.10, 0.70, 0.98],
            "face_bbox": [0.43, 0.12, 0.57, 0.26],
            "keypoints": _base_kps(lw=lw, rw=(0.58, 0.52)),
        })
    vu = VideoUnderstanding()
    vu.analyze_video(_video(frames, profile={"mirrored": True}))
    [r] = vu.resolve_queries([{"type": "event_detection", "event": "point_left"}])
    assert r.status == QueryStatus.completed
    # 镜像修正后：画面左侧指向的手是人物右手
    assert r.events[0].properties.get("pointing_hand") == "right"


def test_mirror_tracks_swaps_sides():
    tracks = load_tracks({"video_id": "v", "fps": 30, "frames": [
        {"timestamp": 0.0, "keypoints": {"left_wrist": (0.2, 0.5), "right_wrist": (0.8, 0.5)}},
    ]})
    mirror_tracks(tracks)
    assert tracks.frames[0].keypoints["left_wrist"] == (0.8, 0.5)
    assert tracks.frames[0].keypoints["right_wrist"] == (0.2, 0.5)


# ---------------------------------------------------------------------------
# 数据覆盖度 → 置信度降级
# ---------------------------------------------------------------------------


def test_low_coverage_downgrades_to_uncertain():
    """末段静止但 person_bbox 只在 40% 帧出现 → ending_pose 能检出
    （静止度不依赖 person_bbox），但 data_coverage<0.5 → uncertain。

    "部位不可见"不等于"动作没做"——覆盖度写进置信度来源（§43）。
    """
    frames = []
    for i in range(int(DURATION * FPS)):
        t = i / FPS
        moving = t < 4.0  # 前 4s 有运动，末段静止
        dx = math.sin(t * 3) * 0.05 if moving else 0.0
        bbox = [0.30 + dx, 0.10, 0.70 + dx, 0.98]
        # 末段 60% 帧 person_bbox 缺失（轮椅/出框导致检测不稳）
        if not moving and int(t * FPS) % 5 < 3:
            bbox = None
        frames.append({
            "timestamp": t,
            "person_bbox": bbox,
            "face_bbox": [0.43 + dx, 0.12, 0.57 + dx, 0.26],
            "keypoints": _base_kps(lw=(0.42 + dx, 0.5), rw=(0.58 + dx, 0.5), dx=dx),
        })
    vu = VideoUnderstanding()
    vu.analyze_video(_video(frames))
    [r] = vu.resolve_queries([{"type": "event_detection", "event": "ending_pose"}])
    assert r.status == QueryStatus.low_confidence
    assert r.events[0].confidence.status.value == "uncertain"
    assert r.events[0].confidence.sources.get("data_coverage", 1.0) < 0.5
