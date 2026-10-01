import math
from dataclasses import replace

import pytest
from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import ParkingAisle, ParkingStall, VehicleSpec
from openparkcad.swept_path import (
    conservative_swept_envelope,
    reverse_in_90_template,
    reverse_in_angled_template,
    reverse_in_t_end_template,
    reverse_parallel_template,
    validate_stall_swept_path,
    validate_swept_path,
    vehicle_footprint,
)
from openparkcad.vehicle_kinematics import (
    MotionSegment,
    VehiclePose,
    arc_motion,
    rear_axle_turning_radius,
    simulate_bicycle_path,
    steering_angle_for_radius,
    straight_motion,
)


@pytest.fixture
def design_vehicle() -> VehicleSpec:
    return VehicleSpec(
        id="passenger-car",
        length=4.8,
        width=1.9,
        wheelbase=2.8,
        min_turning_radius=5.5,
        turning_radius_reference="outer_front_wheel",
        track_width=1.6,
        swept_path_margin=0.3,
        max_reverse_distance=12.0,
    )


def test_straight_motion_is_deterministic_and_builds_conservative_envelope(design_vehicle):
    segment = straight_motion(10.0)

    result = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), [segment], sample_step=0.5)
    repeated = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), [segment], sample_step=0.5)

    assert result.valid is True
    assert result.reason is None
    assert result.poses == repeated.poses
    assert result.final_pose == VehiclePose(10.0, 0.0, 0.0)
    assert result.reverse_distance == 0.0
    assert result.path_length == 10.0
    assert result.segments[0].turn_radius is None

    envelope = conservative_swept_envelope(design_vehicle, result.poses)
    assert envelope.bounds == pytest.approx((-1.3, -1.25, 14.1, 1.25))


def test_constant_radius_arc_uses_exact_bicycle_solution(design_vehicle):
    segment = arc_motion(design_vehicle, 90.0, radius=6.0)

    result = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), [segment])

    assert result.valid is True
    assert result.minimum_turn_radius_used == pytest.approx(6.0)
    assert result.final_pose is not None
    assert result.final_pose.x == pytest.approx(6.0)
    assert result.final_pose.y == pytest.approx(6.0)
    assert result.final_pose.heading_degrees == pytest.approx(90.0)
    assert result.segments[0].heading_change_degrees == pytest.approx(90.0)


def test_reverse_arc_tracks_distance_and_world_heading_change(design_vehicle):
    segment = arc_motion(design_vehicle, -90.0, radius=6.0, reverse=True)

    result = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), [segment])

    assert result.valid is True
    assert result.reverse_distance == pytest.approx(math.pi * 3.0)
    assert result.final_pose is not None
    assert result.final_pose.x == pytest.approx(-6.0)
    assert result.final_pose.y == pytest.approx(6.0)
    assert result.final_pose.heading_degrees == pytest.approx(270.0)
    assert result.segments[0].direction == "reverse"


def test_turn_below_vehicle_minimum_is_rejected_with_auditable_radius(design_vehicle):
    too_tight = MotionSegment(
        distance=4.0,
        steering_angle_degrees=steering_angle_for_radius(design_vehicle.wheelbase or 0.0, 3.0),
    )

    result = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), [too_tight])

    assert result.valid is False
    assert result.reason == "turning_radius_below_minimum"
    assert result.details["segment_index"] == 0
    assert result.details["observed_radius"] == pytest.approx(3.0)
    assert result.details["required_radius"] == pytest.approx(3.9339201514)
    assert result.details["turning_radius_resolution"]["input_reference"] == "outer_front_wheel"


def test_outer_front_wheel_radius_conversion_is_explicit_and_auditable(design_vehicle):
    resolution = rear_axle_turning_radius(design_vehicle)

    assert resolution.valid is True
    assert resolution.rear_axle_radius == pytest.approx(3.9339201514)
    assert resolution.track_width == 1.6
    assert resolution.formula == "R_rear = sqrt(R_outer^2 - wheelbase^2) - track_width/2"
    assert resolution.assumptions == ()


