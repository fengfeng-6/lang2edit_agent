"""GraphCompiler（§22）：ResolvedEditingPlan + ExecutionContext → DesiredProjectGraph。

管线：InitProject → SourceMedia → LogicalTracks → Compile producing
→ Apply mutations → Resolve asset MediaRefs → Resolve artifacts
→ Build source timeline → Normalize keyframes → Fingerprints → Validate。

全程确定性，不调用 LLM。
"""

from __future__ import annotations

from typing import Dict, List

from editing_planner.models import PlanItemStatus, PlanOperation
from gesture_intent.models import model_dump

from ..models import (
    DesiredProjectGraph,
    ExecutorInput,
    MediaRef,
    ProjectSpec,
    TrackSpec,
    _stable_uid,
)
from .context import CompileContext
from .fingerprint import graph_fingerprint, object_fingerprint
from .mutations import apply_mutation
from .operations import (
    MUTATION_OPERATIONS,
    OPERATION_COMPILERS,
    SOURCE_VIDEO_MEDIA_REF,
)
from .source_timeline import compile_source_timeline

_ACTIVE_STATUSES = {PlanItemStatus.planned.value, PlanItemStatus.degraded.value}
_TRACK_Z = {
    "audio": -1,
    "background": 0,
    "main_video": 100,
    "overlay": 200,
    "effect": 250,
    "text": 300,
}
# 六条逻辑轨道全部预建：稳定 trk_<name> 身份 + 防对象悬空轨道引用（§9）
_MINIMUM_TRACKS = tuple(_TRACK_Z)


def compile_graph(executor_input: ExecutorInput) -> DesiredProjectGraph:
    plan = executor_input.resolved_plan
    ctx = CompileContext(
        project_id=executor_input.project_id,
        plan=plan,
        source_media=executor_input.source_media,
        asset_registry=executor_input.asset_registry,
        artifact_registry=executor_input.analysis_artifacts,
    )

    _init_project(ctx)
    _create_source_media(ctx)
    _create_logical_tracks(ctx)
    _compile_items(ctx)
    # 源时间线必须先于 mutation：volume/scale/position 的目标
    # （main_video / 切片 / original_audio）是系统对象，此刻才存在。
    _build_source_timeline(ctx)
    _apply_mutations(ctx)
    _resolve_asset_media_refs(ctx)
    _normalize_keyframes(ctx)
    _compute_fingerprints(ctx)
    # 编译期产物缺口（蒙版/轨迹缺失）随 graph.warnings 透出；
    # apply() 的 preflight 已在更早处拦截，这里是 compile-only 路径的诚实面。
    for dep in ctx.required_artifacts:
        ctx.warnings.append(dep.reason)

    graph = DesiredProjectGraph(
        graph_uid=_stable_uid(
            "graph",
            executor_input.project_id,
            plan.plan_uid,
            [obj.fingerprint for obj in sorted(
                ctx.objects.values(), key=lambda o: o.timeline_object_uid
            )],
        ),
        project_id=executor_input.project_id,
        source_plan_uid=plan.plan_uid,
        source_plan_version=plan.source_logical_plan_version,
        project_spec=ctx.project_spec,
        media_refs=ctx.media_refs,
        tracks=ctx.tracks,
        objects=ctx.objects,
        total_duration=ctx.project_spec.duration,
        warnings=list(ctx.warnings),
    )
    ctx.graph = graph
    return graph


def _init_project(ctx: CompileContext) -> None:
    source = ctx.source_media
    duration = ctx.plan.timeline_mapping.total_duration or source.duration
    ctx.project_spec = ProjectSpec(
        width=source.width,
        height=source.height,
        fps=source.fps,
        aspect_ratio=(source.width / source.height) if source.height else 0.0,
        audio_sample_rate=None,
        duration=duration,
    )


def _create_source_media(ctx: CompileContext) -> None:
    source = ctx.source_media
    ctx.media_refs[SOURCE_VIDEO_MEDIA_REF] = MediaRef(
        media_ref_uid=SOURCE_VIDEO_MEDIA_REF,
        source_type="source_video",
        source_uid=source.media_uid,
        local_uri=source.local_uri,
        media_type="video",
        content_hash=source.content_hash,
        technical_metadata={
            "duration": source.duration,
            "fps": source.fps,
            "width": source.width,
            "height": source.height,
            "rotation": source.rotation,
            "has_audio": source.has_audio,
        },
    )


def _create_logical_tracks(ctx: CompileContext) -> None:
    names: Dict[str, int] = {}
    for track in ctx.plan.timeline_mapping.tracks:
        names[track.name] = track.z_order
    for name in _MINIMUM_TRACKS:
        names.setdefault(name, _TRACK_Z[name])
    for name in sorted(names, key=lambda n: names[n]):
        uid = f"trk_{name}"
        ctx.tracks[uid] = TrackSpec(
            track_uid=uid,
            logical_name=name,
            track_type="audio" if name == "audio" else "video",
            z_order=names[name],
            object_uids=[],
        )


def _compile_items(ctx: CompileContext) -> None:
    for item in ctx.plan.resolved_items:
        if item.status.value not in _ACTIVE_STATUSES:
            continue
        op = item.operation.value
        if op in MUTATION_OPERATIONS:
            continue
        compiler = OPERATION_COMPILERS.get(op)
        if compiler is None:
            ctx.warnings.append(
                f"{item.plan_item_uid}: operation {op} 无编译器，跳过"
            )
            continue
        for obj in compiler.compile(item, ctx):
            ctx.add_object(obj)


def _apply_mutations(ctx: CompileContext) -> None:
    for item in ctx.plan.resolved_items:
        if item.status.value not in _ACTIVE_STATUSES:
            continue
        if item.operation.value in MUTATION_OPERATIONS:
            apply_mutation(item, ctx)


def _resolve_asset_media_refs(ctx: CompileContext) -> None:
    registry = (
        ctx.asset_registry.registry if ctx.asset_registry is not None else {}
    )
    for obj in ctx.objects.values():
        if not obj.asset_uid or obj.media_ref in ctx.media_refs:
            continue
        record = registry.get(obj.asset_uid)
        if record is None:
            ctx.warnings.append(
                f"{obj.timeline_object_uid}: asset {obj.asset_uid} 不在 registry"
            )
            continue
        uid = f"mref_{obj.asset_uid}"
        ctx.media_refs[uid] = MediaRef(
            media_ref_uid=uid,
            source_type="asset",
            source_uid=obj.asset_uid,
            local_uri=record.local_uri,
            media_type=record.media_type,
            content_hash=record.integrity.content_hash,
            technical_metadata=model_dump(record.technical_metadata),
        )
        obj.media_ref = uid


def _build_source_timeline(ctx: CompileContext) -> None:
    compile_source_timeline(ctx)


def _normalize_keyframes(ctx: CompileContext) -> None:
    """排序、clamp 到 project_time 区间、4dp（与模块三约定一致）、同时刻 keep-last。"""
    for obj in ctx.objects.values():
        if not obj.keyframes:
            continue
        start, end = obj.project_time.start, obj.project_time.end
        dedup: Dict[float, Any] = {}
        for kf in obj.keyframes:
            kf.time = round(min(max(kf.time, start), end), 4)
            dedup[kf.time] = kf
        obj.keyframes = [dedup[t] for t in sorted(dedup)]


def _compute_fingerprints(ctx: CompileContext) -> None:
    for obj in ctx.objects.values():
        obj.fingerprint = object_fingerprint(obj)
