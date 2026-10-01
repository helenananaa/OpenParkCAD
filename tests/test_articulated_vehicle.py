import math
from dataclasses import replace

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.maneuver_validation import validate_maneuvers
from openparkcad.models import (
    AisleClassSpec,
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteSpec,
    StallSpec,
    TrailerSpec,
    VehicleSpec,
    is_articulated_vehicle,
    site_from_dict,
)
from openparkcad.swept_path import validate_stall_swept_path
from openparkcad.vehicle_kinematics import articulated_off_tracking, resolve_articulated_geometry


def _tractor_trailer(*, hitch_offset: float | None = 0.5, turning_radius: float = 12.0) -> VehicleSpec:
    return VehicleSpec(
        id="wb-15",
        length=6.2,
        width=2.55,
        wheelbase=3.8,
        min_turning_radius=turning_radius,
        turning_radius_reference="outer_front_wheel",
        track_width=2.0,
        front_overhang=1.4,
        rear_overhang=1.0,
        swept_path_margin=0.3,
        max_reverse_distance=40.0,
        configuration="articulated",
        hitch_offset=hitch_offset,
        trailer=TrailerSpec(
            length=13.6,
            width=2.55,
            wheelbase=8.0,
            track_width=2.0,
            front_overhang=1.5,
            rear_overhang=4.1,
        ),
    )


def _truck_stall() -> StallSpec:
    return StallSpec(width=3.5, length=20.0, allowed_angles=(90.0,))


