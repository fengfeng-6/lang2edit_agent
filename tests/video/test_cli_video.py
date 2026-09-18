"""CLI 与模块一输出对接测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from gesture_intent import IntentParser, IntentParserInput
from video_understanding import VideoUnderstanding

from conftest import make_video

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _env():
    return dict(os.environ, PYTHONPATH=_SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))


def test_cli_run_roundtrip(tmp_path):
    payload = {
        "video": make_video(),
        "queries": [
            {"type": "event_detection", "event": "heart_gesture",
             "required_occurrence": {"type": "index", "value": 2}},
        ],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "video_understanding", "run",
         "--input", str(input_path), "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["video"]["video_id"] == "v_dance"
    assert out["query_results"][0]["status"] == "completed"
    assert out["semantic_view"]["video"]["aspect_ratio"] == "9:16"

    # view 子命令从落盘 workspace 重建
    proc2 = subprocess.run(
        [sys.executable, "-m", "video_understanding", "view",
         "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, env=_env(),
    )
    assert proc2.returncode == 0, proc2.stderr
    view = json.loads(proc2.stdout)
    assert any(e["canonical"] == "heart_gesture" for e in view["relevant_events"])


def test_module1_queries_flow_into_module2():
    """模块一 parse 出的 required_video_queries 直接被模块二消费（§53）。"""
    out = IntentParser().parse(IntentParserInput(
        user_utterance="每次比心的时候出现爱心，向右指的时候出现贝壳，手举到头顶的时候加星星",
    ))
    queries = out.required_video_queries
    assert queries  # 模块一确实产出了查询

    vu = VideoUnderstanding()
    vu.analyze_video(make_video())
    results = vu.resolve_queries(queries)
    by_type = {}
    for q, r in zip(queries, results):
        by_type.setdefault(q.type, []).append(r.status.value)
    assert by_type["event_detection"].count("completed") >= 2  # heart + point_right
    assert by_type["pose_condition_detection"] == ["completed"]  # hand above head
