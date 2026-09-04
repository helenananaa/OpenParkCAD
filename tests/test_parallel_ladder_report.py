from __future__ import annotations

from dataclasses import replace

from openparkcad.generator import generate_layout
from openparkcad.layout_search import layout_search_report
from tests.v0_5_parallel_ladder_support import load_case_site


def test_nt23_same_input_keeps_official_identity() -> None:
    site = load_case_site("N-T01")
    first = generate_layout(site)
    second = generate_layout(site)
    assert [stall.id for stall in first.stalls] == [stall.id for stall in second.stalls]
    assert [aisle.id for aisle in first.aisles] == [aisle.id for aisle in second.aisles]


def test_enabled_search_report_survives_layout_search_report() -> None:
    site = load_case_site("N-T01")
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": 4,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    optimization["promote_candidate_layout_preview"] = False
    layout = generate_layout(replace(site, optimization=optimization))
    payload = layout_search_report(layout)
    block = payload["road_network_search"]
    assert block["version"] == "road-network-search-1"
    assert block["requested"] is True
    assert block["executed"] is True
    assert "parallel_ladder" in block["families"]
