from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import time

import pytest
from jsonschema import Draft202012Validator
from shapely.geometry import Polygon

from openparkcad.generator import _layout_valid, generate_layout
from openparkcad.ladder_repair import repair_ladder_layout, _select_stalls
from openparkcad.layout_locks import lock_from_stalls, site_with_generation_locks
from openparkcad.models import site_from_dict
from openparkcad.parking_motion_adapter import parking_motion_for_stall
from openparkcad.road_network_config import RepairConfig, parse_road_network_mapping
from openparkcad.road_transitions import build_occupancy
from openparkcad.road_traversal_models import layout_traversal_identity, parse_traversal_policy
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def raw_layout():
    raw = json.loads((ROOT / "tests/fixtures/dense_repair/rectangle.json").read_text())
    site = site_from_dict(raw)
    skeleton = generate_parallel_ladder_skeletons(site).candidates[0].skeleton
    catalog = build_and_select_ladder_modules(site, skeleton, backend="greedy")
    return layout_from_skeleton(site, skeleton, catalog.selected_stalls)


def config_for(layout):
    return parse_road_network_mapping(layout.site.optimization["road_network"]).repair


@pytest.fixture(scope="module")
def repaired(raw_layout):
    return repair_ladder_layout(raw_layout, config_for(raw_layout), backend="cpsat")


def test_dense_conflict_repair_validates_every_retained_journey(raw_layout, repaired):
    layout, report = repaired
    assert report["accepted"] and report["status"] == "passed"
    assert _layout_valid(layout)
    assert raw_layout.stall_count == 30 and layout.stall_count == 12
    assert layout.aisles == raw_layout.aisles
    assert layout.site == raw_layout.site
    assert layout.road_traversal_validation["stall_coverage"] == layout.stall_count
    assert layout.road_traversal_validation["layout_identity"] == layout_traversal_identity(layout, parse_traversal_policy(layout.site))
    assert report["rounds"][0]["parking_passed"] == 0
    assert report["rounds"][-1]["parking_passed"] == 12
    assert {x["stall_id"] for x in report["removed_stalls"]} == {s.id for s in raw_layout.stalls} - {s.id for s in layout.stalls}
    assert all(row["conflicts_with_retained"] for row in report["removed_stalls"])
    assert report["rounds"][0]["selector"]["status"] == "optimal"
    assert "not_site_global" in report["solver_scope"]
    assert raw_layout.road_traversal_validation == {}


def test_collision_diagnostic_names_actual_stalls(raw_layout):
    stall = raw_layout.stalls[0]
    motion = parking_motion_for_stall(raw_layout, stall, build_occupancy(raw_layout), parse_traversal_policy(raw_layout.site), raw_layout.site.vehicle)
    assert motion.reason == "swept_path_intersects_obstacle"
    assert motion.details["blocking_stall_ids"]
    assert not motion.details["hard_obstacle_collision"]
    assert stall.id not in motion.details["blocking_stall_ids"]
    assert set(motion.details["blocking_stall_ids"]) <= {s.id for s in raw_layout.stalls}


def test_static_obstacle_is_never_repaired_by_deleting_stalls(raw_layout):
    point = Polygon(raw_layout.stalls[0].polygon).centroid
    obstacle = [(point.x - .1, point.y - .1), (point.x + .1, point.y - .1),
                (point.x + .1, point.y + .1), (point.x - .1, point.y + .1)]
    blocked = replace(raw_layout, site=replace(raw_layout.site, obstacles=[obstacle], obstacle_specs=()))
    layout, report = repair_ladder_layout(blocked, config_for(blocked))
    assert not report["accepted"] and report["reason"] == "conflict_not_repairable_by_stall_selection"
    assert layout.stalls == blocked.stalls and not report["removed_stalls"]
    assert any(f["hard_obstacle_collision"] for f in report["rounds"][0]["failures"])


