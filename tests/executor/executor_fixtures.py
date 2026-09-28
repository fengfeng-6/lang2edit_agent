"""tests/executor 公共 fixture：直接构造 ResolvedEditingPlan + 假媒体文件。

不走模块一/二/三运行路径——按 §92 契约手写 ResolvedPlanItem；
媒体文件用 tmp_path 空文件（preflight 只验 isfile/decodable 标志）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from asset_manager.models import (
    AssetRecord,
    AssetSource,
    Integrity,
    ProjectAssetState,
    TechnicalMetadata,
)
from editing_planner.models import (
    PlanItemStatus,
    PlanOperation,
    ProjectTime,
    ResolvedEditingPlan,
    ResolvedPlanItem,
    TimelineMapping,
    TimelineTrack,
    Transform,
    ValidationReport,
    ValidationStatus,
)

from edit_executor import EditingExecutor
from edit_executor.models import (
    AnalysisArtifact,
    AnalysisArtifactRegistry,
    BackendConfig,
    ExecutionOptions,
    ExecutorInput,
    ExportSpec,
    SourceMedia,
)

PROJECT = "proj1"
DURATION = 10.0


def write_file(path: Path, content: bytes = b"x") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def make_source(
    tmp_path: Path,
    duration: float = DURATION,
    has_audio: bool = True,
    video_id: str = "vid_test",
) -> SourceMedia:
    uri = write_file(tmp_path / "media" / "source.mp4")
    return SourceMedia(
        media_uid=video_id,
        video_id=video_id,
        local_uri=uri,
        duration=duration,
        fps=30.0,
        width=1920,
        height=1080,
        has_audio=has_audio,
        content_hash="hash_src",
        version="v1",
    )


def make_item(
    uid: str,
    operation: PlanOperation,
    start: float = 0.0,
    end: float = DURATION,
    *,
    asset_uid: Optional[str] = None,
    transform: Optional[Transform] = None,
    follow: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    resolved_capability: Optional[str] = None,
    target: Optional[Dict[str, Any]] = None,
    source_time: Optional[float] = None,
    freeze_audio_policy: Optional[str] = None,
    status: PlanItemStatus = PlanItemStatus.planned,
    requirements: Optional[List[str]] = None,
    plan_key: str = "",
) -> ResolvedPlanItem:
    return ResolvedPlanItem(
        plan_item_uid=uid,
        plan_key=plan_key or uid,
        operation=operation,
        asset_uid=asset_uid,
        project_time=ProjectTime(start=start, end=end),
        transform=transform,
        follow=follow,
        parameters=dict(parameters or {}),
        resolved_capability=resolved_capability,
        target=dict(target or {}),
        source_time=source_time,
        freeze_audio_policy=freeze_audio_policy,
        source_requirement_ids=list(requirements or []),
        status=status,
    )


def overlay_item(
    uid: str = "item_heart",
    asset_uid: str = "ast_heart",
    start: float = 1.0,
    end: float = 4.0,
    scale: float = 0.16,
    position=(0.5, 0.3),
    **kwargs: Any,
) -> ResolvedPlanItem:
    return make_item(
        uid,
        PlanOperation.add_overlay,
        start,
        end,
        asset_uid=asset_uid,
        transform=Transform(position=position, scale=scale),
        parameters={"object_type": "heart"},
        **kwargs,
    )


def make_plan(
    items: Iterable[ResolvedPlanItem],
    *,
    plan_uid: str = "plan_001",
    total: float = DURATION,
    tracks: Optional[List[TimelineTrack]] = None,
    shifts: Optional[List[Dict[str, float]]] = None,
    validation_status: ValidationStatus = ValidationStatus.valid,
) -> ResolvedEditingPlan:
    return ResolvedEditingPlan(
        plan_uid=plan_uid,
        resolved_items=list(items),
        timeline_mapping=TimelineMapping(
            tracks=list(tracks or []),
            shifts=list(shifts or []),
            total_duration=total,
        ),
        validation=ValidationReport(status=validation_status),
    )


def make_asset(
    tmp_path: Path,
    asset_uid: str = "ast_heart",
    media_type: str = "image",
    decodable: bool = True,
    suffix: str = ".png",
) -> AssetRecord:
    uri = write_file(tmp_path / "media" / f"{asset_uid}{suffix}")
    return AssetRecord(
        asset_uid=asset_uid,
        source_type=AssetSource.local,
        media_type=media_type,
        local_uri=uri,
        integrity=Integrity(
            decodable=decodable, content_hash=f"hash_{asset_uid}"
        ),
        technical_metadata=TechnicalMetadata(width=200, height=200),
    )


def make_input(
    tmp_path: Path,
    plan: ResolvedEditingPlan,
    *,
    source: Optional[SourceMedia] = None,
    assets: Optional[Iterable[AssetRecord]] = None,
    artifacts: Optional[Iterable[AnalysisArtifact]] = None,
    backend_config: Optional[BackendConfig] = None,
    options: Optional[ExecutionOptions] = None,
    export_spec: Optional[ExportSpec] = None,
    project_id: str = PROJECT,
) -> ExecutorInput:
    registry = {
        rec.asset_uid: rec for rec in (assets or [])
    }
    return ExecutorInput(
        project_id=project_id,
        resolved_plan=plan,
        source_media=source or make_source(tmp_path),
        asset_registry=ProjectAssetState(
            project_id=project_id, registry=registry
        ),
        analysis_artifacts=AnalysisArtifactRegistry(
            artifacts={a.artifact_uid: a for a in (artifacts or [])}
        ),
        backend_config=backend_config or BackendConfig(),
        export_spec=export_spec,
        execution_options=options or ExecutionOptions(),
    )


def make_executor(tmp_path: Path, backend=None) -> EditingExecutor:
    return EditingExecutor(tmp_path / "ws", backend=backend)


def mask_artifact(
    uid: str = "art_mask_1",
    *,
    target: Optional[str] = "foreground_subject",
    artifact_type: str = "foreground_subject_mask",
    contains_person: bool = True,
    contains_mobility_device: bool = False,
) -> AnalysisArtifact:
    return AnalysisArtifact(
        artifact_uid=uid,
        artifact_type=artifact_type,
        target=target,
        video_id="vid_test",
        local_uri="mask.npz",
        semantic_properties={
            "contains_person": contains_person,
            "contains_mobility_device": contains_mobility_device,
        },
        producer="video_understanding",
    )


def tracks_artifact(uid: str = "art_tracks_1") -> AnalysisArtifact:
    return AnalysisArtifact(
        artifact_uid=uid,
        artifact_type="spatial_tracks",
        video_id="vid_test",
        local_uri="tracks.json",
        coordinate_space="source_video_normalized",
        producer="video_understanding",
    )
