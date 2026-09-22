"""editing_planner 命令行入口。

    editing-planner plan --input planner_input.json [--pretty]
    editing-planner materialize --plan plan.json [--bindings b.json]
        [--view v.json] [--capabilities c.json] [--tracks t.json] [--pretty]
    editing-planner replan --existing plan.json --intent i.json
        [--patch p.json] [--view v.json] [--pretty]

plan 的输入 JSON 即 ``EditingPlannerInput``::

    {
      "editing_intent": {...},          // 模块一 IntentParserOutput.editing_intent
      "semantic_view": {...},           // 模块二 build_semantic_view()
      "accessibility_profile": {...},   // MobilityProfile 或规划画像
      "tool_capabilities": {"tracking": false, ...}
    }

输出：LogicalEditingPlan / ResolvedEditingPlan / PlanPatch 的 JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gesture_intent.models import model_dump

from .api import EditingPlanner


def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _print(payload: Any, pretty: bool) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="editing-planner", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan", help="EditingPlannerInput → LogicalEditingPlan")
    plan_cmd.add_argument("--input", "-i", required=True, help="JSON 输入文件，'-' 读 stdin")
    plan_cmd.add_argument("--pretty", action="store_true")

    mat = sub.add_parser("materialize", help="LogicalEditingPlan + bindings → ResolvedEditingPlan")
    mat.add_argument("--plan", required=True, help="LogicalEditingPlan JSON")
    mat.add_argument("--bindings", default=None, help="AssetBinding 列表 JSON")
    mat.add_argument("--view", default=None, help="更新后的 SemanticView JSON")
    mat.add_argument("--capabilities", default=None, help="ToolCapabilityProfile JSON")
    mat.add_argument("--tracks", default=None, help="{target: Trajectory} JSON")
    mat.add_argument("--pretty", action="store_true")

    replan = sub.add_parser("replan", help="已有计划 + 新意图 → PlanPatch")
    replan.add_argument("--existing", required=True, help="LogicalEditingPlan JSON")
    replan.add_argument("--intent", required=True, help="EditingIntent JSON")
    replan.add_argument("--patch", default=None, help="IntentPatch JSON")
    replan.add_argument("--view", default=None, help="SemanticView JSON")
    replan.add_argument("--capabilities", default=None)
    replan.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)
    planner = EditingPlanner()

    try:
        if args.command == "plan":
            out = planner.plan(_read_json(args.input))
            _print(model_dump(out), args.pretty)
            return 0

        if args.command == "materialize":
            out = planner.materialize(
                _read_json(args.plan),
                bindings=_read_json(args.bindings) if args.bindings else None,
                semantic_view=_read_json(args.view) if args.view else None,
                tool_capabilities=_read_json(args.capabilities) if args.capabilities else None,
                spatial_tracks=_read_json(args.tracks) if args.tracks else None,
            )
            _print(model_dump(out), args.pretty)
            return 0

        if args.command == "replan":
            out = planner.replan(
                _read_json(args.existing),
                _read_json(args.intent),
                intent_patch=_read_json(args.patch) if args.patch else None,
                semantic_view=_read_json(args.view) if args.view else None,
                tool_capabilities=_read_json(args.capabilities) if args.capabilities else None,
            )
            _print(model_dump(out), args.pretty)
            return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
