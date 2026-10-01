from dataclasses import replace

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.site_constraints import validate_route_usability, validate_site_constraints


def _spec() -> StallSpec:
    return StallSpec(
        id="accessible-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )


def _site(route: dict) -> SiteSpec:
    spec = _spec()
    return SiteSpec(
        name="accessible-dimensions",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency={"accessible_routes": [route]},
    )


def _layout(site: SiteSpec) -> LayoutResult:
    return LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(2, 8), (4.5, 8), (4.5, 13), (2, 13)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="accessible-90",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def _polyline_route(*, width: float, min_width: float | None = 1.5, max_slope: float | None = None) -> dict:
    item: dict = {
        "id": "west-walk",
        "geometry": {"type": "polyline_buffer", "points": [[0.4, 6], [0.4, 18]], "width": width},
        "parking_allowed": False,
        "vehicle_allowed": False,
        "priority": "hard",
    }
    if min_width is not None:
        item["min_width"] = min_width
    if max_slope is not None:
        item["max_slope"] = max_slope
    return item


def test_polyline_min_width_passes_when_declared_width_is_enough():
    report = validate_route_usability(_layout(_site(_polyline_route(width=1.8, min_width=1.5))))

    assert report["accessible_route_dimensions"]["status"] == "active"
    assert report["accessible_route_dimensions"]["min_width_ok_ids"] == ["west-walk"]
    assert report["valid"] is True


def test_polyline_min_width_fails_when_too_narrow():
    site = _site(_polyline_route(width=1.2, min_width=1.5))
    layout = _layout(site)
    report = validate_site_constraints(layout)
    layout = replace(layout, site_constraint_validation=report)

    dimensions = report["route_usability"]["accessible_route_dimensions"]
    assert report["valid"] is False
    assert dimensions["reason"] == "accessible_route_narrower_than_min_width"
    assert dimensions["too_narrow_ids"] == ["west-walk"]
    assert build_input_diagnostics(site, layout)["field_support"]["pedestrian_and_emergency.accessible_route_dimensions"] == "active_failed"


def test_min_width_on_polygon_is_not_auditable():
    route = {
        "id": "plaza",
        "geometry": {"type": "polygon", "points": [[1, 6], [3, 6], [3, 18], [1, 18]]},
        "min_width": 1.5,
        "priority": "hard",
        "parking_allowed": False,
    }
    report = validate_route_usability(_layout(_site(route)))

    dimensions = report["accessible_route_dimensions"]
    assert report["valid"] is False
    assert dimensions["reason"] == "accessible_route_width_not_auditable_for_geometry"
    assert dimensions["width_unresolved_ids"] == ["plaza"]


def test_declared_max_slope_fails_closed_without_elevation():
    site = _site(_polyline_route(width=1.8, min_width=None, max_slope=0.083))
    layout = _layout(site)
    report = validate_site_constraints(layout)
    engineering = build_engineering_validation(
        replace(
            layout,
            site_constraint_validation=report,
            maneuver_validation={"vehicle_validation": {"valid": True, "requested": {}}},
        )
    )

    dimensions = report["route_usability"]["accessible_route_dimensions"]
    assert report["valid"] is False
    assert dimensions["reason"] == "accessible_route_slope_check_unsupported"
    assert dimensions["slope_declared_ids"] == ["west-walk"]
    assert any(item["id"] == "route.accessible_dimensions" and item["status"] == "failed" for item in engineering["rules"]["active"])


def test_dimensions_not_requested_without_min_width_or_max_slope():
    report = validate_route_usability(_layout(_site(_polyline_route(width=1.8, min_width=None))))

    assert report["accessible_route_dimensions"]["status"] == "not_requested"
    assert report["valid"] is True
