"""CLI：edit-executor compile / apply / inspect / revisions JSON 往返。"""

from __future__ import annotations

import json

from gesture_intent.models import model_dump

from edit_executor.cli import main

from executor_fixtures import (
    PROJECT,
    make_asset,
    make_input,
    make_plan,
    overlay_item,
)


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _input_path(tmp_path):
    heart = make_asset(tmp_path, "ast_heart")
    plan = make_plan([overlay_item("item_heart")])
    executor_input = make_input(tmp_path, plan, assets=[heart])
    return _write(tmp_path, "input.json", model_dump(executor_input))


def test_cli_compile(tmp_path, capsys):
    inp = _input_path(tmp_path)
    assert main(["compile", "--input", inp]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["project_id"] == PROJECT
    assert out["objects"]
    assert "trk_overlay" in out["tracks"]


def test_cli_apply_and_inspect(tmp_path, capsys):
    inp = _input_path(tmp_path)
    ws = str(tmp_path / "ws")
    assert main(["apply", "--input", inp, "--workspace", ws]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed"
    assert out["revision"] == 1

    assert main(["inspect", "--workspace", ws, "--project", PROJECT]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["revision"] == 1
    assert info["objects"] > 0

    assert main(["revisions", "--workspace", ws, "--project", PROJECT]) == 0
    rev = json.loads(capsys.readouterr().out)
    assert rev["revision"] == 1
    assert rev["history"][0]["status"] == "completed"


def test_cli_dry_run(tmp_path, capsys):
    inp = _input_path(tmp_path)
    ws = str(tmp_path / "ws")
    assert main(["apply", "--input", inp, "--workspace", ws, "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "dry_run"
    assert out["patch"]["operations"]
