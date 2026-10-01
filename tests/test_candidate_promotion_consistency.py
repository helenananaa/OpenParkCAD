import json
from dataclasses import replace
from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict


def test_promoted_layout_rebuilds_official_snapshot_and_provenance():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    layout = generate_layout(site_from_dict(data))

    assert layout.generation_mode == "candidate_layout_promoted"

    official_aisle_ids = {aisle.id for aisle in layout.aisles}
    official_stall_ids = {stall.id for stall in layout.stalls}
    assert all(stall_id.startswith("P-") for stall_id in official_stall_ids)
    assert all(not stall_id.startswith("PL-STALL-") for stall_id in official_stall_ids)
    selected_aisle_sources = {
        str(item.metadata.get("source_id"))
        for item in layout.candidate_objects
        if item.kind == "aisle" and item.status == "selected"
    }
    selected_stall_sources = {
        str(item.metadata.get("source_id"))
        for item in layout.candidate_objects
        if item.kind == "stall" and item.status == "selected"
    }

    assert official_aisle_ids <= selected_aisle_sources
    assert official_stall_ids <= selected_stall_sources
    assert layout.candidate_network_preview["status"] == "promoted_to_official"
    assert layout.candidate_layout_preview["status"] == "promoted_to_official"
    assert layout.candidate_layout_preview["validation"]["status"] == "official"
    assert layout.candidate_layout_preview["validation"]["traffic_graph"] == layout.graph_validation
    assert layout.candidate_layout_preview["validation"]["maneuver_validation"] == layout.maneuver_validation
    assert layout.candidate_layout_preview["validation"]["site_constraint_validation"] == layout.site_constraint_validation
    assert layout.candidate_layout_preview["validation"]["engineering_validation"] == layout.engineering_validation
    assert layout.candidate_layout_preview["validation"]["operational_quality"] == layout.operational_quality
    assert layout.candidate_layout_preview["score"] == layout.score

    promotion = layout.candidate_layout_promotion
    assert set(promotion["official_aisle_ids"]) == official_aisle_ids
    assert set(promotion["official_stall_ids"]) == official_stall_ids
    assert set(promotion["candidate_to_official_aisle_ids"].values()) == official_aisle_ids

    assert all(branch["id"] in official_aisle_ids and branch["source_id"].startswith("A-BRANCH-") for branch in layout.selected_branches)
    assert all(connector["id"] in official_aisle_ids for connector in layout.selected_connectors)
    assert all(set(connector["connects"]) <= official_aisle_ids for connector in layout.selected_connectors)
    assert set(layout.graph_validation["reachable_aisles"]) <= official_aisle_ids


def test_empty_candidate_preview_cannot_be_promoted():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    site = replace(
        site,
        margin=100.0,
        optimization={**site.optimization, "promote_candidate_layout_preview": True},
    )

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.aisles == []
    assert layout.generation_mode != "candidate_layout_promoted"
    assert layout.candidate_layout_promotion["status"] == "rejected"
    assert layout.candidate_layout_promotion["blockers"] == [
        "preview_layout_has_no_aisles",
        "preview_layout_has_no_stalls",
        "preview_validation_failed",
    ]
