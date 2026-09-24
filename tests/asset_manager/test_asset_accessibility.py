"""无障碍边界（§40/§71-72/§86-31）：坐姿只改 usage 偏好，不改语义主题。"""

from __future__ import annotations

from asset_manager import AssetManager
from asset_fixtures import (
    lib_entry,
    make_png,
    request_dict,
    write_manifest,
)


def test_seated_context_does_not_alter_semantic_query(tmp_path):
    """坐轮椅不等于往 Query 里塞 wheelchair/disabled（§40/§71）。"""

    lib = tmp_path / "library"
    make_png(str(lib / "b.png"), size=(540, 960))
    write_manifest(str(lib), [
        lib_entry("lib_beach", "b.png", asset_type="background",
                  obj="beach", style=["summer"],
                  technical={"width": 540, "height": 960}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p9")
    req = request_dict(
        request_uid="asset_req_background_01",
        asset_type="background",
        semantic_query="夏日海边",
        technical_requirements={},
        usage_context={
            "subject_region": "center_lower",
            "preferred_background_clutter": "low",
            "canvas_aspect_ratio": 0.5625,
            "posture": "seated",
        },
    )
    result = mgr.resolve_assets([req])
    assert result.bindings
    query = result.search_records[0].queries["local_library"]
    assert "wheelchair" not in str(query).lower()
    assert "disabled" not in str(query).lower()
    assert "beach" in query["canonical_terms"] or "summer" in query["canonical_terms"]


def test_compact_shape_usage_preference(tmp_path):
    """§72：preferred_compact_shape 提升紧凑高占比贴纸的 Usage Score。"""

    lib = tmp_path / "library"
    make_png(str(lib / "tiny.png"), size=(64, 64))   # 小且紧凑
    make_png(str(lib / "wide.png"), size=(256, 64))  # 宽高比 4:1 不紧凑
    write_manifest(str(lib), [
        lib_entry("lib_tiny", "tiny.png", obj="star", attributes=["gold"],
                  technical={"has_alpha": True}),
        lib_entry("lib_wide", "wide.png", obj="star", attributes=["gold"],
                  technical={"has_alpha": True}),
    ])
    mgr = AssetManager(
        workspace_root=str(tmp_path / "ws"), library_root=str(lib),
        project_id="p9")
    req = request_dict(
        semantic_query="金色星星",
        usage_context={"preferred_compact_shape": True},
        technical_requirements={},
    )
    result = mgr.resolve_assets([req])
    top = result.search_records[0].ranked_candidates[0]
    assert "tiny" in top.candidate_uid
    assert top.breakdown["usage"] > 0.5
