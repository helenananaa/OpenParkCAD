from dataclasses import replace

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.site_constraints import validate_route_usability, validate_site_constraints


def _accessible_spec() -> StallSpec:
    return StallSpec(
        id="accessible-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )


def _route(route_id: str, points: list[list[float]], *, connects: list[str] | None = None) -> dict:
    item = {
        "id": route_id,
        "geometry": {"type": "polyline_buffer", "points": points, "width": 0.8},
        "parking_allowed": False,
        "vehicle_allowed": False,
        "priority": "hard",
    }
    if connects is not None:
        item["connects"] = connects
    return item


def _door(origin: tuple[float, float]) -> dict:
    return {
        "id": "building-entry",
        "type": "door",
        "geometry": {
            "type": "rectangle",
            "origin": [origin[0], origin[1]],
            "width": 1.2,
            "height": 0.4,
            "rotation_degrees": 0,
        },
        "affects": ["stall_access"],
        "priority": "hard",
    }


def _site(routes: list[dict], *, features: list[dict] | None = None) -> SiteSpec:
    spec = _accessible_spec()
    return SiteSpec(
        name="accessible-continuity",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        site_features=features or [],
        pedestrian_and_emergency={"accessible_routes": routes},
    )


def _layout(site: SiteSpec, stalls: list[tuple[str, float]]) -> LayoutResult:
    parking = [
        ParkingStall(
            id=stall_id,
            polygon=[(x, 8), (x + 2.5, 8), (x + 2.5, 13), (x, 13)],
            angle_degrees=90.0,
            served_by_aisle_id="A-MAIN",
            aisle_side="left",
            stall_type_id="accessible-90",
        )
        for stall_id, x in stalls
    ]
    return LayoutResult(
        site=site,
        stalls=parking,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def test_connected_pieces_reach_declared_building_entry():
    site = _site(
        [
            _route("south-walk", [[0.4, 6], [0.4, 12]], connects=["accessible-stalls", "building-entry"]),
            _route("north-walk", [[0.4, 12], [0.4, 18]]),
        ],
        features=[_door((0.1, 17.6))],
    )
    report = validate_route_usability(_layout(site, [("P-001", 2.0)]))

    assert report["valid"] is True
    continuity = report["accessible_route_continuity"]
    assert continuity["status"] == "active"
    assert continuity["destination_ids"] == ["building-entry"]
    assert set(continuity["serving_route_ids"]) <= {"south-walk", "north-walk"}


def test_disjoint_route_does_not_reach_destination():
    site = _site(
        [
            _route("west-walk", [[0.4, 6], [0.4, 12]], connects=["building-entry"]),
            _route("east-walk", [[22.0, 16], [22.0, 20]]),
        ],
        features=[_door((21.5, 19.5))],
    )
    layout = _layout(site, [("P-001", 2.0)])
    report = validate_site_constraints(layout)
    layout = replace(layout, site_constraint_validation=report)

    continuity = report["route_usability"]["accessible_route_continuity"]
    assert report["valid"] is False
    assert continuity["reason"] == "accessible_route_does_not_reach_destination"
    assert continuity["unreached_destination_ids"] == ["building-entry"]
    diagnostics = build_input_diagnostics(site, layout)
    assert diagnostics["field_support"]["pedestrian_and_emergency.accessible_route_continuity"] == "active_failed"


def test_stalls_on_disconnected_pieces_fail_closed():
    site = _site(
        [
            _route("west-walk", [[0.4, 6], [0.4, 14]]),
            _route("east-walk", [[22.0, 6], [22.0, 14]]),
        ]
    )
    report = validate_route_usability(_layout(site, [("P-001", 2.0), ("P-002", 18.5)]))

    continuity = report["accessible_route_continuity"]
    assert report["valid"] is False
    assert continuity["reason"] == "accessible_route_network_disconnected"
    assert set(continuity["serving_route_ids"]) == {"west-walk", "east-walk"}


def test_unknown_connects_target_fails_closed():
    site = _site([_route("west-walk", [[0.4, 6], [0.4, 18]], connects=["ghost-door"])])
    layout = _layout(site, [("P-001", 2.0)])
    report = validate_site_constraints(layout)
    engineering = build_engineering_validation(
        replace(
            layout,
            site_constraint_validation=report,
            maneuver_validation={"vehicle_validation": {"valid": True, "requested": {}}},
        )
    )

    continuity = report["route_usability"]["accessible_route_continuity"]
    assert report["valid"] is False
    assert continuity["reason"] == "accessible_route_connects_target_missing"
    assert continuity["missing_connect_ids"] == ["ghost-door"]
    assert any(item["id"] == "route.accessible_continuity" and item["status"] == "failed" for item in engineering["rules"]["active"])


def test_continuity_not_requested_without_accessible_quota():
    spec = _accessible_spec()
    site = SiteSpec(
        name="no-quota",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        pedestrian_and_emergency={"accessible_routes": [_route("west-walk", [[0.4, 6], [0.4, 18]], connects=["ghost-door"])]},
    )
    report = validate_route_usability(_layout(site, [("P-001", 2.0)]))

    assert report["accessible_route_continuity"]["status"] == "not_requested"
    assert report["valid"] is True
