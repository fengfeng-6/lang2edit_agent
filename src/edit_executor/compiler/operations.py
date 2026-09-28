"""PlanItemCompiler 注册表（§23-§28）。

每个 object-producing operation 一个编译器；输出 0/1/N 个
TimelineObject。mutation 类 operation 不在这里（见 mutations.py）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from editing_planner.models import PlanOperation, ResolvedPlanItem, Transform

from ..models import (
    KeyframeGroup,
    SemanticAnimationSpec,
    TimelineObject,
    TrackingBinding,
    _stable_uid,
)
from .context import CompileContext

#: TimelineObject.parameters 已知键（防 magic string 漂移）
PARAM_TEXT_CONTENT = "content"
PARAM_VOLUME_DB = "volume_db"
PARAM_MUTE_RANGES = "mute_ranges"
PARAM_SOURCE_START = "source_start"
PARAM_SOURCE_END = "source_end"
PARAM_SOURCE_TIME = "source_time"
PARAM_FREEZE_AUDIO_POLICY = "freeze_audio_policy"
PARAM_FREEZE_DURATION = "freeze_duration"
PARAM_RESOURCE_REF = "resource_ref"

SOURCE_VIDEO_MEDIA_REF = "mref_source_video"

_ROLE_TRACK = {
    "background": "trk_background",
    "foreground_subject": "trk_main_video",
    "main_video": "trk_main_video",
    "source_slice": "trk_main_video",
    "freeze_frame": "trk_main_video",
    "overlay": "trk_overlay",
    "effect": "trk_effect",
    "text": "trk_text",
    "music": "trk_audio",
    "sound_effect": "trk_audio",
    "original_audio": "trk_audio",
}


def track_uid_for_role(role: str, fallback: str) -> str:
    """多对象项按 role 覆盖轨道；单对象项用模块三的逻辑轨道分配。"""
    return _ROLE_TRACK.get(role, fallback)


def timeline_object_uid(plan_item_uid: str, role: str) -> str:
    """UID_timeline = H(plan_item_uid, role)（§12）。"""
    return _stable_uid("tlobj", plan_item_uid, role)


def _animation_of(item: ResolvedPlanItem) -> Optional[SemanticAnimationSpec]:
    spec = item.animation
    if spec is None or spec.type in ("", "none") and not spec.params:
        return None
    return SemanticAnimationSpec(
        semantic_type=spec.type or "none",
        duration=spec.params.get("duration"),
        intensity=spec.params.get("intensity"),
        params=dict(spec.params),
    )


def _base_object(
    item: ResolvedPlanItem,
    ctx: CompileContext,
    role: str,
    track_uid: str,
    **overrides: Any,
) -> TimelineObject:
    uid = timeline_object_uid(item.plan_item_uid, role)
    target = item.target or {}
    event_uids = (
        [str(target["value"])] if target.get("type") == "event" and target.get("value") else []
    )
    semantic_label = str(
        item.parameters.get("object_type")
        or item.parameters.get("semantic_label")
        or role
    )
    occurrence = sum(
        1
        for obj in ctx.objects.values()
        if obj.role == role and obj.semantic_label == semantic_label
    ) + 1
    obj = TimelineObject(
        timeline_object_uid=uid,
        object_key=f"{item.plan_item_uid}:{role}",
        origin="plan_item",
        source_plan_item_uid=item.plan_item_uid,
        source_requirement_ids=list(item.source_requirement_ids),
        source_event_uids=event_uids,
        object_type=role,
        role=role,
        semantic_label=semantic_label,
        track_uid=track_uid,
        project_time=item.project_time,
        transform=item.transform,
        animation=_animation_of(item),
        parameters=dict(item.parameters),
        provenance={
            "resolved_plan_uid": ctx.plan.plan_uid,
            "plan_item_uid": item.plan_item_uid,
            "plan_key": item.plan_key,
            "resolved_capability": item.resolved_capability,
            "degradation_applied": list(item.degradation_applied),
            "occurrence_index": occurrence,
        },
    )
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def _asset_media_ref_uid(asset_uid: Optional[str]) -> Optional[str]:
    return f"mref_{asset_uid}" if asset_uid else None


class PlanItemCompiler:
    """统一接口（§23）：item → 0/1/N 个 TimelineObject。"""

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        raise NotImplementedError


class OverlayCompiler(PlanItemCompiler):
    """add_overlay / add_effect：一对一（§24）。"""

    def __init__(self, role: str) -> None:
        self.role = role

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        track = _item_track(item, ctx, default=track_uid_for_role(self.role, ""))
        obj = _base_object(
            item,
            ctx,
            role=self.role,
            track_uid=track,
            object_type=str(item.parameters.get("object_type") or self.role),
            asset_uid=item.asset_uid,
            media_ref=_asset_media_ref_uid(item.asset_uid),
        )
        return [obj]


class TrackOverlayCompiler(PlanItemCompiler):
    """track_overlay（§25）：resolved_capability 决定 keyframes/TrackingBinding。"""

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        track = _item_track(item, ctx, default="trk_overlay")
        follow = item.follow or {}
        capability = item.resolved_capability or _infer_capability(item)
        keyframes: List[KeyframeGroup] = []
        tracking: Optional[TrackingBinding] = None
        if capability == "keyframes":
            for kf in follow.get("keyframes") or []:
                keyframes.append(
                    KeyframeGroup(
                        time=float(kf.get("t", 0.0)),
                        values={"x": kf.get("x"), "y": kf.get("y")},
                        interpolation="linear",
                    )
                )
        elif capability == "tracking":
            ref = ctx.require_artifact(
                "spatial_tracks",
                str(follow.get("target") or "head"),
                f"{item.plan_item_uid}: track_overlay 需要目标轨迹",
            )
            tracking = TrackingBinding(
                target=str(follow.get("target") or "head"),
                trajectory_ref=ref or "",
                smoothing=str(follow.get("smoothing") or "source_smoothed"),
                sensitivity=float(follow.get("sensitivity") or 1.0),
                dead_zone=float(follow.get("dead_zone") or 0.01),
            )
        transform = item.transform
        if keyframes and transform is not None:
            first = keyframes[0].values
            transform = Transform(
                position=(
                    float(first.get("x", transform.position[0])),
                    float(first.get("y", transform.position[1])),
                ),
                scale=transform.scale,
                rotation=transform.rotation,
            )
        obj = _base_object(
            item,
            ctx,
            role="overlay",
            track_uid=track,
            object_type=str(item.parameters.get("object_type") or "overlay"),
            asset_uid=item.asset_uid,
            media_ref=_asset_media_ref_uid(item.asset_uid),
            transform=transform,
            keyframes=keyframes,
            tracking=tracking,
        )
        return [obj]


def _infer_capability(item: ResolvedPlanItem) -> str:
    """旧版 ResolvedPlanItem 无 resolved_capability：从 follow 结构反推。"""
    follow = item.follow or {}
    if follow.get("keyframes"):
        return "keyframes"
    if follow.get("trajectory_ref"):
        return "tracking"
    return "static"


class TextCompiler(PlanItemCompiler):
    """add_text（§26）：MVP 保 content/position/scale/time。"""

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        parameters = dict(item.parameters)
        if "text" in parameters:
            parameters[PARAM_TEXT_CONTENT] = parameters.pop("text")
        obj = _base_object(
            item,
            ctx,
            role="text",
            track_uid=_item_track(item, ctx, default="trk_text"),
            object_type="text",
            semantic_label=str(
                item.parameters.get("object_type") or "text"
            ),
            parameters=parameters,
        )
        return [obj]


class MusicCompiler(PlanItemCompiler):
    """add_music / add_sound_effect（§27）：默认不删除原音频。"""

    def __init__(self, role: str) -> None:
        self.role = role

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        obj = _base_object(
            item,
            ctx,
            role=self.role,
            track_uid="trk_audio",
            object_type="audio",
            asset_uid=item.asset_uid,
            media_ref=_asset_media_ref_uid(item.asset_uid),
        )
        return [obj]


class BackgroundCompiler(PlanItemCompiler):
    """replace_background（§28）：背景对象 + 前景主体对象。"""

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        background = _base_object(
            item,
            ctx,
            role="background",
            track_uid="trk_background",
            object_type="background",
            asset_uid=item.asset_uid,
            media_ref=_asset_media_ref_uid(item.asset_uid),
        )
        preserve = bool(item.parameters.get("preserve_mobility_device"))
        mask_uid = _pick_mask_uid(item, ctx, preserve)
        if mask_uid is None:
            ctx.required_artifacts.append(_mask_dependency(item, preserve))
        foreground = _base_object(
            item,
            ctx,
            role="foreground_subject",
            track_uid="trk_main_video",
            object_type="foreground_subject",
            media_ref=SOURCE_VIDEO_MEDIA_REF,
            mask_ref=mask_uid,
            transform=None,
            animation=None,
        )
        return [background, foreground]


def _mask_dependency(item: ResolvedPlanItem, preserve: bool) -> Any:
    from ..models import ExecutorDependency

    return ExecutorDependency(
        dependency_uid=f"dep_mask_{item.plan_item_uid}",
        type="analysis_artifact",
        target="foreground_subject",
        required_resource=(
            "foreground_subject_mask[contains_person,contains_mobility_device]"
            if preserve
            else "foreground_subject_mask|person_mask"
        ),
        blocking=True,
        reason=(
            f"{item.plan_item_uid}: preserve_mobility_device 需要含轮椅主体蒙版"
            if preserve
            else f"{item.plan_item_uid}: 背景替换需要前景主体蒙版"
        ),
    )


def _pick_mask_uid(
    item: ResolvedPlanItem, ctx: CompileContext, preserve: bool
) -> Optional[str]:
    """为前景主体绑定蒙版产物；轮椅场景必须满足语义标志（场景6）。"""
    candidates = ctx.artifact_registry.find(
        "foreground_subject_mask", "foreground_subject"
    )
    if preserve:
        candidates = [
            a
            for a in candidates
            if a.semantic_properties.get("contains_person")
            and a.semantic_properties.get("contains_mobility_device")
        ]
        return candidates[0].artifact_uid if candidates else None
    if candidates:
        return candidates[0].artifact_uid
    person = ctx.artifact_registry.require("person_mask", "person")
    return person.artifact_uid if person is not None else None


class FreezeCompiler(PlanItemCompiler):
    """freeze → 定格对象（§15-§16）；切片由 SourceTimelineCompiler 负责。"""

    def compile(
        self, item: ResolvedPlanItem, ctx: CompileContext
    ) -> List[TimelineObject]:
        parameters = {
            PARAM_SOURCE_TIME: item.source_time,
            PARAM_FREEZE_DURATION: max(
                0.0, item.project_time.end - item.project_time.start
            ),
            PARAM_FREEZE_AUDIO_POLICY: item.freeze_audio_policy
            or item.parameters.get("freeze_audio_policy")
            or "continue",
        }
        obj = _base_object(
            item,
            ctx,
            role="freeze_frame",
            track_uid="trk_main_video",
            object_type="freeze",
            media_ref=SOURCE_VIDEO_MEDIA_REF,
            parameters=parameters,
            transform=None,
            animation=None,
        )
        return [obj]


def _item_track(
    item: ResolvedPlanItem, ctx: CompileContext, default: str
) -> str:
    """优先用模块三 timeline_mapping.tracks[].item_uids 的分配。"""
    for track in ctx.plan.timeline_mapping.tracks:
        if item.plan_item_uid in track.item_uids:
            return f"trk_{track.name}"
    return default


#: object-producing operation → 编译器（§23）；mutation/structural 不在此表
OPERATION_COMPILERS: Dict[str, PlanItemCompiler] = {
    PlanOperation.add_overlay.value: OverlayCompiler("overlay"),
    PlanOperation.add_effect.value: OverlayCompiler("effect"),
    PlanOperation.track_overlay.value: TrackOverlayCompiler(),
    PlanOperation.add_text.value: TextCompiler(),
    PlanOperation.add_music.value: MusicCompiler("music"),
    PlanOperation.add_sound_effect.value: MusicCompiler("sound_effect"),
    PlanOperation.replace_background.value: BackgroundCompiler(),
    PlanOperation.freeze.value: FreezeCompiler(),
}

#: mutation operation（§21.2）：不产出新对象
MUTATION_OPERATIONS = {
    PlanOperation.scale_adjust.value,
    PlanOperation.position_adjust.value,
    PlanOperation.volume_adjust.value,
    PlanOperation.replace_music.value,
}
