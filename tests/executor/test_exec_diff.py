"""DiffEngine + Property-Level Diff（§32-§34，场景2/3/4）。"""

from __future__ import annotations

from edit_executor.compiler.graph import compile_graph
from edit_executor.diff.engine import diff_graphs
from edit_executor.diff.properties import RECREATE
from edit_executor.dsl.models import EditOpType
from edit_executor.models import DiffAction

from executor_fixtures import make_asset, make_input, make_plan, overlay_item
from editing_planner.models import Transform


def _graph(tmp_path, items, **kwargs):
    assets = kwargs.pop("assets", None) or [make_asset(tmp_path, "ast_heart")]
    return compile_graph(
        make_input(tmp_path, make_plan(items), assets=assets, **kwargs)
    )


def test_first_run_all_create(tmp_path):
    graph = _graph(tmp_path, [overlay_item("item_heart")])
    diff = diff_graphs(None, graph)
    assert diff.first_run
    assert diff.summary.created == len(graph.objects)
    assert diff.summary.noop == 0


def test_noop_for_identical_graphs(tmp_path):
    items = [overlay_item("item_heart")]
    assets = [make_asset(tmp_path, "ast_heart")]
    g1 = compile_graph(make_input(tmp_path, make_plan(items), assets=assets))
    g2 = compile_graph(make_input(tmp_path, make_plan(items), assets=assets))
    diff = diff_graphs(g1, g2)
    assert not diff.first_run
    assert diff.summary.noop == len(g1.objects)
    assert diff.summary.created == diff.summary.updated == diff.summary.deleted == 0


def test_scale_update_is_single_set_transform(tmp_path):
    assets = [make_asset(tmp_path, "ast_heart")]
    base = compile_graph(
        make_input(
            tmp_path,
            make_plan([overlay_item("item_heart", scale=0.16)]),
            assets=assets,
        )
    )
    desired = compile_graph(
        make_input(
            tmp_path,
            make_plan([overlay_item("item_heart", scale=0.30)]),
            assets=assets,
        )
    )
    diff = diff_graphs(base, desired)
    changed = [
        d for d in diff.diffs if d.action == DiffAction.update.value
    ]
    assert len(changed) == 1
    ops = [c.op_type for c in changed[0].changed_properties]
    assert ops == [EditOpType.set_transform.value]
    assert changed[0].changed_properties[0].arguments == {"scale": 0.30}


def test_asset_swap_is_replace_media_same_uid(tmp_path):
    heart = make_asset(tmp_path, "ast_heart")
    other = make_asset(tmp_path, "ast_star")
    base = compile_graph(
        make_input(
            tmp_path, make_plan([overlay_item("item_heart")]), assets=[heart]
        )
    )
    desired = compile_graph(
        make_input(
            tmp_path,
            make_plan([overlay_item("item_heart", asset_uid="ast_star")]),
            assets=[heart, other],
        )
    )
    diff = diff_graphs(base, desired)
    changed = [
        d for d in diff.diffs if d.action == DiffAction.update.value
    ]
    assert len(changed) == 1
    uid = changed[0].uid  # uid 不变（§34）
    assert uid in base.objects and uid in desired.objects
    ops = {c.op_type for c in changed[0].changed_properties}
    assert EditOpType.replace_media.value in ops


def test_removed_item_is_delete(tmp_path):
    assets = [make_asset(tmp_path, "ast_heart")]
    base = compile_graph(
        make_input(
            tmp_path,
            make_plan([overlay_item("item_a"), overlay_item("item_b")]),
            assets=assets,
        )
    )
    desired = compile_graph(
        make_input(tmp_path, make_plan([overlay_item("item_a")]), assets=assets)
    )
    diff = diff_graphs(base, desired)
    deleted = [
        d.uid for d in diff.diffs if d.action == DiffAction.delete.value
    ]
    assert len(deleted) == 1
    assert deleted[0] in base.objects
    assert deleted[0] not in desired.objects


def test_structural_change_recreates(tmp_path):
    """role/track 级变化 → recreate（uid 保持，delete+create）。"""
    assets = [make_asset(tmp_path, "ast_heart")]
    base = compile_graph(
        make_input(
            tmp_path, make_plan([overlay_item("item_heart")]), assets=assets
        )
    )
    import copy

    from edit_executor.compiler.fingerprint import object_fingerprint

    uid = next(u for u, o in base.objects.items() if o.role == "overlay")
    new_obj = copy.deepcopy(base.objects[uid])
    new_obj.role = "effect"
    new_obj.fingerprint = object_fingerprint(new_obj)
    desired = base.copy(
        update={"objects": {**base.objects, uid: new_obj}}
    )
    diff = diff_graphs(base, desired)
    changed = [
        d for d in diff.diffs if d.action == DiffAction.update.value
    ]
    assert len(changed) == 1
    assert any(
        c.op_type == RECREATE for c in changed[0].changed_properties
    )
