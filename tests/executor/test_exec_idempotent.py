"""幂等性（§32/§58，场景8）：相同 plan 再执行 → 全 NOOP，revision 不动。"""

from __future__ import annotations

from edit_executor.models import ExecutionStatus
from edit_executor.state.store import ExecutionStore

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_executor,
    make_input,
    make_plan,
    overlay_item,
)


def test_repeated_apply_is_noop(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    r1 = executor.apply(make_input(tmp_path, plan, assets=[heart]))
    assert r1.status == ExecutionStatus.completed.value

    r2 = executor.apply(make_input(tmp_path, plan, assets=[heart]))
    assert r2.status == ExecutionStatus.completed_noop.value
    assert r2.revision == 1
    assert r2.execution_uid == "exe_000002"
    # 无新 candidate / revision 目录
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    assert not store.revision_dir(2).exists()
    assert executor.get_revision(PROJECT) == 1
    # patch 上全 NOOP
    assert r2.patch_summary.noop == len(r2.patch.summary.object_changes)
    # history 两条：completed + completed_noop
    manager = executor._manager(PROJECT)
    statuses = [e.status for e in manager.history()]
    assert statuses == ["completed", "completed_noop"]


def test_partial_change_updates_only_delta(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan1 = make_plan(
        [overlay_item("item_a", scale=0.16), overlay_item("item_b")]
    )
    executor.apply(make_input(tmp_path, plan1, assets=[heart]))
    plan2 = make_plan(
        [overlay_item("item_a", scale=0.30), overlay_item("item_b")],
        plan_uid="plan_002",
    )
    r2 = executor.apply(make_input(tmp_path, plan2, assets=[heart]))
    assert r2.status == ExecutionStatus.completed.value
    assert r2.patch_summary.updated == 1
    assert r2.patch_summary.created == 0
    assert r2.patch_summary.deleted == 0
    # 仅一条 set_transform
    ops = [op.op_type for op in r2.patch.operations]
    assert ops.count("set_transform") == 1
    assert "create_object" not in ops