def test_missing_track_width_fails_hard_but_has_audited_conservative_non_hard_mode(design_vehicle):
    without_track = replace(design_vehicle, track_width=None)

    strict = rear_axle_turning_radius(without_track)
    exploratory = rear_axle_turning_radius(without_track, require_explicit_track_width=False)

    assert strict.valid is False
    assert strict.reason == "vehicle_track_width_missing"
    assert exploratory.valid is True
    assert exploratory.rear_axle_radius == pytest.approx(math.sqrt(5.5**2 - 2.8**2))
    assert exploratory.assumptions == ("track_width_assumed_zero_for_conservative_radius",)


def test_rear_axle_radius_reference_needs_no_track_width(design_vehicle):
    rear_reference = replace(
        design_vehicle,
        min_turning_radius=4.2,
        turning_radius_reference="rear_axle_center",
        track_width=None,
    )

    resolution = rear_axle_turning_radius(rear_reference)

    assert resolution.valid is True
    assert resolution.rear_axle_radius == 4.2
    assert resolution.formula == "R_rear = R_input"


def test_unknown_turning_radius_reference_fails_closed(design_vehicle):
    resolution = rear_axle_turning_radius(replace(design_vehicle, turning_radius_reference="guess"))

    assert resolution.valid is False
    assert resolution.reason == "unsupported_turning_radius_reference"


def test_cumulative_reverse_distance_limit_is_enforced(design_vehicle):
    commands = [straight_motion(7.0, reverse=True), straight_motion(6.0, reverse=True)]

    result = simulate_bicycle_path(design_vehicle, VehiclePose(0.0, 0.0), commands)

    assert result.valid is False
    assert result.reason == "maximum_reverse_distance_exceeded"
    assert result.reverse_distance == 13.0
    assert result.details["maximum_reverse_distance"] == 12.0


def test_vehicle_footprint_uses_balanced_overhang_and_margin(design_vehicle):
    footprint = vehicle_footprint(design_vehicle, VehiclePose(2.0, 3.0, 90.0))

    assert footprint.area == pytest.approx((4.8 + 0.6) * (1.9 + 0.6))
    assert footprint.bounds == pytest.approx((0.75, 1.7, 3.25, 7.1))


def test_swept_path_rejects_vehicle_body_leaving_boundary():
    vehicle = VehicleSpec(
        length=2.0,
        width=1.0,
        wheelbase=1.0,
        min_turning_radius=2.0,
        max_reverse_distance=5.0,
    )

    result = validate_swept_path(
        vehicle,
        VehiclePose(1.0, 2.0),
        [straight_motion(7.0)],
        boundary=[(0.0, 0.0), (8.0, 0.0), (8.0, 4.0), (0.0, 4.0)],
    )

    assert result.valid is False
    assert result.reason == "swept_path_outside_boundary"
    assert result.collision_geometry.area > 0.0


