"""Crash Recovery（§70-§72，场景9）：中败 candidate 丢弃，工程仍可用。"""

from __future__ import annotations

import json

from edit_executor.backends.memory import MemoryBackend
from edit_executor.models import ExecutionStatus
from edit_executor.state.revision import write_candidate
from edit_executor.state.store import ExecutionStore

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_executor,
    make_input,
    make_plan,
    overlay_item,
)


def _input(tmp_path):
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    return make_input(tmp_path, plan, assets=[heart])


def test_failed_execute_leaves_no_revision(tmp_path):
    backend = MemoryBackend(
        sim_dir=tmp_path / "sim", fail_plan={"op_type": "create_object"}
    )
    executor = make_executor(tmp_path, backend=backend)
    result = executor.apply(_input(tmp_path))
    assert result.status == ExecutionStatus.failed.value
    assert result.errors
    assert executor.get_revision(PROJECT) == 0
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    assert not store.state_path.exists()
    # journal 记录了失败 + skipped
    manager = executor._manager(PROJECT)
    statuses = {e.status for e in manager.journal(result.execution_uid)}
    assert "failed" in statuses


def test_recovery_quarantines_candidate_then_recovers(tmp_path):
    bad = MemoryBackend(sim_dir=tmp_path / "sim_bad", fail_plan={"after_ops": 0})
    executor = make_executor(tmp_path, backend=bad)
    result = executor.apply(_input(tmp_path))
    assert result.status == ExecutionStatus.failed.value
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    # candidate rev_0001 存在但未 committed
    manifest = json.loads(
        (store.revision_dir(1) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "candidate"

    good = MemoryBackend(sim_dir=tmp_path / "sim_good")
    executor2 = make_executor(tmp_path, backend=good)
    result2 = executor2.apply(_input(tmp_path))
    assert result2.status == ExecutionStatus.completed.value
    # recover 在 apply 前记录 candidate 隔离；新执行复用 rev_0001 并正常提交
    assert any("rev_0001" in w for w in result2.warnings)
    assert result2.revision == 1
    manifest = json.loads(
        (store.revision_dir(1) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "committed"
    assert executor2.get_revision(PROJECT) == 1


def test_orphan_candidate_recovery(tmp_path):
    """手写未完成 candidate → recover 隔离、adopted_revision=0。"""
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    executor_input = make_input(tmp_path, plan, assets=[heart])
    graph = executor.compile(executor_input)
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    store.ensure_dirs()
    manager = executor._manager(PROJECT)
    state = manager.build_state(
        graph, None, 1, "memory", "0.1.0", "0.1.0", None, "fp"
    )
    write_candidate(store, 1, "exe_ghost", "patch_x", graph, state)
    report = manager.recover()
    assert report.adopted_revision == 0
    assert any("rev_0001" in q for q in report.quarantined)
    assert executor.get_revision(PROJECT) == 0


def test_torn_jsonl_line_tolerated(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    executor.apply(make_input(tmp_path, plan, assets=[heart]))
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    with store.history_path.open("a", encoding="utf-8") as fh:
        fh.write('{"execution_uid": "exe_broken", "status": ')  # 截断行
    manager = executor._manager(PROJECT)
    entries = manager.history()
    assert len(entries) == 1  # 坏行被跳过
    assert entries[0].status == "completed"
