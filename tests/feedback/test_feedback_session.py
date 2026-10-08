"""模块六端到端场景测试（§4.6/§9）——FakeVU + 本地库 + MemoryBackend。

用例对照计划测试矩阵：start 全链 / 实例修改 / replace→update 坍塌 /
歧义删除坍塌 / 实例删除不复活 / 增量 VU 查询 / undo-redo /
needs_clarification / dry_run / 会话恢复 / CLI 往返。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_feedback import FeedbackStatus
from session_feedback.models import SessionState

from feedback_fixtures import (
    FakeVU,
    make_library,
    make_session,
    make_source_video,
    music_objects,
    overlay_objects,
)

START_UTTERANCE = "每次比心的时候出现粉色爱心，不要挡脸，加一段欢快的音乐"


def _start(session, video):
    result = session.start(video, START_UTTERANCE)
    assert result.status == FeedbackStatus.applied, result.message
    return result


def _overlays_by_time(view):
    return sorted(overlay_objects(view), key=lambda o: o.project_time.start)


# ---------------------------------------------------------------------------
# 1. start 全链
# ---------------------------------------------------------------------------


def test_start_full_chain(tmp_path):
    session = make_session(tmp_path)
    video = make_source_video(tmp_path)

    result = session.start(video, START_UTTERANCE)

    assert result.status == FeedbackStatus.applied, result.message
    assert result.turn == 1
    assert result.revision >= 1
    assert result.execution is not None and result.execution.revision == result.revision

    root = tmp_path / "ws" / "proj1"
    # 三件套 + 上游状态落盘
    assert (root / "session" / "session_state.json").is_file()
    assert (root / "session" / "intent" / "state.json").is_file()
    assert (root / "session" / "plan" / "logical_editing_plan.json").is_file()
    assert (root / "session" / "resolved_plan.json").is_file()
    assert (root / "session" / "turns.jsonl").is_file()
    assert (root / "semantic_video_state.json").is_file()
    assert (root / "asset_registry.json").is_file()

    view = result.edit_view
    assert view is not None
    hearts = overlay_objects(view)
    assert len(hearts) == 2  # 两个比心事件 → 两个爱心
    assert len(music_objects(view)) == 1

    state = session.state
    assert state.video_id == "vid_fake"
    assert state.source_uri == video
    # 首轮查询：heart_gesture 事件检测 + 人脸跟踪约束
    assert any(q.get("event") == "heart_gesture" for q in state.queries_seen)


# ---------------------------------------------------------------------------
# 2. 实例级修改："第二个爱心小一点"
# ---------------------------------------------------------------------------


def test_scale_second_heart(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    before = _overlays_by_time(session.get_view())
    assert len(before) == 2
    scale_before = before[1].current_properties["scale"]

    result = session.reply("第二个爱心小一点")

    assert result.status == FeedbackStatus.applied, (
        result.message, result.unresolved, result.notes
    )
    assert result.plan_patch["update_items"] == 1
    assert result.plan_patch["remove_items"] == 0
    after = _overlays_by_time(result.edit_view)
    assert len(after) == 2
    # 只有 occurrence 2 被改；#1 scale 保持默认
    assert after[0].current_properties["scale"] == scale_before
    assert after[0].current_properties["scale"] == before[0].current_properties["scale"]
    assert after[1].current_properties["scale"] < scale_before


# ---------------------------------------------------------------------------
# 3. "音乐换一个更欢快的" → replace→update 归一化（uid 稳定）
# ---------------------------------------------------------------------------


def test_replace_music_collapse(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    music_uid = music_objects(session.get_view())[0].source_plan_item_uid

    result = session.reply("音乐换一个更欢快的")

    assert result.status == FeedbackStatus.applied, (
        result.message, result.unresolved
    )
    assert any("replace" in n for n in result.notes)  # 归一化留痕
    music = music_objects(result.edit_view)
    assert len(music) == 1
    assert music[0].source_plan_item_uid == music_uid  # 就地替换，uid 不变


def test_switch_music_alternative(tmp_path):
    """换一个 fast-path：候选池切换，asset_uid 变化而对象 uid 不变。"""
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    before = music_objects(session.get_view())[0]

    result = session.reply("音乐换一个")

    assert result.status == FeedbackStatus.switched, (
        result.status, result.message, result.unresolved
    )
    after = music_objects(result.edit_view)[0]
    assert after.source_plan_item_uid == before.source_plan_item_uid
    assert after.asset_uid != before.asset_uid


# ---------------------------------------------------------------------------
# 4. "把爱心删掉"（单绑定歧义坍塌 → 全删）
# ---------------------------------------------------------------------------


def test_remove_all_hearts(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))

    result = session.reply("把爱心删掉")

    assert result.status == FeedbackStatus.applied, (
        result.message, result.unresolved, result.notes
    )
    assert result.plan_patch["remove_items"] == 2
    assert overlay_objects(result.edit_view) == []
    assert len(music_objects(result.edit_view)) == 1  # 音乐不受影响


# ---------------------------------------------------------------------------
# 5. "把第二个爱心删掉" → 实例删除 + 不复活
# ---------------------------------------------------------------------------


def test_remove_second_heart_instance(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    hearts = _overlays_by_time(session.get_view())

    result = session.reply("把第二个爱心删掉")

    assert result.status == FeedbackStatus.applied, (
        result.message, result.unresolved, result.notes
    )
    assert result.plan_patch["remove_items"] == 1
    remaining = overlay_objects(result.edit_view)
    assert len(remaining) == 1
    assert remaining[0].source_plan_item_uid == hearts[0].source_plan_item_uid

    # 需求仍在 → 后续 replan 不得复活被删实例
    follow = session.reply("把爱心小一点")  # 落到唯一候选（第一个爱心）
    assert follow.status == FeedbackStatus.applied, (follow.message, follow.unresolved)
    assert len(overlay_objects(session.get_view())) == 1


# ---------------------------------------------------------------------------
# 6. "每次挥手加星星" → 增量 VU 查询 + 新对象
# ---------------------------------------------------------------------------


def test_add_stars_on_wave(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))

    result = session.reply("每次挥手加星星")

    assert result.status == FeedbackStatus.applied, (
        result.message, result.unresolved, result.notes
    )
    # VU 被追加 wave_hand 查询并造出事件
    assert any(
        e.canonical == "wave_hand" for e in session.vu.state.semantic_events
    )
    assert any(
        q.get("event") == "wave_hand" for q in session.state.queries_seen
    )
    overlays = overlay_objects(result.edit_view)
    assert len(overlays) == 3
    star = [o for o in overlays if 6.5 <= o.project_time.start <= 8.5]
    assert len(star) == 1  # 挥手区间上的新对象


# ---------------------------------------------------------------------------
# 7. undo / redo / nothing_to_undo + revision 单调
# ---------------------------------------------------------------------------


def test_undo_redo_cycle(tmp_path):
    session = make_session(tmp_path)
    video = make_source_video(tmp_path)

    _start(session, video)
    assert session.undo().status == FeedbackStatus.nothing_to_undo  # start 不入栈

    edited = session.reply("把第二个爱心删掉")
    assert edited.status == FeedbackStatus.applied
    rev_edit = session.executor.get_revision("proj1")

    undone = session.undo()
    assert undone.status == FeedbackStatus.undone
    assert len(overlay_objects(session.get_view())) == 2  # 恢复
    assert session.executor.get_revision("proj1") > rev_edit  # revert 前进式

    redone = session.redo()
    assert redone.status == FeedbackStatus.redone
    assert len(overlay_objects(session.get_view())) == 1

    undone2 = session.undo()
    assert undone2.status == FeedbackStatus.undone
    assert session.undo().status == FeedbackStatus.nothing_to_undo
    assert session.redo().status in (FeedbackStatus.redone, FeedbackStatus.nothing_to_redo)


def test_redo_cleared_by_new_edit(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    session.reply("把第二个爱心删掉")
    session.undo()
    # 新编辑（新增需求，必定 applied）→ redo 栈清空
    edit = session.reply("每次挥手加星星")
    assert edit.status == FeedbackStatus.applied, (edit.message, edit.unresolved)
    assert session.redo().status == FeedbackStatus.nothing_to_redo


# ---------------------------------------------------------------------------
# 8. "把那个东西删掉" → needs_clarification，intent 不动
# ---------------------------------------------------------------------------


def test_ambiguous_remove_needs_clarification(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    intent_before = json.loads(
        (tmp_path / "ws" / "proj1" / "session" / "intent" / "state.json")
        .read_text(encoding="utf-8")
    )

    result = session.reply("把那个东西删掉")

    assert result.status == FeedbackStatus.needs_clarification
    assert result.unresolved and result.unresolved[0].candidates
    intent_after = json.loads(
        (tmp_path / "ws" / "proj1" / "session" / "intent" / "state.json")
        .read_text(encoding="utf-8")
    )
    assert intent_after["current_intent"] == intent_before["current_intent"]
    assert len(overlay_objects(session.get_view())) == 2


# ---------------------------------------------------------------------------
# 9. dry_run 预览：无副作用
# ---------------------------------------------------------------------------


def test_dry_run_preview(tmp_path):
    session = make_session(tmp_path)
    _start(session, make_source_video(tmp_path))
    rev = session.executor.get_revision("proj1")

    result = session.reply("第二个爱心小一点", dry_run=True)

    assert result.status == FeedbackStatus.preview
    assert session.executor.get_revision("proj1") == rev  # 未提交
    assert session.state.undo_stack == []  # 预览不产生快照
    # 预览后正式执行仍应成功
    applied = session.reply("第二个爱心小一点")
    assert applied.status == FeedbackStatus.applied


# ---------------------------------------------------------------------------
# 10. 会话恢复：新 session 对象接管同一 workspace
# ---------------------------------------------------------------------------


def test_session_resume(tmp_path):
    session1 = make_session(tmp_path)
    _start(session1, make_source_video(tmp_path))

    project_dir = tmp_path / "ws" / "proj1"
    session2 = make_session(tmp_path, vu=FakeVU(project_dir))

    state = session2.get_state()
    assert state["started"] and state["turn"] == 1
    assert state["queries_seen"] >= 1

    result = session2.reply("把第二个爱心删掉")
    assert result.status == FeedbackStatus.applied, result.message
    assert len(overlay_objects(result.edit_view)) == 1

    # 恢复后的 undo 依旧可用（快照在磁盘上）
    undone = session2.undo()
    assert undone.status == FeedbackStatus.undone
    assert len(overlay_objects(session2.get_view())) == 2


# ---------------------------------------------------------------------------
# 11. CLI 往返
# ---------------------------------------------------------------------------


def test_cli_roundtrip(tmp_path, monkeypatch, capsys):
    import session_feedback.cli as cli
    from asset_manager import AssetManager
    from asset_manager.models import AssetSource
    from asset_manager.providers.local import LocalLibraryProvider
    from edit_executor import EditingExecutor
    from session_feedback import FeedbackSession

    from feedback_fixtures import FakeInspector

    library = make_library(tmp_path / "clilib")
    source = make_source_video(tmp_path)

    def factory(workspace, project_id):
        workspace = Path(workspace)
        project_dir = workspace / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        return FeedbackSession(
            workspace,
            project_id,
            vu=FakeVU(project_dir),
            asset_manager=AssetManager(
                str(workspace),
                library_root=str(library),
                project_id=project_id,
                providers={
                    AssetSource.local: LocalLibraryProvider(str(library))
                },
                inspector=FakeInspector(),
            ),
            executor=EditingExecutor(workspace),
        )

    monkeypatch.setattr(cli, "FeedbackSession", factory)
    ws = str(tmp_path / "cliws")

    def run(*argv):
        rc = cli.main(list(argv))
        out = capsys.readouterr().out
        return rc, json.loads(out)

    rc, out = run(
        "start", "--workspace", ws, "--project", "p1",
        "--video", source, "--utterance", START_UTTERANCE,
    )
    assert rc == 0 and out["status"] == "applied", out

    rc, out = run(
        "reply", "--workspace", ws, "--project", "p1",
        "--utterance", "把第二个爱心删掉",
    )
    assert rc == 0 and out["status"] == "applied", out

    rc, out = run("state", "--workspace", ws, "--project", "p1")
    assert rc == 0 and out["turn"] == 2 and out["undo_depth"] == 1

    rc, out = run("undo", "--workspace", ws, "--project", "p1")
    assert rc == 0 and out["status"] == "undone"


# ---------------------------------------------------------------------------
# patch_flow 归一化单元回归
# ---------------------------------------------------------------------------


def test_collapse_replace_skips_non_replace_adds():
    """action=add 或非单例类型的 add_object_requirements 不进 replace→update
    坍塌——and 短路链曾把 False 当 existing 用导致 AttributeError。"""
    from gesture_intent.models import (
        EditingIntent,
        IntentPatch,
        ObjectAction,
        ObjectRequirement,
        ObjectType,
    )
    from session_feedback.patch_flow import normalize_patch

    patch = IntentPatch(add_object_requirements=[
        ObjectRequirement(
            id="obj_req_01", object_type=ObjectType.background,
            action=ObjectAction.add, source_text="海边沙滩背景"),
        ObjectRequirement(
            id="obj_req_02", object_type=ObjectType.sticker,
            action=ObjectAction.replace, source_text="贴纸"),
    ])

    out, unresolved, notes = normalize_patch(patch, [], EditingIntent())

    assert [r.id for r in out.add_object_requirements] == [
        "obj_req_01", "obj_req_02"]
    assert not out.update_object_requirements
    assert not notes


def test_start_parallelizes_analysis_and_parse(tmp_path, monkeypatch):
    """start 的 VU 分析（CPU/CV）与首轮 parse（LLM/网络）并行——
    parse 输入只有 utterance；墙钟应接近 max 而非 sum。"""
    import time

    session = make_session(tmp_path)

    def _slow(fn, secs):
        def _w(*a, **kw):
            time.sleep(secs)
            return fn(*a, **kw)
        return _w

    monkeypatch.setattr(
        session.vu, "analyze_video", _slow(session.vu.analyze_video, 0.5))
    monkeypatch.setattr(
        session.parser, "parse", _slow(session.parser.parse, 0.5))

    t0 = time.perf_counter()
    result = session.start(make_source_video(tmp_path), START_UTTERANCE)
    wall = time.perf_counter() - t0

    assert result.status == FeedbackStatus.applied, result.message
    assert wall < 0.9, f"串行下界 ~1.0s，实测 {wall:.2f}s 说明未并行"
