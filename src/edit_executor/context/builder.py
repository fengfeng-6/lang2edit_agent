"""build_executor_input：从 workspace 组装 ExecutorInput（§4/§6）。

只读消费，绝不调用任何分析：
    workspace/<project_id>/
    ├── asset_registry.json        → ProjectAssetState（模块四）
    ├── semantic_video_state.json  → VideoMetadata → SourceMedia（模块二）
    └── analysis_artifacts/<id>.json → spatial_tracks 等 artifact 引用

蒙版类产物模块二尚无生产端（spatial_asset_refs 是空挂点）——
``extra_artifacts`` 是外部管线/测试直接注入 AnalysisArtifact 的通道。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

from asset_manager.registry.store import ProjectStore
from editing_planner.models import ResolvedEditingPlan
from gesture_intent.models import model_validate

from ..models import (
    AnalysisArtifact,
    AnalysisArtifactRegistry,
    BackendConfig,
    ExecutionOptions,
    ExecutorInput,
    ExportSpec,
    SourceMedia,
    _stable_uid,
)


def build_executor_input(
    workspace_root: Union[str, Path],
    project_id: str,
    resolved_plan: ResolvedEditingPlan,
    *,
    source_uri: Optional[str] = None,
    source_media: Optional[SourceMedia] = None,
    backend_config: Optional[BackendConfig] = None,
    export_spec: Optional[ExportSpec] = None,
    execution_options: Optional[ExecutionOptions] = None,
    extra_artifacts: Optional[Iterable[Union[AnalysisArtifact, Dict[str, Any]]]] = None,
) -> ExecutorInput:
    workspace_root = Path(workspace_root)
    project_dir = workspace_root / project_id

    # 模块四 registry（文件缺失时 load_state 返回空 state）
    asset_state = ProjectStore(str(workspace_root), project_id).load_state()

    # 模块二语义状态 → SourceMedia + artifact 引用
    artifacts: Dict[str, AnalysisArtifact] = {}
    semantic_version = 0
    video_id = ""
    video_version = ""
    if source_media is None:
        source_media, semantic_version, video_id, video_version = (
            _load_semantic(project_dir, source_uri)
        )
    _collect_artifacts(project_dir, artifacts, semantic_version, video_id, video_version)

    for extra in extra_artifacts or []:
        artifact = (
            extra
            if isinstance(extra, AnalysisArtifact)
            else model_validate(AnalysisArtifact, extra)
        )
        if artifact.semantic_state_version == 0:
            artifact.semantic_state_version = semantic_version
        if not artifact.video_id:
            artifact.video_id = video_id
        artifacts[artifact.artifact_uid] = artifact

    return ExecutorInput(
        project_id=project_id,
        resolved_plan=resolved_plan,
        source_media=source_media,
        asset_registry=asset_state,
        analysis_artifacts=AnalysisArtifactRegistry(artifacts=artifacts),
        backend_config=backend_config or BackendConfig(),
        export_spec=export_spec,
        execution_options=execution_options or ExecutionOptions(),
    )


def _load_semantic(project_dir: Path, source_uri: Optional[str]):
    """semantic_video_state.json 存在 → SourceMedia；缺失 → 空壳（preflight 兜底）。"""
    state_path = project_dir / "semantic_video_state.json"
    if not state_path.exists():
        return SourceMedia(media_uid="", video_id="", local_uri=source_uri or ""), 0, "", ""
    from video_understanding.state.store import SemanticStateStore

    manager = SemanticStateStore(project_dir).load()
    state = manager.state
    meta = state.video
    media = SourceMedia(
        media_uid=meta.video_id,
        video_id=meta.video_id,
        local_uri=source_uri or meta.source_uri or "",
        duration=meta.duration,
        fps=meta.fps,
        width=meta.width,
        height=meta.height,
        rotation=meta.rotation,
        has_audio=meta.has_audio,
        version=meta.version,
    )
    return media, int(state.version), meta.video_id, meta.version


def _collect_artifacts(
    project_dir: Path,
    artifacts: Dict[str, AnalysisArtifact],
    semantic_version: int,
    video_id: str,
    video_version: str,
) -> None:
    state_path = project_dir / "semantic_video_state.json"
    if not state_path.exists():
        return
    import json

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    artifacts_dir = project_dir / "analysis_artifacts"
    for artifact_id in raw.get("track_artifact_ids") or []:
        path = artifacts_dir / f"{artifact_id}.json"
        artifacts[artifact_id] = AnalysisArtifact(
            artifact_uid=artifact_id,
            artifact_type="spatial_tracks",
            video_id=video_id,
            video_version=video_version,
            semantic_state_version=semantic_version,
            local_uri=str(path) if path.exists() else None,
            coordinate_space="source_video_normalized",
            producer="video_understanding",
        )
    for ref in raw.get("spatial_asset_refs") or []:
        uid = ref.get("artifact_id") or ref.get("artifact_uid") or _stable_uid(
            "art", ref.get("type", ""), ref.get("local_uri", "")
        )
        artifacts[uid] = AnalysisArtifact(
            artifact_uid=uid,
            artifact_type=ref.get("artifact_type") or ref.get("type") or "",
            video_id=video_id,
            video_version=video_version,
            semantic_state_version=semantic_version,
            target=ref.get("target"),
            local_uri=ref.get("local_uri") or ref.get("path"),
            inline_data=ref.get("inline_data"),
            coordinate_space=ref.get("coordinate_space"),
            semantic_properties=dict(ref.get("semantic_properties") or {}),
            producer=ref.get("producer") or "video_understanding",
            valid=bool(ref.get("valid", True)),
        )
