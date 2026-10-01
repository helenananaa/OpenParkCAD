from __future__ import annotations

import math

import pytest

from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, TrailerSpec, VehicleSpec
from openparkcad.road_traversal_models import (
    ALGORITHM_VERSION,
    ALLOWED_SCOPE,
    DEFAULT_TIME_BUDGET_SECONDS,
    HEADING_TOLERANCE_DEGREES,
    POSITION_TOLERANCE_M,
    RoadTraversalResult,
    layout_traversal_identity,
    parse_road_traversal_mapping,
    parse_traversal_policy,
    poses_joinable,
    road_traversal_satisfies_request,
    vehicle_support_reason,
    wrap_heading_delta,
)
from openparkcad.vehicle_kinematics import VehiclePose


def test_missing_or_disabled_road_traversal_is_not_requested() -> None:
    missing = parse_road_traversal_mapping({})
    disabled = parse_road_traversal_mapping({"road_traversal": {"enabled": False}})

    assert missing.requested is False
    assert missing.enabled is False
    assert missing.scope == ALLOWED_SCOPE
    assert missing.time_budget_seconds == DEFAULT_TIME_BUDGET_SECONDS
    assert disabled.requested is False
    assert disabled.enabled is False
    assert missing.algorithm_version == ALGORITHM_VERSION
    assert road_traversal_satisfies_request(
        RoadTraversalResult(
            algorithm_version=ALGORITHM_VERSION,
            layout_identity="x",
            policy=missing,
            requested=False,
            executed=False,
            status="not_requested",
            valid=None,
            reason=None,
            stall_coverage=0,
            stall_count=0,
            time_budget_seconds=10.0,
            elapsed_seconds=0.0,
            budget_source="constraints.road_traversal.time_budget_seconds",
        )
    )


