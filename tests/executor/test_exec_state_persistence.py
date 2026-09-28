"""状态持久化与锁（§57-§72）：跨实例一致、project.lock 互斥。"""

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


def test_state_survives_executor_recreate(tmp_path):
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    e1 = make_executor(tmp_path)
    r1 = e1.apply(make_input(tmp_path, plan, assets=[heart]))
    assert r1.status == "completed"

    e2 = make_executor(tmp_path)  # 新实例，同一 workspace
    assert e2.get_revision(PROJECT) == 1
    state = e2.get_state(PROJECT)
    assert state is not None
    assert state.project_id == PROJECT
    assert state.graph_fingerprint == r1.execution_state.graph_fingerprint
    view = e2.get_edit_view(PROJECT)
    assert len(view.objects) == len(r1.edit_view.objects)
    assert e2.verify(PROJECT).ok


def test_project_lock_blocks_second_apply(tmp_path):
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    store = ExecutionStore(str(tmp_path / "ws"), PROJECT)
    store.ensure_dirs()
    assert store.acquire_lock("exe_someone_else")

    executor = make_executor(tmp_path)
    result = executor.apply(make_input(tmp_path, plan, assets=[heart]))
    assert result.status == ExecutionStatus.locked.value
    assert executor.get_revision(PROJECT) == 0

    store.release_lock("exe_someone_else")
    result2 = executor.apply(make_input(tmp_path, plan, assets=[heart]))
    assert result2.status == ExecutionStatus.completed.value


def test_execution_uid_sequence(tmp_path):
    executor = make_executor(tmp_path)
    heart = make_asset(tmp_path, "ast_heart")
    for i in range(3):
        plan = make_plan(
            [overlay_item("item_heart")], plan_uid=f"plan_{i}"
        )
        result = executor.apply(make_input(tmp_path, plan, assets=[heart]))
        assert result.execution_uid == f"exe_{i + 1:06d}"
        assert result.status in (
            ExecutionStatus.completed.value,
            ExecutionStatus.completed_noop.value,
        )
