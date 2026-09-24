"""Hard Filter / Inspect（§29-31/§41-42/§86 8-12,17-18）。"""

from __future__ import annotations

import os

from editing_planner.models import AssetRequest

from asset_manager.candidates.filter import hard_filter
from asset_manager.candidates.inspect import Inspector, sniff_format
from asset_manager.models import (
    AssetCandidate,
    AssetSource,
    LicenseMetadata,
    SemanticMetadata,
    TechnicalMetadata,
)
from asset_fixtures import make_png, make_wav


def _cand(**kw):
    base = dict(
        candidate_uid="c1", provider_id="t", source_type=AssetSource.local,
        asset_type="sticker", media_type="image",
    )
    base.update(kw)
    return AssetCandidate(**base)


def _req(**kw):
    base = dict(request_uid="r1", asset_type="sticker", media_type="image")
    base.update(kw)
    return AssetRequest(**base)


def test_filter_alpha_required_rejects_opaque():
    cands = [_cand(candidate_uid="a",
                   technical_metadata=TechnicalMetadata(has_alpha=False))]
    passed, rejected = hard_filter(
        cands, _req(technical_requirements={"has_alpha": True}))
    assert not passed and rejected[0].detail == "alpha_required"


def test_filter_min_size():
    cands = [_cand(candidate_uid="a",
                   technical_metadata=TechnicalMetadata(width=64, height=64))]
    passed, rejected = hard_filter(
        cands, _req(technical_requirements={"min_width": 512}))
    assert not passed and "min_width" in rejected[0].detail


def test_filter_license_restricted_blocked():
    cands = [_cand(candidate_uid="a",
                   license_metadata=LicenseMetadata(type="restricted"))]
    passed, rejected = hard_filter(cands, _req())
    assert not passed and "license" in rejected[0].detail


def test_filter_background_must_be_static_image():
    cands = [_cand(candidate_uid="a", asset_type="background",
                   media_type="video")]
    passed, rejected = hard_filter(
        cands, _req(asset_type="background", media_type="video"))
    assert not passed and "background_not_static_image" in rejected[0].detail


def test_filter_undecodable_rejected():
    cands = [_cand(candidate_uid="a",
                   provenance={"inspected": True, "decodable": False})]
    passed, rejected = hard_filter(cands, _req())
    assert not passed and "not_decodable" in rejected[0].detail


def test_inspect_real_alpha_check(tmp_path):
    """§42：不看文件名/描述，真实检查 Alpha 通道。"""

    inspector = Inspector()
    opaque = make_png(str(tmp_path / "fake_alpha.png"), opaque=True)
    result = inspector.inspect_file(opaque)
    assert result.integrity.decodable
    assert result.technical.has_alpha is False  # 名为 png 但实无透明
    assert result.technical.alpha_ratio == 0.0

    real = make_png(str(tmp_path / "real.png"))
    result = inspector.inspect_file(real)
    assert result.technical.has_alpha is True
    assert 0.3 < result.technical.alpha_ratio < 1.0
    assert 0.1 < result.technical.foreground_occupancy < 0.6
    assert result.integrity.perceptual_hash  # §51 dHash


def test_inspect_corrupt_file(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a real png but long enough to sniff")
    result = Inspector().inspect_file(str(bad))
    assert result.integrity.decodable is False


def test_inspect_wav_duration(tmp_path):
    wav = make_wav(str(tmp_path / "a.wav"), seconds=1.5, rate=8000)
    result = Inspector().inspect_file(wav)
    assert result.integrity.decodable
    assert abs((result.technical.duration or 0) - 1.5) < 0.05


def test_sniff_format_magic(tmp_path):
    png = make_png(str(tmp_path / "x.bin"))
    assert sniff_format(png) == "png"
    wav = make_wav(str(tmp_path / "y.bin"))
    assert sniff_format(wav) == "wav"
