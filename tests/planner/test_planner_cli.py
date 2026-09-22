"""CLI：editing-planner plan / materialize JSON 往返。"""

from __future__ import annotations

import json

from planner_fixtures import heart_intent, make_view

from editing_planner.cli import main


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_cli_plan(tmp_path, capsys):
    from gesture_intent.models import model_dump
    payload = {
        "editing_intent": model_dump(heart_intent()),
        "semantic_view": make_view(),
    }
    inp = _write(tmp_path, "input.json", payload)
    assert main(["plan", "--input", inp]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan_uid"].startswith("plan_")
    assert len(out["plan_items"]) == 2


def test_cli_materialize(tmp_path, capsys):
    from gesture_intent.models import model_dump
    plan_payload = {
        "editing_intent": model_dump(heart_intent()),
        "semantic_view": make_view(),
    }
    inp = _write(tmp_path, "input.json", plan_payload)
    plan_path = tmp_path / "plan.json"
    bindings_path = tmp_path / "bindings.json"

    assert main(["plan", "--input", inp]) == 0
    plan_json = json.loads(capsys.readouterr().out)
    plan_path.write_text(json.dumps(plan_json), encoding="utf-8")
    bindings_path.write_text(json.dumps([{
        "asset_request_uid": "asset_req_sticker_01",
        "asset_uid": "asset_heart_17",
        "media_type": "image",
    }]), encoding="utf-8")

    assert main([
        "materialize", "--plan", str(plan_path),
        "--bindings", str(bindings_path),
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan_uid"].startswith("rpln_")
    assert out["resolved_items"][0]["asset_uid"] == "asset_heart_17"


def test_cli_replan(tmp_path, capsys):
    from gesture_intent.models import model_dump
    plan_payload = {
        "editing_intent": model_dump(heart_intent()),
        "semantic_view": make_view(),
    }
    inp = _write(tmp_path, "input.json", plan_payload)
    assert main(["plan", "--input", inp]) == 0
    plan_json = json.loads(capsys.readouterr().out)
    plan_path = _write(tmp_path, "plan.json", plan_json)
    intent_path = _write(tmp_path, "intent.json", model_dump(heart_intent()))
    view_path = _write(tmp_path, "view.json", make_view())

    assert main([
        "replan", "--existing", plan_path, "--intent", intent_path,
        "--view", view_path,
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "add_plan_items" in out and "update_plan_items" in out
