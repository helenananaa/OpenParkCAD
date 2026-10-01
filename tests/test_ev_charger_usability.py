from dataclasses import replace

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.site_constraints import validate_parking_quotas, validate_route_usability, validate_site_constraints


def _ev_spec() -> StallSpec:
    return StallSpec(
        id="ev-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("ev",),
        fixed_features=({"type": "charging_post"},),
    )


def _charger(*, origin: tuple[float, float], feature_id: str = "charger-1") -> dict:
    return {
        "id": feature_id,
        "type": "charging_post",
        "geometry": {
            "type": "rectangle",
            "origin": [origin[0], origin[1]],
            "width": 0.4,
            "height": 0.4,
            "rotation_degrees": 0,
        },
        "clearance": 0.2,
        "affects": ["stall_access"],
        "priority": "hard",
    }


def _site(*, charger_origin=(1.2, 10.0), ev_min: int = 1, include_charger: bool = True) -> SiteSpec:
    spec = _ev_spec()
    return SiteSpec(
        name="ev-charger-usability",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"ev_min": ev_min} if ev_min else {},
        site_features=[_charger(origin=charger_origin)] if include_charger else [],
    )


def _layout(site: SiteSpec, *, stall_x: float) -> LayoutResult:
    return LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(stall_x, 8), (stall_x + 2.5, 8), (stall_x + 2.5, 13), (stall_x, 13)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="ev-90",
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


def test_ev_stall_near_placed_charger_passes():
    site = _site(charger_origin=(1.2, 10.0))
    report = validate_route_usability(_layout(site, stall_x=2.0))

    assert report["valid"] is True
    assert report["ev_charger"]["status"] == "active"
    assert report["ev_charger"]["reachable_stall_ids"] == ["P-001"]
    assert report["ev_charger"]["charger_ids"] == ["charger-1"]


def test_ev_stall_far_from_placed_charger_fails_closed():
    site = _site()
    layout = _layout(site, stall_x=18.0)
    report = validate_site_constraints(layout)
    layout = replace(layout, site_constraint_validation=report)
    diagnostics = build_input_diagnostics(site, layout)
    engineering = build_engineering_validation(
        replace(
            layout,
            maneuver_validation={"vehicle_validation": {"valid": True, "requested": {}}},
        )
    )

    assert report["valid"] is False
    block = report["route_usability"]["ev_charger"]
    assert block["status"] == "active_failed"
    assert block["reason"] == "ev_stall_does_not_reach_charger"
    assert block["unreachable_stall_ids"] == ["P-001"]
    assert diagnostics["field_support"]["parking.ev_charger_usability"] == "active_failed"
    assert any(item["id"] == "equipment.ev_charger_usability" and item["status"] == "failed" for item in engineering["rules"]["active"])


def test_ev_quota_without_placed_charger_does_not_claim_equipment_check():
    site = _site(include_charger=False)
    layout = _layout(site, stall_x=18.0)
    usability = validate_route_usability(layout)
    quota = validate_parking_quotas(layout)

    assert quota["valid"] is True
    assert quota["actual"]["ev"] == 1
    assert usability["ev_charger"]["status"] == "not_requested"
    assert usability["valid"] is True
    assert build_input_diagnostics(site, layout)["field_support"]["parking.ev_charger_usability"] == "available"


def test_placed_charger_without_ev_quota_is_not_requested():
    site = _site(ev_min=0, include_charger=True)
    report = validate_route_usability(_layout(site, stall_x=18.0))

    assert report["ev_charger"]["status"] == "not_requested"
    assert report["valid"] is True


def test_invalid_ev_charger_tolerance_fails_closed():
    spec = _ev_spec()
    site = SiteSpec(
        name="bad-tolerance",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        parking_quotas={"ev_min": 1},
        site_features=[_charger(origin=(1.2, 10.0))],
        constraints={"ev_charger_touch_tolerance": -2},
    )
    report = validate_route_usability(_layout(site, stall_x=2.0))

    assert report["valid"] is False
    assert report["ev_charger"]["reason"] == "invalid_ev_charger_touch_tolerance"
