"""video_understanding 命令行入口。

    video-understand run --input analysis_input.json [--workspace DIR] [--pretty]
    video-understand view --workspace DIR [--pretty]

run 的输入 JSON::

    {
      "video": {"video_id": "video_001",
                "metadata": {"duration": 14.82, "fps": 30, "width": 1080, "height": 1920},
                "tracks": {"frames": [...]},          // 或 "tracks_path": "...json"
                "audio": {"original_audio": {"bpm": 126, "beats": [...]}}},
      "queries": [{"type": "event_detection", "event": "heart_gesture",
                   "required_occurrence": {"type": "index", "value": 2}}]
    }

输出 JSON：video / query_results / semantic_view / state_version。
tracks 注入路径使整条链路在无 CV 依赖时也可端到端运行（§48 artifact 分离）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gesture_intent.models import model_dump

from .api import VideoUnderstanding


def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print(payload: Any, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="video-understand", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="analyze_video + resolve_queries")
    run.add_argument("--input", required=True, help="JSON 输入文件，'-' 读 stdin")
    run.add_argument("--workspace", default=None, help="语义状态落盘目录（可选）")
    run.add_argument("--pretty", action="store_true")

    view = sub.add_parser("view", help="从已落盘的 workspace 重建 semantic view")
    view.add_argument("--workspace", required=True)
    view.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "run":
        payload = _read_json(args.input)
        vu = VideoUnderstanding(workspace_dir=args.workspace)
        analysis = vu.analyze_video(payload["video"])
        results = vu.resolve_queries(payload.get("queries", []))
        out = {
            "video": model_dump(analysis.video),
            "warnings": analysis.warnings,
            "query_results": [model_dump(r) for r in results],
            "semantic_view": model_dump(vu.build_semantic_view()),
            "state_version": vu.state.version if vu.state else 0,
        }
        _print(out, args.pretty)
        return 0

    if args.command == "view":
        vu = VideoUnderstanding.load(args.workspace)
        _print(model_dump(vu.build_semantic_view()), args.pretty)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
