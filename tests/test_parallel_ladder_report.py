from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.layout_search import layout_search_report
from openparkcad.models import site_from_dict
from openparkcad.project_model import load_project, save_project, site_dict_from_layout
from openparkcad.project_service import ProjectService
from openparkcad.skeleton_identity import official_skeleton_mapping
from tests.v0_5_parallel_ladder_support import load_case_site


def test_nt23_same_input_keeps_official_identity() -> None:
    site = load_case_site("N-T01")
    first = generate_layout(site)
    second = generate_layout(site)
    assert [stall.id for stall in first.stalls] == [stall.id for stall in second.stalls]
    assert [aisle.id for aisle in first.aisles] == [aisle.id for aisle in second.aisles]


def test_nt23_project_save_reopen_keeps_skeleton_and_official_ids(tmp_path: Path) -> None:
    site = load_case_site("N-T01")
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": 8,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    optimization["promote_candidate_layout_preview"] = True
    site = replace(site, optimization=optimization)
    layout = generate_layout(site)
    stall_ids = [stall.id for stall in layout.stalls]
    aisle_ids = [aisle.id for aisle in layout.aisles]
    skeleton_id = (layout.site.metadata or {}).get("skeleton_id")
    service = ProjectService()
    service.accept_layout(layout, site=site_dict_from_layout(layout))
    service.lock_main_aisle(layout.aisles[0].id)
    path = tmp_path / "project.json"
    save_project(service.state, path)
    loaded = load_project(path)
    snapshot = loaded.accepted_layout or {}
    assert [item["id"] for item in snapshot.get("aisles") or []] == aisle_ids
    assert [item["id"] for item in snapshot.get("stalls") or []] == stall_ids
    for object_id in aisle_ids + stall_ids:
        assert object_id in loaded.object_ids
    assert loaded.revisions[-1].locks
    reopened_site = site_from_dict(loaded.accepted_site)
    again = generate_layout(reopened_site)
    assert [stall.id for stall in again.stalls] == stall_ids
    assert [aisle.id for aisle in again.aisles] == aisle_ids
    assert (again.site.metadata or {}).get("skeleton_id") == skeleton_id
    assert (loaded.accepted_site or {}).get("optimization", {}).get("road_network", {}).get("enabled") is True


def test_nt23_skeleton_mapping_covers_official_objects() -> None:
    site = load_case_site("N-T01")
    optimization = dict(site.optimization or {})
    optimization["road_network"] = {
        "enabled": True,
        "families": ["legacy", "parallel_ladder"],
        "max_skeletons": 8,
        "cross_aisle_policy": "both_ends",
        "allow_one_way_loop": False,
    }
    optimization["promote_candidate_layout_preview"] = True
    layout = generate_layout(replace(site, optimization=optimization))
    mapping = official_skeleton_mapping(layout)
    assert mapping["family"] == "parallel_ladder"
    assert mapping["generation_mode"] == "parallel_ladder"
    assert mapping["skeleton_id"]
    for aisle in layout.aisles:
        assert mapping["objects"][aisle.id]["kind"] == "aisle"
        assert mapping["objects"][aisle.id]["role"] == aisle.role
        assert mapping["objects"][aisle.id]["directionality"] == aisle.directionality
    for stall in layout.stalls:
        assert mapping["objects"][stall.id]["kind"] == "stall"
        assert mapping["objects"][stall.id]["source_segment_id"] == stall.served_by_aisle_id
    payload = layout_search_report(layout)
    assert payload["road_network_search"]["official_mapping"]["skeleton_id"] == mapping["skeleton_id"]


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
