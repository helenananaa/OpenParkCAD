from __future__ import annotations

from dataclasses import replace

import pytest

from openparkcad.generator import generate_layout
from openparkcad.models import ParkingStall
from openparkcad.parking_motion_adapter import parking_motion_for_stall
from openparkcad.road_skeleton import make_movement, make_node, make_segment, make_skeleton
from openparkcad.road_transitions import build_occupancy
from openparkcad.road_traversal import validate_road_traversal
from openparkcad.road_traversal_models import parse_traversal_policy
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton, validate_skeleton_road_traversal
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons, read_ladder_config
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph
from tests.v0_5_parallel_ladder_support import load_case_site


def _enable_road(site, enabled: bool):
    constraints = dict(site.constraints or {})
    if enabled:
        constraints["road_traversal"] = {"enabled": True, "scope": "site_interior", "time_budget_seconds": 8.0}
    else:
        constraints.pop("road_traversal", None)
    return replace(site, constraints=constraints)


def _stall_on(aisle_id: str, polygon) -> ParkingStall:
    return ParkingStall(id="P-001", polygon=list(polygon), angle_degrees=90.0, served_by_aisle_id=aisle_id)


def test_nt11_one_way_loop_stays_unavailable() -> None:
    site = load_case_site("N-T01")
    with pytest.raises(ValueError, match="one_way_loop"):
        read_ladder_config(site, {"allow_one_way_loop": True})
    with pytest.raises(ValueError, match="one_way_loop"):
        generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "one_way_loop"})


def test_nt13_unrequested_keeps_current_validity() -> None:
    site = _enable_road(load_case_site("N-T01"), False)
    official = generate_layout(site)
    report = validate_road_traversal(official)
    assert report["requested"] is False
    assert report["status"] == "not_requested"
    assert report["valid"] is None


def test_nt09_graph_contact_but_envelope_collision_fails_traversal() -> None:
    site = load_case_site("N-T04")
    constraints = dict(site.constraints or {})
    constraints["road_traversal"] = {"enabled": True, "scope": "site_interior", "time_budget_seconds": 30.0}
    site = replace(site, constraints=constraints)
    n0 = make_node("N0", "entrance_port", (8.0, 0.0), heading_degrees=90.0, source_id="south-gate")
    n1 = make_node("N1", "junction", (8.0, 40.0), heading_degrees=90.0)
    n2 = make_node("N2", "terminal", (30.0, 40.0), heading_degrees=0.0)
    spine = make_segment("S-SPINE", "parking_aisle", "N0", "N1", ((8.0, 0.0), (8.0, 40.0)), 6.0, "two_way")
    cross = make_segment("S-CROSS", "cross_aisle", "N1", "N2", ((8.0, 40.0), (30.0, 40.0)), 6.0, "two_way")
    skeleton = make_skeleton(
        family="parallel_ladder",
        nodes=[n0, n1, n2],
        segments=[spine, cross],
        movements=[
            make_movement("M-L", "S-SPINE", "S-CROSS", "N1", "left"),
            make_movement("M-R", "S-CROSS", "S-SPINE", "N1", "right"),
        ],
        entrance_ids=("south-gate",),
        site=site,
        strict=False,
    )
    layout = layout_from_skeleton(
        site,
        skeleton,
        stalls=[_stall_on("S-CROSS", [(18.0, 43.0), (20.5, 43.0), (20.5, 48.0), (18.0, 48.0)])],
    )
    graph_obj = build_traffic_graph(layout)
    contact = any(
        ("S-SPINE" in (edge.from_node_id + edge.to_node_id) and "S-CROSS" in (edge.from_node_id + edge.to_node_id))
        for edge in graph_obj.edges
    )
    assert contact
    traversal = validate_skeleton_road_traversal(site, skeleton, layout.stalls)
    assert traversal["status"] == "failed"
    assert traversal.get("valid") is not True
    failures = traversal.get("failures") or []
    assert failures
    assert any(item.get("collision_object") or item.get("reason") for item in failures)


def test_nt12_one_unsupported_junction_does_not_pass_the_site() -> None:
    site = _enable_road(load_case_site("N-T01"), True)
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    assert result.candidates
    skeleton = result.candidates[0].skeleton
    crooked = make_movement("M-OBLIQUE", skeleton.segments[0].id, skeleton.segments[0].id, skeleton.nodes[0].id, "merge")
    from dataclasses import replace as _replace

    tainted = _replace(skeleton, movements=skeleton.movements + (crooked,))
    report = validate_skeleton_road_traversal(site, tainted, stalls=[])
    assert report["status"] == "unsupported"
    assert report["valid"] is not True
    assert report["reason"] == "junction_movement_unsupported"


def test_nt14_route_identity_includes_skeleton_and_vehicle() -> None:
    site = _enable_road(load_case_site("N-T01"), True)
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 2})
    assert result.candidates
    first = validate_skeleton_road_traversal(site, result.candidates[0].skeleton)
    second_site = replace(site, vehicle=replace(site.vehicle, min_turning_radius=7.5) if site.vehicle else site.vehicle)
    second = validate_skeleton_road_traversal(second_site, result.candidates[0].skeleton)
    assert "skeleton:" in str(first.get("layout_identity"))
    assert "vehicle:" in str(first.get("layout_identity"))
    assert first.get("layout_identity") != second.get("layout_identity")
    if len(result.candidates) > 1:
        other = validate_skeleton_road_traversal(site, result.candidates[1].skeleton)
        assert first.get("layout_identity") != other.get("layout_identity")


def test_nt10_both_ends_layout_has_connected_parking_aisles() -> None:
    site = _enable_road(load_case_site("N-T01"), True)
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    assert result.candidates
    layout = layout_from_skeleton(site, result.candidates[0].skeleton)
    parking = [aisle for aisle in layout.aisles if aisle.role == "branch"]
    crosses = [aisle for aisle in layout.aisles if aisle.role == "main"]
    assert len(parking) >= 2
    assert len(crosses) >= 1
    assert any(aisle.connected_aisle_ids for aisle in parking) or any(aisle.parent_aisle_id for aisle in parking)


def test_nt10_retained_stalls_have_graph_and_parking_motion() -> None:
    site = load_case_site("N-T01")
    result = generate_parallel_ladder_skeletons(
        site,
        config={"cross_aisle_policy": "both_ends", "max_skeletons": 1, "max_parallel_aisles": 2},
    )
    assert result.candidates
    catalog = build_and_select_ladder_modules(site, result.candidates[0].skeleton)
    assert catalog.selected_stalls
    assert len({stall.id for stall in catalog.selected_stalls}) == len(catalog.selected_stalls)
    seen_aisles: set[str] = set()
    sample: list = []
    for stall in catalog.selected_stalls:
        aisle_id = stall.served_by_aisle_id or ""
        if aisle_id in seen_aisles:
            continue
        seen_aisles.add(aisle_id)
        sample.append(stall)
        if len(sample) >= 2:
            break
    assert len(sample) >= 2
    layout = layout_from_skeleton(site, result.candidates[0].skeleton, sample)
    graph = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert graph["valid"] is True
    occupancy = build_occupancy(layout)
    policy = parse_traversal_policy(site)
    vehicle = site.vehicle
    assert vehicle is not None
    for stall in sample:
        motion = parking_motion_for_stall(layout, stall, occupancy, policy, vehicle)
        assert motion.valid is True
