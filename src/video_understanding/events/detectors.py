"""Dedicated Event Detector：标准事件的姿态启发式打分器（§15-16）。

每个检测器把稠密轨道转为逐帧置信度 p_e(t)，交给
``aggregator.aggregate`` 做时间定位；``extras_at()`` 在 peak 帧生成
事件锚点 / 方向等空间信息（§27-28、§31）。

打分器全部基于 body keypoints 几何（requirement-driven 的第一阶段允许
hybrid 融合 §25，当前实现为单证据规则，置信度来源记在 sources）。
需要 hand landmarks 的检测器（thumbs_up / v_sign / ok_sign）在轨道
缺失时抛 ``DependencyError``，上层映射为 ``failed`` 而非 ``not_found``（§42）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Callable, Dict, List, Optional, Tuple

from ..models import DenseSpatialTracks, Direction, FrameObservation, Point2
from ..pose.motion import motion_energy
from ..spatial.snapshot import hand_pair_anchor, shoulder_width

Sample = Tuple[float, float]  # (t, conf)


class DependencyError(RuntimeError):
    """检测器依赖的底层轨道不可用（如 hand_landmark_track）。"""


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _dist(a: Point2, b: Point2) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _scale(frame: FrameObservation) -> float:
    """人物尺度基准：肩宽 → person 宽 → 0.15 兜底。"""
    sw = shoulder_width(frame)
    if sw and sw > 1e-4:
        return sw
    if frame.person_bbox:
        w = frame.person_bbox[2] - frame.person_bbox[0]
        if w > 1e-4:
            return w * 0.4
    return 0.15


def _shoulder_y(frame: FrameObservation) -> Optional[float]:
    left = frame.keypoints.get("left_shoulder")
    right = frame.keypoints.get("right_shoulder")
    if left and right:
        return (left[1] + right[1]) / 2
    return None


def _torso_len(frame: FrameObservation) -> float:
    sh = _shoulder_y(frame)
    hips = [frame.keypoints.get(s) for s in ("left_hip", "right_hip")]
    hips = [h for h in hips if h]
    if sh is not None and hips:
        hip_y = sum(h[1] for h in hips) / len(hips)
        if hip_y - sh > 1e-4:
            return hip_y - sh
    return _scale(frame)


@dataclass
class FrameScore:
    """单帧打分结果：置信度 + 该帧若成为 peak 时的空间描述。"""

    t: float
    conf: float
    anchor: Optional[Point2] = None
    direction: Optional[Direction] = None
    properties: Optional[dict] = None


Extras = Tuple[Optional[Point2], Optional[Direction], dict]  # anchor, direction, properties


class RuleDetector:
    """逐帧打分器：score() 产出 FrameScore 序列，extras_at() 描述 peak 帧。"""

    version = "rule_v1"

    def __init__(
        self,
        score_fn: Callable[[DenseSpatialTracks, "DetectContext"], List[FrameScore]],
        extras_fn: Optional[Callable[[DenseSpatialTracks, float], Extras]] = None,
        post_filter_fn: Optional[Callable[[list, "DetectContext"], list]] = None,
    ):
        self._score_fn = score_fn
        self._extras_fn = extras_fn
        self._post_filter_fn = post_filter_fn

    def score(self, tracks: DenseSpatialTracks, ctx: "DetectContext") -> List[FrameScore]:
        return self._score_fn(tracks, ctx)

    def post_filter(self, spans: list, ctx: "DetectContext") -> list:
        """聚合后的事件级约束（如 ending_pose 必须延伸到视频末段）。默认不过滤。"""
        if self._post_filter_fn is not None:
            return self._post_filter_fn(spans, ctx)
        return spans

    def extras_at(self, tracks: DenseSpatialTracks, timestamp: float) -> Extras:
        if self._extras_fn is not None:
            return self._extras_fn(tracks, timestamp)
        frame = tracks.at(timestamp)
        if frame is None:
            return None, None, {}
        return hand_pair_anchor(frame) or frame.body_center(), None, {}


@dataclass
class DetectContext:
    """检测上下文：视频时长等轨道外信息。"""

    duration: float = 0.0


def _per_frame(
    scorer: Callable[[FrameObservation, DetectContext], FrameScore],
    extras_fn: Optional[Callable[[DenseSpatialTracks, float], Extras]] = None,
) -> RuleDetector:
    """把"逐帧打分函数"适配成 RuleDetector。

    零置信度帧也必须保留——否则两段分离的动作在采样序列里变成相邻
    样本，时间缺口消失，aggregator 会把多次发生错误并成一段（§16）。
    """

    def score_fn(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
        out: List[FrameScore] = []
        for frame in tracks.frames:
            s = scorer(frame, ctx)
            if s is not None:
                s.t = frame.timestamp
                out.append(s)
        return out

    return RuleDetector(score_fn, extras_fn)


def _anchor_extras(resolve: Callable[[FrameObservation], Optional[Point2]]):
    def extras(tracks: DenseSpatialTracks, timestamp: float) -> Extras:
        frame = tracks.at(timestamp)
        if frame is None:
            return None, None, {}
        return resolve(frame) or hand_pair_anchor(frame) or frame.body_center(), None, {}

    return extras


# ---------------------------------------------------------------------------
# 手势打分器
# ---------------------------------------------------------------------------


def _heart_score(frame: FrameObservation, ctx: DetectContext) -> FrameScore:
    """比心：双手合拢是决定性特征——closeness 不足时一票否决，
    避免"双手高举分开"等姿态被 lift 项单独顶成候选。"""
    lw = frame.hand_center("left")
    rw = frame.hand_center("right")
    sh_y = _shoulder_y(frame)
    if not lw or not rw or sh_y is None:
        return FrameScore(frame.timestamp, 0.0)
    scale = _scale(frame)
    closeness = _clamp01(1.0 - _dist(lw, rw) / (1.0 * scale))
    if closeness < 0.25:
        return FrameScore(frame.timestamp, 0.0)
    lift = _clamp01((sh_y - max(lw[1], rw[1])) / scale + 0.25)
    conf = _clamp01(0.7 * closeness + 0.3 * lift)
    return FrameScore(frame.timestamp, conf, anchor=((lw[0] + rw[0]) / 2, (lw[1] + rw[1]) / 2))


def _make_point_scorer(sign: float) -> Callable[[FrameObservation, DetectContext], FrameScore]:
    """指向 sign<0 画面左 / sign>0 画面右：腕相对肩外展 + 手臂近水平。"""

    def scorer(frame: FrameObservation, ctx: DetectContext) -> FrameScore:
        scale = _scale(frame)
        best = FrameScore(frame.timestamp, 0.0)
        for side in ("left", "right"):
            wrist = frame.hand_center(side)
            shoulder = frame.keypoints.get(f"{side}_shoulder")
            elbow = frame.keypoints.get(f"{side}_elbow")
            if not wrist or not shoulder:
                continue
            dx = wrist[0] - shoulder[0]
            extension = (dx * sign) / scale
            if extension <= 0.8:
                continue
            horizontal = 1.0 - _clamp01(abs(wrist[1] - shoulder[1]) / (abs(dx) + 1e-6))
            conf = _clamp01((extension - 0.8) / 1.2) * _clamp01(0.4 + 0.6 * horizontal)
            if conf > best.conf:
                base = elbow or shoulder
                best = FrameScore(
                    frame.timestamp, conf, anchor=wrist,
                    direction=Direction.from_delta(wrist[0] - base[0], wrist[1] - base[1]),
                    properties={"pointing_hand": side},
                )
        return best

    return scorer


def _wave_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """挥手：手腕高过肩 + 水平振荡（0.6s 窗口内 x 位移幅度）。"""
    window = 0.6
    history: Dict[str, List[Tuple[float, float]]] = {"left": [], "right": []}
    out: List[FrameScore] = []
    for frame in tracks.frames:
        sh_y = _shoulder_y(frame)
        scale = _scale(frame)
        best = FrameScore(frame.timestamp, 0.0)
        if sh_y is not None:
            for side in ("left", "right"):
                wrist = frame.hand_center(side)
                if not wrist:
                    continue
                raised = _clamp01((sh_y - wrist[1]) / scale - 0.1)
                if raised > 0:
                    xs = [x for t, x in history[side] if frame.timestamp - window <= t]
                    xs.append(wrist[0])
                    x_range = (max(xs) - min(xs)) / scale if xs else 0.0
                    oscillation = _clamp01((x_range - 0.3) / 0.7)
                    conf = _clamp01(raised * (0.4 + 0.6 * oscillation))
                    if conf > best.conf:
                        best = FrameScore(frame.timestamp, conf, anchor=wrist,
                                          properties={"waving_hand": side})
        for side in ("left", "right"):
            pt = frame.hand_center(side)
            if pt:
                history[side].append((frame.timestamp, pt[0]))
        out.append(best)
    return out


def _open_hands_score(frame: FrameObservation, ctx: DetectContext) -> FrameScore:
    """双手张开：腕距显著大于肩宽。"""
    lw = frame.hand_center("left")
    rw = frame.hand_center("right")
    if not lw or not rw:
        return FrameScore(frame.timestamp, 0.0)
    d = _dist(lw, rw) / _scale(frame)
    conf = _clamp01((d - 1.5) / 0.9)
    return FrameScore(frame.timestamp, conf,
                      anchor=((lw[0] + rw[0]) / 2, (lw[1] + rw[1]) / 2))


def _close_hands_score(frame: FrameObservation, ctx: DetectContext) -> FrameScore:
    """双手合拢：腕距小且在躯干前方（肩线以下、髋线以上）。"""
    lw = frame.hand_center("left")
    rw = frame.hand_center("right")
    sh_y = _shoulder_y(frame)
    if not lw or not rw or sh_y is None:
        return FrameScore(frame.timestamp, 0.0)
    scale = _scale(frame)
    d = _dist(lw, rw) / scale
    together = _clamp01((0.55 - d) / 0.35)
    hip_y = sh_y + _torso_len(frame)
    mid_y = (lw[1] + rw[1]) / 2
    in_front = 1.0 if sh_y - 0.1 * scale <= mid_y <= hip_y + 0.2 * scale else 0.3
    conf = _clamp01(together * in_front)
    return FrameScore(frame.timestamp, conf,
                      anchor=((lw[0] + rw[0]) / 2, (lw[1] + rw[1]) / 2))


# ---------------------------------------------------------------------------
# 身体动作打分器
# ---------------------------------------------------------------------------


def _turn_body_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """转身：肩宽相对全段中位数的收窄（侧对镜头时肩投影变窄）。"""
    widths = [(f.timestamp, shoulder_width(f) or 0.0) for f in tracks.frames]
    base = median((w for _, w in widths if w > 1e-6), default=0.0)
    out: List[FrameScore] = []
    for frame, (_, w) in zip(tracks.frames, widths):
        conf = _clamp01((0.72 - w / base) / 0.5) if base > 1e-6 and w > 1e-6 else 0.0
        out.append(FrameScore(frame.timestamp, conf,
                              anchor=frame.body_center() if conf > 0 else None))
    return out


def _make_move_score(sign: float) -> Callable[[DenseSpatialTracks, DetectContext], List[FrameScore]]:
    """整体左移 / 右移：person center 在 0.4s 窗口内的水平速度。"""

    def score_fn(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
        series = tracks.series("person")
        window = 0.4
        out: List[FrameScore] = []
        for i, (t, p) in enumerate(series):
            past = next(((pt, pp) for pt, pp in reversed(series[:i]) if t - window <= pt), None)
            conf = 0.0
            if past is not None:
                dt = max(t - past[0], 1e-6)
                vx = (p[0] - past[1][0]) / dt * sign
                conf = _clamp01((vx - 0.08) / 0.25)
            out.append(FrameScore(t, conf, anchor=p if conf > 0 else None,
                                  direction=Direction.from_delta(vx * sign, 0.0) if conf > 0 else None))
        # 无 person 观测的帧也补 0，保持时间轴完整
        have = {s.t for s in out}
        for frame in tracks.frames:
            if frame.timestamp not in have:
                out.append(FrameScore(frame.timestamp, 0.0))
        out.sort(key=lambda s: s.t)
        return out

    return score_fn


def _jump_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """跳跃：头部竖直速度向上的尖峰（y 向下为正，向上速度为负）。"""
    out: List[FrameScore] = []
    prev: Optional[Tuple[float, Point2]] = None
    for frame in tracks.frames:
        head = frame.head_center()
        conf = 0.0
        if head is not None and prev is not None:
            dt = max(frame.timestamp - prev[0], 1e-6)
            vy = (head[1] - prev[1][1]) / dt
            conf = _clamp01((-vy - 0.25) / 0.9)
        out.append(FrameScore(frame.timestamp, conf, anchor=head if conf > 0 else None))
        if head is not None:
            prev = (frame.timestamp, head)
    return out


def _squat_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """下蹲：头位低于自身站立基线（全段最高头位）的相对深度。"""
    series = tracks.series("head")
    if not series:
        return [FrameScore(f.timestamp, 0.0) for f in tracks.frames]
    standing = min(y for _, (_, y) in series)  # y 越小越高
    torso = max((_torso_len(f) for f in tracks.frames), default=0.0) or 0.2
    out: List[FrameScore] = []
    for frame in tracks.frames:
        head = frame.head_center()
        conf = _clamp01(((head[1] - standing) / torso - 0.12) / 0.35) if head else 0.0
        out.append(FrameScore(frame.timestamp, conf, anchor=head if conf > 0 else None))
    return out


def _stand_up_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """起身：近期存在明显头位下落 + 当前向上速度。"""
    series = tracks.series("head")
    out: List[FrameScore] = []
    prev: Optional[Tuple[float, Point2]] = None
    for frame in tracks.frames:
        head = frame.head_center()
        conf = 0.0
        if head is not None and prev is not None:
            dt = max(frame.timestamp - prev[0], 1e-6)
            vy = (head[1] - prev[1][1]) / dt
            recent_low = max(
                (y for t, (_, y) in series if frame.timestamp - 1.2 <= t < frame.timestamp),
                default=head[1],
            )
            drop_context = (recent_low - head[1]) / _torso_len(frame)
            conf = _clamp01((-vy - 0.15) / 0.6) * _clamp01(drop_context / 0.2)
        out.append(FrameScore(frame.timestamp, conf, anchor=head if conf > 0 else None))
        if head is not None:
            prev = (frame.timestamp, head)
    return out


def _lean_body_score(frame: FrameObservation, ctx: DetectContext) -> FrameScore:
    """身体倾斜：头相对髋中点的横向偏移。"""
    head = frame.head_center()
    hips = [frame.keypoints.get(s) for s in ("left_hip", "right_hip")]
    hips = [h for h in hips if h]
    if head is None or not hips:
        return FrameScore(frame.timestamp, 0.0)
    hip_mid_x = sum(h[0] for h in hips) / len(hips)
    offset = abs(head[0] - hip_mid_x) / _torso_len(frame)
    conf = _clamp01((offset - 0.35) / 0.4)
    return FrameScore(frame.timestamp, conf, anchor=head)


def _approach_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """靠近镜头：person bbox 高度持续增长。"""
    series = [(f.timestamp, f.person_bbox[3] - f.person_bbox[1])
              for f in tracks.frames if f.person_bbox]
    window = 0.5
    by_t: Dict[float, float] = {}
    for i, (t, h) in enumerate(series):
        past = next(((pt, ph) for pt, ph in reversed(series[:i]) if t - window <= pt), None)
        conf = 0.0
        if past is not None:
            rate = (h - past[1]) / max(t - past[0], 1e-6)
            conf = _clamp01((rate - 0.08) / 0.4)
        by_t[t] = conf
    out: List[FrameScore] = []
    for frame in tracks.frames:
        conf = by_t.get(frame.timestamp, 0.0)
        out.append(FrameScore(frame.timestamp, conf,
                              anchor=frame.person_center() if conf > 0 else None))
    return out


def _ending_pose_score(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
    """Ending Pose 打分：纯静止度（时间无关）。

    "末段"语义由 post_filter 保证——只保留延伸到视频结尾的静止段，
    避免把 mid-video 的定住动作（如 point hold）误判为结束姿势。
    """
    energies = {t: e for t, e in motion_energy(tracks)}
    out: List[FrameScore] = []
    for frame in tracks.frames:
        e = energies.get(frame.timestamp, 1e9)
        conf = _clamp01(1.0 - e / 2.5)  # 归一化位移/秒
        out.append(FrameScore(frame.timestamp, conf,
                              anchor=frame.body_center() if conf > 0 else None))
    return out


def _ending_pose_post_filter(spans: list, ctx: DetectContext) -> list:
    if ctx.duration <= 0:
        return []
    return [s for s in spans if s.temporal.end_time >= ctx.duration - 0.75]


# ---------------------------------------------------------------------------
# Hand-landmark 打分器（按需轨道，§11）
# ---------------------------------------------------------------------------

_FINGERS = ("index", "middle", "ring", "pinky")


def _hand_size(pts: Dict[str, Point2]) -> float:
    wrist = pts.get("wrist")
    mcp = pts.get("middle_mcp")
    return _dist(wrist, mcp) if wrist and mcp else 0.1


def _finger_extended(pts: Dict[str, Point2], finger: str) -> float:
    """指尖比指根更远离腕 → 伸直程度 0..1。"""
    wrist = pts.get("wrist")
    tip = pts.get(f"{finger}_tip")
    pip = pts.get(f"{finger}_pip")
    if not wrist or not tip or not pip:
        return 0.0
    d_tip, d_pip = _dist(tip, wrist), _dist(pip, wrist)
    return _clamp01((d_tip - d_pip * 0.95) / (_hand_size(pts) * 0.8 + 1e-6))


def _thumb_extended(pts: Dict[str, Point2]) -> float:
    wrist = pts.get("wrist")
    tip = pts.get("thumb_tip")
    ip = pts.get("thumb_ip")
    if not wrist or not tip or not ip:
        return 0.0
    return _clamp01((_dist(tip, wrist) - _dist(ip, wrist) * 0.95) / (_hand_size(pts) * 0.7 + 1e-6))


def _landmark_score_frame(frame: FrameObservation, fn: Callable[[Dict[str, Point2]], float]) -> FrameScore:
    best = FrameScore(frame.timestamp, 0.0)
    for side in ("left", "right"):
        hand = frame.hands.get(side)
        if hand is None or not hand.landmarks:
            continue
        conf = _clamp01(fn(hand.landmarks))
        if conf > best.conf:
            best = FrameScore(frame.timestamp, conf, anchor=hand.center,
                              properties={"hand": side})
    return best


def _require_landmarks(tracks: DenseSpatialTracks) -> None:
    for frame in tracks.frames:
        if any(h.landmarks for h in frame.hands.values()):
            return
    raise DependencyError("hand landmark track unavailable")


def _thumbs_up_fn(pts: Dict[str, Point2]) -> float:
    thumb = _thumb_extended(pts)
    others = [_finger_extended(pts, f) for f in _FINGERS]
    curled = sum(1.0 - o for o in others) / len(others)
    return _clamp01(thumb * 0.7 + curled * 0.3)


def _v_sign_fn(pts: Dict[str, Point2]) -> float:
    ext = (_finger_extended(pts, "index") + _finger_extended(pts, "middle")) / 2
    curled = (2.0 - _finger_extended(pts, "ring") - _finger_extended(pts, "pinky")) / 2
    return _clamp01(ext * 0.65 + curled * 0.35)


def _ok_sign_fn(pts: Dict[str, Point2]) -> float:
    thumb_tip, index_tip = pts.get("thumb_tip"), pts.get("index_tip")
    if not thumb_tip or not index_tip:
        return 0.0
    circle = _clamp01(1.0 - _dist(thumb_tip, index_tip) / (_hand_size(pts) * 0.6 + 1e-6))
    others = (_finger_extended(pts, "middle") + _finger_extended(pts, "ring") + _finger_extended(pts, "pinky")) / 3
    return _clamp01(circle * 0.7 + others * 0.3)


def _landmark_detector(fn: Callable[[Dict[str, Point2]], float]) -> RuleDetector:
    def score_fn(tracks: DenseSpatialTracks, ctx: DetectContext) -> List[FrameScore]:
        _require_landmarks(tracks)
        return [_landmark_score_frame(frame, fn) for frame in tracks.frames]

    def extras(tracks: DenseSpatialTracks, timestamp: float) -> Extras:
        frame = tracks.at(timestamp)
        if frame is None:
            return None, None, {}
        s = _landmark_score_frame(frame, fn)
        return s.anchor or frame.body_center(), s.direction, s.properties or {}

    return RuleDetector(score_fn, extras)


# ---------------------------------------------------------------------------
# 检测器工厂：registry.detector 名 → RuleDetector
# ---------------------------------------------------------------------------


def build_detector(name: str) -> RuleDetector:
    if name == "heart_gesture":
        return _per_frame(_heart_score)
    if name == "point_left":
        return _per_frame(_make_point_scorer(-1.0))
    if name == "point_right":
        return _per_frame(_make_point_scorer(1.0))
    if name == "wave_hand":
        return RuleDetector(_wave_score)
    if name == "open_both_hands":
        return _per_frame(_open_hands_score)
    if name == "close_both_hands":
        return _per_frame(_close_hands_score)
    if name == "turn_body":
        return RuleDetector(_turn_body_score)
    if name == "move_left":
        return RuleDetector(_make_move_score(-1.0))
    if name == "move_right":
        return RuleDetector(_make_move_score(1.0))
    if name == "jump":
        return RuleDetector(_jump_score)
    if name == "squat":
        return RuleDetector(_squat_score)
    if name == "stand_up":
        return RuleDetector(_stand_up_score)
    if name == "lean_body":
        return _per_frame(_lean_body_score)
    if name == "approach_camera":
        return RuleDetector(_approach_score)
    if name == "ending_pose":
        return RuleDetector(
            _ending_pose_score,
            _anchor_extras(lambda f: f.body_center() or f.head_center()),
            post_filter_fn=_ending_pose_post_filter,
        )
    if name == "thumbs_up":
        return _landmark_detector(_thumbs_up_fn)
    if name == "v_sign":
        return _landmark_detector(_v_sign_fn)
    if name == "ok_sign":
        return _landmark_detector(_ok_sign_fn)
    raise KeyError(f"no detector named {name}")
