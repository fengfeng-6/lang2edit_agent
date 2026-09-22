"""Accessibility 策略常量（§20-22/§31-32/§44）。

集中放置确定性阈值与优先级表，供 spatial / capability / validator 共用；
全部为 Planner 内部常量，可由 ``PlannerContext.options`` 逐项覆盖。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Protected Region 优先级（§22）
# ---------------------------------------------------------------------------
# P0 face → P1 active gesture region / active hands → P2 upper torso
# → P3 mobility device / important subject region

REGION_PRIORITY: Dict[str, int] = {
    "face": 0,
    "active_gesture": 1,
    "active_hands": 1,
    "upper_torso": 2,
    "mobility_device": 3,
    "person": 3,
}

#: IoU 低于该阈值视为"不占用手势区"（§22 τ）
DEFAULT_AVOID_IOU_THRESHOLD = 0.05

#: P1 手势区/手部保护框：以锚点或手点为中心的外扩半径（归一化）
GESTURE_REGION_RADIUS = 0.09
HAND_REGION_RADIUS = 0.07

# ---------------------------------------------------------------------------
# 低幅度动作视觉增强白名单（§20/§57）
# ---------------------------------------------------------------------------
# low physical amplitude → stronger semantic visual support，
# 而不是 fake larger body movement / 大幅镜头运动。

LOW_AMPLITUDE_ENHANCEMENTS: Tuple[str, ...] = (
    "gesture_overlay",
    "local_glow",
    "small_pulse",
    "color_emphasis",
    "particle_accent",
    "text_accent",
    "beat_flash",
)

#: 低幅度/震颤场景下禁止 Planner 自动采用的运动手段
DISCOURAGED_MOTION = ("camera_shake", "aggressive_zoom", "large_reframe")

# ---------------------------------------------------------------------------
# 跟随与震颤稳定化（§31）
# ---------------------------------------------------------------------------

FOLLOW_DEFAULT = {
    "smoothing": "source_smoothed",
    "sensitivity": 1.0,
    "dead_zone": 0.01,
    "keyframe_density": 1.0,
}

FOLLOW_TREMOR = {
    "smoothing": "strong",
    "sensitivity": 0.5,
    "dead_zone": 0.03,
    "keyframe_density": 0.3,
}

# ---------------------------------------------------------------------------
# 节拍对齐优先级（§33）：按语义强度而非动作幅度
# ---------------------------------------------------------------------------

BEAT_SNAP_PRIORITY: Dict[str, str] = {
    "heart_gesture": "high",
    "single_hand_heart": "high",
    "finger_heart": "high",
    "clap": "high",
    "point_left": "medium_high",
    "point_right": "medium_high",
    "hand_raise": "medium_high",
    "head_tilt": "medium",
    "lean_body": "medium",
    "upper_body_lean": "medium",  # 文档 §33 命名 → 注册表实际为 lean_body
}

#: 优先级 → 允许的最大对齐平移（秒）；low 不对齐
BEAT_SNAP_MAX_SHIFT: Dict[str, float] = {
    "high": 0.20,
    "medium_high": 0.12,
    "medium": 0.06,
}

# ---------------------------------------------------------------------------
# 单侧上肢（§32）
# ---------------------------------------------------------------------------

#: active_hands 单只时 follow/track 目标；绝不计算双手中点
SINGLE_HAND_TARGET = {"left": "left_hand", "right": "right_hand"}

#: 坐姿下不应被 Planner 依赖的站姿事件（§44 检查项；模块二已做 not_seated 过滤，
#: 这里是防御性断言）
STANDING_ONLY_EVENTS = ("jump", "squat", "stand_up")

#: 坐姿构图应避免素材落入画面下三分之一（站姿假设槽位）
SEATED_LOWER_THIRD_Y = 2.0 / 3.0


def follow_policy(tremor: bool, options: Dict[str, object]) -> Dict[str, float]:
    """跟随策略参数（§31）：tremor → 更强平滑 + 更低灵敏度 + 更大死区。"""
    base = dict(FOLLOW_TREMOR if tremor else FOLLOW_DEFAULT)
    if tremor and "tremor_keyframe_density" in options:
        base["keyframe_density"] = float(options["tremor_keyframe_density"])
    elif not tremor and "follow_keyframe_density" in options:
        base["keyframe_density"] = float(options["follow_keyframe_density"])
    return base
