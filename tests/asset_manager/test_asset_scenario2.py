"""MVP 验收场景二（§81）：背景 best_available 多源合并排序。

anime summer beach background 9:16 → User+Local+Online 候选统一标准化
→ Hard Filter → Semantic + Aspect/Visual/Usage Ranking → 选中的在线
素材下载并注册到 Project Registry。
"""

from __future__ import annotations

import os

from asset_manager import AssetManager, ResolutionStatus
from asset_fixtures import (
    FakeOnlineAdapter,
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


def test_scenario2_best_available_merges_sources(tmp_path):
    lib = tmp_path / "library"
    # 本地：1:1 比例沙滩
    make_png(str(lib / "beach_sq.png"), size=(512, 512), color=(30, 160, 220, 255))
    write_manifest(str(lib), [
        lib_entry("lib_beach_sq", "beach_sq.png", asset_type="background",
                  obj="beach", style=["anime", "summer"],
                  technical={"width": 512, "height": 512}),
    ])
    # 在线：9:16 竖屏沙滩（更贴合 canvas）
    tall = tmp_path / "beach_tall.png"
    make_png(str(tall), size=(540, 960), color=(20, 140, 200, 255))
    adapter = FakeOnlineAdapter(rows=[{
        "id": "bg1", "title": "anime summer beach background",
        "tags": ["beach", "summer", "anime"], "asset_type": "background",
        "original_url": "https://x/beach_tall.png", "width": 540, "height": 960,
        "license": {"type": "cc0"},
    }], files={"https://x/beach_tall.png": str(tall)})

    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p2", online_adapters=[adapter])
    req = request_dict(
        request_uid="asset_req_background_01",
        asset_type="background",
        semantic_query="anime summer beach background",
        technical_requirements={"min_width": 480},
        usage_context={"canvas_aspect_ratio": 0.5625},  # 9:16
        search_strategy="best_available",
    )
    result = mgr.resolve_assets([req])

    assert result.status in (
        ResolutionStatus.resolved, ResolutionStatus.resolved_with_warnings)
    assert adapter.search_calls == 1  # best_available 查询了在线源
    record = mgr.registry.get(result.bindings[0].asset_uid)
    # 9:16 在线背景应胜过 1:1 本地背景
    assert record.source_type.value == "online"
    assert "downloaded" in record.local_uri
    assert record.technical_metadata.width == 540


def test_scenario2_local_only_policy_skips_online(tmp_path):
    lib = tmp_path / "library"
    make_png(str(lib / "beach_sq.png"), size=(512, 512))
    write_manifest(str(lib), [
        lib_entry("lib_beach_sq", "beach_sq.png", asset_type="background",
                  obj="beach", style=["summer"],
                  technical={"width": 512, "height": 512}),
    ])
    adapter = FakeOnlineAdapter(rows=[{
        "id": "bg1", "title": "beach", "asset_type": "background",
        "original_url": "https://x/b.png", "license": {"type": "cc0"},
    }], files={"https://x/b.png": str(lib / "beach_sq.png")})
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p2", online_adapters=[adapter])
    req = request_dict(
        request_uid="asset_req_background_01", asset_type="background",
        semantic_query="summer beach", technical_requirements={},
        source_policy="local_only")
    result = mgr.resolve_assets([req])
    assert adapter.search_calls == 0  # local_only 不碰在线源
    assert result.bindings
