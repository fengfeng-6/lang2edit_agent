"""Plan Materializer（§9 第二段 / §50-51）。

    LogicalEditingPlan + AssetBindings + (更新的) SemanticView
    + ToolCapabilityProfile + spatial_tracks
        ↓
    ResolvedEditingPlan

把事件相对锚点解析成工程时间（含 freeze insert_duration 移位）、
把空间引用解析成具体坐标（按最新视图重算避让）、把跟随策略
落成关键帧或 trajectory_ref——仍是软件无关表示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from gesture_intent.models import ConstraintLevel

from ..models import (
    AssetBinding,
    EventBoundary,
    LogicalEditingPlan,
    PlanItem,
    PlanItemStatus,
    PlanOperation,
    ProjectTime,
    ResolvedEditingPlan,
    ResolvedPlanItem,
    SubReport,
    TimelineMapping,
    TimelineTrack,
    Transform,
    AnimationSpec,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
    _stable_uid,
)
from ..temporal.timeline import source_to_project
from ..spatial.placement import (
    apply_relation,
    bbox_around,
    choose_position,
    collect_avoid_regions,
    direction_relation,
)
from ..expansion.requirements import EventIndex

_INACTIVE = {
    PlanItemStatus.blocked,
    PlanItemStatus.skipped,
    PlanItemStatus.unfulfilled,
    PlanItemStatus.unsupported,
}

#: 结构性 anchor key → structural 字典键
_STRUCTURE_KEYS = ("video_start", "video_end", "first_action", "last_action")


def materialize_plan(
    plan: LogicalEditingPlan,
    bindings: List[AssetBinding],
    view: Optional[Any],
    capabilities: Any,
    *,
    spatial_tracks: Optional[Dict[str, Any]] = None,
) -> ResolvedEditingPlan:
    warnings: List[str] = []
    binding_map = {b.asset_request_uid: b for b in bindings}
    event_index = EventIndex(view) if view is not None else None
    profile = plan.accessibility_profile
    duration = plan.timeline_structure.source_duration

    # ---- 1. 绑定检查 ----
    for item in plan.plan_items:
        if item.status in _INACTIVE:
            continue
        if not item.asset_request_ref:
            continue
        binding = binding_map.get(item.asset_request_ref)
        if binding is None:
            hard = _item_hard(plan, item)
            item.status = (
                PlanItemStatus.unfulfilled if hard else PlanItemStatus.skipped
            )
            warnings.append(
                f"{item.plan_item_uid}: 素材请求 {item.asset_request_ref} 未返回绑定"
            )

    # ---- 2. freeze 移位表 ----
    shifts: List[Dict[str, float]] = []
    for item in plan.plan_items:
        if item.status in _INACTIVE:
            continue
        if item.timeline_effect.value != "insert_duration":
            continue
        anchor_time = _resolve_anchor_time(item, event_index, duration)
        if anchor_time is None:
            warnings.append(f"{item.plan_item_uid}: freeze 锚点无法解析，跳过移位")
            continue
        delta = _duration_value(item, 0.0)
        item.temporal_spec.source_time_hint = anchor_time
        if delta > 0:
            shifts.append({"from_source_time": anchor_time, "delta": delta})
    shifts.sort(key=lambda s: s["from_source_time"])
    total_duration = duration + sum(s["delta"] for s in shifts)

    # ---- 3. 逐项解析 ----
    resolved: List[ResolvedPlanItem] = []
    for item in plan.plan_items:
        if item.status in _INACTIVE:
            resolved.append(_stub_resolved(item))
            continue

        item_warnings: List[str] = []
        # 时间
        start, end = _resolve_time(item, event_index, duration, shifts, view, item_warnings)

        # 空间
        transform = _resolve_transform(item, event_index, profile, item_warnings)

        # 跟随
        follow = _resolve_follow(item, spatial_tracks, shifts, item_warnings)

        # 素材
        asset_uid = None
        if item.asset_request_ref:
            binding = binding_map.get(item.asset_request_ref)
            asset_uid = binding.asset_uid if binding else None

        resolved.append(ResolvedPlanItem(
            plan_item_uid=item.plan_item_uid,
            plan_key=item.plan_key,
            operation=item.operation,
            asset_uid=asset_uid,
            project_time=ProjectTime(start=start, end=end),
            transform=transform,
            animation=AnimationSpec(
                type=item.style_spec.animation or "none",
                params=dict(item.style_spec.animation_params),
            ),
            follow=follow,
            parameters=dict(item.parameters),
            source_requirement_ids=list(item.source_requirement_ids),
            degradation_applied=list(item.degradation_applied),
            status=item.status,
            warnings=item_warnings,
        ))
        warnings += item_warnings

    # ---- 4. 组装 ----
    validation = _materialize_validation(resolved, plan)
    return ResolvedEditingPlan(
        plan_uid=_stable_uid("rpln", plan.plan_uid, plan.version,
                             [b.asset_uid for b in bindings]),
        source_logical_plan_version=plan.version,
        resolved_items=resolved,
        asset_bindings=bindings,
        timeline_mapping=TimelineMapping(
            tracks=list(plan.timeline_structure.tracks),
            shifts=shifts,
            total_duration=total_duration,
        ),
        validation=validation,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 内部解析
# ---------------------------------------------------------------------------


def _item_hard(plan: LogicalEditingPlan, item: PlanItem) -> bool:
    """该项的素材请求是否 hard 级。"""
    for req in plan.asset_requests:
        if req.request_uid == item.asset_request_ref:
            return req.constraint_level == ConstraintLevel.hard
    return True


def _stub_resolved(item: PlanItem) -> ResolvedPlanItem:
    return ResolvedPlanItem(
        plan_item_uid=item.plan_item_uid,
        plan_key=item.plan_key,
        operation=item.operation,
        project_time=ProjectTime(start=0.0, end=0.0),
        status=item.status,
        warnings=[f"item {item.status.value}"],
    )


def _boundary_time(brief: Dict[str, Any], boundary: EventBoundary) -> float:
    if boundary == EventBoundary.start:
        return float(brief.get("start_time") or 0.0)
    if boundary == EventBoundary.end:
        return float(brief.get("end_time") or 0.0)
    return float(brief.get("peak_time") or 0.0)


def _anchor_time(anchor: Any, event_index: Optional[EventIndex], duration: float) -> Optional[float]:
    """TimeAnchor → 源时间。"""
    if anchor is None:
        return None
    atype = anchor.type.value
    if atype == "source_time":
        return anchor.time
    if atype == "semantic_event" and anchor.event_uid:
        if event_index is None:
            return None
        brief = event_index.brief(anchor.event_uid)
        if brief is None:
            return None
        return _boundary_time(brief, anchor.boundary)
    if atype == "video_structure":
        key = anchor.structure_key or "video_start"
        if event_index is None:
            return None
        structural = event_index.structural
        value = structural.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        # first_action / last_action → event_uid 引用
        if isinstance(value, dict) and value.get("event_ref"):
            brief = event_index.brief(value["event_ref"])
            if brief:
                return _boundary_time(brief, anchor.boundary)
        if isinstance(value, str):
            brief = event_index.brief(value)
            if brief:
                return _boundary_time(brief, anchor.boundary)
        if key == "video_end":
            return duration
        if key == "video_start":
            return 0.0
    return None


def _resolve_anchor_time(item: PlanItem, event_index: Optional[EventIndex], duration: float) -> Optional[float]:
    spec = item.temporal_spec
    if spec is None:
        return None
    if spec.start_anchor is not None:
        return _anchor_time(spec.start_anchor, event_index, duration)
    if spec.end_anchor is not None:
        end = _anchor_time(spec.end_anchor, event_index, duration)
        if end is not None:
            return end - _duration_value(item, 0.0)
    return None


def _duration_value(item: PlanItem, default: float) -> float:
    if item.temporal_spec and item.temporal_spec.duration:
        return float(item.temporal_spec.duration.value or default)
    return default


def _resolve_time(
    item: PlanItem,
    event_index: Optional[EventIndex],
    duration: float,
    shifts: List[Dict[str, float]],
    view: Optional[Any],
    warnings: List[str],
) -> Tuple[float, float]:
    """TemporalSpec → (project_start, project_end)。"""
    spec = item.temporal_spec
    if spec is None:
        return 0.0, duration

    if spec.mode == "event_relative" and spec.start_anchor is None and spec.end_anchor is not None:
        # before/until：end 锚定，start = end - duration
        end_t = _anchor_time(spec.end_anchor, event_index, duration)
        if end_t is None:
            warnings.append(f"{item.plan_item_uid}: end_anchor 无法解析")
            end_t = duration
        start_t = max(0.0, end_t - _duration_value(item, 0.8))
    else:
        start_t = _anchor_time(spec.start_anchor, event_index, duration)
        if start_t is None:
            warnings.append(f"{item.plan_item_uid}: start_anchor 无法解析，用 0")
            start_t = 0.0
        start_t += spec.offset
        end_anchor_t = _anchor_time(spec.end_anchor, event_index, duration)
        if end_anchor_t is not None:
            end_t = end_anchor_t + spec.end_offset
        else:
            end_t = start_t + _resolve_duration(item, event_index)

    # 节拍对齐（§33）：把事件 peak 对齐到最近的 beat/downbeat
    if spec.beat_snap and view is not None:
        snapped = _beat_snap(item, spec, event_index, view, warnings)
        if snapped is not None:
            delta = snapped
            start_t += delta
            end_t += delta

    return source_to_project(start_t, shifts), source_to_project(end_t, shifts)


def _resolve_duration(item: PlanItem, event_index: Optional[EventIndex]) -> float:
    spec = item.temporal_spec
    d = spec.duration
    if d.mode.value == "exact" and d.value:
        return d.value
    if d.mode.value == "range":
        value = d.value or d.min or 0.8
        if d.min is not None:
            value = max(value, d.min)
        if d.max is not None:
            value = min(value, d.max)
        return value
    if d.mode.value == "event_span" and spec.start_anchor and spec.start_anchor.event_uid:
        if event_index is not None:
            brief = event_index.brief(spec.start_anchor.event_uid)
            if brief:
                return max(0.0, float(brief.get("end_time", 0)) - float(brief.get("start_time", 0)))
        return 0.0
    return float(d.value or 0.8)


def _beat_snap(item, spec, event_index, view, warnings) -> Optional[float]:
    """返回需要加在时间上的平移量（秒）；不对齐返回 None。"""
    snap = spec.beat_snap
    max_shift = float(snap.get("max_shift", 0.0))
    if max_shift <= 0:
        return None
    # 对齐目标：事件 peak（或 start）
    anchor = spec.start_anchor
    if anchor is None or anchor.type.value != "semantic_event":
        return None
    brief = event_index.brief(anchor.event_uid) if event_index else None
    if brief is None:
        return None
    peak = float(brief.get("peak_time") or 0.0)

    audio = getattr(view, "audio_summary", None) or (
        view.get("audio_summary") if isinstance(view, dict) else {}
    )
    candidates: List[float] = []
    bpm = None
    for summary in (audio or {}).values():
        candidates.extend(float(d) for d in summary.get("downbeats") or [])
        bpm = bpm or summary.get("bpm")
    if not candidates and bpm:
        interval = 60.0 / float(bpm)
        n = int(peak / interval) + 2
        candidates = [i * interval for i in range(n + 1)]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda t: abs(t - peak))
    shift = nearest - peak
    if abs(shift) <= max_shift:
        item.provenance.rules.append(f"beat_snap:{shift:+.3f}s")
        return shift
    warnings.append(f"{item.plan_item_uid}: 最近节拍距事件峰 {abs(shift):.2f}s 超上限，未对齐")
    return None


def _resolve_transform(
    item: PlanItem,
    event_index: Optional[EventIndex],
    profile: Any,
    warnings: List[str],
) -> Optional[Transform]:
    spec = item.spatial_spec
    if spec is None:
        return None
    scale = float(spec.scale_policy.get("value", 0.16))
    anchor = _anchor_point(item, event_index)
    if anchor is None:
        warnings.append(f"{item.plan_item_uid}: 空间锚点无法解析")
        return None
    relation = spec.relation.value
    brief = _spatial_brief(item, event_index)
    if spec.direction_policy.get("mode") == "match_event_direction" and brief:
        label = (brief.get("direction") or {}).get("label")
        relation = direction_relation(label or "") or relation
    avoid = collect_avoid_regions(brief, spec.avoid_regions, profile, asset_half=scale / 2.0)
    point, chosen, fell_back = choose_position(
        anchor, relation, spec.fallback_positions, avoid,
        distance=float(spec.offset_policy.get("distance", 0.08)),
        scale=scale,
        hard_p0="face" in spec.avoid_regions,
    )
    if fell_back:
        warnings.append(f"{item.plan_item_uid}: materialize 避让后位置仍与保护区冲突")
    offset = item.parameters.get("position_offset")
    if isinstance(offset, dict):
        point = (
            min(max(point[0] + float(offset.get("dx", 0.0)), 0.0), 1.0),
            min(max(point[1] + float(offset.get("dy", 0.0)), 0.0), 1.0),
        )
    return Transform(position=point, scale=scale)


def _anchor_point(item: PlanItem, event_index: Optional[EventIndex]) -> Optional[Tuple[float, float]]:
    spec = item.spatial_spec
    anchor = spec.anchor
    if anchor is None:
        return None
    atype = anchor.type.value
    if atype == "event_anchor" and anchor.event_uid:
        brief = event_index.brief(anchor.event_uid) if event_index else None
        spatial = event_index.spatial_brief(brief) if event_index else None
        if spatial:
            if spatial.get("event_anchor"):
                return tuple(spatial["event_anchor"])
            if spatial.get("head_center"):
                return tuple(spatial["head_center"])
            if spatial.get("person_bbox"):
                x1, y1, x2, y2 = spatial["person_bbox"]
                return ((x1 + x2) / 2, (y1 + y2) / 2)
        return (0.5, 0.3)
    if atype == "structural":
        return {"screen_center": (0.5, 0.5)}.get(anchor.structure_key, (0.5, 0.5))
    if atype == "absolute" and anchor.point:
        return tuple(anchor.point)
    if atype == "spatial_track":
        # 轨迹锚：follow 项在 _resolve_follow 里处理；静态退化用事件锚点/屏幕中上
        target = item.target.get("value") if item.target else None
        brief = event_index.brief(target) if (event_index and target) else None
        spatial = event_index.spatial_brief(brief) if (event_index and brief) else None
        if spatial and spatial.get("event_anchor"):
            return tuple(spatial["event_anchor"])
        return (0.5, 0.3)
    return None


def _spatial_brief(item: PlanItem, event_index: Optional[EventIndex]):
    if event_index is None:
        return None
    uid = None
    if item.spatial_spec and item.spatial_spec.anchor:
        uid = item.spatial_spec.anchor.event_uid
    if uid is None and item.target.get("type") == "event":
        uid = item.target.get("value")
    brief = event_index.brief(uid) if uid else None
    return event_index.spatial_brief(brief) if brief else None


def _resolve_follow(
    item: PlanItem,
    spatial_tracks: Optional[Dict[str, Any]],
    shifts: List[Dict[str, float]],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    spec = item.spatial_spec
    if spec is None or not spec.follow.enabled:
        return None
    follow = spec.follow
    target = spec.anchor.target if spec.anchor else "head"
    track = (spatial_tracks or {}).get(target)
    if track is None:
        # 无轨迹数据：留 trajectory_ref 给模块五自取（非阻塞）
        warnings.append(
            f"{item.plan_item_uid}: 未提供 '{target}' 轨迹，follow 保留 trajectory_ref"
        )
        return {
            "target": target,
            "mode": follow.mode,
            "smoothing": follow.smoothing,
            "trajectory_ref": {"target": target, "smoothing": follow.smoothing},
        }
    points = getattr(track, "points", None) or track.get("points") or []
    density = float(follow.keyframe_density or 1.0)
    keyframes = _resample(points, density, follow.dead_zone)
    for kf in keyframes:
        kf["t"] = round(source_to_project(kf["t"], shifts), 4)
    return {
        "target": target,
        "mode": "keyframes",
        "smoothing": follow.smoothing,
        "sensitivity": follow.sensitivity,
        "dead_zone": follow.dead_zone,
        "keyframes": keyframes,
    }


def _resample(points: List[Any], density: float, dead_zone: float) -> List[Dict[str, float]]:
    """按 keyframe_density（帧/秒）+ dead_zone 抽稀轨迹为关键帧。"""
    if not points:
        return []
    seq = [
        (float(p.t if hasattr(p, "t") else p["t"]),
         float(p.x if hasattr(p, "x") else p["x"]),
         float(p.y if hasattr(p, "y") else p["y"]))
        for p in points
    ]
    if density <= 0:
        density = 1.0
    min_dt = 1.0 / density
    out: List[Dict[str, float]] = []
    last_t, last_x, last_y = -1e9, 0.0, 0.0
    for t, x, y in seq:
        if t - last_t < min_dt:
            continue
        if out and abs(x - last_x) < dead_zone and abs(y - last_y) < dead_zone:
            continue
        out.append({"t": round(t, 4), "x": round(x, 4), "y": round(y, 4)})
        last_t, last_x, last_y = t, x, y
    if not out:
        t, x, y = seq[0]
        out.append({"t": round(t, 4), "x": round(x, 4), "y": round(y, 4)})
    return out


def _materialize_validation(
    resolved: List[ResolvedPlanItem], plan: LogicalEditingPlan
) -> ValidationReport:
    issues: List[ValidationIssue] = []
    for item in resolved:
        if item.status in (PlanItemStatus.planned, PlanItemStatus.degraded):
            if item.operation in (
                PlanOperation.add_overlay, PlanOperation.add_text,
                PlanOperation.add_effect, PlanOperation.track_overlay,
            ) and item.transform is None:
                issues.append(ValidationIssue(
                    code="missing_transform",
                    severity="warning",
                    message=f"{item.plan_item_uid} 空间项未解析出 transform",
                    item_uids=[item.plan_item_uid],
                ))
            if item.project_time.end <= item.project_time.start:
                issues.append(ValidationIssue(
                    code="nonpositive_duration",
                    severity="warning",
                    message=f"{item.plan_item_uid} project_time 非正时长",
                    item_uids=[item.plan_item_uid],
                ))
    hard = [i for i in issues if i.severity == "hard"]
    status = (
        ValidationStatus.blocked if hard
        else ValidationStatus.valid_with_warnings if issues
        else ValidationStatus.valid
    )
    return ValidationReport(
        status=status,
        temporal=SubReport(status="warn" if issues else "pass", issues=issues),
        hard_violations=hard,
        warnings=[i for i in issues if i.severity != "hard"],
    )
