from __future__ import annotations

import pytest

from openparkcad.models import LayoutResult, ParkingStall, SiteSpec, StallSpec
from openparkcad.parking_motion_adapter import parking_motion_for_stall, reverse_motion_segments, stall_family_for
from openparkcad.road_transitions import build_occupancy
from openparkcad.road_traversal_models import parse_traversal_policy, poses_joinable
from openparkcad.swept_path import reverse_in_90_template
from openparkcad.vehicle_kinematics import simulate_bicycle_path
from tests.road_traversal_support import design_vehicle, through_layout


def test_perpendicular_adapter_returns_real_template_poses_and_exit_candidate() -> None:
    layout = through_layout()
    stall = layout.stalls[0]
    aisle = layout.aisles[0]
    vehicle = design_vehicle()
    template = reverse_in_90_template(vehicle, stall, aisle)
    assert template.valid is True
    occupancy = build_occupancy(layout)
    policy = parse_traversal_policy(layout.site)
    motion = parking_motion_for_stall(layout, stall, occupancy, policy, vehicle)

    assert motion.valid is True
    assert motion.family == "perpendicular"
    assert motion.start_state is not None
    assert motion.parked_state is not None
    assert motion.exit_state is not None
    assert poses_joinable(motion.start_state.pose, template.start_pose)
    assert poses_joinable(motion.parked_state.pose, template.final_pose)
    outbound = simulate_bicycle_path(vehicle, motion.parked_state.pose, list(motion.outbound_segments))
    assert outbound.valid is True
    assert outbound.final_pose is not None
    assert poses_joinable(outbound.final_pose, motion.start_state.pose)
    assert motion.details["parking_reverse_distance"] == pytest.approx(motion.reverse_distance)
    assert "exit_reverse_distance" in motion.details


def test_time_reversal_is_rechecked_and_not_used_as_one_way_road_exit() -> None:
    segments = reverse_in_90_template(
        design_vehicle(),
        through_layout().stalls[0],
        through_layout().aisles[0],
    ).segments
    reversed_segments = reverse_motion_segments(segments)
    assert len(reversed_segments) == len(segments)
    assert reversed_segments[0].distance == pytest.approx(-segments[-1].distance)
    one_way = through_layout(one_way=True)
    motion = parking_motion_for_stall(
        one_way,
        one_way.stalls[0],
        build_occupancy(one_way),
        parse_traversal_policy(one_way.site),
        design_vehicle(),
    )
    assert motion.valid is True
    assert motion.exit_state is not None
    assert motion.exit_state.pose.heading_degrees == pytest.approx(motion.start_state.pose.heading_degrees)
    # Exit pose keeps the approach heading; callers must not reverse the inbound road path.


def test_unsupported_family_is_not_reported_as_covered() -> None:
    layout = through_layout()
    stall = ParkingStall(
        id="P-ODD",
        polygon=layout.stalls[0].polygon,
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
        stall_type_id="odd-family",
    )
    site = SiteSpec(
        name=layout.site.name,
        boundary=layout.site.boundary,
        stall=StallSpec(id="odd-family", family="herringbone", width=2.5, length=5.0),
        stall_candidates=(StallSpec(id="odd-family", family="herringbone", width=2.5, length=5.0),),
        entrances=layout.site.entrances,
        vehicle=layout.site.vehicle,
        constraints=layout.site.constraints,
    )
    odd = LayoutResult(site=site, stalls=[stall], aisles=layout.aisles)
    assert stall_family_for(odd, stall) == "herringbone"
    motion = parking_motion_for_stall(
        odd, stall, build_occupancy(odd), parse_traversal_policy(odd.site), design_vehicle()
    )
    assert motion.valid is False
    assert motion.reason == "parking_family_unsupported"
    assert motion.details.get("unsupported") is True
