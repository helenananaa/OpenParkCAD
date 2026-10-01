"""R1.3: independent kinematics and between-sample envelope checks."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import VehicleSpec
from openparkcad.road_transitions import farthest_body_radius, heading_step_sagitta
from openparkcad.swept_path import conservative_swept_envelope, validate_swept_path, vehicle_footprint
from openparkcad.vehicle_kinematics import VehiclePose, arc_motion, simulate_bicycle_path, straight_motion


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        id="passenger-car",
        length=4.8,
        width=1.9,
        wheelbase=2.8,
        min_turning_radius=5.5,
        turning_radius_reference="outer_front_wheel",
        track_width=1.6,
        front_overhang=1.0,
        rear_overhang=1.0,
        swept_path_margin=0.3,
    )


def test_straight_endpoint_is_hand_calculated() -> None:
    vehicle = _vehicle()
    result = simulate_bicycle_path(vehicle, VehiclePose(2.0, 3.0, 90.0), [straight_motion(5.0)], sample_step=0.5)
    # Heading 90° is +Y; 5 m forward from (2, 3) is (2, 8).
    assert result.valid is True
    assert result.final_pose == VehiclePose(2.0, 8.0, 90.0)


def test_ninety_degree_arc_endpoint_and_left_right_mirror_are_hand_calculated() -> None:
    vehicle = _vehicle()
    left = arc_motion(vehicle, 90.0, radius=6.0)
    right = arc_motion(vehicle, -90.0, radius=6.0)
    left_result = simulate_bicycle_path(vehicle, VehiclePose(0.0, 0.0, 0.0), [left])
    right_result = simulate_bicycle_path(vehicle, VehiclePose(0.0, 0.0, 0.0), [right])

    assert left_result.valid is True
    assert left_result.final_pose is not None
    assert left_result.final_pose.x == pytest.approx(6.0)
    assert left_result.final_pose.y == pytest.approx(6.0)
    assert left_result.final_pose.heading_degrees == pytest.approx(90.0)

    assert right_result.valid is True
    assert right_result.final_pose is not None
    assert right_result.final_pose.x == pytest.approx(6.0)
    assert right_result.final_pose.y == pytest.approx(-6.0)
    assert (right_result.final_pose.heading_degrees % 360.0) == pytest.approx(270.0)
    assert right_result.final_pose.y == pytest.approx(-left_result.final_pose.y)
    assert (right_result.final_pose.heading_degrees % 360.0) == pytest.approx(
        (360.0 - left_result.final_pose.heading_degrees) % 360.0
    )


def test_footprint_uses_overhang_width_radius_reference_and_margin() -> None:
    vehicle = _vehicle()
    pose = VehiclePose(0.0, 0.0, 0.0)
    body = vehicle_footprint(vehicle, pose)
    minx, miny, maxx, maxy = body.bounds
    # Rear axle at origin, heading +X. Rear = -(1.0 + 0.3), front = 2.8 + 1.0 + 0.3, half-width = 0.95 + 0.3
    assert minx == pytest.approx(-1.3)
    assert maxx == pytest.approx(4.1)
    assert miny == pytest.approx(-1.25)
    assert maxy == pytest.approx(1.25)

    missing = VehicleSpec(length=4.8, width=1.9, wheelbase=None, min_turning_radius=5.5)
    with pytest.raises(ValueError, match="wheelbase"):
        vehicle_footprint(missing, pose)


def test_obstacle_between_arc_samples_is_caught_by_conservative_envelope() -> None:
    vehicle = VehicleSpec(
        length=2.0,
        width=1.0,
        wheelbase=1.0,
        min_turning_radius=2.0,
        turning_radius_reference="rear_axle",
        track_width=0.8,
        front_overhang=0.5,
        rear_overhang=0.5,
        swept_path_margin=0.0,
    )
    # 90° left arc of radius 4 m from origin heading 0. Rear-axle path is a quarter circle.
    # At 45° the rear axle is at (4-4/√2, 4/√2) ≈ (1.172, 2.828).
    segment = arc_motion(vehicle, 90.0, radius=4.0)
    result = simulate_bicycle_path(
        vehicle,
        VehiclePose(0.0, 0.0, 0.0),
        [segment],
        sample_step=4.0,
        max_heading_step_degrees=90.0,
        require_min_turning_radius=False,
        require_explicit_track_width=False,
    )
    assert result.valid is True
    envelope = conservative_swept_envelope(vehicle, result.poses)
    # Obstacle sitting on the outer-front circular sweep, between the 0° and 90° samples.
    obstacle = ShapelyPolygon([(3.6, 3.6), (4.4, 3.6), (4.4, 4.4), (3.6, 4.4)])
    sagitta = heading_step_sagitta(farthest_body_radius(vehicle), 90.0)
    # Independent sagitta of a 4 m path with 90° step: 4*(1-cos(45°)) ≈ 1.17 m.
    assert sagitta == pytest.approx(farthest_body_radius(vehicle) * (1.0 - math.cos(math.radians(45.0))))
    grown = obstacle.buffer(sagitta)
    collision = envelope.intersects(obstacle) or envelope.intersects(grown)
    assert collision is True

    checked = validate_swept_path(
        vehicle,
        VehiclePose(0.0, 0.0, 0.0),
        [segment],
        obstacles=[obstacle],
        sample_step=0.2,
        max_heading_step_degrees=2.0,
        require_min_turning_radius=False,
        require_explicit_track_width=False,
    )
    assert checked.valid is False
    assert checked.reason == "swept_path_intersects_obstacle"
    assert checked.colliding_obstacle_indices == (0,)
