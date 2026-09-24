"""MVP 验收场景一（§80）：本地贴纸 first_satisfactory 链路。

cute pink heart + transparent required：
User 无匹配 → Local 多候选 → 检查 Alpha 过滤无效 → Semantic/Usage
Ranking → 选中 → Registry → Binding；first_satisfactory 命中即停，
不再访问 Online。
"""

from __future__ import annotations

import os

from asset_manager import AssetManager, ResolutionStatus
from asset_manager.models import ProviderStatus
from asset_fixtures import (
    FakeOnlineAdapter,
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


def _build(tmp_path, online_adapter=None):
    lib = tmp_path / "library"
    make_png(str(lib / "pink_heart.png"), size=(128, 128), shape="ellipse")
    make_png(str(lib / "red_heart.png"), size=(128, 128), shape="rect",
             color=(255, 0, 0, 255))
    make_png(str(lib / "opaque_heart.png"), size=(128, 128), opaque=True)
    write_manifest(str(lib), [
        lib_entry("lib_heart_pink", "pink_heart.png",
                  obj="heart", attributes=["pink"], style=["cute", "cartoon"],
                  usage_tags=["gesture_overlay", "compact_overlay"],
                  technical={"has_alpha": True, "width": 128, "height": 128}),
        lib_entry("lib_heart_red", "red_heart.png",
                  obj="heart", attributes=["red"], style=["realistic"],
                  technical={"has_alpha": True, "width": 128, "height": 128}),
        lib_entry("lib_heart_opaque", "opaque_heart.png",
                  obj="heart", attributes=["pink"], style=["cute"],
                  technical={"has_alpha": True, "width": 128, "height": 128}),
    ])
    adapters = [online_adapter] if online_adapter else []
    return AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p1", online_adapters=adapters), lib


def test_scenario1_first_satisfactory_local_hit(tmp_path):
    adapter = FakeOnlineAdapter()
    mgr, _lib = _build(tmp_path, adapter)
    result = mgr.resolve_assets([request_dict()])

    assert result.status in (
        ResolutionStatus.resolved, ResolutionStatus.resolved_with_warnings)
    assert not result.unresolved_requests
    assert len(result.bindings) == 1

    binding = result.bindings[0]
    assert binding.asset_uid.startswith("ast_")
    assert binding.uri and os.path.isfile(binding.uri)

    # 真实 Alpha 检查：声称透明但实际不透明的候选被 Import 复核挡下
    record = mgr.registry.get(binding.asset_uid)
    assert record.integrity.decodable
    assert record.technical_metadata.has_alpha
    assert record.technical_metadata.foreground_occupancy > 0.1

    # first_satisfactory：本地已满足，Online 未被调用
    assert adapter.search_calls == 0
    # alternatives 留档（§50）
    assert result.binding_records[0].alternatives

    # SearchRecord 持久化（§65）
    history = mgr.store.search_history()
    assert history and history[-1]["request_uid"] == "asset_req_sticker_01"


def test_scenario1_online_fallback_when_local_empty(tmp_path):
    src = tmp_path / "src.png"
    make_png(str(src), size=(96, 96))
    adapter = FakeOnlineAdapter(
        rows=[{
            "id": "ext1", "title": "pink heart", "tags": ["heart", "pink", "cute"],
            "asset_type": "sticker", "original_url": "https://x/heart.png",
            "width": 96, "height": 96, "has_alpha": True,
            "license": {"type": "cc0"},
        }],
        files={"https://x/heart.png": str(src)},
    )
    mgr, _lib = _build(tmp_path, adapter)
    # 清空本地 manifest 让 Local miss
    write_manifest(str(tmp_path / "library"), [])
    result = mgr.resolve_assets([request_dict()])

    assert result.status in (
        ResolutionStatus.resolved, ResolutionStatus.resolved_with_warnings)
    assert adapter.search_calls == 1
    assert adapter.download_calls == 1  # 只下载被选中的候选（§24）
    record = mgr.registry.get(result.bindings[0].asset_uid)
    assert "downloaded" in record.local_uri


def test_scenario1_all_miss_unresolved(tmp_path):
    mgr, _lib = _build(tmp_path)
    write_manifest(str(tmp_path / "library"), [])
    result = mgr.resolve_assets([request_dict()])
    assert result.status == ResolutionStatus.failed
    assert result.unresolved_requests == ["asset_req_sticker_01"]
    statuses = {p.provider_id: p.status for p in result.provider_warnings}
    assert statuses["online_assets"] == ProviderStatus.unavailable


def test_soft_constraint_unresolved_is_warning(tmp_path):
    from asset_manager.models import ResolutionStatus as RS

    mgr, _lib = _build(tmp_path)
    write_manifest(str(tmp_path / "library"), [])
    req = request_dict(constraint_level="soft")
    result = mgr.resolve_assets([req])
    # §68：soft → skip/warning，整体不算 failed
    assert result.status == RS.resolved_with_warnings
    assert result.unresolved_requests == ["asset_req_sticker_01"]
