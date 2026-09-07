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
    bare_opt = dict(site.optimization or {})
    bare_opt.pop("road_network", None)
    base = generate_layout(replace(site, optimization=bare_opt))
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
    assert layout.generation_mode != "parallel_ladder"


def test_illegal_family_fail_closes() -> None:
    site = _with_network(load_case_site("N-T01"), enabled=True, families=["legacy", "maze"])
    with pytest.raises(ValueError, match="unknown road_network family"):
        generate_layout(site)


def test_illegal_numeric_road_network_fail_closes_in_generate() -> None:
    site = load_case_site("N-T01")
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": False,
    }
    with pytest.raises(ValueError, match="positive integer"):
        generate_layout(replace(site, optimization=optimization))


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
        assert item.get("family") == "parallel_ladder"
        assert item.get("source") == "parallel_ladder"
        if item.get("incomplete"):
            assert item.get("failure_class") == "budget_exhausted"
            continue
        assert "elapsed_seconds" in item
        assert item.get("selector", {}).get("requested")
        assert item.get("selector", {}).get("actual")
        assert "module_count" in (item.get("modules") or {})
        assert "graph" in (item.get("gates") or {})
        assert "road_traversal" in (item.get("gates") or {})


def test_nt17_complete_higher_score_ladder_promotes() -> None:
    site = load_case_site("N-T01")
    baseline = generate_layout(_with_network(site, enabled=False))
    promoted = generate_layout(_with_network(site, enabled=True, promote=True))
    report = promoted.layout_search["road_network_search"]
    assert report["counts"]["verified"] >= 1
    assert any(item.get("selected_reason") == "promoted" for item in report["skeletons"])
    assert promoted.generation_mode == "parallel_ladder"
    assert promoted.stall_count > baseline.stall_count
    assert _geometry_key(promoted) != _geometry_key(baseline)


def test_nt19_budget_exhausted_incomplete_cannot_promote() -> None:
    site = load_case_site("N-T01")
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": 8,
        "max_full_evaluations": 1,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    optimization["promote_candidate_layout_preview"] = True
    layout = generate_layout(replace(site, optimization=optimization))
    report = layout.layout_search["road_network_search"]
    assert report["budget"]["exhausted"] is True
    assert report["counts"]["incomplete"] >= 1
    for item in report["skeletons"]:
        if item.get("selected_reason") == "budget_exhausted":
            assert item.get("incomplete") is True
            assert item.get("valid") is False
    assert layout.generation_mode != "parallel_ladder" or all(
        item.get("selected_reason") != "budget_exhausted" or item.get("skeleton_id") != (layout.site.metadata or {}).get("skeleton_id")
        for item in report["skeletons"]
    )


def test_nt20_invalid_baseline_promotion_off_refuses_official() -> None:
    site = _blocked_spine_site(promote=False)
    layout = generate_layout(site)
    report = (layout.layout_search or {}).get("road_network_search") or {}
    assert report.get("requested") is True
    assert layout.generation_mode != "parallel_ladder"


def test_nt21_invalid_baseline_promotion_on_may_recover() -> None:
    site = _blocked_spine_site(promote=True)
    layout = generate_layout(site)
    report = layout.layout_search["road_network_search"]
    assert report["requested"] is True
    assert report["executed"] is True
    if report["counts"]["verified"] >= 1:
        assert layout.generation_mode == "parallel_ladder"
        assert any(item.get("selected_reason") == "recovered_feasible" for item in report["skeletons"])
        assert layout.stall_count > 0
    else:
        assert layout.generation_mode != "parallel_ladder"


def _blocked_spine_site(*, promote: bool):
    site = load_case_site("N-T01")
    obstacles = list(site.obstacles)
    obstacles.append(((26.0, 10.0), (30.0, 10.0), (30.0, 40.0), (26.0, 40.0)))
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": 8,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    optimization["promote_candidate_layout_preview"] = promote
    return replace(site, obstacles=obstacles, optimization=optimization)
