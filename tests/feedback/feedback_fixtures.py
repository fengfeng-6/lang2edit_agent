"""tests/feedback 公共 fixtures：FakeVU / FakeInspector / 本地素材库 / make_session。

全程无 CV/LLM：
- FakeVU 复用真 ``SemanticStateManager`` + 真 ``build_semantic_view`` /
  ``to_intent_view``——``resolve_queries`` 按剧本表给 canonical 造事件，
  event_uid / occurrence / spatial_snapshot / query_statuses 全真；
- IntentParser（规则抽取）、EditingPlanner、AssetManager（manifest 本地库）、
  EditingExecutor（MemoryBackend）全真——只换掉模型依赖与外部检索。

文件名保持全局唯一（feedback_fixtures.py），避免跨目录 helper 撞 sys.modules。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from asset_manager import AssetManager
from asset_manager.candidates.inspect import InspectResult
from asset_manager.models import AssetSource, Integrity, TechnicalMetadata
from asset_manager.providers.local import LocalLibraryProvider
from edit_executor import EditingExecutor
from session_feedback import FeedbackSession
from video_understanding.events.aggregator import AggregatedSpan
from video_understanding.events.router import normalize_query, query_id_for
from video_understanding.models import (
    AnalysisRecord,
    AudioAnalysis,
    ConfidenceStatus,
    DetectorInfo,
    EventType,
    MobilityProfile,
    QueryResult,
    QueryStatus,
    SemanticEvent,
    SpatialSnapshot,
    TemporalSpan,
    VideoAnalysisResult,
    VideoMetadata,
)
from video_understanding.state.manager import SemanticStateManager
from video_understanding.state.store import SemanticStateStore
from video_understanding.state.view import build_semantic_view, to_intent_view

VIDEO_DURATION = 12.0

#: 剧本表：canonical → [(start, peak, end)]。analyze 不 seed——
#: resolve_queries 被问到的 canonical 才造事件（与真 VU 的增量分析同形）。
DEFAULT_SCRIPT: Dict[str, List[tuple]] = {
    "clap": [(2.0, 2.2, 2.4)],
    "heart_gesture": [(4.0, 4.35, 4.7), (9.8, 10.35, 10.9)],
    "point_right": [(6.0, 6.3, 6.6)],
    "ending_pose": [(11.0, 11.4, 11.8)],
    "wave_hand": [(7.2, 7.5, 7.9)],
}

_EVENT_TYPES = {
    "ending_pose": EventType.body_action,
    "first_action": EventType.video_structure,
    "last_action": EventType.video_structure,
}

#: 无 event 的查询类型一律 completed（对齐 base_spatial / tracking 语义）。
_NON_EVENT_TYPES = {"person_face_tracking", "person_tracking", "pose_condition_detection"}


class FakeVU:
    """VideoUnderstanding 替身：真状态管理 + 真视图构建，检测器换成剧本表。

    ``.state`` 惰性从磁盘补载——会话恢复（新 FeedbackSession + 新 FakeVU）
    不需要显式 load。
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        *,
        video: Optional[Dict[str, Any]] = None,
        script: Optional[Dict[str, List[tuple]]] = None,
    ):
        self._store = SemanticStateStore(project_dir)
        self._manager: Optional[SemanticStateManager] = None
        self._video = dict(video or {})
        self._script = dict(script or DEFAULT_SCRIPT)
        self.query_calls: List[str] = []  # 观测增量查询（调试用断言）

    # -- VideoUnderstanding 协议 -------------------------------------------

    @property
    def state(self):
        if self._manager is None and self._store.state_path.exists():
            self._manager = self._store.load()
        return self._manager.state if self._manager is not None else None

    @classmethod
    def load(cls, directory: Union[str, Path], **kwargs) -> "FakeVU":
        vu = cls(directory, **kwargs)
        if vu._store.state_path.exists():
            vu._manager = vu._store.load()
        return vu

    def analyze_video(self, video: Any) -> VideoAnalysisResult:
        meta = VideoMetadata(
            video_id=self._video.get("video_id", "vid_fake"),
            duration=float(self._video.get("duration", VIDEO_DURATION)),
            fps=float(self._video.get("fps", 15.0)),
            width=int(self._video.get("width", 720)),
            height=int(self._video.get("height", 1280)),
            aspect_ratio=self._video.get("aspect_ratio", "9:16"),
            has_audio=True,
            source_uri=self._video.get("source_uri")
            or (str(video) if isinstance(video, (str, Path)) else ""),
        )
        manager = SemanticStateManager(meta)
        manager.state.subject_profile = MobilityProfile(
            posture="seated", amplitude="low", amplitude_scale=0.6,
            inferred=True,
        )
        manager.state.audio_state["original_audio"] = AudioAnalysis(
            asset_id="original_audio", bpm=120.0,
            beats=[i * 0.5 for i in range(25)],
            downbeats=[0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
            onsets=[0.0], analyzer="fake_audio", confidence=0.9,
        )
        manager.state.spatial_summary = {
            "frames": 0, "person_present_ratio": 1.0,
            "has_hand_landmarks": True,
        }
        self._manager = manager
        self._persist()
        return VideoAnalysisResult(
            video=meta, state_version=manager.state.version,
        )

    def resolve_queries(
        self, queries: Iterable[Any]
    ) -> List[QueryResult]:
        manager = self._require_manager()
        out: List[QueryResult] = []
        seen: Dict[str, QueryResult] = {}
        for query in queries:
            data = normalize_query(
                query if isinstance(query, dict) else _dump(query)
            )
            qid = query_id_for(data)
            self.query_calls.append(qid)
            if qid in seen:
                out.append(seen[qid])
                continue
            covering = manager.find_covering_record(data)
            if covering is not None:
                wanted = set(covering.result_refs)
                events = sorted(
                    (e for e in manager.state.semantic_events
                     if e.event_uid in wanted and not e.invalidated),
                    key=lambda e: (e.temporal.start_time, e.canonical),
                )
                for event in events:
                    if qid not in event.source_query_ids:
                        event.source_query_ids.append(qid)
                result = QueryResult(
                    query_id=qid, status=covering.status,
                    strategy=covering.strategy, events=events,
                    selected_event_uids=[e.event_uid for e in events],
                    cache_hit=True,
                )
            else:
                result = self._resolve_one(data, qid)
            seen[qid] = result
            out.append(result)
        self._persist()
        return out

    def build_semantic_view(self, context: Optional[dict] = None, *,
                            as_intent_view: bool = False):
        manager = self._require_manager()
        if as_intent_view:
            return to_intent_view(manager.state)
        context = context or {}
        return build_semantic_view(
            manager.state,
            queries=context.get("queries"),
            event_uids=context.get("event_uids"),
            include_audio=context.get("include_audio", True),
        )

    # -- 内部 ---------------------------------------------------------------

    def _require_manager(self) -> SemanticStateManager:
        if self._manager is None:
            if self._store.state_path.exists():
                self._manager = self._store.load()
        if self._manager is None:
            raise RuntimeError("call analyze_video() first")
        return self._manager

    def _resolve_one(self, data: Dict[str, Any], qid: str) -> QueryResult:
        manager = self._manager
        assert manager is not None
        canonical = data.get("event")
        qtype = data.get("type") or ""
        spans_spec = self._script.get(canonical) if canonical else None

        record = AnalysisRecord(
            query_id=qid, query=data, strategy="fake_detector",
            source_video_version=(
                f"{manager.state.video.video_id}_{manager.state.video.version}"
            ),
            model_version="fake_v1",
        )
        events: List[SemanticEvent] = []
        if canonical and spans_spec:
            spans = [
                AggregatedSpan(
                    temporal=TemporalSpan(
                        start_time=s, peak_time=p, end_time=e,
                    ),
                    confidence=0.92,
                    status=ConfidenceStatus.confirmed,
                    sample_count=3,
                )
                for s, p, e in spans_spec
            ]
            events = manager.add_events(
                canonical,
                _EVENT_TYPES.get(canonical, EventType.gesture),
                spans,
                query_id=qid,
                detector=DetectorInfo(
                    strategy="fake_detector", version="fake_v1",
                ),
            )
            for event in events:
                self._inject_snapshot(manager, event)
            record.result_refs = [e.event_uid for e in events]
            record.status = QueryStatus.completed
        elif canonical or qtype not in _NON_EVENT_TYPES:
            record.status = QueryStatus.not_found
        else:
            record.status = QueryStatus.completed
        manager.record_query(record)
        return QueryResult(
            query_id=qid, status=record.status, strategy="fake_detector",
            events=events,
            selected_event_uids=[e.event_uid for e in events],
        )

    @staticmethod
    def _inject_snapshot(manager: SemanticStateManager, event: SemanticEvent) -> None:
        """事件级空间快照（与 planner_fixtures._spatial 同形态）。"""
        face = (0.4, 0.08, 0.6, 0.24)
        snap = SpatialSnapshot(
            spatial_id=f"{event.event_uid}_snap",
            timestamp=event.temporal.peak_time,
            event_anchor=(0.5, 0.62),
            face_bbox=face,
            person_bbox=(0.3, 0.06, 0.7, 0.95),
            head_center=(0.5, 0.16),
            left_hand=(0.42, 0.58),
            right_hand=(0.58, 0.58),
            protected_regions={
                "face": (face[0] - 0.02, face[1] - 0.02,
                         face[2] + 0.02, face[3] + 0.02),
            },
        )
        manager.state.spatial_snapshots[snap.spatial_id] = snap
        event.spatial_ref = snap.spatial_id

    def _persist(self) -> None:
        if self._manager is not None:
            self._store.save(self._manager)


def _dump(model: Any) -> Dict[str, Any]:
    from gesture_intent.models import model_dump

    return model_dump(model)


class FakeInspector:
    """不解码的 Inspector：任何文件都判 decodable，图片带 alpha。"""

    def inspect_file(self, path: str) -> InspectResult:
        with open(path, "rb") as fh:
            data = fh.read()
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        is_audio = ext in ("mp3", "wav", "flac", "ogg", "m4a", "aac")
        technical = TechnicalMetadata(
            format=ext,
            file_size=len(data),
            mime_type="audio/mpeg" if is_audio else "image/png",
            width=0 if is_audio else 200,
            height=0 if is_audio else 200,
            aspect_ratio=0.0 if is_audio else 1.0,
            has_alpha=None if is_audio else True,
            duration=30.0 if is_audio else None,
            bpm=128.0 if is_audio else None,
        )
        integrity = Integrity(
            content_hash=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            mime_type=technical.mime_type,
            decodable=True,
            validated_at="2026-01-01T00:00:00Z",
        )
        return InspectResult(technical, integrity)


# ---------------------------------------------------------------------------
# 本地素材库（manifest.json + 假字节文件）
# ---------------------------------------------------------------------------


def write_media(path: Path, kind: str = "png") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 文件名进字节——同字节文件会被 content_hash/dHash 判重合并，备选池会塌掉
    tag = path.name.encode("utf-8")
    if kind == "png":
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + tag + b"\x00" * 32)
    elif kind == "mp3":
        path.write_bytes(b"ID3" + tag + b"\x00" * 64)
    else:
        path.write_bytes(tag + b"\x00" * 32)
    return str(path)


