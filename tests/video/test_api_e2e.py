"""VideoUnderstanding 门面端到端（§52-61 验收点）。"""

from __future__ import annotations

from video_understanding import QueryStatus, VideoUnderstanding

HEART_ALL = {"type": "event_detection", "event": "heart_gesture",
             "required_occurrence": {"type": "all"}}


def test_metadata_normalized(video_input):
    vu = VideoUnderstanding()
    result = vu.analyze_video(video_input)
    assert result.video.width == 1080 and result.video.height == 1920
    assert result.video.aspect_ratio == "9:16"
    assert result.base_track_ref  # dense data 以 artifact 引用入状态（§48）


def test_heart_gesture_two_occurrences_with_times(analyzed):
    [r] = analyzed.resolve_queries([HEART_ALL])
    assert r.status == QueryStatus.completed
    assert len(r.events) == 2
    first, second = r.events
    assert first.occurrence_index == 1 and second.occurrence_index == 2
    assert first.display_id == "gesture_heart_01"
    assert second.display_id == "gesture_heart_02"
    assert abs(first.temporal.start_time - 4.0) < 0.35  # 手收拢才构成比心，起点略晚于相位起点
    assert 4.0 < first.temporal.peak_time < 4.7  # peak 在区间内且靠后（收拢后保持）
    assert abs(second.temporal.end_time - 10.9) < 0.25
    assert first.event_uid != second.event_uid  # §38 双 ID 稳定区分


def test_cache_hit_and_incremental_analysis(analyzed):
    analyzed.resolve_queries([HEART_ALL])
    n_records = len(analyzed.state.analysis_registry)

    # index(2) 被 "all" 查询覆盖：cache_hit，不再跑检测（§45-46）
    [r2] = analyzed.resolve_queries([{
        "type": "event_detection", "event": "heart_gesture",
        "required_occurrence": {"type": "index", "value": 2},
    }])
    assert r2.cache_hit is True
    assert r2.status == QueryStatus.completed
    assert len(r2.selected_event_uids) == 1
    assert len(analyzed.state.analysis_registry) == n_records  # 不产生新分析记录

    # 新需求（point_left）触发增量分析：registry 只加一条
    [r3] = analyzed.resolve_queries([{"type": "event_detection", "event": "point_left"}])
    assert r3.cache_hit is False and r3.status == QueryStatus.completed
    assert len(analyzed.state.analysis_registry) == n_records + 1


def test_not_found_vs_failed(analyzed):
    # not_found：正常分析但视频中没有 squat（§42）
    [nf] = analyzed.resolve_queries([{"type": "event_detection", "event": "squat"}])
    assert nf.status == QueryStatus.not_found
    assert nf.events == []

    # failed：thumbs_up 依赖 hand_landmark_track，轨道缺失是技术错误而非"没有"
    [f] = analyzed.resolve_queries([{"type": "event_detection", "event": "thumbs_up"}])
    assert f.status == QueryStatus.failed
    assert "landmark" in (f.error or "")

    # 未注册事件且未配置 open verifier → failed
    [o] = analyzed.resolve_queries([{"type": "event_detection", "event": "casting_spell"}])
    assert o.status == QueryStatus.failed


def test_occurrence_index_out_of_range_is_not_found(analyzed):
    analyzed.resolve_queries([HEART_ALL])
    [r] = analyzed.resolve_queries([{
        "type": "event_detection", "event": "heart_gesture",
        "required_occurrence": {"type": "index", "value": 5},
    }])
    assert r.status == QueryStatus.not_found
    assert r.cache_hit is True


def test_spatial_snapshot_anchor_and_protected_region(analyzed):
    [r] = analyzed.resolve_queries([HEART_ALL])
    snap = analyzed.get_spatial_snapshot(r.events[0].event_uid)
    assert snap is not None
    # 双手锚点 = 两手中心中点（§28）
    assert snap.event_anchor is not None
    assert 0.48 <= snap.event_anchor[0] <= 0.52
    # face 保护区域 = Expand(face_bbox, m)（§29）
    assert "face" in snap.protected_regions
    fx1, fy1, fx2, fy2 = snap.protected_regions["face"]
    assert fx1 < 0.43 and fy2 > 0.26


