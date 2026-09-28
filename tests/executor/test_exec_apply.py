"""apply() 全链（§52/§63）：preflight → compile → diff → backend → commit。"""

from __future__ import annotations

import json

from edit_executor.backends.memory import MemoryBackend
from edit_executor.dsl.models import OperationStatus
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


def _applied(tmp_path, backend=None):
    executor = make_executor(tmp_path, backend=backend)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan(
        [overlay_item("item_heart_01"), overlay_item("item_heart_02")]
    )
    result = executor.apply(
        make_input(tmp_path, plan, assets=[heart])
    )
    return executor, result


def test_apply_completed_first_revision(tmp_path):
    executor, result = _applied(tmp_path)
    assert result.status == ExecutionStatus.completed.value
    assert result.revision == 1
    assert result.base_revision == 0
    assert result.execution_uid == "exe_000001"
    assert result.project_ref is not None
    assert result.project_ref.valid
    assert result.edit_view is not None
    assert result.patch_summary is not None
    assert result.patch_summary.created > 0


def test_apply_writes_state_layout(tmp_path):
    executor, result = _applied(tmp_path)
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    assert store.state_path.exists()
    assert store.current_graph_path.exists()
    pointer = json.loads(store.current_path.read_text(encoding="utf-8"))
    assert pointer["revision"] == 1
    manifest = json.loads(
        (store.revision_dir(1) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "committed"
    assert manifest["execution_uid"] == "exe_000001"
    # run dir artifacts
    run_dir = store.run_dir("exe_000001")
    assert (run_dir / "patch.json").exists()
    assert (run_dir / "graph.json").exists()
    # state 索引
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert "item_heart_01" in state["plan_item_index"]
    assert "ast_heart" in state["asset_usage_index"]


def test_apply_history_and_journal(tmp_path):
    executor, result = _applied(tmp_path)
    manager = executor._manager(PROJECT)
    history = manager.history()
    assert len(history) == 1
    assert history[0].status == "completed"
    assert history[0].target_revision == 1
    journal = manager.journal("exe_000001")
    assert journal
    assert all(
        e.status == OperationStatus.completed.value for e in journal
    )
    types = {e.operation_type for e in journal}
    assert "create_project" in types and "save_project" in types


def test_apply_sim_and_verify(tmp_path):
    sim_dir = tmp_path / "sim"
    backend = MemoryBackend(sim_dir=sim_dir)
    executor, result = _applied(tmp_path, backend=backend)
    assert result.status == ExecutionStatus.completed.value
    assert (sim_dir / "sim.json").exists()
    report = executor.verify(PROJECT)
    assert report.ok


def test_apply_second_revision_increments(tmp_path):
    executor, result1 = _applied(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan2 = make_plan(
        [overlay_item("item_heart_01"), overlay_item("item_new", asset_uid="ast_heart")],
        plan_uid="plan_002",
    )
    result2 = executor.apply(make_input(tmp_path, plan2, assets=[heart]))
    assert result2.status == ExecutionStatus.completed.value
    assert result2.revision == 2
    assert result2.base_revision == 1
    assert executor.get_revision(PROJECT) == 2