def make_library(root: Path) -> Path:
    """写 manifest + 假素材文件，返回 library_root。"""
    entries = [
        {
            "asset_uid": "lib_heart_pink", "path": "stickers/heart_pink.png",
            "asset_type": "sticker", "media_type": "image",
            "semantic_metadata": {
                "object": "heart", "attributes": ["pink"],
                "style": ["cute"], "usage_tags": ["爱心", "贴纸"],
            },
            "license_metadata": {"status": "cleared"},
            "technical_metadata": {
                "width": 200, "height": 200, "has_alpha": True,
            },
        },
        {
            "asset_uid": "lib_heart_red", "path": "stickers/heart_red.png",
            "asset_type": "sticker", "media_type": "image",
            "semantic_metadata": {
                "object": "heart", "attributes": ["red"],
                "style": ["cute"], "usage_tags": ["爱心"],
            },
            "license_metadata": {"status": "cleared"},
            "technical_metadata": {
                "width": 200, "height": 200, "has_alpha": True,
            },
        },
        {
            "asset_uid": "lib_star", "path": "stickers/star.png",
            "asset_type": "sticker", "media_type": "image",
            "semantic_metadata": {
                "object": "star", "attributes": ["yellow"],
                "style": ["cute"], "usage_tags": ["星星"],
            },
            "license_metadata": {"status": "cleared"},
            "technical_metadata": {
                "width": 200, "height": 200, "has_alpha": True,
            },
        },
        {
            "asset_uid": "lib_music_a", "path": "music/upbeat_a.mp3",
            "asset_type": "music", "media_type": "audio",
            "semantic_metadata": {
                "object": "music_note", "attributes": [],
                "style": ["upbeat", "energetic"],
                "usage_tags": ["bgm", "欢快"],
            },
            "license_metadata": {"status": "cleared"},
            "technical_metadata": {"duration": 30.0, "bpm": 128},
        },
        {
            "asset_uid": "lib_music_b", "path": "music/upbeat_b.mp3",
            "asset_type": "music", "media_type": "audio",
            "semantic_metadata": {
                "object": "music_note", "attributes": [],
                "style": ["upbeat"],
                "usage_tags": ["bgm", "欢快", "轻快"],
            },
            "license_metadata": {"status": "cleared"},
            "technical_metadata": {"duration": 32.0, "bpm": 132},
        },
    ]
    root.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        suffix = Path(entry["path"]).suffix.lstrip(".")
        write_media(root / entry["path"], kind="mp3" if suffix == "mp3" else "png")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"assets": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return root


