"""Provider 状态区分 + 顶层状态聚合（§66-67/§86 27-28）。"""

from __future__ import annotations

from asset_manager import AssetManager, ResolutionStatus
from asset_manager.models import ProviderStatus
from asset_fixtures import (
    FakeOnlineAdapter,
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


class BoomAdapter:
    """调用即炸——测 failed 与 unavailable/no_result 的区分（§66）。"""

    adapter_id = "boom"
    asset_types = ["sticker", "image"]
    authorized_for_music = False

    def search(self, text_query, negative_terms, limit):
        raise ConnectionError("boom")

    def download(self, uri, dest_dir):
        raise AssertionError("not reached")


def test_provider_status_distinction(tmp_path):
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"),
        library_root=str(tmp_path / "no_such_lib"),  # manifest 缺失
        project_id="p8", online_adapters=[])
    result = mgr.resolve_assets([request_dict()])
    statuses = {p.provider_id: p.status for p in result.provider_warnings}
    assert statuses["user_assets"] == ProviderStatus.no_result   # 正常执行但无候选
    assert statuses["local_library"] == ProviderStatus.unavailable  # manifest 缺失
    assert statuses["online_assets"] == ProviderStatus.unavailable  # 未配置
    assert result.status == ResolutionStatus.failed


def test_provider_failed_distinct(tmp_path):
    lib = tmp_path / "library"
    make_png(str(lib / "h.png"), size=(64, 64))
    write_manifest(str(lib), [
        lib_entry("lib_h", "h.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p8", online_adapters=[BoomAdapter()])
    # best_available 会问到在线源 → adapter 炸 → failed 而非 no_result
    req = request_dict(search_strategy="best_available")
    result = mgr.resolve_assets([req])
    statuses = {p.provider_id: p.status for p in result.provider_warnings}
    assert statuses["online_assets"] == ProviderStatus.failed
    # 本地已满足 → 整体仍 resolved（Provider 失败≠请求失败 §67）
    assert result.status == ResolutionStatus.resolved_with_warnings


def test_partial_status(tmp_path):
    lib = tmp_path / "library"
    make_png(str(lib / "h.png"), size=(64, 64))
    write_manifest(str(lib), [
        lib_entry("lib_h", "h.png", obj="heart", attributes=["pink"],
                  technical={"has_alpha": True}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p8")
    ok = request_dict(request_uid="req_ok")
    bad = request_dict(
        request_uid="req_bad", asset_type="music", media_type="audio",
        semantic_query="不存在的音乐", technical_requirements={})
    result = mgr.resolve_assets([ok, bad])
    assert result.status == ResolutionStatus.partial
    assert set(result.unresolved_requests) == {"req_bad"}
