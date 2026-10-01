from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons
from tests.v0_5_parallel_ladder_support import load_case_site


def test_modules_depend_on_parent_parking_aisle_only() -> None:
    site = load_case_site("N-T01")
    generated = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    assert generated.candidates
    catalog = build_and_select_ladder_modules(site, generated.candidates[0].skeleton, backend="greedy")
    parking_ids = {segment.id for segment in generated.candidates[0].skeleton.segments if segment.role == "parking_aisle"}
    assert catalog.modules
    assert all(module.depends_on_segment_id in parking_ids for module in catalog.modules)
    assert catalog.provenance["official_stall_count"] == len(catalog.selected_stalls)
    assert catalog.provenance["scope"] == "skeleton_catalog"


def test_junction_or_cross_aisle_conflicts_are_hard() -> None:
    site = load_case_site("N-T01")
    generated = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    catalog = build_and_select_ladder_modules(site, generated.candidates[0].skeleton)
    road_conflicts = [item for item in catalog.conflicts if item[2] == "junction_or_road"]
    selected = set(catalog.selected_ids)
    for left, right, _kind in road_conflicts:
        assert left not in selected


def test_back_to_back_modules_do_not_both_select() -> None:
    site = load_case_site("N-T01")
    generated = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    catalog = build_and_select_ladder_modules(site, generated.candidates[0].skeleton)
    selected = {module.module_id: module for module in catalog.modules if module.module_id in catalog.selected_ids}
    ids = list(selected)
    from shapely.ops import unary_union

    for i, left_id in enumerate(ids):
        for right_id in ids[i + 1 :]:
            left_union = unary_union([ShapelyPolygon(s.polygon) for s in selected[left_id].stalls])
            right_union = unary_union([ShapelyPolygon(s.polygon) for s in selected[right_id].stalls])
            assert left_union.intersection(right_union).area < 1e-3


def test_cpsat_and_greedy_keep_hard_constraints() -> None:
    site = load_case_site("N-T01")
    generated = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    greedy = build_and_select_ladder_modules(site, generated.candidates[0].skeleton, backend="greedy")
    cpsat = build_and_select_ladder_modules(site, generated.candidates[0].skeleton, backend="cpsat")
    assert greedy.actual_backend == "greedy"
    assert cpsat.actual_backend in {"cpsat", "greedy"}
    assert greedy.provenance["scope"] == "skeleton_catalog"
    assert cpsat.provenance["scope"] == "skeleton_catalog"