def test_enabled_site_interior_policy_is_requested_with_effective_vehicle_strategy() -> None:
    policy = parse_road_traversal_mapping(
        {
            "maneuvering": {"require_swept_path_check": False, "max_reverse_distance": 8.0},
            "road_traversal": {"enabled": True, "scope": "site_interior", "time_budget_seconds": 4.5},
        }
    )

    assert policy.requested is True
    assert policy.enabled is True
    assert policy.scope == "site_interior"
    assert policy.time_budget_seconds == 4.5
    assert policy.allow_forward_road_only is True
    assert policy.allow_reverse_parking is True
    assert policy.vehicle_configuration == "rigid"
    assert policy.source_maneuvering["max_reverse_distance"] == 8.0
    assert policy.source_maneuvering["require_swept_path_check"] is False
    record = policy.to_record()
    assert record["position_tolerance_m"] == POSITION_TOLERANCE_M
    assert record["heading_tolerance_degrees"] == HEADING_TOLERANCE_DEGREES


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"enabled": True, "scope": "offsite"}, "scope"),
        ({"enabled": True, "time_budget_seconds": True}, "time_budget_seconds"),
        ({"enabled": True, "time_budget_seconds": 0}, "time_budget_seconds"),
        ({"enabled": True, "time_budget_seconds": math.inf}, "time_budget_seconds"),
        ({"enabled": "yes"}, "enabled"),
        ({"enabled": True, "mystery": 1}, "unknown"),
        (["enabled"], "object"),
    ],
)
def test_invalid_road_traversal_mapping_is_input_error(raw: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_road_traversal_mapping({"road_traversal": raw})


def test_null_road_traversal_block_is_input_error() -> None:
    with pytest.raises(ValueError, match="object"):
        parse_road_traversal_mapping({"road_traversal": None})


def test_status_publication_requires_passed_and_valid_true() -> None:
    policy = parse_road_traversal_mapping({"road_traversal": {"enabled": True}})
    base = dict(
        algorithm_version=ALGORITHM_VERSION,
        layout_identity="id",
        policy=policy,
        requested=True,
        executed=True,
        stall_coverage=1,
        stall_count=1,
        time_budget_seconds=10.0,
        elapsed_seconds=0.1,
        budget_source="constraints.road_traversal.time_budget_seconds",
    )
    passed = RoadTraversalResult(status="passed", valid=True, reason=None, **base)
    failed = RoadTraversalResult(status="failed", valid=False, reason="no_supported_route_found", **base)
    unsupported = RoadTraversalResult(status="unsupported", valid=None, reason="articulated_vehicle_unsupported", **base)
    incomplete = RoadTraversalResult(status="incomplete", valid=None, reason="time_budget_exhausted", **base)
    failed_but_true = RoadTraversalResult(status="failed", valid=True, reason="inconsistent", **base)

    assert road_traversal_satisfies_request(passed) is True
    assert road_traversal_satisfies_request(passed.to_record()) is True
    assert road_traversal_satisfies_request(failed) is False
    assert road_traversal_satisfies_request(unsupported) is False
    assert road_traversal_satisfies_request(incomplete) is False
    assert road_traversal_satisfies_request(failed_but_true) is False
    assert road_traversal_satisfies_request(None) is False
    assert road_traversal_satisfies_request({}) is False


def test_vehicle_support_and_pose_join_contract() -> None:
    rigid = VehicleSpec(wheelbase=2.8, min_turning_radius=5.5, length=4.8, width=1.9)
    trailer = VehicleSpec(
        configuration="articulated",
        wheelbase=2.8,
        min_turning_radius=8.0,
        trailer=TrailerSpec(length=6.0, width=2.2),
    )
    assert vehicle_support_reason(None) == "design_vehicle_missing"
    assert vehicle_support_reason(trailer) == "articulated_vehicle_unsupported"
    assert vehicle_support_reason(rigid) is None
    assert wrap_heading_delta(270) == pytest.approx(-90)
    assert poses_joinable(VehiclePose(0.0, 0.0, 0.0), VehiclePose(0.0005, 0.0, 359.99))
    assert not poses_joinable(VehiclePose(0.0, 0.0, 0.0), VehiclePose(0.02, 0.0, 0.0))
    assert not poses_joinable(VehiclePose(0.0, 0.0, 0.0), VehiclePose(0.0, 0.0, 1.0))


def test_layout_identity_changes_with_geometry_vehicle_and_policy() -> None:
    site = SiteSpec(
        name="id-site",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        entrances=[EntranceSpec("E1", "shared", (10, 0), 8.0, 90.0)],
        vehicle=VehicleSpec(length=4.8, width=1.9, wheelbase=2.8, min_turning_radius=5.5),
        constraints={"road_traversal": {"enabled": True}},
    )
    layout = LayoutResult(
        site=site,
        stalls=[ParkingStall("P-001", [(2, 8), (4.5, 8), (4.5, 13), (2, 13)], 90.0, served_by_aisle_id="A-1")],
        aisles=[ParkingAisle("A-1", [(5, 0), (15, 0), (15, 20), (5, 20)], 90.0, role="main")],
    )
    policy = parse_traversal_policy(site)
    first = layout_traversal_identity(layout, policy)
    moved = LayoutResult(
        site=site,
        stalls=layout.stalls,
        aisles=[ParkingAisle("A-1", [(6, 0), (16, 0), (16, 20), (6, 20)], 90.0, role="main")],
    )
    other_vehicle = LayoutResult(
        site=SiteSpec(
            name=site.name,
            boundary=site.boundary,
            entrances=site.entrances,
            vehicle=VehicleSpec(length=4.8, width=1.9, wheelbase=2.8, min_turning_radius=6.5),
            constraints=site.constraints,
        ),
        stalls=layout.stalls,
        aisles=layout.aisles,
    )
    assert first != layout_traversal_identity(moved, policy)
    assert first != layout_traversal_identity(other_vehicle, policy)
    assert first == layout_traversal_identity(layout, policy)
