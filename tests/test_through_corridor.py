from __future__ import annotations

import json
from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from openparkcad import through_corridor
from openparkcad.through_corridor import FAMILY_ID, build_through_corridor_layout, through_corridor_enabled


FROZEN = Path("examples/dual_entrance_site.json")


def _dual(road: bool, through: bool) -> dict:
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    data.setdefault("constraints", {})
    if road:
        data["constraints"]["road_traversal"] = {"enabled": True, "scope": "site_interior", "time_budget_seconds": 45.0}
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
    assert layout.generation_mode == "phase1_through_corridor"
    assert layout.stall_count > 1
    road = layout.road_traversal_validation
    assert road.get("requested") is True
    assert road.get("executed") is True
    assert road.get("status") == "passed"
    assert road.get("valid") is True
    assert road.get("stall_coverage") == layout.stall_count
    _assert_auditable_through_corridor_selection(layout)


def test_k3_k4_same_condition_comparison_reports_gain() -> None:
    off = generate_layout(site_from_dict(_dual(road=True, through=False)))
    on = generate_layout(site_from_dict(_dual(road=True, through=True)))
    assert off.stall_count == 0
    assert on.stall_count > 1
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


def test_through_corridor_builder_does_not_cap_official_stalls_at_one() -> None:
    assert not hasattr(through_corridor, "MAX_OFFICIAL_STALLS")
    site = site_from_dict(_dual(road=False, through=True))
    built = build_through_corridor_layout(site)
    assert built is not None
    assert built.stall_count > 1
    _assert_auditable_through_corridor_selection(built)
    candidates = built.through_corridor_report.get("candidate_polygons") or []
    assert len(candidates) >= built.stall_count


def _assert_auditable_through_corridor_selection(layout) -> None:
    report = layout.through_corridor_report or {}
    dropped = report.get("dropped") or []
    if not dropped:
        return
    candidate_keys = {
        tuple((round(float(x), 6), round(float(y), 6)) for x, y in polygon)
        for polygon in report.get("candidate_polygons") or []
    }
    kept_keys = {
        tuple((round(float(x), 6), round(float(y), 6)) for x, y in stall.polygon)
        for stall in layout.stalls
    }
    assert kept_keys <= candidate_keys
    for item in dropped:
        assert item.get("reason")
        assert item.get("polygon")
        drop_key = tuple((round(float(x), 6), round(float(y), 6)) for x, y in item["polygon"])
        assert drop_key in candidate_keys
        assert drop_key not in kept_keys
