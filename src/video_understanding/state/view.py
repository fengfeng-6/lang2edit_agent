"""Semantic View Builder（§49）：V_semantic = f(S, Q)。

LLM / Planner 不直接读 Dense Analysis Data；根据当前需求动态生成
精简语义视图：视频概况、相关事件（含 occurrence 与时间）、每事件的
空间摘要（锚点 / 人脸关系 / 保护区域）、音频摘要、结构信息与查询状态。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from ..models import (
    SemanticEvent,
    SemanticVideoState,
    SemanticView,
    SpatialSnapshot,
)


def _event_brief(event: SemanticEvent) -> Dict[str, Any]:
    return {
        "event_uid": event.event_uid,
        "display_id": event.display_id,
        "canonical": event.canonical,
        "event_type": event.event_type.value,
        "occurrence_index": event.occurrence_index,
        "start_time": event.temporal.start_time,
        "peak_time": event.temporal.peak_time,
        "end_time": event.temporal.end_time,
        "confidence": event.confidence.overall,
        "status": event.confidence.status.value,
        "invalidated": event.invalidated,
        "properties": event.properties,
    }


def _spatial_brief(snapshot: SpatialSnapshot) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if snapshot.event_anchor:
        out["event_anchor"] = list(snapshot.event_anchor)
    if snapshot.face_bbox:
        out["face_bbox"] = list(snapshot.face_bbox)
        if snapshot.event_anchor:
            ax, ay = snapshot.event_anchor
            fx1, fy1, fx2, fy2 = snapshot.face_bbox
            # 锚点与人脸的相对位置，供 Planner 判断"不要挡脸"
            if ay < fy1:
                out["anchor_vs_face"] = "above"
            elif ay > fy2:
                out["anchor_vs_face"] = "below"
            elif ax < fx1:
                out["anchor_vs_face"] = "left_of"
            elif ax > fx2:
                out["anchor_vs_face"] = "right_of"
            else:
                out["anchor_vs_face"] = "overlapping"
    if snapshot.person_bbox:
        out["person_bbox"] = list(snapshot.person_bbox)
    if snapshot.head_center:
        out["head_center"] = list(snapshot.head_center)
    if snapshot.direction:
        out["direction"] = {"vector": list(snapshot.direction.vector), "label": snapshot.direction.label.value}
    if snapshot.protected_regions:
        out["protected_regions"] = {k: list(v) for k, v in snapshot.protected_regions.items()}
    out["timestamp"] = snapshot.timestamp
    return out


def build_semantic_view(
    state: SemanticVideoState,
    *,
    queries: Optional[Iterable[dict]] = None,
    event_uids: Optional[Iterable[str]] = None,
    include_audio: bool = True,
) -> SemanticView:
    """按当前需求构造精简视图。

    - queries 给出时只保留其 canonical 相关事件 + 对应 query 状态；
    - event_uids 给出时保留指定事件；
    - 都为空时给出全部有效事件（适合首轮规划）。
    """
    wanted_uids = set(event_uids or [])
    wanted_canonicals = set()
    query_statuses: Dict[str, str] = {}
    if queries:
        from ..events.router import normalize_query, query_id_for

        for q in queries:
            data = normalize_query(q)
            if data.get("event"):
                wanted_canonicals.add(data["event"])
            record = next((r for r in state.analysis_registry if r.query_id == query_id_for(data)), None)
            if record:
                query_statuses[record.query_id] = record.status.value

    events = [e for e in state.semantic_events if not e.invalidated]
    if wanted_uids:
        events = [e for e in events if e.event_uid in wanted_uids]
    elif wanted_canonicals:
        events = [e for e in events if e.canonical in wanted_canonicals]
    events.sort(key=lambda e: (e.temporal.start_time, e.canonical))

    spatial_summaries = {
        e.display_id or e.event_uid: _spatial_brief(state.spatial_snapshots[e.spatial_ref])
        for e in events
        if e.spatial_ref and e.spatial_ref in state.spatial_snapshots
    }

    audio_summary: Dict[str, Any] = {}
    if include_audio and state.audio_state:
        for asset_id, analysis in state.audio_state.items():
            audio_summary[asset_id] = {
                "bpm": analysis.bpm,
                "beat_count": len(analysis.beats),
                "downbeat_count": len(analysis.downbeats),
                "downbeats": analysis.downbeats[:8],
            }

    video = state.video
    return SemanticView(
        video={
            "video_id": video.video_id,
            "duration": video.duration,
            "fps": video.fps,
            "resolution": [video.width, video.height],
            "aspect_ratio": video.aspect_ratio,
            "has_audio": video.has_audio,
            "version": video.version,
        },
        relevant_events=[_event_brief(e) for e in events],
        spatial_summaries=spatial_summaries,
        audio_summary=audio_summary,
        structural={
            "video_start": state.structural_state.video_start,
            "video_end": state.structural_state.video_end,
            "first_action": state.structural_state.first_action.event_ref if state.structural_state.first_action else None,
            "last_action": state.structural_state.last_action.event_ref if state.structural_state.last_action else None,
        },
        query_statuses=query_statuses,
    )