def test_spatial_track_smoothed(analyzed):
    traj = analyzed.get_spatial_track("left_hand", time_range=(3.5, 5.2))
    assert traj is not None and traj.smoothed
    assert all(3.5 <= p.t <= 5.2 for p in traj.points)
    # 轨迹中途应到达比心高度（y≈0.24），首尾都是 idle 位（y≈0.52）
    ys = [p.y for p in traj.points]
    assert min(ys) < ys[0] - 0.15


def test_pose_condition_query(analyzed):
    [r] = analyzed.resolve_queries([{
        "type": "pose_condition_detection",
        "condition": {"subject": "hand", "relation": "above", "reference": "head"},
    }])
    assert r.status == QueryStatus.completed
    assert r.events
    for e in r.events:
        assert 2.0 <= e.temporal.peak_time <= 2.7  # 只有 hands_up 相位满足


def test_audio_events_materialized(analyzed):
    [r] = analyzed.resolve_queries([{"type": "audio_event_detection", "event": "downbeat"}])
    assert r.status == QueryStatus.completed
    assert len(r.events) == 6
    assert all(e.event_type.value == "audio_event" for e in r.events)
    assert r.events[0].temporal.peak_time == 0.5


def test_structural_last_action_reuses_detected(analyzed):
    analyzed.resolve_queries([HEART_ALL])
    [r] = analyzed.resolve_queries([{"type": "video_structure_detection", "event": "last_action"}])
    assert r.status == QueryStatus.completed
    assert r.events[0].properties.get("source_event")  # 依据已检测事件
    assert analyzed.state.structural_state.last_action is not None


def test_person_face_tracking_query(analyzed):
    [r] = analyzed.resolve_queries([{"type": "person_face_tracking", "reference": "face"}])
    assert r.status == QueryStatus.completed


def test_semantic_view_compact(analyzed):
    analyzed.resolve_queries([HEART_ALL,
                              {"type": "audio_event_detection", "event": "downbeat"}])
    view = analyzed.build_semantic_view({"queries": [HEART_ALL]})
    assert view.video["aspect_ratio"] == "9:16"
    assert {e["canonical"] for e in view.relevant_events} == {"heart_gesture"}
    assert view.audio_summary["original_audio"]["bpm"] == 120.0
    # 空间摘要：锚点与人脸相对位置
    brief = next(iter(view.spatial_summaries.values()))
    assert "event_anchor" in brief and "face_bbox" in brief


def test_invalidation_by_dependency(analyzed):
    analyzed.resolve_queries([HEART_ALL])
    summary = analyzed.invalidate({"type": "dependency", "name": "pose_track"})
    assert summary["invalidated_queries"] == 1
    assert summary["invalidated_events"] == 2
    assert all(e.invalidated for e in analyzed.state.semantic_events)
    # 失效事件不再出现在读取结果里
    assert analyzed.get_semantic_events() == []


def test_edit_trim_does_not_invalidate(analyzed):
    analyzed.resolve_queries([HEART_ALL])
    summary = analyzed.invalidate({"type": "edit", "detail": "trim head 2s"})
    assert summary["no_op"] is True
    assert not any(e.invalidated for e in analyzed.state.semantic_events)


def test_video_replacement_invalidates_everything(analyzed):
    analyzed.resolve_queries([HEART_ALL,
                              {"type": "audio_event_detection", "event": "downbeat"}])
    summary = analyzed.invalidate({"type": "video"})
    assert summary["invalidated_queries"] == 2


def test_persistence_roundtrip(tmp_path, video_input):
    vu = VideoUnderstanding(workspace_dir=tmp_path)
    vu.analyze_video(video_input)
    vu.resolve_queries([HEART_ALL])

    loaded = VideoUnderstanding.load(tmp_path)
    assert loaded.state.video.video_id == "v_dance"
    assert len(loaded.state.semantic_events) == 2
    # 状态复用：重新查询走缓存
    [r] = loaded.resolve_queries([HEART_ALL])
    assert r.cache_hit is True