def test_swept_path_rejects_obstacle_collision_and_reports_index():
    vehicle = VehicleSpec(
        length=2.0,
        width=1.0,
        wheelbase=1.0,
        min_turning_radius=2.0,
        swept_path_margin=0.1,
        max_reverse_distance=5.0,
    )
    obstacles = [
        ShapelyPolygon([(20.0, 20.0), (21.0, 20.0), (21.0, 21.0), (20.0, 21.0)]),
        ShapelyPolygon([(4.0, 1.0), (5.0, 1.0), (5.0, 3.0), (4.0, 3.0)]),
    ]

    result = validate_swept_path(
        vehicle,
        VehiclePose(1.0, 2.0),
        [straight_motion(5.0)],
        boundary=[(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (0.0, 4.0)],
        obstacles=obstacles,
    )

    assert result.valid is False
    assert result.reason == "swept_path_intersects_obstacle"
    assert result.colliding_obstacle_indices == (1,)
    assert result.collision_geometry.area > 0.0
    assert result.to_record(include_trajectory=False)["pose_count"] > 1


def test_valid_swept_path_has_no_collision_geometry():
    vehicle = VehicleSpec(
        length=2.0,
        width=1.0,
        wheelbase=1.0,
        min_turning_radius=2.0,
        max_reverse_distance=5.0,
    )

    result = validate_swept_path(
        vehicle,
        VehiclePose(1.0, 2.0),
        [straight_motion(5.0)],
        boundary=[(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (0.0, 4.0)],
        obstacles=[[(8.5, 0.0), (9.5, 0.0), (9.5, 1.0), (8.5, 1.0)]],
    )

    assert result.valid is True
    assert result.reason is None
    assert result.envelope.area > 0.0
    assert result.collision_geometry.is_empty


def test_reverse_in_90_template_binds_vehicle_path_to_stall_and_serving_aisle(design_vehicle):
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 0.0), (20.0, 0.0), (20.0, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )

    template = reverse_in_90_template(design_vehicle, stall, aisle)
    validation = validate_stall_swept_path(
        design_vehicle,
        stall,
        aisle,
        boundary=[(-1.0, -1.0), (21.0, -1.0), (21.0, 12.0), (-1.0, 12.0)],
    )

    assert template.valid is True
    assert template.reason is None
    assert template.start_pose is not None
    assert template.final_pose is not None
    assert template.final_pose.heading_degrees == pytest.approx(270.0)
    assert template.rear_axle_turn_radius == pytest.approx(3.9339201514)
    assert template.details["template_version"] == "reverse_in_90_bicycle_v1"
    assert template.details["path_length"] == pytest.approx(template.details["reverse_distance"])
    assert template.details["arc_end_depth"] > 0.0
    assert template.details["reverse_distance"] <= design_vehicle.max_reverse_distance
    assert validation.valid is True
    assert validation.details["template"]["variant"] in {
        "front_edge_start_to_end",
        "front_edge_end_to_start",
    }


def test_reverse_in_90_template_fails_closed_when_serving_aisle_is_too_narrow(design_vehicle):
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
    )
    narrow_aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 1.0), (20.0, 1.0), (20.0, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )

    template = reverse_in_90_template(design_vehicle, stall, narrow_aisle)
    validation = validate_stall_swept_path(design_vehicle, stall, narrow_aisle)

    assert template.valid is False
    assert template.reason == "serving_aisle_too_narrow_for_reverse_in_90"
    assert template.details["outside_drivable_area"] > 0.0
    assert validation.valid is False
    assert validation.reason == "serving_aisle_too_narrow_for_reverse_in_90"


def test_reverse_in_90_forbidden_centerline_keeps_rear_axle_on_stall_side(design_vehicle):
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 0.0), (20.0, 0.0), (20.0, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )

    template = reverse_in_90_template(
        design_vehicle,
        stall,
        aisle,
        centerline_crossing="forbidden",
    )

    assert template.valid is True
    assert template.details["centerline_crossing"] == "forbidden"
    assert template.details["reference_path_outside_length"] == 0.0
    assert template.details["arc_end_depth"] >= 1.0


def test_reverse_in_90_fails_when_explicit_reference_area_cannot_contain_rear_axle_path(design_vehicle):
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 0.0), (20.0, 0.0), (20.0, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )

    result = validate_stall_swept_path(
        design_vehicle,
        stall,
        aisle,
        centerline_crossing="forbidden",
        allowed_reference_area=stall.polygon,
    )

    assert result.valid is False
    assert result.reason == "rear_axle_crosses_forbidden_aisle_centerline"
    assert result.details["template"]["details"]["reference_path_outside_length"] > 0.0


def _angled_60_stall_and_aisle(*, aisle_y_min: float = 0.0) -> tuple[ParkingStall, ParkingAisle]:
    angle = 60.0
    theta = math.radians(angle)
    width, length = 2.5, 5.0
    front_pitch = width / math.sin(theta)
    forward_shift = length * math.cos(theta)
    lateral_depth = length * math.sin(theta)
    start_u = 8.0
    front_v = 6.0
    stall = ParkingStall(
        id="P-001",
        polygon=[
            (start_u, front_v),
            (start_u + front_pitch, front_v),
            (start_u + front_pitch + forward_shift, front_v + lateral_depth),
            (start_u + forward_shift, front_v + lateral_depth),
        ],
        angle_degrees=60.0,
        served_by_aisle_id="A-MAIN",
        aisle_side="left",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, aisle_y_min), (30.0, aisle_y_min), (30.0, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )
    return stall, aisle


def test_reverse_in_90_template_rejects_acute_parallelogram(design_vehicle):
    stall, aisle = _angled_60_stall_and_aisle()
    template = reverse_in_90_template(design_vehicle, stall, aisle)
    assert template.valid is False
    assert template.reason == "stall_not_perpendicular_to_serving_aisle"


