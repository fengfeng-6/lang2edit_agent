"""asset_manager 命令行入口。

    asset-manager resolve --requests reqs.json [--project p1] [--pretty]
    asset-manager import --file x.png --project p1 [--caption ..] [--tag ..]
    asset-manager switch --request asset_req_sticker_01 --project p1 [--pretty]
    asset-manager state --project p1 [--pretty]

``requests`` JSON 即模块三 ``LogicalEditingPlan.asset_requests`` 列表::

    [{"request_uid": "...", "asset_type": "sticker", "media_type": "image",
      "semantic_query": "可爱的粉色爱心", "technical_requirements": {"has_alpha": true}}]

``ASSET_ONLINE_SOURCES`` 环境变量可配在线源（JSON 数组，见 providers/online.py）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gesture_intent.models import model_dump

from .api import AssetManager


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _emit(value, pretty: bool) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False,
              indent=2 if pretty else None)
    sys.stdout.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="asset-manager")
    parser.add_argument("--workspace", default="workspace")
    parser.add_argument("--library", default="data/asset_library")
    parser.add_argument("--offline", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve", help="消费 AssetRequest[] → AssetResolutionResult")
    p.add_argument("--requests", required=True)
    p.add_argument("--project", default="default")
    p.add_argument("--pretty", action="store_true")

    p = sub.add_parser("import", help="导入用户素材")
    p.add_argument("--file", required=True)
    p.add_argument("--project", default="default")
    p.add_argument("--caption", default="")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--asset-type", default="image")
    p.add_argument("--pretty", action="store_true")

    p = sub.add_parser("switch", help="切换到备选素材（§85）")
    p.add_argument("--request", required=True)
    p.add_argument("--project", default="default")
    p.add_argument("--pretty", action="store_true")

    p = sub.add_parser("state", help="查看项目素材状态")
    p.add_argument("--project", default="default")
    p.add_argument("--pretty", action="store_true")

    args = parser.parse_args(argv)
    mgr = AssetManager(
        workspace_root=args.workspace, library_root=args.library,
        project_id=args.project, offline=args.offline)

    if args.command == "resolve":
        result = mgr.resolve_assets(_load_json(args.requests))
        _emit(model_dump(result), args.pretty)
        return 0 if result.status.value in ("resolved", "resolved_with_warnings") else 1
    if args.command == "import":
        record = mgr.import_user_asset(
            args.file, caption=args.caption, tags=args.tag,
            asset_type=args.asset_type)
        _emit(model_dump(record), args.pretty)
        return 0
    if args.command == "switch":
        binding = mgr.switch_alternative(args.request)
        if binding is None:
            sys.stderr.write("no alternative available\n")
            return 1
        _emit(model_dump(binding), args.pretty)
        return 0
    if args.command == "state":
        _emit(model_dump(mgr.state), args.pretty)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
