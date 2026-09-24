"""Registry / Binding / Usage Index / 持久化（§52-64/§70）。"""

from __future__ import annotations

import os

from asset_manager import AssetManager
from asset_fixtures import (
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


def _manager(tmp_path, entries=None):
    lib = tmp_path / "library"
    write_manifest(str(lib), entries or [])
    return AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p7"), lib


def test_stable_asset_uid_from_content(tmp_path):
    mgr, lib = _manager(tmp_path)
    f1 = make_png(str(tmp_path / "a.png"))
    r1 = mgr.import_user_asset(str(f1))
    assert r1.asset_uid == f"ast_{r1.integrity.content_hash[:8]}"
    assert len(r1.integrity.content_hash) == 64  # 完整 SHA-256（§55）

    # 同内容再次导入 → 同一 record；不同 source_ref 追加 origin（§57-58）
    f2 = tmp_path / "b.png"
    import shutil
    shutil.copyfile(f1, f2)
    r2 = mgr.import_user_asset(str(f2), caption="换个名字")
    assert r2.asset_uid == r1.asset_uid
    assert len(r2.origins) == 2

def test_multi_source_same_content_shares_uid(tmp_path):
    """local + user 同内容共享 asset_uid（§57）。"""

    mgr, lib = _manager(tmp_path)
    src = tmp_path / "same.png"
    make_png(str(src))
    import shutil
    lib_file = lib / "lib.png"
    shutil.copyfile(src, lib_file)
    write_manifest(str(lib), [
        lib_entry("lib_same", "lib.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    user_rec = mgr.import_user_asset(str(src), asset_type="sticker")
    result = mgr.resolve_assets([request_dict(
        source_policy="local_only")])
    bound = mgr.registry.get(result.bindings[0].asset_uid)
    assert bound.asset_uid == user_rec.asset_uid
    assert len(bound.origins) == 2


def test_binding_version_keeps_history(tmp_path):
    """request 升版 → 新 Binding 版本，旧版本保留不覆盖（§64）。"""

    lib_entries = []
    mgr, lib = _manager(tmp_path)
    make_png(str(lib / "h.png"), size=(64, 64))
    write_manifest(str(lib), [
        lib_entry("lib_h", "h.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    v1 = mgr.resolve_assets([request_dict(semantic_query="粉色爱心")])
    v2_req = request_dict(semantic_query="粉色爱心", version=2)
    v2 = mgr.resolve_assets([v2_req])

    chain = mgr.state.bindings["asset_req_sticker_01"]
    assert len(chain) == 2
    assert chain[0].request_version == 1 and not chain[0].active
    assert chain[1].request_version == 2 and chain[1].active


def test_usage_index_tracks_plan_items(tmp_path):
    mgr, lib = _manager(tmp_path)
    make_png(str(lib / "h.png"), size=(64, 64))
    write_manifest(str(lib), [
        lib_entry("lib_h", "h.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    req = request_dict(usage_context={"plan_item": "pln_a"})
    result = mgr.resolve_assets([req])
    uid = result.bindings[0].asset_uid
    assert mgr.state.usage_index[uid] == ["pln_a"]


def test_state_persists_across_instances(tmp_path):
    mgr, lib = _manager(tmp_path)
    src = tmp_path / "u.png"
    make_png(str(src))
    rec = mgr.import_user_asset(str(src))

    mgr2, _ = _manager(tmp_path)  # 同一路径新实例
    assert rec.asset_uid in mgr2.state.registry
    loaded = mgr2.registry.get(rec.asset_uid)
    assert loaded.integrity.content_hash == rec.integrity.content_hash


def test_project_asset_no_auto_gc(tmp_path):
    """§70：项目素材入库后生命周期内不自动清理。"""

    mgr, _lib = _manager(tmp_path)
    src = tmp_path / "u.png"
    make_png(str(src))
    rec = mgr.import_user_asset(str(src))
    mgr._persist()
    assert os.path.isfile(rec.local_uri)
    assert rec.asset_uid in mgr.state.registry
