"""Semantic Video State 管理（§36-51）。

``SemanticStateManager`` 持有：
- ``SemanticVideoState``（对外可序列化的语义状态，只存事件/引用/摘要）；
- ``DenseSpatialTracks``（Dense Data，只以 artifact_id 入状态，§48）；
- Analysis Registry + History（§40/§51）。

职责：
- 事件物化：AggregatedSpan → SemanticEvent，分配稳定 event_uid、
  occurrence_index 与 display_id（§37-38）；
- 事件级空间快照（§27-29）；
- 分析记录与失效管理（§47）；
- 版本号随分析演化（§50）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..events.aggregator import AggregatedSpan
from ..models import (
    AnalysisHistoryEntry,
    AnalysisRecord,
    Confidence,
    DenseSpatialTracks,
    DetectorInfo,
    EventType,
    QueryStatus,
    SemanticEvent,
    SemanticVideoState,
    SpatialSnapshot,
    StructuralState,
    VideoMetadata,
)
from ..spatial.snapshot import build_snapshot

ExtrasFn = Optional[Callable[[DenseSpatialTracks, float], Tuple[Any, Any, dict]]]

_EVENT_TYPE_SHORT = {
    EventType.gesture: "gesture",
    EventType.body_action: "body",
    EventType.pose_condition: "pose",
    EventType.video_structure: "struct",
    EventType.audio_event: "audio",
}

_CANONICAL_STRIP_SUFFIXES = ("_gesture", "_action", "_body", "_hand")


def event_uid_for(video_id: str, canonical: str, start: float, peak: float, end: float) -> str:
    """稳定 event_uid：时间一致的重检测结果 uid 不变（§38）。"""
    raw = f"{video_id}|{canonical}|{start:.3f}|{peak:.3f}|{end:.3f}"
    return "evt_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def display_id_for(event_type: EventType, canonical: str, occurrence: int) -> str:
    """时序 display_id：gesture_heart_02 / body_turn_01（§38）。"""
    short = canonical
    for suffix in _CANONICAL_STRIP_SUFFIXES:
        if short.endswith(suffix):
            short = short[: -len(suffix)]
            break
    return f"{_EVENT_TYPE_SHORT.get(event_type, 'event')}_{short}_{occurrence:02d}"


class SemanticStateManager:
    """单个视频的语义状态容器。"""

    def __init__(self, video: VideoMetadata):
        self.state = SemanticVideoState(
            state_id=f"semantic_state_{video.video_id}",
            video=video,
            structural_state=StructuralState(video_start=0.0, video_end=video.duration),
        )
        self.tracks: Optional[DenseSpatialTracks] = None
        self._analysis_seq = 0

    # ------------------------------------------------------------------
    # 轨道与快照
    # ------------------------------------------------------------------

    def attach_tracks(self, tracks: DenseSpatialTracks) -> str:
        """登记稠密轨道为 artifact，状态只保存 artifact_id（§48）。"""
        artifact_id = tracks.artifact_id or f"{tracks.producer or 'tracks'}_{tracks.video_id}"
        tracks.artifact_id = artifact_id
        self.tracks = tracks
        n = len(tracks.frames)
        present = sum(1 for f in tracks.frames if f.person_bbox or f.keypoints)
        self.state.spatial_summary = {
            "frames": n,
            "person_present_ratio": round(present / n, 3) if n else 0.0,
            "has_hand_landmarks": any(f.hands for f in tracks.frames),
        }
        if artifact_id not in self.state.track_artifact_ids:
            self.state.track_artifact_ids.append(artifact_id)
            self.state.version += 1
        return artifact_id

    def snapshot_event(self, event: SemanticEvent, *, anchor=None, direction=None) -> Optional[SpatialSnapshot]:
        if self.tracks is None:
            return None
        snapshot = build_snapshot(
            self.tracks, event.temporal.peak_time,
            spatial_id=f"{event.event_uid}_snap", anchor=anchor, direction=direction,
        )
        if snapshot is not None:
            self.state.spatial_snapshots[snapshot.spatial_id] = snapshot
            event.spatial_ref = snapshot.spatial_id
        return snapshot

    # ------------------------------------------------------------------
    # 事件物化
    # ------------------------------------------------------------------

    def add_events(
        self,
        canonical: str,
        event_type: EventType,
        spans: List[AggregatedSpan],
        *,
        query_id: str,
        detector: DetectorInfo,
        extras_fn: ExtrasFn = None,
        event_properties: Optional[dict] = None,
        confidence_sources: Optional[Dict[str, float]] = None,
        span_sources: Optional[List[Dict[str, float]]] = None,
    ) -> List[SemanticEvent]:
        """候选区间 → SemanticEvent 入库：分配 uid、快照、occurrence 编号。

        同一 canonical 的检测是对全时间轴的整体重算——新结果入库前，
        该 canonical 现存的（未被取代的）旧事件整体标记 ``invalidated``
        （软删除留痕，§47），避免重检后新旧结果并存成重复事件。
        """
        for event in self.state.semantic_events:
            if event.canonical == canonical and not event.invalidated:
                event.invalidated = True
        created: List[SemanticEvent] = []
        for i, span in enumerate(spans):
            uid = event_uid_for(
                self.state.video.video_id, canonical,
                span.temporal.start_time, span.temporal.peak_time, span.temporal.end_time,
            )
            existing = next((e for e in self.state.semantic_events if e.event_uid == uid), None)
            if existing is not None:
                existing.invalidated = False  # 重检同 uid：刚被取代标记的旧事件复活
                if query_id not in existing.source_query_ids:
                    existing.source_query_ids.append(query_id)
                created.append(existing)
                continue
            anchor, direction, props = (None, None, {})
            if extras_fn is not None and self.tracks is not None:
                anchor, direction, props = extras_fn(self.tracks, span.temporal.peak_time)
            event = SemanticEvent(
                event_uid=uid,
                event_type=event_type,
                canonical=canonical,
                source_query_ids=[query_id],
                temporal=span.temporal,
                confidence=Confidence(
                    overall=span.confidence,
                    status=span.status,
                    sources={**(confidence_sources or {}),
                             **((span_sources or [{}])[i] if span_sources and i < len(span_sources) else {})},
                ),
                detector=detector,
                properties={**(event_properties or {}), **(props or {})},
            )
            self.state.semantic_events.append(event)
            self.snapshot_event(event, anchor=anchor, direction=direction)
            created.append(event)
        self._renumber()
        if created:
            self.state.version += 1
        return created

    def _renumber(self) -> None:
        """按 source time 重排同 canonical 有效事件，更新 occurrence_index/display_id。"""
        by_canonical: Dict[str, List[SemanticEvent]] = {}
        for event in self.state.semantic_events:
            if not event.invalidated:
                by_canonical.setdefault(event.canonical, []).append(event)
        for canonical, group in by_canonical.items():
            group.sort(key=lambda e: (e.temporal.start_time, e.temporal.peak_time))
            for i, event in enumerate(group, start=1):
                event.occurrence_index = i
                event.display_id = display_id_for(event.event_type, canonical, i)

    # ------------------------------------------------------------------
    # 查询记录 / 历史 / 失效（§40-47/§51）
    # ------------------------------------------------------------------

    def record_query(self, record: AnalysisRecord) -> AnalysisRecord:
        existing = next((r for r in self.state.analysis_registry if r.query_id == record.query_id), None)
        if existing is not None:
            idx = self.state.analysis_registry.index(existing)
            self.state.analysis_registry[idx] = record
        else:
            self.state.analysis_registry.append(record)
        self._analysis_seq += 1
        self.state.analysis_history.append(AnalysisHistoryEntry(
            analysis_id=f"analysis_{self._analysis_seq:03d}",
            query_id=record.query_id,
            strategy=record.strategy or "unknown",
            outputs=list(record.result_refs),
            started_at=datetime.now(timezone.utc).isoformat(),
        ))
        return record

    def find_covering_record(self, query: dict) -> Optional[AnalysisRecord]:
        """缓存命中：存在覆盖该查询且仍有效的 completed/not_found 记录。"""
        from .cache import query_covers

        for record in self.state.analysis_registry:
            if record.validity != "valid":
                continue
            if record.status not in (QueryStatus.completed, QueryStatus.not_found, QueryStatus.low_confidence):
                continue
            if query_covers(record.query, query):
                return record
        return None

    def invalidate(self, dependency: dict) -> dict:
        """分析结果失效（§47）三级语义。

        - ``{"type": "model", "name": x}``：模型/检测器版本升级 → 命中记录标
          ``stale``——结果与事件保留可见，但不再覆盖新查询（下次查询重算，
          由同 canonical 取代机制换新）；
        - ``{"type": "dependency", "name": x}`` / ``{"type": "video"}``：
          依赖产物失效 / 源视频替换 → 记录 invalidated + 事件 invalidated；
        - ``{"type": "query", "name"|"query_id": x}``：单条查询记录失效；
        - ``{"type": "edit"}``：工程裁剪 → 语义状态不失效（§47.2，no-op）。
        """
        dtype = dependency.get("type", "")
        name = dependency.get("name") or dependency.get("model") or dependency.get("query_id") or ""
        if dtype == "edit":
            return {"invalidated_queries": 0, "invalidated_events": 0, "no_op": True}

        if dtype == "model":
            stale = [
                r for r in self.state.analysis_registry
                if r.validity == "valid" and name and name in (r.model_version or "")
            ]
            for record in stale:
                record.validity = "stale"  # 仅标陈：status 与事件保留
            if stale:
                self.state.version += 1
            return {"stale_queries": len(stale), "invalidated_events": 0, "no_op": False}

        def hit(record: AnalysisRecord) -> bool:
            if dtype == "video":
                return True
            if dtype == "dependency":
                return name in record.dependencies
            if dtype == "query":
                return record.query_id == name
            return False

        affected_queries = [
            r for r in self.state.analysis_registry
            if hit(r) and r.status != QueryStatus.invalidated
        ]
        affected_uids = {uid for r in affected_queries for uid in r.result_refs}
        n_events = 0
        for event in self.state.semantic_events:
            if event.event_uid in affected_uids and not event.invalidated:
                event.invalidated = True
                n_events += 1
        for record in affected_queries:
            record.status = QueryStatus.invalidated
            record.validity = "invalidated"
        if affected_queries:
            self.state.version += 1
        return {
            "invalidated_queries": len(affected_queries),
            "invalidated_events": n_events,
            "no_op": False,
        }
