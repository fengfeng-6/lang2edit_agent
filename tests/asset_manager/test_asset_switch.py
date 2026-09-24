"""MVP 验收场景六（§85）：换素材优先用候选池 alternatives，不重新联网搜。"""

from __future__ import annotations

from asset_manager import AssetManager, ResolutionStatus
from asset_fixtures import (
    FakeOnlineAdapter,
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


def _setup(tmp_path, adapter):
    lib = tmp_path / "library"
    make_png(str(lib / "h1.png"), size=(128, 128), shape="ellipse")
    make_png(str(lib / "h2.png"), size=(128, 128), shape="rect",
             color=(200, 120, 255, 255))
    write_manifest(str(lib), [
        lib_entry("lib_h1", "h1.png", obj="heart", attributes=["pink"],
                  style=["cute"], technical={"has_alpha": True}),
        lib_entry("lib_h2", "h2.png", obj="heart", attributes=["pink"],
                  style=["cute"], technical={"has_alpha": True}),
    ])
    return AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p6", online_adapters=[adapter] if adapter else [])


def test_scenario6_switch_alternative_no_research(tmp_path):
    adapter = FakeOnlineAdapter()
    mgr = _setup(tmp_path, adapter)
    result = mgr.resolve_assets([request_dict()])
    first_uid = result.bindings[0].asset_uid
    assert result.binding_records[0].alternatives

    switched = mgr.switch_alternative("asset_req_sticker_01")
    assert switched is not None
    assert switched.asset_uid != first_uid
    assert adapter.search_calls == 0  # 切换不重新搜索

    # 版本链：旧绑定下架，新绑定 active（§64）
    chain = mgr.state.bindings["asset_req_sticker_01"]
    assert len(chain) == 2
    assert [b.active for b in chain].count(True) == 1
    assert chain[-1].active and chain[-1].asset_uid == switched.asset_uid


def test_scenario6_no_alternative_returns_none(tmp_path):
    lib = tmp_path / "library"
    make_png(str(lib / "only.png"), size=(128, 128))
    write_manifest(str(lib), [
        lib_entry("lib_only", "only.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p6", online_adapters=[FakeOnlineAdapter()])
    result = mgr.resolve_assets([request_dict()])
    assert result.bindings
    # 只有一个候选 → 无 alternatives → 返回 None（调用方可决定是否重新搜）
    assert mgr.switch_alternative("asset_req_sticker_01") is None


def test_switch_after_restart_uses_search_history(tmp_path):
    adapter = FakeOnlineAdapter()
    mgr = _setup(tmp_path, adapter)
    mgr.resolve_assets([request_dict()])
    first_uid = mgr.state.bindings["asset_req_sticker_01"][-1].asset_uid

    # 模拟新会话：重新加载 state，候选池从 search_history 恢复
    mgr2 = _setup(tmp_path, adapter)
    switched = mgr2.switch_alternative("asset_req_sticker_01")
    assert switched is not None and switched.asset_uid != first_uid
