from __future__ import annotations

import json
from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from openparkcad.through_corridor import FAMILY_ID, build_through_corridor_layout, through_corridor_enabled


FROZEN = Path("examples/dual_entrance_site.json")


def _dual(road: bool, through: bool) -> dict:
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    data.setdefault("constraints", {})
    if road:
        data["constraints"]["road_traversal"] = {"enabled": True, "scope": "site_interior", "time_budget_seconds": 10.0}
    data.setdefault("optimization", {})
    data["optimization"]["enable_through_corridor"] = through
    return data


def test_k1_frozen_dual_entrance_road_enabled_is_empty_without_family() -> None:
    site = site_from_dict(_dual(road=True, through=False))
    layout = generate_layout(site)
    assert through_corridor_enabled(site) is False
    assert layout.stall_count == 0
    assert layout.road_traversal_validation.get("status") in {"failed", "incomplete"}


def test_k2_through_corridor_family_recovers_frozen_case() -> None:
    site = site_from_dict(_dual(road=True, through=True))
    assert through_corridor_enabled(site) is True
    layout = generate_layout(site)
    assert layout.generation_mode == "phase1_through_corridor" or layout.stall_count > 0
    assert layout.stall_count > 0
    road = layout.road_traversal_validation
    assert road.get("requested") is True
    assert road.get("executed") is True
    assert road.get("status") in {"passed", "failed", "incomplete"}
    if road.get("status") == "passed":
        assert road.get("valid") is True
        assert road.get("stall_coverage") == layout.stall_count


def test_k3_k4_same_condition_comparison_reports_gain() -> None:
    off = generate_layout(site_from_dict(_dual(road=True, through=False)))
    on = generate_layout(site_from_dict(_dual(road=True, through=True)))
    assert off.stall_count == 0
    assert on.stall_count > off.stall_count
    baseline = generate_layout(site_from_dict(_dual(road=False, through=False)))
    assert baseline.stall_count > 0
    assert baseline.road_traversal_validation.get("requested") is False


def test_through_corridor_builder_uses_distinct_entry_and_exit() -> None:
    site = site_from_dict(_dual(road=False, through=True))
    built = build_through_corridor_layout(site)
    assert built is not None
    assert built.generation_mode == "phase1_through_corridor"
    assert {aisle.role for aisle in built.aisles} >= {"main", "exit"}
    assert FAMILY_ID == "through_corridor"
