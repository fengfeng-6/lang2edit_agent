"""MVP 验收场景四/五（§83-84）：音乐的显式需求门槛与依赖请求。

场景四：没有音乐 AssetRequest → 整条音乐链路不执行（不搜、不推荐）。
场景五：显式音乐需求 → User→Local→授权在线 → Top-K → 缺 BPM 时
产出 audio_analysis dependency_request（§47），不直接调 Module 2。
"""

from __future__ import annotations

from asset_manager import AssetManager, ResolutionStatus
from asset_fixtures import (
    FakeOnlineAdapter,
    lib_entry,
    make_png,
    make_wav,
    request_dict,
    write_manifest,
)


def test_scenario4_no_music_request_no_music_search(tmp_path):
    lib = tmp_path / "library"
    make_wav(str(lib / "upbeat.wav"), seconds=2.0)
    write_manifest(str(lib), [
        lib_entry("lib_bgm_upbeat", "upbeat.wav", asset_type="music",
                  media_type="audio", style=["upbeat", "summer"],
                  technical={"duration": 2.0, "bpm": 128}),
    ])
    adapter = FakeOnlineAdapter(authorized_for_music=True)
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p4", online_adapters=[adapter])

    # 没有任何 AssetRequest（原视频无音乐但用户没提需求）→ 什么都不做
    result = mgr.resolve_assets([])
    assert result.status == ResolutionStatus.resolved
    assert not result.search_records and not result.bindings
    assert adapter.search_calls == 0

    # 贴纸请求不会召回音乐条目（asset_type 过滤）
    make_png(str(tmp_path / "h.png"))
    write_manifest(str(lib), [
        lib_entry("lib_bgm_upbeat", "upbeat.wav", asset_type="music",
                  media_type="audio", technical={"duration": 2.0}),
        lib_entry("lib_heart", "../h.png", obj="heart",
                  technical={"has_alpha": True, "width": 64, "height": 64}),
    ])
    result = mgr.resolve_assets([request_dict()])
    assert result.bindings
    record = mgr.registry.get(result.bindings[0].asset_uid)
    assert record.asset_type == "sticker"


def test_scenario5_music_request_with_bpm(tmp_path):
    lib = tmp_path / "library"
    make_wav(str(lib / "upbeat.wav"), seconds=20.0)
    make_wav(str(lib / "calm.wav"), seconds=20.0)
    write_manifest(str(lib), [
        lib_entry("lib_bgm_upbeat", "upbeat.wav", asset_type="music",
                  media_type="audio", style=["upbeat", "summer"],
                  technical={"duration": 30.0, "bpm": 128}),
        lib_entry("lib_bgm_calm", "calm.wav", asset_type="music",
                  media_type="audio", style=["calm"],
                  technical={"duration": 30.0, "bpm": 80}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p4")
    req = request_dict(
        request_uid="asset_req_music_01",
        asset_type="music", media_type="audio",
        semantic_query="轻快 夏日 音乐",
        technical_requirements={"bpm_range": [115, 140], "min_duration": 15},
        usage_context={"mood": ["upbeat"]},
    )
    result = mgr.resolve_assets([req])
    assert result.bindings
    record = mgr.registry.get(result.bindings[0].asset_uid)
    assert record.asset_type == "music"
    assert record.technical_metadata.bpm == 128  # 轻快夏日 → upbeat 胜出
    # manifest 自带 bpm/duration → 无需依赖请求
    assert not result.dependency_requests


def test_scenario5_music_missing_bpm_emits_dependency(tmp_path):
    lib = tmp_path / "library"
    make_wav(str(lib / "unknown.wav"), seconds=2.0)
    write_manifest(str(lib), [
        lib_entry("lib_bgm_unknown", "unknown.wav", asset_type="music",
                  media_type="audio", style=["upbeat"], technical={}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p4")
    req = request_dict(
        request_uid="asset_req_music_01",
        asset_type="music", media_type="audio",
        semantic_query="轻快音乐",
        technical_requirements={"bpm_range": [115, 140]},
    )
    result = mgr.resolve_assets([req])
    deps = result.dependency_requests
    assert deps and deps[0].type == "audio_analysis"
    assert "bpm" in deps[0].required_analyses
    assert deps[0].request_ref == "asset_req_music_01"


def test_online_music_only_authorized(tmp_path):
    lib = tmp_path / "library"
    write_manifest(str(lib), [])
    adapter = FakeOnlineAdapter(
        authorized_for_music=False,
        asset_types=["music"],
        rows=[{"id": "m1", "title": "song", "asset_type": "music",
               "original_url": "https://x/s.mp3", "license": {"type": "cc0"}}],
    )
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p4", online_adapters=[adapter])
    req = request_dict(
        request_uid="asset_req_music_01", asset_type="music",
        media_type="audio", semantic_query="upbeat",
        technical_requirements={})
    result = mgr.resolve_assets([req])
    # §46：未授权在线音乐源不得返回音乐候选
    assert adapter.search_calls == 0
    assert result.unresolved_requests == ["asset_req_music_01"]
