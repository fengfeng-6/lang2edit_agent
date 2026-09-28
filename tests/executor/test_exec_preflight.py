"""ExecutionPreflight（§85-§87，场景5/6）。"""

from __future__ import annotations

from edit_executor.backends.memory import MemoryBackend
from edit_executor.models import BackendConfig, ExecutionStatus

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_executor,
    make_input,
    make_item,
    make_plan,
    mask_artifact,
    overlay_item,
    tracks_artifact,
)
from editing_planner.models import (
    PlanItemStatus,
    PlanOperation,
    ValidationStatus,
)


def _apply(tmp_path, executor_input, backend=None):
    executor = make_executor(tmp_path, backend=backend)
    return executor.apply(executor_input)


def test_blocked_plan_rejected(tmp_path):
    plan = make_plan(
        [overlay_item("item_heart")],
        validation_status=ValidationStatus.blocked,
    )
    heart = make_asset(tmp_path, "ast_heart")
    result = _apply(tmp_path, make_input(tmp_path, plan, assets=[heart]))
    assert result.status == ExecutionStatus.blocked.value
    assert result.errors[0].code == "plan_blocked"


def test_missing_asset_collects_dependencies(tmp_path):
    items = [
        overlay_item("item_a", asset_uid="ast_missing_1"),
        overlay_item("item_b", asset_uid="ast_missing_2"),
    ]
    plan = make_plan(items)
    result = _apply(tmp_path, make_input(tmp_path, plan, assets=[]))
    assert result.status == ExecutionStatus.needs_dependency.value
    targets = {d.target for d in result.dependencies}
    assert {"ast_missing_1", "ast_missing_2"} <= targets


def test_pending_dependency_item(tmp_path):
    items = [
        make_item(
            "item_dep",
            PlanOperation.add_overlay,
            0.0,
            3.0,
            status=PlanItemStatus.pending_dependency,
        ),
    ]
    plan = make_plan(items)
    result = _apply(tmp_path, make_input(tmp_path, plan))
    assert result.status == ExecutionStatus.needs_dependency.value
    assert any(d.target == "item_dep" for d in result.dependencies)


def test_replace_background_missing_mask(tmp_path):
    item = make_item(
        "item_bg", PlanOperation.replace_background, 0.0, 10.0,
        asset_uid="ast_bg",
    )
    plan = make_plan([item])
    bg = make_asset(tmp_path, "ast_bg", media_type="video", suffix=".mp4")
    result = _apply(tmp_path, make_input(tmp_path, plan, assets=[bg]))
    assert result.status == ExecutionStatus.needs_dependency.value
    dep = next(d for d in result.dependencies if d.type == "analysis_artifact")
    assert "mask" in dep.required_resource


def test_preserve_mobility_requires_flagged_mask(tmp_path):
    item = make_item(
        "item_bg", PlanOperation.replace_background, 0.0, 10.0,
        asset_uid="ast_bg",
        parameters={"preserve_mobility_device": True},
    )
    plan = make_plan([item])
    bg = make_asset(tmp_path, "ast_bg", media_type="video", suffix=".mp4")
    # 场景6：有蒙版但不含轮椅标志 → 仍 blocking（不自动退化）
    plain_mask = mask_artifact(contains_mobility_device=False)
    result = _apply(
        tmp_path, make_input(tmp_path, plan, assets=[bg], artifacts=[plain_mask])
    )
    assert result.status == ExecutionStatus.needs_dependency.value
    assert any(
        "contains_mobility_device" in d.required_resource
        for d in result.dependencies
    )
    # 带标志的蒙版 → 放行
    wheel_mask = mask_artifact("art_mask_wheel", contains_mobility_device=True)
    ok = _apply(
        tmp_path, make_input(tmp_path, plan, assets=[bg], artifacts=[wheel_mask])
    )
    assert ok.status == ExecutionStatus.completed.value


def test_capability_mismatch_no_degrade(tmp_path):
    """场景5：resolved_capability=keyframes 但 backend 不支持 → 拒绝不降级。"""
    follow = {"target": "head", "keyframes": [{"t": 1.0, "x": 0.5, "y": 0.3}]}
    item = make_item(
        "item_track",
        PlanOperation.track_overlay,
        1.0,
        4.0,
        asset_uid="ast_heart",
        follow=follow,
        resolved_capability="keyframes",
    )
    plan = make_plan([item])
    heart = make_asset(tmp_path, "ast_heart")
    backend = MemoryBackend(
        sim_dir=tmp_path / "sim",
        config=BackendConfig(
            backend_id="memory",
            capabilities_override={"keyframes": "unsupported"},
        ),
    )
    result = _apply(
        tmp_path, make_input(tmp_path, plan, assets=[heart]), backend=backend
    )
    assert result.status == ExecutionStatus.capability_mismatch.value
    assert result.errors[0].category == "capability"
    # 工程未产生 revision
    store_check = make_executor(tmp_path)
    assert store_check.get_revision(PROJECT) == 0


def test_unreadable_source_media(tmp_path):
    plan = make_plan([overlay_item("item_heart")])
    heart = make_asset(tmp_path, "ast_heart")
    from executor_fixtures import make_source

    source = make_source(tmp_path)
    source.local_uri = str(tmp_path / "nonexistent.mp4")
    result = _apply(
        tmp_path, make_input(tmp_path, plan, source=source, assets=[heart])
    )
    assert result.status == ExecutionStatus.needs_dependency.value
    assert any(d.type == "source_media" for d in result.dependencies)


def test_skip_media_fs_check_option(tmp_path):
    plan = make_plan([overlay_item("item_heart")])
    heart = make_asset(tmp_path, "ast_heart")
    from executor_fixtures import make_source

    source = make_source(tmp_path)
    source.local_uri = str(tmp_path / "nonexistent.mp4")
    config = BackendConfig(
        backend_id="memory", options={"skip_media_fs_check": True}
    )
    result = _apply(
        tmp_path,
        make_input(
            tmp_path, plan, source=source, assets=[heart], backend_config=config
        ),
    )
    assert result.status == ExecutionStatus.completed.value
