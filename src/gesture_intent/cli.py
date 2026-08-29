"""Command-line JSON interface for the intent parser."""

from __future__ import annotations

import argparse
import json
import locale
import sys
from pathlib import Path

from .models import IntentParserInput, model_dump, model_validate
from .parser import IntentParser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gesture-intent", description="Parse natural-language video editing intent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse = subparsers.add_parser("parse", help="parse an IntentParserInput JSON document")
    parse.add_argument("--input", "-i", default="-", help="input JSON file, or - for stdin")
    parse.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "parse":
        return 2
    try:
        raw = _read_stdin() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        input_model = model_validate(IntentParserInput, json.loads(raw))
        output = IntentParser().parse(input_model)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(model_dump(output), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


def _read_stdin() -> str:
    """Prefer UTF-8 while tolerating Windows console/pipeline encodings."""
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