def make_source_video(tmp_path: Path) -> str:
    """假源视频文件（preflight 只验 isfile / decodable 标志）。"""
    return write_media(tmp_path / "media" / "source.mp4", kind="mp4")


def make_session(
    tmp_path: Path,
    *,
    project_id: str = "proj1",
    vu: Optional[FakeVU] = None,
    script: Optional[Dict[str, List[tuple]]] = None,
) -> FeedbackSession:
    """全链路假件装配：workspace + 本地库 + FakeVU + MemoryExecutor。"""
    workspace = tmp_path / "ws"
    library = make_library(tmp_path / "library")
    project_dir = workspace / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    vu = vu or FakeVU(project_dir, script=script)
    asset_mgr = AssetManager(
        str(workspace),
        library_root=str(library),
        project_id=project_id,
        providers={AssetSource.local: LocalLibraryProvider(str(library))},
        inspector=FakeInspector(),
    )
    return FeedbackSession(
        workspace,
        project_id,
        vu=vu,
        asset_manager=asset_mgr,
        executor=EditingExecutor(workspace),
    )


def overlay_objects(view) -> list:
    """edit_view 里的贴纸类对象（role=overlay）。"""
    return [o for o in view.objects if o.role == "overlay"]


def music_objects(view) -> list:
    return [o for o in view.objects if o.role == "music"]
