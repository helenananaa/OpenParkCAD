from __future__ import annotations

from dataclasses import replace

import pytest
from shapely.geometry import Point, Polygon

from openparkcad.layout_geometry import available_area
from openparkcad.models import (
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteAreaSpec,
    SiteSpec,
    StallSpec,
    site_from_dict,
)
from openparkcad.site_constraints import (
    constraint_conflicts,
    declared_constraint_geometries,
    site_exclusion_geometry,
    stall_type_capabilities,
    validate_parking_quotas,
    validate_site_constraint_definitions,
    validate_site_constraints,
)


def test_parser_preserves_constraint_declarations_quota_and_vehicle_geometry():
    site = site_from_dict(
        {
            "name": "declarations",
            "site": {
                "boundary": {"type": "polygon", "points": _square(0, 0, 20, 20)},
                "obstacles": [
                    {
                        "id": "column-block",
                        "type": "column",
                        "geometry": {"type": "polygon", "points": _square(5, 5, 6, 6)},
                        "clearance": 0.25,
                    }
                ],
                "reserved_areas": [
                    {
                        "id": "landscape",
                        "type": "landscape",
                        "geometry": {"type": "circle", "center": [10, 10], "radius": 2},
                        "clearance": 0.5,
                        "authority": "project_policy",
                    }
                ],
            },
            "vehicles": {
                "design_vehicle": {
                    "turning_radius_reference": "rear_axle_center",
                    "track_width": 1.6,
                    "front_overhang": 0.9,
                    "rear_overhang": 1.0,
                }
            },
            "parking": {
                "stall_types": [
                    {
                        "id": "accessible-ev",
                        "qualifications": ["accessible", "ev"],
                        "fixed_features": [{"type": "charging_post"}],
                    }
                ],
                "quotas": {"accessible_min": 1, "ev_min": 1},
            },
            "constraints": {"setbacks": {"obstacle": 0.75}},
        }
    )

    assert site.obstacle_specs[0].id == "column-block"
    assert site.obstacle_specs[0].clearance == 0.75
    assert site.reserved_areas[0].id == "landscape"
    assert site.reserved_areas[0].authority == "project_policy"
    assert site.reserved_areas[0].priority == "hard"
    assert site.parking_quotas == {"accessible_min": 1, "ev_min": 1}
    assert site.stall.classifications == ("accessible", "ev")
    assert site.stall.fixed_features[0]["type"] == "charging_post"
    assert site.vehicle is not None
    assert site.vehicle.turning_radius_reference == "rear_axle_center"
    assert site.vehicle.track_width == 1.6
    assert site.vehicle.front_overhang == 0.9
    assert site.vehicle.rear_overhang == 1.0


def test_available_area_applies_obstacle_clearance_reserved_area_and_legacy_setback():
    parsed = site_from_dict(
        {
            "name": "clearances",
            "site": {
                "boundary": {"type": "polygon", "points": _square(0, 0, 20, 20)},
                "obstacles": [
                    {
                        "id": "equipment",
                        "geometry": {"type": "polygon", "points": _square(5, 5, 6, 6)},
                        "clearance": 1.0,
                    }
                ],
                "reserved_areas": [
                    {
                        "id": "landscape",
                        "geometry": {"type": "circle", "center": [12, 12], "radius": 1},
                        "clearance": 0.5,
                    }
                ],
            },
        }
    )
    assert not available_area(parsed).covers(Point(4.1, 5.5))
    assert not available_area(parsed).covers(Point(13.4, 12))

    legacy = SiteSpec(
        name="legacy",
        boundary=_square(0, 0, 20, 20),
        obstacles=[_square(5, 5, 6, 6)],
        constraints={"setbacks": {"obstacle": 0.5}},
        margin=0,
    )
    assert not available_area(legacy).covers(Point(4.6, 5.5))


