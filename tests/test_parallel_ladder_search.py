from __future__ import annotations

from dataclasses import replace

import pytest

from openparkcad.generator import generate_layout
from tests.test_layout_search_integration import _geometry_key
from tests.v0_5_parallel_ladder_support import load_case_site


def _with_network(site, *, enabled: bool, families=None, promote: bool | None = None):
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": enabled,
        "families": families or ["legacy", "parallel_ladder"],
        "max_skeletons": 8,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    if promote is not None:
        optimization["promote_candidate_layout_preview"] = promote
    return replace(site, optimization=optimization)


def test_nt15_family_off_matches_current_official() -> None:
    site = load_case_site("N-T01")
    base = generate_layout(site)
    off = generate_layout(_with_network(site, enabled=False, promote=True))
    assert _geometry_key(base) == _geometry_key(off)
    assert "road_network_search" not in (off.layout_search or {})


def test_nt16_family_on_promotion_off_keeps_baseline() -> None:
    site = _with_network(load_case_site("N-T01"), enabled=True, promote=False)
    layout = generate_layout(site)
    report = (layout.layout_search or {}).get("road_network_search") or {}
    assert report.get("requested") is True
    assert report.get("executed") is True
    assert "parallel_ladder" in report.get("families", [])
    assert layout.generation_mode != "parallel_ladder_shadow"


def test_illegal_family_fail_closes() -> None:
    site = _with_network(load_case_site("N-T01"), enabled=True, families=["legacy", "maze"])
    with pytest.raises(ValueError, match="unknown road_network family"):
        generate_layout(site)


def test_nt22_report_block_has_versioned_counts() -> None:
    site = _with_network(load_case_site("N-T01"), enabled=True, promote=False)
    layout = generate_layout(site)
    report = layout.layout_search["road_network_search"]
    assert report["version"] == "road-network-search-1"
    assert "generated" in report["counts"]
    assert "retained" in report["counts"]
    for item in report["skeletons"]:
        assert "prefilter_score" in item
        assert "score_total" in item or item.get("valid") is False