def test_conflicting_locked_stalls_fail_closed(raw_layout, repaired):
    a, b = repaired[1]["learned_conflicts"][0]
    by_id = {s.id: s for s in raw_layout.stalls}
    locks = [lock_from_stalls([by_id[key]], lock_id=key, project_object_id=key) for key in [a, b]]
    locked = replace(raw_layout, site=site_with_generation_locks(raw_layout.site, locks))
    layout, report = repair_ladder_layout(locked, config_for(locked), backend="cpsat")
    assert not report["accepted"] and report["status"] == "failed"
    assert layout.stalls == locked.stalls
    assert report["pinned_stall_ids"] == sorted([a, b])
    assert report["rounds"][0]["selector"]["status"] == "infeasible"


def test_retention_floor_cannot_be_satisfied_by_an_empty_or_tiny_layout(raw_layout):
    layout, report = repair_ladder_layout(raw_layout, replace(config_for(raw_layout), min_retained_stalls=30), backend="cpsat")
    assert not report["accepted"] and report["reason"] == "retention_floor_or_lock_conflict"
    assert layout.stalls == raw_layout.stalls


def test_repair_cannot_bypass_required_accessible_quota(raw_layout):
    constrained = replace(raw_layout, site=replace(raw_layout.site, parking_quotas={"accessible_min": 1}))
    layout, report = repair_ladder_layout(constrained, config_for(constrained), backend="cpsat")
    assert not report["accepted"] and report["reason"] == "full_layout_validation_failed"
    assert layout.site.parking_quotas["accessible_min"] == 1
    assert report["final_validation"]["engineering_valid"] is False


def test_time_and_round_budgets_never_become_pass(raw_layout):
    _, expired = repair_ladder_layout(raw_layout, config_for(raw_layout), deadline=time.perf_counter() - 1)
    assert expired["status"] == "incomplete" and not expired["accepted"] and not expired["rounds"]
    _, limited = repair_ladder_layout(raw_layout, replace(config_for(raw_layout), max_rounds=1), backend="cpsat")
    assert limited["status"] == "incomplete" and not limited["accepted"]
    assert "final_validation" not in limited


def test_disabled_repair_preserves_object_and_no_road_request_is_unsupported(raw_layout):
    result, report = repair_ladder_layout(raw_layout, RepairConfig())
    assert result is raw_layout and report["status"] == "not_requested"
    constraints = {**raw_layout.site.constraints, "road_traversal": {"enabled": False}}
    _, report = repair_ladder_layout(replace(raw_layout, site=replace(raw_layout.site, constraints=constraints)), config_for(raw_layout))
    assert report["status"] == "unsupported" and not report["accepted"]


def test_missing_optimizer_uses_a_reported_greedy_fallback(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "ortools.sat.python", None)
    selected, report = _select_stalls(["a", "b", "c"], {("a", "b"), ("b", "c")}, set(), "cpsat", time.perf_counter() + 10)
    assert selected == {"a", "c"}
    assert report["actual"] == "greedy" and report["status"] == "heuristic"
    assert report["fallback_reason"] == "ortools_missing"


@pytest.mark.parametrize("repair", [None, False, {"unknown": 1}, {"max_rounds": True}, {"max_rounds": 0},
                                    {"min_retained_stalls": 0}, {"time_budget_seconds": None}, {"time_budget_seconds": -1}])
def test_repair_schema_and_parser_fail_closed(repair):
    raw = json.loads((ROOT / "tests/fixtures/dense_repair/rectangle.json").read_text())
    raw["optimization"]["road_network"]["repair"] = repair
    schema = json.loads((ROOT / "schema/openparkcad-input.schema.json").read_text())
    assert any("repair" in list(error.path) for error in Draft202012Validator(schema).iter_errors(raw))
    with pytest.raises(ValueError, match="road_network.repair"):
        site_from_dict(raw)


def test_disabled_repair_does_not_change_unrequested_generation():
    raw = json.loads((ROOT / "examples/parallel_ladder_rect_site.json").read_text())
    first = generate_layout(site_from_dict(deepcopy(raw)))
    raw["optimization"]["road_network"]["repair"] = {"enabled": False}
    second = generate_layout(site_from_dict(raw))
    assert first.aisles == second.aisles and first.stalls == second.stalls
    assert first.score == second.score
    assert all("repair" not in row for row in second.layout_search["road_network_search"]["skeletons"])
