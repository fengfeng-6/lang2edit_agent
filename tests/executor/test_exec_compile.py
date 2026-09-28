"""Compiler：operation → TimelineObject（§21-§28，场景1 双爱心）。"""

from __future__ import annotations

from edit_executor.compiler.graph import compile_graph
from edit_executor.compiler.mutations import MAIN_VIDEO_UID, ORIGINAL_AUDIO_UID
from edit_executor.compiler.operations import (
    SOURCE_VIDEO_MEDIA_REF,
    timeline_object_uid,
)

from executor_fixtures import (
    mask_artifact,
    make_asset,
    make_input,
    make_item,
    make_plan,
    overlay_item,
    tracks_artifact,
)
from editing_planner.models import (
    PlanItemStatus,
    PlanOperation,
    Transform,
)


def test_overlay_compile_creates_objects(tmp_path):
    items = [
        overlay_item("item_heart_01", "ast_heart", 1.0, 4.0, position=(0.4, 0.3)),
        overlay_item("item_heart_02", "ast_heart", 5.0, 8.0, position=(0.6, 0.3)),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(
        make_input(tmp_path, plan, assets=[heart])
    )
    uid1 = timeline_object_uid("item_heart_01", "overlay")
    uid2 = timeline_object_uid("item_heart_02", "overlay")
    assert {uid1, uid2} <= set(graph.objects)
    obj = graph.objects[uid1]
    assert obj.track_uid == "trk_overlay"
    assert obj.asset_uid == "ast_heart"
    assert obj.media_ref == "mref_ast_heart"
    assert obj.project_time.start == 1.0 and obj.project_time.end == 4.0
    assert obj.transform.position == (0.4, 0.3)
    # 素材 MediaRef 解析（§20）
    assert graph.media_refs["mref_ast_heart"].local_uri == heart.local_uri
    # 系统对象（§13）
    main = graph.objects[MAIN_VIDEO_UID]
    assert main.media_ref == SOURCE_VIDEO_MEDIA_REF
    assert (main.project_time.start, main.project_time.end) == (0.0, 10.0)
    audio = graph.objects[ORIGINAL_AUDIO_UID]
    assert audio.track_uid == "trk_audio"
    # 轨道索引
    assert uid1 in graph.tracks["trk_overlay"].object_uids


def test_compile_is_deterministic(tmp_path):
    items = [overlay_item("item_heart_01")]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    g1 = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    g2 = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    assert g1.graph_uid == g2.graph_uid
    assert {u: o.fingerprint for u, o in g1.objects.items()} == {
        u: o.fingerprint for u, o in g2.objects.items()
    }


def test_text_and_music_compile(tmp_path):
    items = [
        make_item(
            "item_txt",
            PlanOperation.add_text,
            2.0,
            5.0,
            parameters={"text": "你好", "font": "sans"},
        ),
        make_item(
            "item_bgm",
            PlanOperation.add_music,
            0.0,
            10.0,
            asset_uid="ast_bgm",
            parameters={"volume_db": -6.0},
        ),
    ]
    plan = make_plan(items)
    bgm = make_asset(tmp_path, "ast_bgm", media_type="audio", suffix=".mp3")
    graph = compile_graph(make_input(tmp_path, plan, assets=[bgm]))
    text_obj = graph.objects[timeline_object_uid("item_txt", "text")]
    assert text_obj.track_uid == "trk_text"
    assert text_obj.parameters["content"] == "你好"
    music_obj = graph.objects[timeline_object_uid("item_bgm", "music")]
    assert music_obj.track_uid == "trk_audio"
    assert music_obj.media_ref == "mref_ast_bgm"
    assert music_obj.parameters["volume_db"] == -6.0


def test_replace_background_two_objects(tmp_path):
    item = make_item(
        "item_bg",
        PlanOperation.replace_background,
        0.0,
        10.0,
        asset_uid="ast_bg",
    )
    plan = make_plan([item])
    bg = make_asset(tmp_path, "ast_bg", media_type="video", suffix=".mp4")
    mask = mask_artifact()
    graph = compile_graph(
        make_input(tmp_path, plan, assets=[bg], artifacts=[mask])
    )
    background = graph.objects[timeline_object_uid("item_bg", "background")]
    foreground = graph.objects[
        timeline_object_uid("item_bg", "foreground_subject")
    ]
    assert background.track_uid == "trk_background"
    assert background.media_ref == "mref_ast_bg"
    assert foreground.track_uid == "trk_main_video"
    assert foreground.media_ref == SOURCE_VIDEO_MEDIA_REF
    assert foreground.mask_ref == "art_mask_1"


def test_track_overlay_keyframes(tmp_path):
    follow = {
        "target": "head",
        "mode": "keyframes",
        "keyframes": [
            {"t": 1.0, "x": 0.4, "y": 0.3},
            {"t": 3.0, "x": 0.6, "y": 0.3},
        ],
    }
    item = make_item(
        "item_track",
        PlanOperation.track_overlay,
        1.0,
        4.0,
        asset_uid="ast_heart",
        transform=Transform(position=(0.5, 0.3), scale=0.2),
        follow=follow,
        resolved_capability="keyframes",
    )
    plan = make_plan([item])
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    obj = graph.objects[timeline_object_uid("item_track", "overlay")]
    assert len(obj.keyframes) == 2
    assert obj.keyframes[0].time == 1.0
    assert obj.keyframes[0].values == {"x": 0.4, "y": 0.3}
    # 首帧位置写入 transform
    assert obj.transform.position == (0.4, 0.3)
    assert obj.tracking is None


def test_track_overlay_tracking_binding(tmp_path):
    follow = {
        "target": "left_hand",
        "mode": "tracking",
        "trajectory_ref": {"kind": "spatial_tracks"},
        "smoothing": "strong",
    }
    item = make_item(
        "item_track",
        PlanOperation.track_overlay,
        1.0,
        4.0,
        asset_uid="ast_heart",
        transform=Transform(),
        follow=follow,
        resolved_capability="tracking",
    )
    plan = make_plan([item])
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(
        make_input(tmp_path, plan, assets=[heart], artifacts=[tracks_artifact()])
    )
    obj = graph.objects[timeline_object_uid("item_track", "overlay")]
    assert obj.tracking is not None
    assert obj.tracking.target == "left_hand"
    assert obj.tracking.trajectory_ref == "art_tracks_1"


def test_inactive_items_not_compiled(tmp_path):
    items = [
        overlay_item("item_ok"),
        make_item(
            "item_dep",
            PlanOperation.add_overlay,
            0.0,
            3.0,
            status=PlanItemStatus.pending_dependency,
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    assert timeline_object_uid("item_dep", "overlay") not in graph.objects
