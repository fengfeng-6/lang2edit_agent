"""SourceTimelineCompiler：freeze → 切片序列（§13-§16，场景7）。"""

from __future__ import annotations

from edit_executor.compiler.graph import compile_graph
from edit_executor.compiler.mutations import MAIN_VIDEO_UID, ORIGINAL_AUDIO_UID
from edit_executor.compiler.operations import timeline_object_uid

from executor_fixtures import make_input, make_item, make_plan
from editing_planner.models import PlanOperation


def _freeze_plan(audio_policy="continue"):
    freeze = make_item(
        "item_freeze",
        PlanOperation.freeze,
        3.0,
        5.0,
        source_time=3.0,
        freeze_audio_policy=audio_policy,
    )
    # freeze 把 10s 源拉成 12s 工程（insert_duration 移位已由模块三算好）
    return make_plan(
        [freeze],
        total=12.0,
        shifts=[{"from_source_time": 3.0, "delta": 2.0}],
    )


def _slices(graph):
    return sorted(
        (o for o in graph.objects.values() if o.object_type == "source_slice"),
        key=lambda o: o.project_time.start,
    )


def test_no_freeze_single_main_video(tmp_path):
    plan = make_plan([], total=10.0)
    graph = compile_graph(make_input(tmp_path, plan))
    main = graph.objects[MAIN_VIDEO_UID]
    assert (main.project_time.start, main.project_time.end) == (0.0, 10.0)
    assert main.parameters["source_start"] == 0.0
    assert main.parameters["source_end"] == 10.0
    assert len(_slices(graph)) == 1


def test_freeze_slices_and_total(tmp_path):
    plan = _freeze_plan()
    graph = compile_graph(make_input(tmp_path, plan))
    slices = _slices(graph)
    assert len(slices) == 2
    a, b = slices
    # slice A：源 [0,3) → 工程 [0,3)
    assert (a.project_time.start, a.project_time.end) == (0.0, 3.0)
    assert (a.parameters["source_start"], a.parameters["source_end"]) == (0.0, 3.0)
    # slice B：源 [3,10) → 工程 [5,12)（anchor 复现帧后续播）
    assert (b.project_time.start, b.project_time.end) == (5.0, 12.0)
    assert (b.parameters["source_start"], b.parameters["source_end"]) == (3.0, 10.0)
    # freeze 对象本体
    freeze = graph.objects[timeline_object_uid("item_freeze", "freeze_frame")]
    assert (freeze.project_time.start, freeze.project_time.end) == (3.0, 5.0)
    assert freeze.parameters["source_time"] == 3.0
    assert freeze.parameters["freeze_duration"] == 2.0
    assert graph.total_duration == 12.0
    # 切片 uid 内容派生（边界变化 → DELETE+CREATE）
    assert {a.timeline_object_uid, b.timeline_object_uid} != {MAIN_VIDEO_UID}


def test_freeze_silence_mute_ranges(tmp_path):
    plan = _freeze_plan(audio_policy="silence")
    graph = compile_graph(make_input(tmp_path, plan))
    audio = graph.objects[ORIGINAL_AUDIO_UID]
    assert audio.parameters["mute_ranges"] == [{"start": 3.0, "end": 5.0}]
    assert (audio.project_time.start, audio.project_time.end) == (0.0, 12.0)


def test_freeze_hold_warns(tmp_path):
    plan = _freeze_plan(audio_policy="hold")
    graph = compile_graph(make_input(tmp_path, plan))
    assert any("hold" in w for w in graph.warnings)


def test_no_audio_source_skips_original_audio(tmp_path):
    from executor_fixtures import make_source

    plan = make_plan([], total=10.0)
    source = make_source(tmp_path, has_audio=False)
    graph = compile_graph(make_input(tmp_path, plan, source=source))
    assert ORIGINAL_AUDIO_UID not in graph.objects
