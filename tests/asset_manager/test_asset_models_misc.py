"""模型契约 / dedup / ranking / HTTP adapter 兜底（§26-33/§51/§86 19-22）。"""

from __future__ import annotations

import json

from editing_planner.models import AssetRequest
from gesture_intent.models import model_dump, model_validate

from asset_manager.candidates.dedup import dedup_candidates
from asset_manager.candidates.rank import rank_candidates
from asset_manager.models import (
    AssetCandidate,
    AssetResolutionResult,
    AssetSource,
    SemanticMetadata,
    SemanticQuery,
    TechnicalMetadata,
)
from asset_manager.providers.online import HttpJsonAdapter


def _cand(uid, **kw):
    base = dict(
        candidate_uid=uid, provider_id="t", source_type=AssetSource.local,
        asset_type="sticker", media_type="image")
    base.update(kw)
    return AssetCandidate(**base)


def test_request_new_fields_optional():
    """模块三 AssetRequest 补字段向后兼容：旧 JSON 仍能解析。"""

    old = {"request_uid": "r", "asset_type": "sticker", "media_type": "image"}
    req = model_validate(AssetRequest, old)
    assert req.version == 1 and req.resolution_mode is None
    assert req.source_ref is None and req.search_strategy is None


def test_dedup_by_uri_and_hash():
    a = _cand("a", original_uri="https://x/1.png")
    b = _cand("b", original_uri="https://x/1.png")  # 同 uri
    c = _cand("c", provenance={"content_hash": "abc"})
    d = _cand("d", provenance={"content_hash": "abc"})  # 同内容
    e = _cand("e", original_uri="https://x/2.png")
    kept, rejected = dedup_candidates([a, b, c, d, e])
    assert [c.candidate_uid for c in kept] == ["a", "c", "e"]
    assert {r.reason for r in rejected} == {"dedup_exact"}


def test_dedup_perceptual():
    a = _cand("a", provenance={"perceptual_hash": "ff00ff00ff00ff00"})
    b = _cand("b", provenance={"perceptual_hash": "ff00ff00ff00ff01"})  # 差 1 bit
    c = _cand("c", provenance={"perceptual_hash": "0000111122223333"})
    kept, rejected = dedup_candidates([a, b, c])
    assert len(kept) == 2
    assert rejected[0].reason == "dedup_perceptual"


def test_ranking_semantic_beats_retrieval():
    """§27：provider retrieval_score 只是弱特征，semantic 主导。"""

    from asset_manager.models import RetrievalMetadata

    good = _cand("good", semantic_metadata=SemanticMetadata(
        object="heart", attributes=["pink"], style=["cute"]))
    bad = _cand("bad", semantic_metadata=SemanticMetadata(
        object="star", attributes=["gold"], style=["realistic"]),
        retrieval_metadata=RetrievalMetadata(retrieval_score=1.0))
    req = AssetRequest(request_uid="r", asset_type="sticker",
                     media_type="image", semantic_query="可爱的粉色爱心")
    ranked = rank_candidates(
        [bad, good], req,
        SemanticQuery(raw="可爱的粉色爱心", object="heart",
                      attributes=["pink"], style=["cute"]))
    assert ranked[0].candidate_uid == "good"


def test_resolution_result_serialization_roundtrip():
    result = AssetResolutionResult()
    raw = model_dump(result)
    back = model_validate(AssetResolutionResult, raw)
    assert back.status == result.status


def test_http_json_adapter_search_and_download(tmp_path, monkeypatch):
    """通用 HTTP adapter：JSON 行解析 + 下载落盘（不连真实网络）。"""

    import io

    payload = json.dumps({"results": [
        {"id": "x1", "title": "heart", "tags": ["heart"], "asset_type": "sticker",
         "original_url": "https://cdn/x.png", "width": 64, "height": 64,
         "license": {"type": "cc0"}},
    ]}).encode()

    class Resp:
        def __init__(self, data): self._d = data
        def read(self, *a): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if "search" in url:
            return Resp(payload)
        return Resp(b"binarypng")

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    adapter = HttpJsonAdapter({"adapter_id": "a1", "endpoint": "https://api/search"})
    rows = adapter.search("cute heart", [], 5)
    assert rows and rows[0]["id"] == "x1"
    dest = adapter.download("https://cdn/x.png", str(tmp_path))
    assert dest.endswith("x.png")


def test_http_adapter_unconfigured_returns_empty():
    adapter = HttpJsonAdapter({})
    assert adapter.search("x", [], 5) == []
