from __future__ import annotations

import pytest

from openparkcad.models import LayoutResult, ParkingStall, SiteSpec, TrailerSpec, VehicleSpec
from openparkcad.parking_motion_adapter import parking_motion_for_stall
from openparkcad.road_transitions import build_occupancy
from openparkcad.road_traversal import apply_road_traversal, validate_road_traversal
from openparkcad.road_traversal_models import parse_traversal_policy
from openparkcad.vehicle_kinematics import VehiclePose
from tests.road_traversal_support import design_vehicle, through_layout


def test_unrequested_layout_is_not_executed() -> None:
    layout = through_layout(enabled=False)
    record = validate_road_traversal(layout)
    assert record["requested"] is False
    assert record["executed"] is False
    assert record["status"] == "not_requested"
    assert record["valid"] is None
    assert record["scope"] == "site_interior"


def test_through_site_with_90_degree_stall_covers_every_retained_stall() -> None:
    layout = through_layout()
    record = validate_road_traversal(layout)
    assert record["requested"] is True
    assert record["executed"] is True
    assert record["status"] == "passed"
    assert record["valid"] is True
    assert record["stall_coverage"] == layout.stall_count
    journey = record["journeys"][0]
    assert journey["stall_id"] == "P-001"
    assert journey["valid"] is True
    assert journey["entrance_id"] == "entry-gate"
    assert journey["exit_id"] == "exit-gate"
    assert journey["parking_family"] == "perpendicular"
    assert journey["continuity_valid"] is True
    assert journey["inbound_transition_ids"]
    assert journey["outbound_transition_ids"]
    assert journey["parked_pose"] is not None


def test_graph_contact_but_body_blocked_fails_with_collision_object() -> None:
    layout = through_layout(
        extra_obstacle=[(8.0, 2.2), (12.0, 2.2), (12.0, 7.8), (8.0, 7.8)],
        time_budget_seconds=60.0,
    )
    record = validate_road_traversal(layout)
    assert record["status"] == "failed"
    assert record["valid"] is False
    assert record["journeys"]
    assert record["journeys"][0]["valid"] is not True
    reasons = {item.get("reason") for item in record["failures"]} | {record.get("reason")}
    assert any(reason for reason in reasons if reason)


def test_rear_axle_in_site_but_body_outside_is_rejected() -> None:
    layout = through_layout()
    occupancy = build_occupancy(layout)
    vehicle = design_vehicle()
    from openparkcad.road_transitions import entrance_inner_pose

    pose, reason = entrance_inner_pose(layout.site.entrances[0], vehicle, occupancy)
    assert reason is None
    assert pose is not None
    # A pose with rear axle on the boundary and heading out of the site is not an inner anchor.
    from openparkcad.road_transitions import evaluate_motion
    from openparkcad.vehicle_kinematics import straight_motion

    policy = parse_traversal_policy(layout.site)
    outside = evaluate_motion(
        vehicle,
        VehiclePose(0.0, 5.0, 180.0),
        (straight_motion(0.0, label="hold"),),
        allowed=occupancy.site,
        obstacles=[],
        policy=policy,
        family="body_outside",
    )
    assert outside.valid is False
    assert outside.reason == "swept_path_outside_boundary"


def test_rt05_local_motions_that_do_not_join_cannot_pass() -> None:
    from openparkcad.road_transitions import connect_states, make_state
    from openparkcad.vehicle_kinematics import VehiclePose

    layout = through_layout()
    occupancy = build_occupancy(layout)
    policy = parse_traversal_policy(layout.site)
    start = make_state("road", VehiclePose(5.0, 5.0, 0.0), aisle_id="A-MAIN")
    end = make_state("road", VehiclePose(12.0, 5.0, 90.0), aisle_id="A-MAIN")
    found = connect_states(design_vehicle(), start, end, occupancy=occupancy, policy=policy)
    assert found is None


def test_rt07_second_reachable_entrance_is_enough() -> None:
    layout = through_layout()
    record = validate_road_traversal(layout)
    assert record["status"] == "passed"
    assert record["journeys"][0]["entrance_id"] == "entry-gate"


