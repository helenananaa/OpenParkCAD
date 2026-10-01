from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import time

import pytest
from jsonschema import Draft202012Validator
from shapely.geometry import Polygon

from openparkcad.generator import _layout_valid
from openparkcad.ladder_repair import repair_ladder_layout
from openparkcad.layout_locks import LayoutLock, site_with_generation_locks
from openparkcad.models import LayoutResult, site_from_dict
from openparkcad.project_model import save_project, load_project
from openparkcad.project_service import ProjectService
from openparkcad.review_bundle import build_review_bundle
from openparkcad.road_network_config import SpacingSearchConfig, parse_road_network_mapping
from openparkcad.road_network_search import apply_road_network_search
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.ladder_spacing import expand_spacing_candidates
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site():
    return site_from_dict(json.loads((ROOT / "tests/fixtures/spacing_search/rectangle.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def baseline(site):
    skeleton = generate_parallel_ladder_skeletons(site).candidates[0].skeleton
    catalog = build_and_select_ladder_modules(site, skeleton, backend="cpsat")
    raw = layout_from_skeleton(site, skeleton, catalog.selected_stalls)
    result, report = repair_ladder_layout(raw, parse_road_network_mapping(site.optimization["road_network"]).repair, backend="cpsat")
    assert report["accepted"]
    return result


@pytest.fixture(scope="module")
def spaced(site):
    return apply_road_network_search(site, LayoutResult(site=site, stalls=[]))


def test_spacing_improves_verified_capacity_without_shrinking_dimensions(site, baseline, spaced):
    assert baseline.stall_count == 12 and spaced.stall_count == 24
    assert _layout_valid(spaced)
    assert spaced.road_traversal_validation["stall_coverage"] == 24
    assert spaced.site.vehicle == site.vehicle and spaced.site.stall == site.stall
    assert spaced.aisles != baseline.aisles
    assert all(Polygon(s.polygon).area == pytest.approx(site.stall.width * site.stall.length) for s in spaced.stalls)
    report = spaced.layout_search["road_network_search"]
    assert report["spacing_search"]["stopped_after_improvement"]
    assert report["counts"]["fully_evaluated"] == 2
    assert report["counts"]["not_evaluated"] == 2
    assert report["counts"]["incomplete"] == 0
    assert not report["budget"]["exhausted"]


def test_placement_variants_have_distinct_ids_but_geometry_skeleton_ids_stay_stable(site):
    generation = generate_parallel_ladder_skeletons(site)
    expanded, report = expand_spacing_candidates(site, generation, SpacingSearchConfig(enabled=True))
    assert len(expanded.candidates) == 4
    assert len({c.candidate_id or c.skeleton.skeleton_id for c in expanded.candidates}) == 4
    assert len({c.skeleton.skeleton_id for c in expanded.candidates}) == 2
    assert expanded.candidates[0] is generation.candidates[0]
    assert all(segment.width == site.aisle_width for c in expanded.candidates for segment in c.skeleton.segments)
    assert report["inter_aisle_gaps_m"] == [0.0, .65]
    again, _ = expand_spacing_candidates(site, generation, SpacingSearchConfig(enabled=True))
    assert [c.candidate_id for c in expanded.candidates] == [c.candidate_id for c in again.candidates]


def test_oversized_spacing_is_rejected_instead_of_narrowing_roads(site):
    generation = generate_parallel_ladder_skeletons(site)
    expanded, report = expand_spacing_candidates(site, generation,
        SpacingSearchConfig(enabled=True, inter_aisle_gaps_m=(0., 1000.), stall_gaps_m=(0.,)))
    assert expanded.candidates == generation.candidates
    assert report["attempts"][0]["status"] == "rejected_geometry"


def test_spacing_geometry_survives_rotation_and_translation(site):
    def transform(p):
        return (100.0 - p[1], 200.0 + p[0])
    shifted = replace(site, boundary=[transform(p) for p in site.boundary],
                      entrances=[replace(e, center=transform(e.center), heading_degrees=(e.heading_degrees + 90) % 360)
                                 for e in site.entrances])
    expanded, _ = expand_spacing_candidates(shifted, generate_parallel_ladder_skeletons(shifted), SpacingSearchConfig(enabled=True))
    assert len(expanded.candidates) == 4
    catalog = build_and_select_ladder_modules(shifted, expanded.candidates[1].skeleton, backend="greedy")
    assert len(catalog.selected_stalls) == 24
    assert all(Polygon(shifted.boundary).covers(Polygon(s.polygon)) for s in catalog.selected_stalls)
    result = apply_road_network_search(shifted, LayoutResult(site=shifted, stalls=[]))
    assert _layout_valid(result) and result.stall_count == 24
    assert result.road_traversal_validation["stall_coverage"] == 24


def test_disabled_spacing_and_exhausted_expansion_keep_baseline(site):
    generated = generate_parallel_ladder_skeletons(site)
    unchanged, report = expand_spacing_candidates(site, generated, SpacingSearchConfig())
    assert unchanged is generated and report is None
    expired, report = expand_spacing_candidates(site, generated, SpacingSearchConfig(enabled=True), deadline=time.perf_counter()-1)
    assert expired.candidates == generated.candidates
    assert all(row["status"] == "incomplete" for row in report["attempts"])


def test_review_and_project_preserve_the_selected_placement(site, spaced, tmp_path):
    bundle = build_review_bundle(spaced)
    assert len({c["candidate_id"] for c in bundle["candidates"]}) == 4
    assert sum(c["official"] for c in bundle["candidates"]) == 1
    assert sum(c["status"] == "not_solved" for c in bundle["candidates"]) == 2
    service = ProjectService()
    raw = json.loads((ROOT / "tests/fixtures/spacing_search/rectangle.json").read_text(encoding="utf-8"))
    service.accept_layout(spaced, site=raw)
    path = tmp_path / "project.json"
    save_project(service.state, path)
    reopened = ProjectService(load_project(path))
    assert reopened.last_accepted.aisles == spaced.aisles
    assert reopened.last_accepted.stalls == spaced.stalls
    assert reopened.last_accepted.site.metadata["spacing_candidate_id"] == bundle["official"]["candidate_id"]
    exported = reopened.export_accepted()
    assert build_review_bundle(exported)["official"]["candidate_id"] == bundle["official"]["candidate_id"]
    assert _layout_valid(exported)


def test_unchanged_locked_entry_road_keeps_project_identity(baseline, spaced):
    service = ProjectService()
    service.accept_layout(baseline)
    road = next(a for a in baseline.aisles if a.id == "S-CROSS-ENTRY")
    assert next(a for a in spaced.aisles if a.id == road.id).polygon == road.polygon
    locked = service.lock_road(road.id)
    service.accept_layout(spaced)
    assert service.state.object_ids[road.id] == locked.project_object_id
    assert service.state.object_sources[locked.project_object_id]["skeleton_id"] == spaced.site.metadata["skeleton_id"]


def test_locked_roads_can_repack_stalls_but_cannot_move(site, baseline):
    locks = [LayoutLock(lock_id=a.id, kind="road", object_id=a.id, geometry=a.polygon,
                        directionality=a.directionality) for a in baseline.aisles]
    locked = site_with_generation_locks(site, locks)
    result = apply_road_network_search(locked, replace(baseline, site=replace(locked, metadata=baseline.site.metadata)))
    assert _layout_valid(result)
    assert result.aisles == baseline.aisles
    assert result.stall_count >= baseline.stall_count
    report = result.layout_search["road_network_search"]
    assert any(row.get("repair", {}).get("reason") == "initial_lock_conflict" for row in report["skeletons"])


def test_promotion_off_and_tiny_budget_preserve_valid_incumbent(site, baseline):
    opt = deepcopy(site.optimization)
    opt["promote_candidate_layout_preview"] = False
    opt["road_network"]["spacing_search"]["max_variants"] = 2
    off = replace(site, optimization=opt)
    kept = apply_road_network_search(off, baseline)
    assert kept.aisles == baseline.aisles and kept.stalls == baseline.stalls
    opt = deepcopy(site.optimization)
    opt["road_network"]["refinement_budget_seconds"] = 1e-9
    kept = apply_road_network_search(replace(site, optimization=opt), baseline)
    assert kept.aisles == baseline.aisles and kept.stalls == baseline.stalls
    assert kept.layout_search["road_network_search"]["budget"]["exhausted"]


@pytest.mark.parametrize("spacing", [None, False, {"unknown": 1}, {"max_variants": True},
    {"max_variants": 0}, {"inter_aisle_gaps_m": []}, {"stall_gaps_m": [-.1]},
    {"stall_gaps_m": [True]}, {"stop_after_improvement": 1}, {"inter_aisle_gaps_m": None}])
def test_spacing_schema_and_runtime_reject_illegal_input(spacing):
    raw = json.loads((ROOT / "tests/fixtures/spacing_search/rectangle.json").read_text(encoding="utf-8"))
    raw["optimization"]["road_network"]["spacing_search"] = spacing
    schema = json.loads((ROOT / "schema/openparkcad-input.schema.json").read_text(encoding="utf-8"))
    assert any("spacing_search" in list(e.path) for e in Draft202012Validator(schema).iter_errors(raw))
    with pytest.raises(ValueError, match="spacing_search"):
        site_from_dict(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_gaps_are_rejected(value):
    with pytest.raises(ValueError, match="finite nonnegative"):
        parse_road_network_mapping({"spacing_search": {"stall_gaps_m": [value]}})
