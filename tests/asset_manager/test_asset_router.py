"""ResolutionMode / SourcePolicy / SearchStrategy 路由（§8/§12-13）。"""

from __future__ import annotations

from editing_planner.models import AssetRequest

from asset_manager.models import AssetSource, ResolutionMode, SourcePolicy
from asset_manager.resolution import policy as policy_mod
from asset_manager.resolution.router import resolution_mode


def _req(**kw):
    base = dict(request_uid="r1", asset_type="sticker", media_type="image")
    base.update(kw)
    return AssetRequest(**base)


def test_explicit_resolution_mode_wins():
    assert resolution_mode(_req(resolution_mode="exact_reference")) == ResolutionMode.exact_reference
    assert resolution_mode(_req(
        resolution_mode="semantic_search", source_ref="x.png")) == ResolutionMode.semantic_search


def test_source_ref_implies_exact():
    assert resolution_mode(_req(source_ref="me.png")) == ResolutionMode.exact_reference


def test_user_provided_policy_implies_exact():
    assert resolution_mode(_req(source_policy="user_provided")) == ResolutionMode.exact_reference


def test_default_is_semantic():
    assert resolution_mode(_req()) == ResolutionMode.semantic_search


def test_policy_aliases():
    assert policy_mod.normalize_policy(_req(source_policy="any"))[0] == SourcePolicy.online_allowed
    assert policy_mod.normalize_policy(_req(source_policy="user_provided"))[0] == SourcePolicy.user_only
    assert policy_mod.normalize_policy(_req(source_policy="local_only"))[0] == SourcePolicy.local_only
    assert policy_mod.normalize_policy(_req(source_policy="user_first"))[0] == SourcePolicy.user_first


def test_generated_policy_rejected():
    policy, warning = policy_mod.normalize_policy(_req(source_policy="generated"))
    assert policy is None and warning == "generation_not_supported"


def test_offline_forces_local_only():
    policy, warning = policy_mod.normalize_policy(
        _req(source_policy="any"), offline=True)
    assert policy == SourcePolicy.local_only and warning == "offline_forced_local_only"


def test_provider_order_per_policy():
    assert policy_mod.provider_order(SourcePolicy.user_only) == [AssetSource.user]
    assert policy_mod.provider_order(SourcePolicy.local_first)[0] == AssetSource.local
    assert policy_mod.provider_order(SourcePolicy.online_allowed) == [
        AssetSource.user, AssetSource.local, AssetSource.online]


def test_search_strategy_defaults():
    assert policy_mod.search_strategy(_req(asset_type="sticker")) == "first_satisfactory"
    assert policy_mod.search_strategy(_req(asset_type="background")) == "best_available"
    assert policy_mod.search_strategy(
        _req(asset_type="background", search_strategy="first_satisfactory")) == "first_satisfactory"