def test_declared_affects_and_routes_have_explicit_scopes():
    site = SiteSpec(
        name="scope",
        boundary=_square(0, 0, 30, 30),
        margin=0,
        site_features=[
            {
                "id": "charger",
                "type": "charging_post",
                "geometry": {"type": "rectangle", "origin": [10, 10], "width": 1, "height": 1},
                "affects": ["door_clearance"],
            },
            {
                "id": "advisory-column",
                "geometry": {"type": "circle", "center": [15, 15], "radius": 1},
                "affects": ["forbidden"],
                "authority": "advisory",
            },
        ],
        pedestrian_and_emergency={
            "fire_lanes": [
                {
                    "id": "fire",
                    "geometry": {"type": "polyline_buffer", "points": [[2, 0], [2, 30]], "width": 2},
                    "parking_allowed": False,
                }
            ],
            "pedestrian_routes": [
                {
                    "id": "walk",
                    "geometry": {"type": "polyline_buffer", "points": [[25, 0], [25, 30]], "width": 2},
                    "affects": ["stall", "swept_path"],
                    "priority": "hard",
                }
            ],
        },
    )

    declarations = {item.id: item for item in declared_constraint_geometries(site)}
    assert declarations["charger"].purposes == frozenset({"stall"})
    assert declarations["fire"].purposes == frozenset({"stall"})
    assert declarations["walk"].purposes == frozenset({"stall", "swept_path"})
    assert "advisory-column" not in declarations
    assert site_exclusion_geometry(site, "stall").covers(Point(10.5, 10.5))
    assert not site_exclusion_geometry(site, "aisle").covers(Point(10.5, 10.5))
    assert not site_exclusion_geometry(site, "aisle").covers(Point(2, 15))
    assert not site_exclusion_geometry(site, "aisle").covers(Point(25, 15))
    assert site_exclusion_geometry(site, "swept_path").covers(Point(25, 15))


def test_validate_site_constraints_reports_stall_aisle_and_swept_path_conflicts():
    site = SiteSpec(
        name="conflicts",
        boundary=_square(0, 0, 20, 20),
        margin=0,
        reserved_areas=(
            SiteAreaSpec(
                id="reserved",
                kind="landscape",
                geometry={"type": "polygon", "points": _square(5, 5, 8, 8)},
            ),
        ),
    )
    layout = LayoutResult(
        site=site,
        stalls=[ParkingStall("S1", _square(6, 6, 9, 9), 0)],
        aisles=[ParkingAisle("A1", _square(7, 7, 12, 10), 0)],
    )

    report = validate_site_constraints(layout)

    assert report["version"] == "v0.3-site-constraints"
    assert report["valid"] is False
    assert report["scope"]["swept_path"].startswith("vehicle maneuver")
    assert report["authority"]["project_policy"]["active_ids"] == ["reserved"]
    assert report["conflicts"]["stalls"][0]["constraint_id"] == "reserved"
    assert report["conflicts"]["aisles"][0]["constraint_id"] == "reserved"
    sweep_conflicts = constraint_conflicts(site, Polygon(_square(4, 4, 6, 6)), "swept_path")
    assert sweep_conflicts[0]["constraint_id"] == "reserved"


def test_definition_validation_fails_closed_for_out_of_bounds_and_missing_routes():
    outside = SiteSpec(
        name="outside",
        boundary=_square(0, 0, 10, 10),
        reserved_areas=(
            SiteAreaSpec(
                id="outside-reservation",
                kind="landscape",
                geometry={"type": "circle", "center": [10, 5], "radius": 2},
            ),
        ),
        pedestrian_and_emergency={"emergency_access_required": True},
    )

    report = validate_site_constraint_definitions(outside)

    assert report["valid"] is False
    assert any("extends outside" in error for error in report["errors"])
    assert any("needs a hard fire/access route" in error for error in report["errors"])

    advisory_route = replace(
        outside,
        reserved_areas=(),
        pedestrian_and_emergency={
            "emergency_access_required": True,
            "fire_lanes": [
                {
                    "geometry": {"type": "polyline_buffer", "points": [[2, 0], [2, 10]], "width": 2},
                    "authority": "advisory",
                }
            ],
        },
    )
    assert validate_site_constraint_definitions(advisory_route)["valid"] is False

    explicitly_required = replace(
        outside,
        reserved_areas=(),
        pedestrian_and_emergency={
            "pedestrian_routes": [{"id": "required-walk", "required": True}],
        },
    )
    required_report = validate_site_constraint_definitions(explicitly_required)
    assert required_report["valid"] is False
    assert any("required pedestrian_route 'required-walk' needs geometry" in error for error in required_report["errors"])