def _articulated_layout(
    vehicle: VehicleSpec,
    *,
    stall: StallSpec | None = None,
    aisle_width: float = 12.0,
    require_swept_path: bool = False,
    require_turning_radius: bool = True,
) -> LayoutResult:
    stall = stall or _truck_stall()
    site = SiteSpec(
        name="articulated-vehicle",
        boundary=[(0, 0), (40, 0), (40, 40), (0, 40)],
        stall=stall,
        aisle_width=aisle_width,
        margin=0.0,
        vehicle=vehicle,
        constraints={
            "maneuvering": {
                "require_turning_radius_check": require_turning_radius,
                "require_swept_path_check": require_swept_path,
            }
        },
        aisle_classes=[
            AisleClassSpec(
                id="truck-aisle",
                width=aisle_width,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="truck-aisle",
    )
    return LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(12, 12), (15.5, 12), (15.5, 32), (12, 32)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (40, 0), (40, 12), (0, 12)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def test_trailer_fields_parse_and_mark_vehicle_articulated():
    site = site_from_dict(
        {
            "name": "articulated-parse",
            "site": {"boundary": {"type": "polygon", "points": [[0, 0], [20, 0], [20, 20], [0, 20]]}},
            "vehicles": {
                "design_vehicle": {
                    "id": "wb-15",
                    "length": 6.2,
                    "width": 2.55,
                    "wheelbase": 3.8,
                    "hitch_offset": 0.5,
                    "trailer": {
                        "length": 13.6,
                        "width": 2.55,
                        "wheelbase": 8.0,
                        "front_overhang": 1.5,
                        "rear_overhang": 4.1,
                    },
                }
            },
        }
    )

    assert site.vehicle is not None
    assert is_articulated_vehicle(site.vehicle)
    assert site.vehicle.configuration == "articulated"
    assert site.vehicle.hitch_offset == 0.5
    assert site.vehicle.trailer is not None
    assert site.vehicle.trailer.length == 13.6
    assert site.vehicle.trailer.wheelbase == 8.0


def test_combination_length_matches_front_to_hitch_plus_trailer_rear():
    geometry = resolve_articulated_geometry(_tractor_trailer())
    expected = 1.4 + 3.8 + 0.5 + (13.6 - 1.5)
    assert geometry.valid is True
    assert geometry.combination_length is not None
    assert abs(geometry.combination_length - expected) < 1e-9


def test_missing_hitch_offset_is_assumed_zero_and_reported():
    geometry = resolve_articulated_geometry(_tractor_trailer(hitch_offset=None))
    assert geometry.valid is True
    assert geometry.hitch_offset == 0.0
    assert "hitch_offset_assumed_zero" in geometry.assumptions


def test_articulated_without_trailer_fails_geometry():
    vehicle = replace(_tractor_trailer(), trailer=None)
    geometry = resolve_articulated_geometry(vehicle)
    assert geometry.valid is False
    assert geometry.reason == "articulated_trailer_missing"


def test_off_tracking_formula_is_auditable():
    vehicle = _tractor_trailer()
    tracking = articulated_off_tracking(vehicle, aisle_width=12.0)
    radius = tracking.tractor_rear_axle_radius
    assert tracking.valid is True
    assert radius is not None
    hitch_radius = math.sqrt(radius * radius + 0.5 * 0.5)
    trailer_radius = math.sqrt(hitch_radius * hitch_radius - 8.0 * 8.0)
    expected = radius - trailer_radius
    assert tracking.off_tracking is not None
    assert abs(tracking.off_tracking - expected) < 1e-9
    assert tracking.required_aisle_width is not None
    assert tracking.required_aisle_width == 2.55 + expected + 2 * 0.3


def test_tight_turning_radius_fails_closed_as_trailer_jackknife_bound():
    tracking = articulated_off_tracking(_tractor_trailer(turning_radius=7.5), aisle_width=12.0)
    assert tracking.valid is False
    assert tracking.reason == "articulated_turn_tighter_than_trailer_wheelbase"


def test_narrow_aisle_fails_off_tracking_bound():
    tracking = articulated_off_tracking(_tractor_trailer(), aisle_width=6.0)
    assert tracking.valid is False
    assert tracking.reason == "aisle_too_narrow_for_articulated_off_tracking"


def test_exact_swept_path_fails_closed_and_does_not_run_bicycle_template():
    layout = _articulated_layout(_tractor_trailer(), require_swept_path=True, require_turning_radius=False)
    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    assert validation["vehicle_validation"]["valid"] is False
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["reason"] == "articulated_vehicle_template_not_supported"
    assert check["rule_id"] == "vehicle_template_dispatch_v1"
    assert check["vehicle_class"] == "articulated"
    assert "reverse_in_90_bicycle_v1" not in validation.get("vehicle_rule_counts", {})


def test_validate_stall_swept_path_rejects_articulated_vehicle():
    vehicle = _tractor_trailer()
    stall = ParkingStall(
        id="P-001",
        polygon=[(12, 12), (15.5, 12), (15.5, 32), (12, 32)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
        aisle_side="left",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0, 0), (40, 0), (40, 12), (0, 12)],
        angle_degrees=90.0,
        role="main",
    )

    result = validate_stall_swept_path(vehicle, stall, aisle)

    assert result.valid is False
    assert result.reason == "articulated_vehicle_template_not_supported"


def test_conservative_analytic_accepts_auditable_truck_stall():
    layout = _articulated_layout(_tractor_trailer())
    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["articulated_vehicle_analytic_v1"] == 1
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["status"] == "active_conservative"
    assert check["vehicle_class"] == "articulated"
    assert check["articulated_geometry"]["valid"] is True
    assert check["articulated_off_tracking"]["valid"] is True
    rear_radius = check["turning_radius_resolution"]["rear_axle_radius"]
    assert check["reverse_distance_upper_bound"] == math.pi * rear_radius / 2.0 + 13.6


def test_conservative_analytic_rejects_car_sized_stall():
    layout = _articulated_layout(
        _tractor_trailer(),
        stall=StallSpec(width=3.5, length=5.3, allowed_angles=(90.0,)),
    )
    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["reason"] == "vehicle_too_long_for_stall"
    assert check["rule_id"] == "articulated_vehicle_analytic_v1"


def test_conservative_analytic_rejects_incomplete_trailer():
    vehicle = replace(_tractor_trailer(), trailer=None)
    layout = _articulated_layout(vehicle)
    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["reason"] == "articulated_trailer_missing"


def test_unknown_configuration_without_trailer_fails_closed():
    vehicle = VehicleSpec(
        id="mystery",
        length=4.8,
        width=1.9,
        wheelbase=2.8,
        min_turning_radius=5.5,
        track_width=1.6,
        configuration="bus",
    )
    layout = _articulated_layout(vehicle, stall=StallSpec(width=2.5, length=5.3, allowed_angles=(90.0,)))
    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["reason"] == "vehicle_configuration_not_supported"


def test_field_support_reports_articulated_exact_as_active_failed():
    layout = _articulated_layout(_tractor_trailer(), require_swept_path=True)
    layout = replace(layout, maneuver_validation=validate_maneuvers(layout))
    diagnostics = build_input_diagnostics(layout.site, layout)

    assert diagnostics["field_support"]["vehicles.articulated"] == "active_failed"
    assert diagnostics["field_support"]["constraints.swept_path"] == "active_failed"
    assert "no exact articulated swept-path template" in diagnostics["vehicle_validation"]["scope"]
    assert any("no exact swept-path template" in warning for warning in diagnostics["warnings"])


def test_field_support_reports_articulated_analytic_as_active_conservative():
    layout = _articulated_layout(_tractor_trailer())
    layout = replace(layout, maneuver_validation=validate_maneuvers(layout))
    diagnostics = build_input_diagnostics(layout.site, layout)

    assert diagnostics["field_support"]["vehicles.articulated"] == "active_conservative"
    assert diagnostics["field_support"]["vehicles.design_vehicle"] == "active_conservative"
    assert "trailer off-tracking" in diagnostics["vehicle_validation"]["scope"]


def test_generated_layout_fails_closed_when_articulated_swept_path_is_requested():
    site = SiteSpec(
        name="articulated-generate",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        vehicle=_tractor_trailer(),
        constraints={"maneuvering": {"require_swept_path_check": True}},
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=7.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="wide-two-way",
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    )

    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.maneuver_validation["vehicle_validation"]["valid"] is False
    assert diagnostics["field_support"]["vehicles.articulated"] == "active_failed"
    checks = layout.maneuver_validation["vehicle_validation"]["checks"]
    assert checks
    assert all(item["reason"] == "articulated_vehicle_template_not_supported" for item in checks)