def test_rt12_occupied_adjacent_stall_is_an_obstacle() -> None:
    extra = ParkingStall(
        id="P-002",
        polygon=[(10.0, 8.0), (12.6, 8.0), (12.6, 13.4), (10.0, 13.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
    )
    layout = through_layout(extra_stall=extra)
    occupancy = build_occupancy(layout)
    assert "P-002" in occupancy.stall_faces
    obstacles = occupancy.obstacles_excluding_stall("P-001")
    assert obstacles


def test_one_way_cannot_invert_inbound_road_as_exit_evidence() -> None:
    layout = through_layout(one_way=True, second_entrance=False)
    # Shared entrance on the west with one-way eastbound: leaving west would reverse the inbound road.
    record = validate_road_traversal(layout)
    assert record["status"] in {"failed", "unsupported", "incomplete"}
    assert record["valid"] is not True
    if record["journeys"]:
        assert record["journeys"][0]["status"] != "passed" or record["journeys"][0]["exit_id"] != "entry-gate"


def test_parking_feasible_but_start_pose_unreachable_fails_complete_route() -> None:
    layout = through_layout()
    occupancy = build_occupancy(layout)
    motion = parking_motion_for_stall(
        layout, layout.stalls[0], occupancy, parse_traversal_policy(layout.site), design_vehicle()
    )
    assert motion.valid is True
    blocked = through_layout(extra_obstacle=[(7.0, 2.0), (10.0, 2.0), (10.0, 8.0), (7.0, 8.0)])
    blocked_motion = parking_motion_for_stall(
        blocked,
        blocked.stalls[0],
        build_occupancy(blocked),
        parse_traversal_policy(blocked.site),
        design_vehicle(),
    )
    assert blocked_motion.valid is True
    record = validate_road_traversal(blocked)
    assert record["status"] == "failed"
    assert record["valid"] is False
    assert record["journeys"][0]["valid"] is not True
    assert record["journeys"][0]["reason"] == "no_supported_route_found"


def test_articulated_vehicle_is_unsupported_not_a_pass() -> None:
    layout = through_layout()
    site = SiteSpec(
        name=layout.site.name,
        boundary=layout.site.boundary,
        entrances=layout.site.entrances,
        stall=layout.site.stall,
        vehicle=VehicleSpec(
            configuration="articulated",
            length=6.0,
            width=2.2,
            wheelbase=3.5,
            min_turning_radius=8.0,
            trailer=TrailerSpec(length=7.0, width=2.4),
        ),
        constraints=layout.site.constraints,
    )
    articulated = LayoutResult(site=site, stalls=layout.stalls, aisles=layout.aisles)
    record = validate_road_traversal(articulated)
    assert record["status"] == "unsupported"
    assert record["valid"] is None
    assert record["reason"] == "articulated_vehicle_unsupported"
    assert record["executed"] is True


def test_budget_exhaustion_is_incomplete_not_passed() -> None:
    layout = through_layout()
    record = validate_road_traversal(layout, deadline=0.0)
    assert record["status"] == "incomplete"
    assert record["valid"] is None
    assert record["reason"] == "time_budget_exhausted"
    assert record["status"] != "passed"


def test_identity_mismatch_is_not_reusable_across_vehicle_change() -> None:
    layout = through_layout()
    first = apply_road_traversal(layout)
    other_site = SiteSpec(
        name=layout.site.name,
        boundary=layout.site.boundary,
        entrances=layout.site.entrances,
        stall=layout.site.stall,
        vehicle=design_vehicle(min_turning_radius=8.5),
        constraints=layout.site.constraints,
    )
    other = LayoutResult(
        site=other_site,
        stalls=layout.stalls,
        aisles=layout.aisles,
        road_traversal_validation=first.road_traversal_validation,
    )
    from openparkcad.road_traversal import road_traversal_satisfied

    assert road_traversal_satisfied(first) is True
    assert road_traversal_satisfied(other) is False


def test_internal_exception_is_not_rewritten_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    layout = through_layout()

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic-internal-fault")

    monkeypatch.setattr("openparkcad.road_traversal.build_occupancy", boom)
    with pytest.raises(RuntimeError, match="synthetic-internal-fault"):
        validate_road_traversal(layout)
