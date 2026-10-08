"""Command-line JSON interface for the feedback session orchestrator."""

from __future__ import annotations

import argparse
import json
import locale
import sys
from pathlib import Path

from gesture_intent.models import model_dump

from .session import FeedbackSession


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-feedback",
        description="Module-6 feedback session: start / reply / undo / redo / view / state",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("start", "start a session: analyze video + first utterance"),
        ("reply", "apply a feedback utterance"),
        ("undo", "revert the last applied turn"),
        ("redo", "re-apply the last undone turn"),
        ("view", "current ProjectEditView"),
        ("state", "session summary"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--workspace", "-w", default="workspace")
        p.add_argument("--project", "-p", required=True)
        if name == "start":
            p.add_argument("--video", required=True,
                           help="video path or JSON dict (tracks injection)")
            p.add_argument("--utterance", "-u", required=True)
            p.add_argument("--video-json", action="store_true",
                           help="--video carries a JSON dict, not a path")
        if name == "reply":
            p.add_argument("--utterance", "-u", required=True)
            p.add_argument("--dry-run", action="store_true")
        p.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = FeedbackSession(args.workspace, args.project)
    try:
        if args.command == "start":
            video = (
                json.loads(args.video) if args.video_json else args.video
            )
            output = session.start(video, args.utterance)
        elif args.command == "reply":
            output = session.reply(args.utterance, dry_run=args.dry_run)
        elif args.command == "undo":
            output = session.undo()
        elif args.command == "redo":
            output = session.redo()
        elif args.command == "view":
            output = session.get_view()
        elif args.command == "state":
            output = session.get_state()
        else:
            return 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    payload = output if isinstance(output, dict) else model_dump(output)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _read_stdin() -> str:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    data = buffer.read()
    for encoding in ("utf-8", locale.getpreferredencoding(False), "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode(locale.getpreferredencoding(False), errors="replace")
