"""Object-Mutation 编译（§21.2）：四类 mutation + 目标解析链。"""

from __future__ import annotations

from edit_executor.compiler.graph import compile_graph
from edit_executor.compiler.mutations import MAIN_VIDEO_UID, ORIGINAL_AUDIO_UID
from edit_executor.compiler.operations import timeline_object_uid

from executor_fixtures import (
    make_asset,
    make_input,
    make_item,
    make_plan,
    overlay_item,
)
from editing_planner.models import PlanOperation, Transform


def test_scale_adjust_by_plan_item_ref(tmp_path):
    items = [
        overlay_item("item_heart", scale=0.16),
        make_item(
            "item_scale",
            PlanOperation.scale_adjust,
            1.0,
            4.0,
            transform=Transform(position=(0.5, 0.3), scale=0.30),
            target={"type": "plan_item_ref", "value": "item_heart"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    obj = graph.objects[timeline_object_uid("item_heart", "overlay")]
    assert obj.transform.scale == 0.30
    # mutation 不产出新对象
    assert timeline_object_uid("item_scale", "overlay") not in graph.objects


def test_scale_adjust_factor(tmp_path):
    items = [
        overlay_item("item_heart", scale=0.20),
        make_item(
            "item_scale",
            PlanOperation.scale_adjust,
            1.0,
            4.0,
            parameters={"scale_factor": 1.5},
            target={"type": "plan_item_ref", "value": "item_heart"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    obj = graph.objects[timeline_object_uid("item_heart", "overlay")]
    assert abs(obj.transform.scale - 0.30) < 1e-9


def test_position_adjust_offset(tmp_path):
    items = [
        overlay_item("item_heart", position=(0.5, 0.3)),
        make_item(
            "item_pos",
            PlanOperation.position_adjust,
            1.0,
            4.0,
            parameters={"position_offset": {"dx": 0.1, "dy": -0.05}},
            target={"type": "plan_item_ref", "value": "item_heart"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    obj = graph.objects[timeline_object_uid("item_heart", "overlay")]
    assert obj.transform.position == (0.6, 0.25)


def test_volume_adjust_default_target(tmp_path):
    items = [
        make_item(
            "item_vol",
            PlanOperation.volume_adjust,
            0.0,
            10.0,
            parameters={"delta_db": -3.0, "volume_cap": -1.0},
        ),
    ]
    plan = make_plan(items)
    graph = compile_graph(make_input(tmp_path, plan))
    audio = graph.objects[ORIGINAL_AUDIO_UID]
    # 0 + (-3)，cap -1 → -3（cap 只限制上限）
    assert audio.parameters["volume_db"] == -3.0


def test_replace_music_existing(tmp_path):
    items = [
        make_item(
            "item_bgm",
            PlanOperation.add_music,
            0.0,
            10.0,
            asset_uid="ast_old",
        ),
        make_item(
            "item_rep",
            PlanOperation.replace_music,
            0.0,
            10.0,
            asset_uid="ast_new",
        ),
    ]
    plan = make_plan(items)
    old = make_asset(tmp_path, "ast_old", media_type="audio", suffix=".mp3")
    new = make_asset(tmp_path, "ast_new", media_type="audio", suffix=".mp3")
    graph = compile_graph(make_input(tmp_path, plan, assets=[old, new]))
    music = graph.objects[timeline_object_uid("item_bgm", "music")]
    assert music.asset_uid == "ast_new"
    assert music.media_ref == "mref_ast_new"


def test_replace_music_fallback_creates(tmp_path):
    items = [
        make_item(
            "item_rep",
            PlanOperation.replace_music,
            0.0,
            10.0,
            asset_uid="ast_new",
        ),
    ]
    plan = make_plan(items)
    new = make_asset(tmp_path, "ast_new", media_type="audio", suffix=".mp3")
    graph = compile_graph(make_input(tmp_path, plan, assets=[new]))
    created = graph.objects[timeline_object_uid("item_rep", "music")]
    assert created.asset_uid == "ast_new"
    assert any("replace_music" in w for w in graph.warnings)


def test_event_target_resolution(tmp_path):
    items = [
        overlay_item(
            "item_heart",
            target={"type": "event", "value": "evt_wave"},
        ),
        make_item(
            "item_scale",
            PlanOperation.scale_adjust,
            1.0,
            4.0,
            transform=Transform(scale=0.5),
            target={"type": "event", "value": "evt_wave"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    obj = graph.objects[timeline_object_uid("item_heart", "overlay")]
    assert obj.transform.scale == 0.5


def test_unresolved_target_warns(tmp_path):
    items = [
        overlay_item("item_heart"),
        make_item(
            "item_scale",
            PlanOperation.scale_adjust,
            1.0,
            4.0,
            transform=Transform(scale=0.5),
            target={"type": "event", "value": "evt_missing"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    assert graph.objects[
        timeline_object_uid("item_heart", "overlay")
    ].transform.scale == 0.16
    assert any("item_scale" in w for w in graph.warnings)


def test_track_target_hits_all_objects(tmp_path):
    items = [
        overlay_item("item_a"),
        overlay_item("item_b"),
        make_item(
            "item_scale",
            PlanOperation.scale_adjust,
            1.0,
            4.0,
            transform=Transform(scale=0.5),
            target={"type": "track", "value": "overlay"},
        ),
    ]
    plan = make_plan(items)
    heart = make_asset(tmp_path, "ast_heart")
    graph = compile_graph(make_input(tmp_path, plan, assets=[heart]))
    for uid in ("item_a", "item_b"):
        assert graph.objects[
            timeline_object_uid(uid, "overlay")
        ].transform.scale == 0.5
