from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from openparkcad.generator import collect_layout_candidate_contexts, generate_layout, generate_layout_legacy
from openparkcad.layout_candidates import context_from_layout
from openparkcad.models import ParkingAisle, site_from_dict
from openparkcad.road_skeleton import turn_allowed
from openparkcad.through_corridor import build_through_corridor_layout
from openparkcad.topology_generators.legacy_adapter import adapt_layout_to_skeleton
from tests.test_layout_candidate_context import _straight_offset_site
from tests.test_layout_search_integration import _geometry_key, _simple_site
from tests.test_main_aisle_dogleg import _blocked_spine_site, _staggered_multi_obstacle_site


def _adapt(layout):
    result = adapt_layout_to_skeleton(layout, strict=False)
    assert result.skeleton is not None
    return result


def _through_layout():
    payload = json.loads(Path("examples/dual_entrance_site.json").read_text(encoding="utf-8"))
    payload.setdefault("optimization", {})["enable_through_corridor"] = True
    built = build_through_corridor_layout(site_from_dict(payload))
    assert built is not None
    return built


def test_straight_offset_dogleg_multi_jog_and_through_corridor_adapt() -> None:
    straight = generate_layout(_simple_site())
    offset = next(
        item.template_layout
        for item in collect_layout_candidate_contexts(_straight_offset_site())
        if item.template_layout.aisles and item.source.get("aisle_lateral_offset") == 6.0
    )
    dogleg = generate_layout(_blocked_spine_site())
    multi_jog = generate_layout(_staggered_multi_obstacle_site())
    through = _through_layout()

    assert dogleg.generation_mode == "phase1_main_aisle_dogleg"
    assert multi_jog.generation_mode == "phase1_main_aisle_multi_jog"
    assert through.generation_mode == "phase1_through_corridor"

    families = {
        "straight": straight,
        "offset": offset,
        "dogleg": dogleg,
        "multi_jog": multi_jog,
        "through_corridor": through,
    }
    for name, layout in families.items():
        adapted = _adapt(layout)
        assert adapted.skeleton.family == "legacy"
        for aisle in layout.aisles:
            assert aisle.id in adapted.aisle_to_segment
            segment_id = adapted.aisle_to_segment[aisle.id]
            assert adapted.segment_to_aisle[segment_id] == aisle.id
        assert adapted.comparison["all_within_tolerance"] is True, (name, adapted.comparison["aisles"])
        roles = {segment.role for segment in adapted.skeleton.segments}
        if name == "dogleg":
            assert "jog" in roles
        if name == "through_corridor":
            assert "exit" in roles


def test_default_official_report_has_no_skeleton_block() -> None:
    layout = generate_layout(_simple_site())
    search = layout.layout_search or {}
    assert "skeleton_id" not in search
    assert "road_network_search" not in search
    assert "skeletons" not in search


def test_top_k_one_official_geometry_matches_legacy() -> None:
    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 1, "refinement_budget_seconds": 10.0},
        promote_candidate_layout_preview=True,
    )
    assert _geometry_key(generate_layout_legacy(site)) == _geometry_key(generate_layout(site))


def test_adapter_does_not_invent_movements_for_undeclared_overlap() -> None:
    layout = generate_layout(_simple_site())
    overlapping = [
        ParkingAisle(
            id="A-MAIN",
            polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
            angle_degrees=90.0,
            role="main",
            connected_to_entrance_id="main",
        ),
        ParkingAisle(
            id="A-GHOST",
            polygon=[(10, 8), (16, 8), (16, 18), (10, 18)],
            angle_degrees=90.0,
            role="branch",
        ),
    ]
    adapted = adapt_layout_to_skeleton(replace(layout, aisles=overlapping), strict=False)
    assert adapted.skeleton is not None
    assert turn_allowed(adapted.skeleton, "seg-A-MAIN", "seg-A-GHOST") is False
    pair_moves = [
        movement
        for movement in adapted.skeleton.movements
        if set((movement.from_segment_id, movement.to_segment_id)) == {"seg-A-MAIN", "seg-A-GHOST"}
    ]
    assert pair_moves == []


def test_multi_spine_context_carries_shadow_skeleton_without_changing_ids() -> None:
    layout = generate_layout(_simple_site())
    context = context_from_layout(layout)
    again = context_from_layout(layout)
    assert context.candidate_id == again.candidate_id
    assert context.spine_id == again.spine_id
    assert context.skeleton is not None
    assert context.skeleton.family == "legacy"
    assert context.template_layout.aisles[0].polygon == layout.aisles[0].polygon