def test_reverse_in_angled_template_binds_path_to_parallelogram_stall(design_vehicle):
    stall, aisle = _angled_60_stall_and_aisle()
    template = reverse_in_angled_template(design_vehicle, stall, aisle)
    validation = validate_stall_swept_path(
        design_vehicle,
        stall,
        aisle,
        boundary=[(-1.0, -1.0), (31.0, -1.0), (31.0, 14.0), (-1.0, 14.0)],
        stall_family="angled",
    )

    assert template.valid is True
    assert template.reason is None
    assert template.start_pose is not None
    assert template.final_pose is not None
    assert template.details["template_version"] == "reverse_in_angled_bicycle_v1"
    assert abs(template.details["heading_change_degrees"]) == pytest.approx(60.0, abs=0.05)
    assert template.details["path_length"] == pytest.approx(template.details["reverse_distance"])
    assert template.details["reverse_distance"] <= design_vehicle.max_reverse_distance
    assert validation.valid is True
    assert validation.details["template"]["details"]["template_version"] == "reverse_in_angled_bicycle_v1"


def test_validate_stall_swept_path_auto_detects_angled_parallelogram(design_vehicle):
    stall, aisle = _angled_60_stall_and_aisle()
    validation = validate_stall_swept_path(design_vehicle, stall, aisle)
    assert validation.valid is True
    assert validation.details["template"]["details"]["template_version"] == "reverse_in_angled_bicycle_v1"


def test_reverse_in_angled_template_fails_closed_when_serving_aisle_is_too_narrow(design_vehicle):
    stall, aisle = _angled_60_stall_and_aisle(aisle_y_min=2.0)
    template = reverse_in_angled_template(design_vehicle, stall, aisle)
    validation = validate_stall_swept_path(design_vehicle, stall, aisle, stall_family="angled")

    assert template.valid is False
    assert template.reason == "serving_aisle_too_narrow_for_reverse_in_angled"
    assert template.details["outside_drivable_area"] > 0.0
    assert validation.valid is False
    assert validation.reason == "serving_aisle_too_narrow_for_reverse_in_angled"


def _parallel_stall_and_aisle(*, aisle_x_max: float = 24.0, stall_length: float = 6.0) -> tuple[ParkingStall, ParkingAisle]:
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (8.0 + stall_length, 6.0), (8.0 + stall_length, 8.5), (8.0, 8.5)],
        angle_degrees=0.0,
        served_by_aisle_id="A-MAIN",
        aisle_side="left",
    )
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 0.0), (aisle_x_max, 0.0), (aisle_x_max, 6.0), (0.0, 6.0)],
        angle_degrees=0.0,
    )
    return stall, aisle


def test_reverse_parallel_template_binds_s_curve_to_stall_and_aisle(design_vehicle):
    stall, aisle = _parallel_stall_and_aisle()
    template = reverse_parallel_template(design_vehicle, stall, aisle)
    validation = validate_stall_swept_path(
        design_vehicle,
        stall,
        aisle,
        boundary=[(-1.0, -1.0), (25.0, -1.0), (25.0, 12.0), (-1.0, 12.0)],
        stall_family="parallel",
    )

    assert template.valid is True
    assert template.reason is None
    assert template.start_pose is not None
    assert template.final_pose is not None
    assert template.details["template_version"] == "parallel_reverse_s_curve_bicycle_v1"
    assert template.details["path_length"] == pytest.approx(template.details["reverse_distance"])
    assert template.details["reverse_distance"] <= design_vehicle.max_reverse_distance
    assert len(template.segments) == 2
    assert validation.valid is True
    assert validation.details["template"]["details"]["template_version"] == "parallel_reverse_s_curve_bicycle_v1"


def test_validate_stall_swept_path_auto_detects_parallel_long_edge(design_vehicle):
    stall, aisle = _parallel_stall_and_aisle()
    validation = validate_stall_swept_path(design_vehicle, stall, aisle)
    assert validation.valid is True
    assert validation.details["template"]["details"]["template_version"] == "parallel_reverse_s_curve_bicycle_v1"


def test_reverse_parallel_template_fails_closed_when_stall_is_too_short(design_vehicle):
    stall, aisle = _parallel_stall_and_aisle(stall_length=4.0)
    template = reverse_parallel_template(design_vehicle, stall, aisle)
    assert template.valid is False
    assert template.reason == "vehicle_too_long_for_stall"


