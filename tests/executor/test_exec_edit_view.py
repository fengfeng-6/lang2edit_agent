"""ProjectEditView（§75-§76）：模块六的可编辑对象视图。"""

from __future__ import annotations

from edit_executor.compiler.mutations import MAIN_VIDEO_UID, ORIGINAL_AUDIO_UID
from edit_executor.compiler.operations import timeline_object_uid

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_executor,
    make_input,
    make_item,
    make_plan,
    overlay_item,
)
from editing_planner.models import PlanOperation


def _applied_view(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    bgm = make_asset(tmp_path, "ast_bgm", media_type="audio", suffix=".mp3")
    plan = make_plan(
        [
            overlay_item(
                "item_heart", requirements=["req_sticker_1"]
            ),
            make_item(
                "item_bgm",
                PlanOperation.add_music,
                0.0,
                10.0,
                asset_uid="ast_bgm",
            ),
        ]
    )
    executor.apply(make_input(tmp_path, plan, assets=[heart, bgm]))
    return executor.get_edit_view(PROJECT)


def test_edit_view_fields(tmp_path):
    view = _applied_view(tmp_path)
    assert view.project_id == PROJECT
    assert view.revision == 1
    by_uid = {o.object_uid: o for o in view.objects}
    overlay = by_uid[timeline_object_uid("item_heart", "overlay")]
    assert overlay.display_id.startswith("overlay_")
    assert overlay.source_plan_item_uid == "item_heart"
    assert overlay.source_requirement_ids == ["req_sticker_1"]
    assert overlay.asset_uid == "ast_heart"
    assert overlay.semantic_label == "heart"
    assert (overlay.project_time.start, overlay.project_time.end) == (1.0, 4.0)
    # current_properties 平铺（§76）
    assert overlay.current_properties["scale"] == 0.16
    assert overlay.current_properties["position"] == [0.5, 0.3]
    assert overlay.current_properties["asset_uid"] == "ast_heart"
    assert set(overlay.editable_properties) == {
        "scale", "position", "asset", "animation"
    }


def test_edit_view_system_objects_not_editable(tmp_path):
    view = _applied_view(tmp_path)
    by_uid = {o.object_uid: o for o in view.objects}
    assert by_uid[MAIN_VIDEO_UID].editable_properties == []
    assert by_uid[ORIGINAL_AUDIO_UID].editable_properties == []


def test_edit_view_audio_object(tmp_path):
    view = _applied_view(tmp_path)
    by_uid = {o.object_uid: o for o in view.objects}
    music = by_uid[timeline_object_uid("item_bgm", "music")]
    assert music.object_type == "audio"
    assert set(music.editable_properties) == {"asset", "volume", "time_range"}
