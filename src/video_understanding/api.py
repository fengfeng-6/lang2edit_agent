"""模块二对外接口（§52）。

``VideoUnderstanding`` 是模块门面，第一阶段接口：

  analyze_video(video)                    基础视频分析：Metadata + Base Spatial State
  resolve_queries(required_video_queries) 需求驱动的定向分析（消费模块一输出，§53）
  get_semantic_events(query)              读取已有语义事件
  get_spatial_snapshot(event_uid)         事件级空间快照
  get_spatial_track(target, time_range)   空间轨迹（跟头 / 跟手 / 跟人物）
  analyze_audio(asset)                    音频分析（原视频与外部音乐同协议，§35）
  build_semantic_view(context)            为 Planner / LLM 构建任务相关语义视图
  invalidate(dependency)                  分析结果失效（§47）

设计取向：
- 核心链路（路由 / 聚合 / 状态 / 缓存 / 视图）纯标准库 + Pydantic；
- 重模型依赖（MediaPipe / librosa）以可插拔 analyzer 懒加载，缺失时
  query 记 ``failed`` 并写明依赖名，绝不等价于 ``not_found``（§42）；
- Dense Data（稠密轨道）与 Semantic State 分离（§48），可注入 JSON
  artifact 复现完整链路。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from gesture_intent.models import RequiredVideoQuery, model_validate

from .audio.analyzer import (
    AudioAnalyzer,
    LibrosaAudioAnalyzer,
    ProvidedAudioAnalyzer,
    default_audio_analyzer,
)
from .events.aggregator import AggregatedSpan, TemporalConfig, aggregate
from .events.detectors import (
    DependencyError,
    DetectContext,
    build_detector,
    parts_present,
)
from .events import registry
from .events.open_semantic import MotionEnergyProposer, OpenSemanticVerifier, detect_open_semantic
from .events.router import RoutedPlan, normalize_query, route
from .events.structural import resolve_structural
from .models import (
    AnalysisRecord,
    AudioAnalysis,
    ConfidenceStatus,
    DenseSpatialTracks,
    DetectorInfo,
    EventType,
    MobilityProfile,
    QueryResult,
    QueryStatus,
    SemanticEvent,
    SemanticView,
    SpatialSnapshot,
    StructuralRef,
    TemporalSpan,
    Trajectory,
    VideoAnalysisResult,
    VideoMetadata,
)
from .pose.conditions import compile_condition, condition_min_duration
from .preprocessing.metadata import resolve_metadata
from .spatial.loaders import SpatialAnalyzer, load_tracks
from .spatial.mediapipe_backend import MediaPipeSpatialAnalyzer
from .spatial.profile import infer_mobility_profile, mirror_tracks
from .state.cache import select_by_occurrence
from .state.manager import SemanticStateManager
from .state.store import SemanticStateStore
from .state.view import build_semantic_view

ORIGINAL_AUDIO_ASSET = "original_audio"


class VideoUnderstanding:
    """模块二门面：单个视频的需求驱动分析。

    生命周期：analyze_video(video) 建立状态 → resolve_queries(...) 增量分析
    → get_* / build_semantic_view 读取 → invalidate(...) 失效。
    """

    def __init__(
        self,
        *,
        workspace_dir: Optional[Union[str, Path]] = None,
        spatial_analyzer: Optional[SpatialAnalyzer] = None,
        audio_analyzer: Optional[AudioAnalyzer] = None,
        open_verifier: Optional[OpenSemanticVerifier] = None,
    ):
        self._spatial_analyzer = spatial_analyzer
        self._audio_analyzer = audio_analyzer
        self._open_verifier = open_verifier
        self._manager: Optional[SemanticStateManager] = None
        self._store = SemanticStateStore(workspace_dir) if workspace_dir else None
        self._audio_inputs: Dict[str, Any] = {}  # asset_id → 提供方数据/路径

    # ------------------------------------------------------------------
    # §52 analyze_video
    # ------------------------------------------------------------------

    def analyze_video(self, video: Union[str, Path, dict, VideoMetadata]) -> VideoAnalysisResult:
        """基础视频分析：metadata + base spatial state。

        video 可以是：路径 / VideoMetadata / dict{video_id, path?, metadata?,
        tracks?, tracks_path?, audio?}。dict 注入 tracks 时跳过模型分析——
        测试、外部管线与缓存复现共用此路径。
        """
        metadata = resolve_metadata(video)
        manager = SemanticStateManager(metadata)
        self._manager = manager
        warnings: List[str] = []

        declared_profile = None
        if isinstance(video, dict):
            for asset_id, payload in (video.get("audio") or {}).items():
                self._audio_inputs[asset_id] = payload
            if video.get("profile"):
                declared_profile = model_validate(MobilityProfile, video["profile"])

        tracks = self._produce_tracks(video, metadata, warnings)
        if tracks is not None and declared_profile is not None and declared_profile.mirrored:
            mirror_tracks(tracks)  # 前置镜像自拍：先修正左右手标签再分析
        # 可动性画像：显式声明优先，否则从轨道覆盖度自动推断
        profile = declared_profile
        if profile is None and tracks is not None:
            profile = infer_mobility_profile(tracks)
        manager.state.subject_profile = profile or MobilityProfile()

        base_ref = manager.attach_tracks(tracks) if tracks is not None else None
        self._persist()
        return VideoAnalysisResult(
            video=metadata, base_track_ref=base_ref,
            state_version=manager.state.version, warnings=warnings,
        )

    def _produce_tracks(self, video: Any, metadata: VideoMetadata, warnings: List[str]) -> Optional[DenseSpatialTracks]:
        if isinstance(video, dict) and (video.get("tracks") is not None or video.get("tracks_path")):
            payload = video.get("tracks") if video.get("tracks") is not None else video["tracks_path"]
            tracks = load_tracks(payload, video_id=metadata.video_id)
            tracks.producer = tracks.producer or "json_tracks_v1"
            return tracks
        analyzer = self._spatial_analyzer or MediaPipeSpatialAnalyzer()
        try:
            if not analyzer.available():
                warnings.append(
                    f"spatial analyzer '{getattr(analyzer, 'name', analyzer)}' unavailable; "
                    "queries depending on tracks will fail"
                )
                return None
            return analyzer.analyze(video)
        except Exception as exc:  # 分析器技术错误：metadata 仍可用，检测查询记 failed
            warnings.append(f"spatial analysis failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # §52 resolve_queries —— 需求驱动的增量分析
    # ------------------------------------------------------------------

    def resolve_queries(self, queries: Iterable[Union[RequiredVideoQuery, dict]]) -> List[QueryResult]:
        if self._manager is None:
            raise RuntimeError("call analyze_video() first")
        return [self._resolve_one(q) for q in queries]

    def _resolve_one(self, query: Union[RequiredVideoQuery, dict]) -> QueryResult:
        plan = route(query)
        data = normalize_query(query)
        manager = self._manager
        assert manager is not None

        # ---- 缓存覆盖（§45-46）：已有覆盖记录 → 直接复用事件 ----
        covering = manager.find_covering_record(data)
        if covering is not None:
            events = self._events_for_record(covering)
            selected = select_by_occurrence(events, data.get("required_occurrence"))
            status = covering.status
            if covering.status == QueryStatus.completed and not selected \
                    and covering.strategy != "base_spatial":
                status = QueryStatus.not_found
            return QueryResult(
                query_id=plan.query_id, status=status, strategy=covering.strategy,
                events=selected, selected_event_uids=[e.event_uid for e in selected],
                cache_hit=True,
            )

        record = AnalysisRecord(
            query_id=plan.query_id, query=data, status=QueryStatus.running,
            strategy=plan.strategy,
            source_video_version=f"{manager.state.video.video_id}_{manager.state.video.version}",
            dependencies=list(plan.entry.dependencies) if plan.entry else [],
        )

        if plan.error:
            record.status = QueryStatus.failed
            record.error = plan.error
            manager.record_query(record)
            return QueryResult(query_id=plan.query_id, status=record.status,
                               strategy=plan.strategy, error=plan.error)

        try:
            events, status, error, note = self._execute_plan(plan, record)
        except DependencyError as exc:
            events, status, error, note = [], QueryStatus.failed, str(exc), None
        except Exception as exc:  # 技术错误 → failed（§42）
            events, status, error, note = [], QueryStatus.failed, f"{type(exc).__name__}: {exc}", None

        if events:
            record.result_refs = [e.event_uid for e in events]
        record.status = status
        record.error = error
        manager.record_query(record)
        self._persist()

        selected = select_by_occurrence(events, data.get("required_occurrence"))
        # occurrence 过滤只适用于产出事件的策略；base_spatial 本就不产事件
        if status == QueryStatus.completed and not selected and record.strategy != "base_spatial":
            status = QueryStatus.not_found
        return QueryResult(
            query_id=plan.query_id, status=status, strategy=plan.strategy,
            events=events, selected_event_uids=[e.event_uid for e in selected],
            cache_hit=False, error=error, note=note,
        )

    def _events_for_record(self, record: AnalysisRecord) -> List[SemanticEvent]:
        state = self._manager.state
        wanted = set(record.result_refs)
        return sorted(
            (e for e in state.semantic_events if e.event_uid in wanted and not e.invalidated),
            key=lambda e: (e.temporal.start_time, e.canonical),
        )

    def _execute_plan(self, plan: RoutedPlan, record: AnalysisRecord):
        """执行分析，返回 (events, status, error, note)。"""
        manager = self._manager
        assert manager is not None

        if plan.strategy == "base_spatial":
            return self._run_base_spatial(plan, record)
        if plan.strategy == "structural":
            return self._run_structural(plan, record)
        if plan.strategy == "audio_analyzer":
            return self._run_audio(plan, record)
        if plan.strategy == "open_semantic":
            return self._run_open_semantic(plan, record)
        if plan.strategy == "pose_motion_rule":
            return self._run_pose_condition(plan, record)
        return self._run_dedicated(plan, record)

    # ---- dedicated detector（§15）----

    def _run_dedicated(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        entry = plan.entry
        if manager.tracks is None or not manager.tracks.frames:
            return [], QueryStatus.failed, "dense spatial tracks unavailable", None

        # ---- 可动性适配（无障碍）----
        profile = manager.state.subject_profile
        note = None
        adapted_from = None
        if len(profile.available_hands) < entry.requires_hands:
            variant = registry.lookup(entry.single_hand_variant) if entry.single_hand_variant else None
            if variant is not None and variant.supported and variant.detector:
                adapted_from = entry.canonical
                note = (f"'{entry.canonical}' 需要 {entry.requires_hands} 只可用双手，"
                        f"单手主体降级为 '{variant.canonical}'")
                entry = variant
            else:
                return [], QueryStatus.not_found, None, (
                    f"'{entry.canonical}' 需要 {entry.requires_hands} 只可用双手，"
                    f"主体画像仅 {profile.available_hands}；该事件对本主体不适用")
        if profile.posture == "seated" and entry.not_seated:
            return [], QueryStatus.not_found, None, (
                f"'{entry.canonical}' 对坐姿主体不适用（轮椅/坐姿视频）")

        detector = build_detector(entry.detector)
        ctx = DetectContext(duration=manager.state.video.duration, profile=profile)
        scores = detector.score(manager.tracks, ctx)
        record.model_version = f"{entry.detector}_v1"
        samples = [(s.t, s.conf) for s in scores]
        spans = detector.post_filter(aggregate(samples, entry.temporal), ctx)

        # 数据覆盖度：span 内必需部位齐备的帧比例写入置信度来源；
        # 覆盖不足时 confirmed 降为 uncertain——"缺肢体"不等于"没做动作"
        span_sources: List[dict] = []
        if detector.required_parts:
            for span in spans:
                frames_in_span = manager.tracks.between(
                    span.temporal.start_time, span.temporal.end_time)
                cov = (sum(1 for f in frames_in_span
                           if parts_present(f, detector.required_parts))
                       / len(frames_in_span)) if frames_in_span else 0.0
                span_sources.append({"data_coverage": round(cov, 3)})
                if cov < 0.5 and span.status == ConfidenceStatus.confirmed:
                    span.status = ConfidenceStatus.uncertain
        else:
            span_sources = [{} for _ in spans]

        events = manager.add_events(
            entry.canonical, entry.event_type, spans,
            query_id=record.query_id,
            detector=DetectorInfo(strategy="dedicated_detector", version=record.model_version),
            extras_fn=lambda tracks, t: detector.extras_at(tracks, t),
            span_sources=span_sources,
            event_properties=({"adapted_from": adapted_from} if adapted_from else None),
        )
        return events, _status_of(spans), None, note

    # ---- pose / motion rule（§21-23）----

    def _run_pose_condition(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        if manager.tracks is None or not manager.tracks.frames:
            return [], QueryStatus.failed, "dense spatial tracks unavailable", None
        # 坐姿主体（轮椅入框致 person bbox 宽度失真）改用肩宽归一化
        profile = manager.state.subject_profile
        norm_scale = "shoulder" if profile.posture == "seated" else "person"
        predicate = compile_condition(plan.condition, norm_scale=norm_scale)
        samples = [(f.timestamp, predicate(f)) for f in manager.tracks.frames]
        config = TemporalConfig(
            threshold=0.6, candidate_threshold=0.25,
            min_duration=condition_min_duration(plan.condition, 0.2),
            merge_gap=0.15, smooth_half_window=1,
        )
        spans = aggregate(samples, config)
        canonical = _pose_condition_canonical(plan.condition)
        record.model_version = "condition_compiler_v1"
        record.dependencies = ["pose_track"]
        subject = plan.condition.get("subject", "hand")
        accessor = subject if isinstance(subject, str) else "person"
        events = manager.add_events(
            canonical, EventType.pose_condition, spans,
            query_id=record.query_id,
            detector=DetectorInfo(strategy="pose_motion_rule", version=record.model_version),
            extras_fn=_condition_extras(accessor),
            event_properties={"condition": plan.condition, "norm_scale": norm_scale},
        )
        return events, _status_of(spans), None, None

    # ---- audio（§33-35）----

    def _run_audio(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        analysis = self._ensure_audio(ORIGINAL_AUDIO_ASSET)
        if analysis is None:
            return [], QueryStatus.failed, "audio analysis unavailable (no audio data or analyzer)", None
        canonical = plan.canonical
        if canonical == "beat":
            times = analysis.beats
        elif canonical == "downbeat":
            times = analysis.downbeats
        elif canonical == "music_onset":
            times = analysis.onsets[:1]
        else:
            return [], QueryStatus.failed, f"audio event '{canonical}' not supported", None
        record.model_version = analysis.analyzer
        record.dependencies = [f"audio:{analysis.asset_id}"]
        spans = [
            AggregatedSpan(
                temporal=TemporalSpan(start_time=t, peak_time=t, end_time=t),
                confidence=analysis.confidence, status=ConfidenceStatus.confirmed,
            )
            for t in times
        ]
        events = manager.add_events(
            canonical, EventType.audio_event, spans,
            query_id=record.query_id,
            detector=DetectorInfo(strategy="audio_analyzer", version=analysis.analyzer),
            confidence_sources={"audio_analysis": analysis.confidence},
        )
        return events, _status_of(spans), None, None

    # ---- structural（§39）----

    def _run_structural(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        spans, props = resolve_structural(
            plan.canonical,
            duration=manager.state.video.duration,
            tracks=manager.tracks,
            events=manager.state.semantic_events,
        )
        record.model_version = "structural_v1"
        events = manager.add_events(
            plan.canonical, EventType.video_structure, spans,
            query_id=record.query_id,
            detector=DetectorInfo(strategy="structural", version=record.model_version),
            event_properties=props,
        )
        # first_action / last_action 回填 structural_state（§39）
        for event in events:
            ref = StructuralRef(event_ref=event.event_uid, confidence=event.confidence.overall)
            if plan.canonical == "first_action":
                manager.state.structural_state.first_action = ref
            if plan.canonical == "last_action":
                manager.state.structural_state.last_action = ref
        return events, _status_of(spans), None, None

    # ---- open semantic（§24）----

    def _run_open_semantic(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        if manager.tracks is None or not manager.tracks.frames:
            return [], QueryStatus.failed, "dense spatial tracks unavailable", None
        result = detect_open_semantic(
            manager.tracks, plan.description,
            verifier=self._open_verifier, proposer=MotionEnergyProposer(),
        )
        if result.verifier_name is None:
            return [], QueryStatus.failed, \
                "open semantic verifier not configured; candidates proposed but unverified", None
        spans = [
            AggregatedSpan(temporal=span, confidence=conf,
                           status=ConfidenceStatus.confirmed if conf >= 0.6 else ConfidenceStatus.uncertain)
            for span, conf in result.verified
        ]
        canonical = "open_" + hashlib.sha1(plan.description.encode("utf-8")).hexdigest()[:6]
        record.model_version = result.verifier_name
        events = manager.add_events(
            canonical, EventType.body_action, spans,
            query_id=record.query_id,
            detector=DetectorInfo(strategy="open_semantic", version=result.verifier_name),
            event_properties={"description": plan.description,
                              "candidates": len(result.candidates)},
            confidence_sources={"open_verifier": max((c for _, c in result.verified), default=0.0)},
        )
        return events, _status_of(spans), None, None

    # ---- base spatial（§8/§11）----

    def _run_base_spatial(self, plan: RoutedPlan, record: AnalysisRecord):
        manager = self._manager
        if manager.tracks is None or not manager.tracks.frames:
            return [], QueryStatus.failed, "dense spatial tracks unavailable", None
        reference = (plan.query.get("reference") or "person")
        has_target = any(
            (f.face_bbox if reference == "face" else f.person_bbox) is not None
            for f in manager.tracks.frames
        )
        record.model_version = manager.tracks.producer
        record.result_refs = list(manager.state.track_artifact_ids)
        if not has_target:
            return [], QueryStatus.not_found, None, None
        return [], QueryStatus.completed, None, None

    # ------------------------------------------------------------------
    # 音频入口（§52 analyze_audio / §35）
    # ------------------------------------------------------------------

    def analyze_audio(self, asset: Union[str, dict, AudioAnalysis]) -> AudioAnalysis:
        """分析任意音频资产（原视频音频 / BGM / 生成音乐共用协议）。"""
        if self._manager is None:
            raise RuntimeError("call analyze_video() first")
        if isinstance(asset, AudioAnalysis):
            analysis = asset
        elif isinstance(asset, dict) and (asset.get("beats") or asset.get("bpm") or asset.get("downbeats")):
            analysis = ProvidedAudioAnalyzer(asset).analyze(asset)
        elif isinstance(asset, dict) and asset.get("asset_id") in self._audio_inputs:
            payload = self._audio_inputs[asset["asset_id"]]
            if isinstance(payload, (dict, AudioAnalysis)):
                analysis = ProvidedAudioAnalyzer(payload).analyze(asset)
            else:  # payload 是文件路径等非结构化输入 → 交给真实分析器
                analyzer = self._audio_analyzer or default_audio_analyzer()
                analysis = analyzer.analyze(payload)
        else:
            analyzer = self._audio_analyzer or default_audio_analyzer()
            analysis = analyzer.analyze(asset)
        self._manager.state.audio_state[analysis.asset_id] = analysis
        self._manager.state.version += 1
        self._persist()
        return analysis

    def _ensure_audio(self, asset_id: str) -> Optional[AudioAnalysis]:
        manager = self._manager
        if asset_id in manager.state.audio_state:
            return manager.state.audio_state[asset_id]
        try:
            if asset_id in self._audio_inputs:
                return self.analyze_audio({"asset_id": asset_id})
            video = manager.state.video
            if video.has_audio and video.source_uri:
                return self.analyze_audio({"asset_id": asset_id, "path": video.source_uri})
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # §52 读取接口
    # ------------------------------------------------------------------

    @property
    def state(self):
        if self._manager is None:
            return None
        return self._manager.state

    @property
    def tracks(self) -> Optional[DenseSpatialTracks]:
        """Dense Analysis Data（稠密轨道本体；state 里只存 artifact 引用，§48）。"""
        if self._manager is None:
            return None
        return self._manager.tracks

    def get_semantic_events(self, query: Optional[Union[RequiredVideoQuery, dict]] = None) -> List[SemanticEvent]:
        """读取已有语义事件；query 给出时按 canonical + occurrence 过滤。"""
        if self._manager is None:
            return []
        events = [e for e in self._manager.state.semantic_events if not e.invalidated]
        events.sort(key=lambda e: (e.temporal.start_time, e.canonical))
        if query is None:
            return events
        data = normalize_query(query)
        if data.get("event"):
            events = [e for e in events if e.canonical == data["event"]]
        if data.get("required_occurrence"):
            events = select_by_occurrence(events, data["required_occurrence"])
        return events

    def get_spatial_snapshot(self, event_uid: str) -> Optional[SpatialSnapshot]:
        if self._manager is None:
            return None
        event = next((e for e in self._manager.state.semantic_events if e.event_uid == event_uid), None)
        if event is None or not event.spatial_ref:
            return None
        return self._manager.state.spatial_snapshots.get(event.spatial_ref)

    def get_spatial_track(self, target: str, time_range: Optional[tuple] = None) -> Optional[Trajectory]:
        """跟头 / 跟手 / 跟人物的平滑轨迹（§9.4/§52）。

        震颤主体（profile.tremor）加中值预滤波 + 更宽平滑窗。
        """
        if self._manager is None or self._manager.tracks is None:
            return None
        accessor = _TRACK_TARGETS.get(target, target)
        profile = self._manager.state.subject_profile
        trajectory = self._manager.tracks.trajectory(
            accessor,
            smooth_half_window=5 if profile.tremor else 3,
            median_prefilter=profile.tremor,
        )
        if time_range:
            start, end = time_range
            trajectory.points = [p for p in trajectory.points if start <= p.t <= end]
        return trajectory

    def build_semantic_view(self, context: Optional[dict] = None) -> SemanticView:
        """context: {queries, event_uids, include_audio}（§49）。"""
        if self._manager is None:
            raise RuntimeError("call analyze_video() first")
        context = context or {}
        return build_semantic_view(
            self._manager.state,
            queries=context.get("queries"),
            event_uids=context.get("event_uids"),
            include_audio=context.get("include_audio", True),
        )

    def invalidate(self, dependency: dict) -> dict:
        """依赖变化 → 分析结果失效（§47）。"""
        if self._manager is None:
            return {"invalidated_queries": 0, "invalidated_events": 0, "no_op": True}
        result = self._manager.invalidate(dependency)
        self._persist()
        return result

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, directory: Optional[Union[str, Path]] = None) -> None:
        store = SemanticStateStore(directory) if directory else self._store
        if store is None:
            raise RuntimeError("no workspace_dir configured")
        store.save(self._manager)

    @classmethod
    def load(cls, directory: Union[str, Path], **kwargs) -> "VideoUnderstanding":
        vu = cls(workspace_dir=directory, **kwargs)
        vu._manager = SemanticStateStore(directory).load()
        return vu

    def _persist(self) -> None:
        if self._store is not None and self._manager is not None:
            self._store.save(self._manager)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _status_of(spans: List[AggregatedSpan]) -> QueryStatus:
    if not spans:
        return QueryStatus.not_found
    if any(s.status == ConfidenceStatus.confirmed for s in spans):
        return QueryStatus.completed
    return QueryStatus.low_confidence


def _pose_condition_canonical(condition: dict) -> str:
    subject = condition.get("subject", "hand")
    if isinstance(subject, list):
        subject = "_".join(str(s).replace("_hand", "") for s in subject) + "_hands"
    relation = condition.get("relation", "near")
    reference = condition.get("reference", "head")
    return f"{subject}_{relation}_{reference}"


def _condition_extras(accessor: str):
    def extras(tracks: DenseSpatialTracks, timestamp: float):
        frame = tracks.at(timestamp)
        if frame is None:
            return None, None, {}
        anchor = DenseSpatialTracks._resolve_point(frame, accessor) \
            or frame.hand_center("left") or frame.body_center()
        return anchor, None, {}

    return extras


_TRACK_TARGETS = {
    "head": "head",
    "face": "face",
    "person": "person",
    "body": "body",
    "hand": "left_hand",  # "跟手"缺省左手；精细需求用 left_hand/right_hand
    "left_hand": "left_hand",
    "right_hand": "right_hand",
    "left_wrist": "left_wrist",
    "right_wrist": "right_wrist",
}
