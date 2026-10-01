from dataclasses import replace

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.models import (
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteSpec,
    StallSpec,
)
from openparkcad.site_constraints import validate_route_usability, validate_site_constraints


def _accessible_site(*, tolerance: float | None = None) -> SiteSpec:
    constraints = {}
    if tolerance is not None:
        constraints["accessible_route_touch_tolerance"] = tolerance
    accessible = StallSpec(
        id="accessible-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )
    return SiteSpec(
        name="route-usability",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=accessible,
        stall_candidates=(accessible,),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        constraints=constraints,
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {
                        "type": "polyline_buffer",
                        "points": [[0.4, 6], [0.4, 18]],
                        "width": 0.8,
                    },
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )


def _accessible_layout(site: SiteSpec, *, stall_x: float) -> LayoutResult:
    stall = ParkingStall(
        id="P-001",
        polygon=[(stall_x, 8), (stall_x + 2.5, 8), (stall_x + 2.5, 13), (stall_x, 13)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
        aisle_side="left",
        stall_type_id="accessible-90",
    )
    return LayoutResult(
        site=site,
        stalls=[stall],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def _emergency_site(*, lane_x: float = 12.0) -> SiteSpec:
    return SiteSpec(
        name="emergency-access",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        pedestrian_and_emergency={
            "emergency_access_required": True,
            "fire_lanes": [
                {
                    "id": "south-fire",
                    "geometry": {
                        "type": "polyline_buffer",
                        "points": [[lane_x, 0], [lane_x, 20]],
                        "width": 4.0,
                    },
                    "parking_allowed": False,
                    "vehicle_allowed": True,
                    "priority": "hard",
                }
            ],
        },
    )


def test_accessible_stall_near_route_passes_usability():
    site = _accessible_site()
    layout = _accessible_layout(site, stall_x=2.0)
    report = validate_route_usability(layout)

    assert report["valid"] is True
    assert report["accessible_route"]["status"] == "active"
    assert report["accessible_route"]["reachable_stall_ids"] == ["P-001"]
    assert report["accessible_route"]["unreachable_stall_ids"] == []


def test_accessible_stall_far_from_route_fails_closed():
    site = _accessible_site()
    layout = _accessible_layout(site, stall_x=18.0)
    report = validate_site_constraints(layout)
    layout = replace(layout, site_constraint_validation=report)

    assert report["valid"] is False
    usability = report["route_usability"]["accessible_route"]
    assert usability["status"] == "active_failed"
    assert usability["unreachable_stall_ids"] == ["P-001"]
    assert usability["reason"] == "accessible_stall_does_not_reach_accessible_route"
    diagnostics = build_input_diagnostics(site, layout)
    assert diagnostics["field_support"]["pedestrian_and_emergency.accessible_route_usability"] == "active_failed"


def test_accessible_usability_not_requested_without_quota():
    site = _accessible_site()
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=site.stall,
        stall_candidates=site.stall_candidates,
        aisle_width=site.aisle_width,
        pedestrian_and_emergency=site.pedestrian_and_emergency,
        parking_quotas={},
    )
    layout = _accessible_layout(site, stall_x=18.0)
    report = validate_route_usability(layout)

    assert report["valid"] is True
    assert report["accessible_route"]["status"] == "not_requested"


def test_emergency_route_at_entrance_passes_connectivity():
    site = _emergency_site(lane_x=12.0)
    layout = LayoutResult(site=site, stalls=[], aisles=[])
    report = validate_route_usability(layout)

    assert report["valid"] is True
    assert report["emergency_access"]["status"] == "active"
    assert report["emergency_access"]["connected_route_ids"] == ["south-fire"]
    assert "main" in report["emergency_access"]["connected_entrance_ids"]
    assert report["emergency_access"]["unconnected_route_ids"] == []


def test_emergency_route_away_from_entrance_fails_closed():
    site = _emergency_site(lane_x=22.0)
    layout = LayoutResult(site=site, stalls=[], aisles=[])
    report = validate_site_constraints(layout)
    engineering = build_engineering_validation(
        LayoutResult(
            site=site,
            stalls=[],
            maneuver_validation={"vehicle_validation": {"valid": True, "requested": {}}},
            site_constraint_validation=report,
        )
    )

    assert report["valid"] is False
    access = report["route_usability"]["emergency_access"]
    assert access["status"] == "active_failed"
    assert access["unconnected_route_ids"] == ["south-fire"]
    assert access["reason"] == "emergency_route_does_not_reach_entrance"
    assert any(item["id"] == "route.emergency_access_connectivity" for item in engineering["rules"]["active"])
    assert any(item["status"] == "failed" for item in engineering["rules"]["active"] if item["id"] == "route.emergency_access_connectivity")


def test_invalid_touch_tolerance_fails_closed():
    site = _accessible_site(tolerance=-1.0)
    layout = _accessible_layout(site, stall_x=2.0)
    report = validate_route_usability(layout)

    assert report["valid"] is False
    assert report["accessible_route"]["reason"] == "invalid_accessible_route_touch_tolerance"
