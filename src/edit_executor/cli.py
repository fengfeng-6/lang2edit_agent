"""edit_executor 命令行入口。

    edit-executor compile  --input executor_input.json [--pretty]
    edit-executor compile  --workspace W --project P --plan resolved_plan.json
    edit-executor apply    --input executor_input.json [--dry-run] [--pretty]
    edit-executor inspect  --workspace W --project P [--pretty]
    edit-executor revisions --workspace W --project P [--pretty]

``--input`` 直接吃 ExecutorInput JSON（含 resolved_plan 内嵌）；
``--workspace/--project/--plan`` 走 build_input 从 workspace 组装。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gesture_intent.models import model_dump, model_validate

from .api import EditingExecutor
from .models import ExecutionOptions, ExecutorInput


def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _print(payload: Any, pretty: bool) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")


def _executor_input(executor: EditingExecutor, args) -> ExecutorInput:
    if getattr(args, "input", None):
        return model_validate(ExecutorInput, _read_json(args.input))
    from editing_planner.models import ResolvedEditingPlan

    plan = model_validate(ResolvedEditingPlan, _read_json(args.plan))
    return executor.build_input(args.project, plan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edit-executor", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_workspace(p) -> None:
        p.add_argument("--workspace", "-w", default="workspace")
        p.add_argument("--project", "-p", required=True)

    compile_cmd = sub.add_parser("compile", help="ExecutorInput → DesiredProjectGraph")
    compile_cmd.add_argument("--input", "-i", default=None, help="ExecutorInput JSON")
    compile_cmd.add_argument("--workspace", "-w", default="workspace")
    compile_cmd.add_argument("--project", "-p", default=None)
    compile_cmd.add_argument("--plan", default=None, help="ResolvedEditingPlan JSON")
    compile_cmd.add_argument("--pretty", action="store_true")

    apply_cmd = sub.add_parser("apply", help="执行 ResolvedEditingPlan → ExecutionResult")
    apply_cmd.add_argument("--input", "-i", default=None)
    apply_cmd.add_argument("--workspace", "-w", default="workspace")
    apply_cmd.add_argument("--project", "-p", default=None)
    apply_cmd.add_argument("--plan", default=None)
    apply_cmd.add_argument("--backend", default=None, help="覆盖 backend_config.backend_id")
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.add_argument("--pretty", action="store_true")

    inspect_cmd = sub.add_parser("inspect", help="execution/ 状态摘要")
    add_workspace(inspect_cmd)
    inspect_cmd.add_argument("--pretty", action="store_true")

    rev_cmd = sub.add_parser("revisions", help="当前 revision + history")
    add_workspace(rev_cmd)
    rev_cmd.add_argument("--pretty", action="store_true")

    view_cmd = sub.add_parser("edit-view", help="ProjectEditView（模块六接口）")
    add_workspace(view_cmd)
    view_cmd.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)
    workspace = getattr(args, "workspace", "workspace")
    executor = EditingExecutor(workspace)

    try:
        if args.command in ("compile", "apply"):
            if args.input is None and not (args.project and args.plan):
                parser.error(f"{args.command} 需要 --input 或 --project+--plan")
            executor_input = _executor_input(executor, args)
            if args.command == "apply":
                if args.backend:
                    executor_input.backend_config.backend_id = args.backend
                if args.dry_run:
                    executor_input.execution_options = ExecutionOptions(dry_run=True)
                _print(model_dump(executor.apply(executor_input)), args.pretty)
            else:
                _print(model_dump(executor.compile(executor_input)), args.pretty)
            return 0
        if args.command == "inspect":
            _print(executor.inspect(args.project), args.pretty)
            return 0
        if args.command == "revisions":
            manager = executor._manager(args.project)
            _print(
                {
                    "project_id": args.project,
                    "revision": executor.get_revision(args.project),
                    "history": [model_dump(e) for e in manager.history()],
                },
                args.pretty,
            )
            return 0
        if args.command == "edit-view":
            _print(model_dump(executor.get_edit_view(args.project)), args.pretty)
            return 0
    except Exception as exc:  # CLI 边界：打印结构化错误
        _print({"error": f"{type(exc).__name__}: {exc}"}, pretty=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
