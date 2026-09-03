from __future__ import annotations

import math

import pytest

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import EntranceSpec
from openparkcad.road_transitions import (
    OccupancySet,
    build_occupancy,
    connect_states,
    entrance_inner_pose,
    evaluate_motion,
    heading_step_sagitta,
    make_state,
    sample_aisle_states,
    try_arc_turn,
    try_dogleg,
    try_straight,
)
from openparkcad.road_traversal_models import parse_traversal_policy
from openparkcad.vehicle_kinematics import VehiclePose, arc_motion, simulate_bicycle_path, straight_motion
from tests.road_traversal_support import design_vehicle, through_layout


def _policy():
    return parse_traversal_policy(through_layout().site)


def _open_occupancy() -> OccupancySet:
    box = ShapelyPolygon([(-40.0, -40.0), (80.0, -40.0), (80.0, 80.0), (-40.0, 80.0)])
    return OccupancySet(
        site=box,
        road_pavement=box,
        hard_obstacles=ShapelyPolygon(),
        stall_faces={},
        aisle_polygons={"A-MAIN": box, "A-BRANCH": box, "A-TURN": box, "A-ODD": box},
        entrance_throats={},
    )


def test_straight_forward_pass_and_reject_pairs() -> None:
    vehicle = design_vehicle()
    layout = through_layout()
    occupancy = build_occupancy(layout)
    policy = _policy()
    start = make_state("road", VehiclePose(5.0, 5.0, 0.0), aisle_id="A-MAIN")
    end = make_state("road", VehiclePose(12.0, 5.0, 0.0), aisle_id="A-MAIN")
    found = try_straight(vehicle, start, end, occupancy=occupancy, policy=policy, family="straight_forward")
    assert found is not None
    transition, evidence = found
    assert evidence.valid is True
    assert transition.family == "straight_forward"
    assert evidence.to_pose is not None
    assert evidence.to_pose.x == pytest.approx(12.0)
    assert evidence.to_pose.y == pytest.approx(5.0)

    blocked = through_layout(extra_obstacle=[(8.0, 3.0), (10.0, 3.0), (10.0, 7.0), (8.0, 7.0)])
    blocked_occ = build_occupancy(blocked)
    rejected = try_straight(vehicle, start, end, occupancy=blocked_occ, policy=policy, family="straight_forward")
    assert rejected is None or rejected[1].valid is False


def test_entrance_and_exit_throat_connections() -> None:
    vehicle = design_vehicle()
    layout = through_layout()
    occupancy = build_occupancy(layout)
    policy = _policy()
    enter = layout.site.entrances[0]
    pose, reason = entrance_inner_pose(enter, vehicle, occupancy, for_exit=False)
    assert reason is None
    assert pose is not None
    # Rear axle inset by rear_overhang + margin + 0.05 = 1.35 m along heading 0.
    assert pose.x == pytest.approx(1.35)
    assert pose.y == pytest.approx(5.0)
    assert pose.heading_degrees == pytest.approx(0.0)
    body_ok = evaluate_motion(
        vehicle,
        pose,
        (straight_motion(0.0, label="hold"),),
        allowed=occupancy.site,
        obstacles=occupancy.obstacles_excluding_stall(None),
        policy=policy,
        family="entrance_hold",
    )
    assert body_ok.valid is True

    exit_ent = layout.site.entrances[1]
    exit_pose, exit_reason = entrance_inner_pose(exit_ent, vehicle, occupancy, for_exit=True)
    assert exit_reason is None
    assert exit_pose is not None
    # Front near x=40, heading 0. rear = 40 - (1.0+2.8+0.3+0.05) = 35.85
    assert exit_pose.x == pytest.approx(35.85)
    assert exit_pose.heading_degrees == pytest.approx(0.0)

    narrow = EntranceSpec("narrow", "shared", (0.0, 5.0), 1.5, 0.0)
    _pose, narrow_reason = entrance_inner_pose(narrow, vehicle, occupancy)
    assert narrow_reason == "entrance_throat_too_narrow"


