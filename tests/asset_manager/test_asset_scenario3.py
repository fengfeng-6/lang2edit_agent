"""MVP 验收场景三（§82）：exact_reference 直查，不做语义搜索。

「用我上传的第二张图」→ resolve → inspect → register → bind。
"""

from __future__ import annotations

from asset_manager import AssetManager, ResolutionStatus
from asset_fixtures import FakeOnlineAdapter, make_png, request_dict


def test_scenario3_exact_reference_no_search(tmp_path):
    adapter = FakeOnlineAdapter()
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"),
        library_root=str(tmp_path / "library"),
        project_id="p3", online_adapters=[adapter])

    up1 = tmp_path / "u1.png"
    up2 = tmp_path / "u2.png"
    make_png(str(up1))
    make_png(str(up2), color=(0, 200, 0, 255))
    rec1 = mgr.import_user_asset(str(up1), caption="第一张", asset_type="image")
    rec2 = mgr.import_user_asset(str(up2), caption="第二张", asset_type="image")

    req = request_dict(
        resolution_mode="exact_reference",
        source_ref="index:2",  # 「第二张图」→ 序号引用
        asset_type="image",
        technical_requirements={},
    )
    result = mgr.resolve_assets([req])

    assert result.status == ResolutionStatus.resolved
    assert result.bindings[0].asset_uid == rec2.asset_uid
    assert result.bindings[0].match_score == 1.0
    # 不得重新进行语义搜索：无 SearchRecord，在线源零调用
    assert not result.search_records
    assert adapter.search_calls == 0


def test_exact_reference_by_filename(tmp_path):
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"),
        library_root=str(tmp_path / "library"),
        project_id="p3")
    src = tmp_path / "logo.png"
    make_png(str(src))
    record = mgr.import_user_asset(str(src), asset_type="image")
    req = request_dict(source_ref="logo.png", asset_type="image",
                       technical_requirements={})
    result = mgr.resolve_assets([req])
    assert result.bindings[0].asset_uid == record.asset_uid


def test_exact_reference_missing_unresolved(tmp_path):
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"),
        library_root=str(tmp_path / "library"), project_id="p3")
    req = request_dict(
        resolution_mode="exact_reference", source_ref="nonexistent.png")
    result = mgr.resolve_assets([req])
    assert result.status.value == "failed"
    assert result.resolutions[0].reason.startswith("reference_not_found")
