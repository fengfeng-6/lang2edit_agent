"""dry_run（§89）：返回 patch，零 revision/state/history/lock 副作用。"""

from __future__ import annotations

from edit_executor.models import ExecutionOptions, ExecutionStatus
from edit_executor.state.store import ExecutionStore

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_executor,
    make_input,
    make_plan,
    overlay_item,
)


def test_dry_run_returns_patch_no_side_effects(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    result = executor.apply(
        make_input(
            tmp_path,
            plan,
            assets=[heart],
            options=ExecutionOptions(dry_run=True),
        )
    )
    assert result.status == ExecutionStatus.dry_run.value
    assert result.patch is not None
    assert result.patch.operations  # 有 DSL 操作可预览
    assert result.execution_uid == ""  # dry_run 不占执行号

    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    assert not store.state_path.exists()
    assert not store.current_path.exists()
    assert not store.history_path.exists()
    assert not store.lock_path.exists()
    assert not store.runs_dir.exists() or not list(store.runs_dir.iterdir())
    assert executor.get_revision(PROJECT) == 0


def test_dry_run_preflight_still_gates(tmp_path):
    executor = make_executor(tmp_path)
    plan = make_plan(
        [overlay_item("item_heart", asset_uid="ast_missing")]
    )
    result = executor.apply(
        make_input(
            tmp_path, plan, assets=[], options=ExecutionOptions(dry_run=True)
        )
    )
    assert result.status == ExecutionStatus.needs_dependency.value
    assert result.patch is None


def test_dry_run_after_real_apply(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    executor.apply(make_input(tmp_path, plan, assets=[heart]))
    result = executor.apply(
        make_input(
            tmp_path,
            plan,
            assets=[heart],
            options=ExecutionOptions(dry_run=True),
        )
    )
    assert result.status == ExecutionStatus.dry_run.value
    assert result.patch.summary.created == 0
    assert executor.get_revision(PROJECT) == 1
