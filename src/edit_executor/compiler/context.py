"""GraphCompiler 内部工作区：编译过程中共享的可变上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from asset_manager.models import ProjectAssetState
from editing_planner.models import ResolvedEditingPlan

from ..models import (
    AnalysisArtifactRegistry,
    DesiredProjectGraph,
    ExecutorDependency,
    MediaRef,
    ProjectSpec,
    SourceMedia,
    TimelineObject,
    TrackSpec,
)


@dataclass
class CompileContext:
    """单次 compile 的工作区（§22）：确定性、无 LLM。"""

    project_id: str
    plan: ResolvedEditingPlan
    source_media: SourceMedia
    asset_registry: Optional[ProjectAssetState] = None
    artifact_registry: AnalysisArtifactRegistry = field(
        default_factory=AnalysisArtifactRegistry
    )
    warnings: List[str] = field(default_factory=list)
    tracks: Dict[str, TrackSpec] = field(default_factory=dict)
    objects: Dict[str, TimelineObject] = field(default_factory=dict)
    media_refs: Dict[str, MediaRef] = field(default_factory=dict)
    plan_item_index: Dict[str, List[str]] = field(default_factory=dict)
    required_artifacts: List[ExecutorDependency] = field(default_factory=list)
    project_spec: ProjectSpec = field(default_factory=ProjectSpec)
    graph: Optional[DesiredProjectGraph] = None

    def add_object(self, obj: TimelineObject) -> TimelineObject:
        self.objects[obj.timeline_object_uid] = obj
        if obj.source_plan_item_uid:
            self.plan_item_index.setdefault(
                obj.source_plan_item_uid, []
            ).append(obj.timeline_object_uid)
        track = self.tracks.get(obj.track_uid)
        if track is not None and obj.timeline_object_uid not in track.object_uids:
            track.object_uids.append(obj.timeline_object_uid)
        return obj

    def require_artifact(
        self, artifact_type: str, target: Optional[str], reason: str
    ) -> Optional[str]:
        """登记分析产物需求；存在则返回 artifact_uid，否则返回 None。"""
        artifact = self.artifact_registry.require(artifact_type, target)
        if artifact is not None:
            return artifact.artifact_uid
        self.required_artifacts.append(
            ExecutorDependency(
                dependency_uid=f"dep_art_{len(self.required_artifacts):03d}",
                type="analysis_artifact",
                target=target or artifact_type,
                required_resource=artifact_type,
                blocking=True,
                reason=reason,
            )
        )
        return None