def test_authority_is_reported_separately_from_priority_and_jurisdiction_needs_metadata():
    site = SiteSpec(
        name="authority",
        boundary=_square(0, 0, 20, 20),
        margin=0,
        reserved_areas=(
            SiteAreaSpec(
                id="advisory-hard-priority",
                kind="painted_hint",
                geometry={"type": "polygon", "points": _square(1, 1, 2, 2)},
                authority="advisory",
                priority="hard",
            ),
            SiteAreaSpec(
                id="jurisdiction-future",
                kind="future_rule",
                geometry={"type": "polygon", "points": _square(3, 3, 4, 4)},
                authority="jurisdictional",
                priority="future",
            ),
        ),
    )

    report = validate_site_constraint_definitions(site)

    assert report["valid"] is False
    assert report["hard_count"] == 0
    assert report["authority"]["advisory"]["active_count"] == 0
    assert report["authority"]["jurisdictional"]["active_count"] == 0
    assert "standards.standard_profile" in report["authority"]["jurisdictional"]["standards_metadata"]["missing"]
    assert any("jurisdictional constraints need standards metadata" in error for error in report["errors"])

    sourced = replace(
        site,
        standards={
            "standard_profile": "named-external-profile-2026",
            "source": "authority publication",
            "effective_date": "2026-01-01",
        },
    )
    assert validate_site_constraint_definitions(sourced)["valid"] is True


def test_quota_validation_counts_classified_and_ev_equipped_stalls():
    accessible = StallSpec(id="accessible", classifications=("accessible",))
    ev = StallSpec(id="charging-space", fixed_features=({"type": "charging_post"},))
    site = SiteSpec(
        name="quota",
        boundary=_square(0, 0, 20, 20),
        stall=accessible,
        stall_candidates=(accessible, ev),
        parking_quotas={"accessible_min": 1, "ev_min": 1},
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall("S-access", _square(1, 1, 3, 5), 0, stall_type_id="accessible"),
            ParkingStall("S-ev", _square(4, 1, 6, 5), 0, stall_type_id="charging-space"),
        ],
    )

    report = validate_parking_quotas(layout)

    assert report["valid"] is True
    assert report["actual"] == {"accessible": 1, "ev": 1}
    assert stall_type_capabilities(ev) == frozenset({"ev"})

    failed = validate_parking_quotas(replace(layout, site=replace(site, parking_quotas={"ev_min": 2})))
    assert failed["valid"] is False
    assert failed["shortfall"]["ev"] == 1
    assert "layout provides 1" in failed["errors"][0]


def test_parser_rejects_negative_clearance_and_fractional_quota():
    base = {
        "name": "invalid",
        "site": {"boundary": {"type": "polygon", "points": _square(0, 0, 10, 10)}},
    }
    negative = {
        **base,
        "site": {
            **base["site"],
            "reserved_areas": [
                {
                    "geometry": {"type": "circle", "center": [5, 5], "radius": 1},
                    "clearance": -0.1,
                }
            ],
        },
    }
    with pytest.raises(ValueError, match="must be non-negative"):
        site_from_dict(negative)

    fractional = {**base, "parking": {"quotas": {"ev_min": 1.5}}}
    with pytest.raises(ValueError, match="must be a whole number"):
        site_from_dict(fractional)


def _square(x1: float, y1: float, x2: float, y2: float) -> list[tuple[float, float]]:
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