def test_main_branch_left_and_right_turns() -> None:
    vehicle = design_vehicle()
    occupancy = _open_occupancy()
    policy = _policy()
    start = make_state("road", VehiclePose(0.0, 0.0, 0.0), aisle_id="A-MAIN")
    left_end_pose = simulate_bicycle_path(vehicle, start.pose, [arc_motion(vehicle, 90.0, radius=6.0)]).final_pose
    right_end_pose = simulate_bicycle_path(vehicle, start.pose, [arc_motion(vehicle, -90.0, radius=6.0)]).final_pose
    assert left_end_pose is not None and right_end_pose is not None
    left = try_arc_turn(
        vehicle,
        start,
        make_state("road", left_end_pose, aisle_id="A-BRANCH"),
        occupancy=occupancy,
        policy=policy,
        family="main_branch_left_turn",
    )
    right = try_arc_turn(
        vehicle,
        start,
        make_state("road", right_end_pose, aisle_id="A-BRANCH"),
        occupancy=occupancy,
        policy=policy,
        family="main_branch_right_turn",
    )
    assert left is not None and left[1].valid is True
    assert right is not None and right[1].valid is True

    mismatch = try_arc_turn(
        vehicle,
        start,
        make_state("road", VehiclePose(8.0, 1.0, 90.0), aisle_id="A-BRANCH"),
        occupancy=occupancy,
        policy=policy,
        family="main_branch_left_turn",
    )
    assert mismatch is None


def test_dogleg_single_and_double_have_pass_and_reject() -> None:
    vehicle = design_vehicle()
    occupancy = _open_occupancy()
    policy = _policy()
    start = make_state("road", VehiclePose(4.0, 5.0, 0.0), aisle_id="A-MAIN")
    end = make_state("road", VehiclePose(16.0, 5.4, 0.0), aisle_id="A-MAIN")
    found = try_dogleg(vehicle, start, end, occupancy=occupancy, policy=policy, family="dogleg_single_jog")
    assert found is not None
    assert found[1].valid is True
    double = try_dogleg(vehicle, start, end, occupancy=occupancy, policy=policy, family="dogleg_double_jog")
    assert double is None or double[1].executed is True

    backward = try_dogleg(
        vehicle,
        start,
        make_state("road", VehiclePose(3.0, 5.4, 0.0), aisle_id="A-MAIN"),
        occupancy=occupancy,
        policy=policy,
    )
    assert backward is None


def test_u_turn_family_and_unsupported_angle() -> None:
    vehicle = design_vehicle()
    occupancy = _open_occupancy()
    policy = _policy()
    start = make_state("road", VehiclePose(10.0, 5.0, 0.0), aisle_id="A-MAIN")
    u_end = simulate_bicycle_path(vehicle, start.pose, [arc_motion(vehicle, 180.0, radius=6.0)]).final_pose
    assert u_end is not None
    found = try_arc_turn(
        vehicle,
        start,
        make_state("road", u_end, aisle_id="A-TURN"),
        occupancy=occupancy,
        policy=policy,
        family="turnaround_u_turn",
    )
    assert found is not None and found[1].valid is True

    odd = connect_states(
        vehicle,
        start,
        make_state("road", VehiclePose(12.0, 8.0, 37.0), aisle_id="A-ODD"),
        occupancy=occupancy,
        policy=policy,
        family_hint="arbitrary_junction_angle",
    )
    assert odd is None


def test_aisle_samples_are_forward_only_and_respect_one_way() -> None:
    layout = through_layout(one_way=True)
    vehicle = design_vehicle()
    states = sample_aisle_states(layout.aisles[0], vehicle)
    headings = {round(item.pose.heading_degrees % 360.0, 1) for item in states}
    assert 0.0 in headings or any(math.isclose(item, 0.0, abs_tol=0.2) for item in headings)
    assert not any(abs((item.pose.heading_degrees % 360.0) - 180.0) < 1.0 for item in states)
    two_way = sample_aisle_states(through_layout(one_way=False).aisles[0], vehicle)
    two_headings = {round(item.pose.heading_degrees % 360.0, 1) for item in two_way}
    assert any(abs(item - 180.0) < 1.0 or abs(item) < 1.0 for item in two_headings)


def test_sagitta_buffer_is_derived_not_a_numeric_fudge() -> None:
    vehicle = design_vehicle()
    step = 2.0
    value = heading_step_sagitta(5.0, step)
    assert value == pytest.approx(5.0 * (1.0 - math.cos(math.radians(step) / 2.0)))
    assert value < vehicle.swept_path_margin