def test_reverse_parallel_template_fails_closed_when_reverse_limit_is_too_tight(design_vehicle):
    limited = replace(design_vehicle, max_reverse_distance=3.0)
    stall, aisle = _parallel_stall_and_aisle()
    template = reverse_parallel_template(limited, stall, aisle)
    validation = validate_stall_swept_path(limited, stall, aisle, stall_family="parallel")
    assert template.valid is False
    assert template.reason == "maximum_reverse_distance_exceeded"
    assert validation.valid is False
    assert validation.reason == "maximum_reverse_distance_exceeded"


def _t_end_stall_and_turnaround(*, aisle_y_min: float = 0.0) -> tuple[ParkingStall, ParkingAisle]:
    stall = ParkingStall(
        id="P-001",
        polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-TURNAROUND",
        aisle_side="end",
    )
    turnaround = ParkingAisle(
        id="A-TURNAROUND",
        polygon=[(0.0, aisle_y_min), (20.0, aisle_y_min), (20.0, 6.0), (0.0, 6.0)],
        angle_degrees=90.0,
        role="turnaround",
    )
    return stall, turnaround


def test_reverse_in_t_end_template_binds_path_to_end_bay_and_turnaround(design_vehicle):
    stall, turnaround = _t_end_stall_and_turnaround()
    template = reverse_in_t_end_template(design_vehicle, stall, turnaround)
    validation = validate_stall_swept_path(
        design_vehicle,
        stall,
        turnaround,
        boundary=[(-1.0, -1.0), (21.0, -1.0), (21.0, 13.0), (-1.0, 13.0)],
        stall_family="t_end",
    )

    assert template.valid is True
    assert template.reason is None
    assert template.start_pose is not None
    assert template.final_pose is not None
    assert template.details["template_version"] == "reverse_in_t_end_bicycle_v1"
    assert template.variant == "straight_reverse"
    assert template.details["heading_change_degrees"] == 0.0
    assert "t_end_straight_reverse_from_dead_end_court" in template.details["geometry_assumptions"]
    assert template.details["path_length"] == pytest.approx(template.details["reverse_distance"])
    assert validation.valid is True
    assert validation.details["template"]["details"]["template_version"] == "reverse_in_t_end_bicycle_v1"


def test_reverse_in_t_end_template_fails_closed_when_court_has_no_start_room(design_vehicle):
    stall, turnaround = _t_end_stall_and_turnaround(aisle_y_min=5.7)
    template = reverse_in_t_end_template(design_vehicle, stall, turnaround)
    validation = validate_stall_swept_path(design_vehicle, stall, turnaround, stall_family="t_end")

    assert template.valid is False
    assert template.reason in {
        "t_end_start_not_in_serving_aisle",
        "serving_aisle_too_narrow_for_reverse_in_t_end",
        "reverse_in_t_end_template_not_constructible",
    }
    assert validation.valid is False


def test_reverse_in_t_end_straight_reverse_uses_parent_aisle_court(design_vehicle):
    stall = ParkingStall(
        id="P-001",
        polygon=[(4.75, 10.0), (7.25, 10.0), (7.25, 15.4), (4.75, 15.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-TURNAROUND",
        aisle_side="end",
    )
    turnaround = ParkingAisle(
        id="A-TURNAROUND",
        polygon=[(0.0, 4.0), (12.0, 4.0), (12.0, 10.0), (0.0, 10.0)],
        angle_degrees=90.0,
        role="turnaround",
        parent_aisle_id="A-MAIN",
    )
    main = ParkingAisle(
        id="A-MAIN",
        polygon=[(3.0, 0.0), (9.0, 0.0), (9.0, 10.0), (3.0, 10.0)],
        angle_degrees=90.0,
        role="main",
    )
    pad_only = reverse_in_t_end_template(design_vehicle, stall, turnaround)
    with_parent = reverse_in_t_end_template(
        design_vehicle,
        stall,
        turnaround,
        drivable_area=main.polygon,
    )
    assert pad_only.valid is True or with_parent.valid is True
    assert with_parent.valid is True
    assert with_parent.variant == "straight_reverse"
