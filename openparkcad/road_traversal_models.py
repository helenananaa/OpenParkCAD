"""Versioned objects and input parsing for interior road-traversal checks."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from openparkcad.models import LayoutResult, SiteSpec, VehicleSpec, is_articulated_vehicle
from openparkcad.vehicle_kinematics import MotionSegment, VehiclePose

ALGORITHM_VERSION = "road-traversal-1"
DEFAULT_TIME_BUDGET_SECONDS = 10.0
POSITION_TOLERANCE_M = 1e-3
HEADING_TOLERANCE_DEGREES = 0.05
SAMPLE_STEP_M = 0.2
MAX_HEADING_STEP_DEGREES = 2.0
ALLOWED_SCOPE = "site_interior"
KNOWN_ROAD_TRAVERSAL_KEYS = frozenset({"enabled", "scope", "time_budget_seconds"})

SUPPORTED_ROAD_FAMILIES = (
    "straight_forward",
    "entrance_throat_to_main",
    "main_to_exit_throat",
    "main_branch_left_turn",
    "main_branch_right_turn",
    "dogleg_single_jog",
    "dogleg_double_jog",
    "turnaround_u_turn",
    "u_connector",
    "exit_turn",
)
UNSUPPORTED_ROAD_FAMILIES = (
    "reverse_road_travel",
    "offsite_approach",
    "articulated_vehicle",
    "arbitrary_junction_angle",
)
SUPPORTED_PARKING_FAMILIES = ("perpendicular", "angled", "parallel", "t_end")


@dataclass(frozen=True)
class TraversalPolicy:
    requested: bool
    enabled: bool
    scope: str
    time_budget_seconds: float
    vehicle_configuration: str
    allow_forward_road_only: bool
    allow_reverse_parking: bool
    position_tolerance_m: float
    heading_tolerance_degrees: float
    sample_step_m: float
    max_heading_step_degrees: float
    algorithm_version: str
    source_maneuvering: dict[str, Any] = field(default_factory=dict)
    budget_source: str = "constraints.road_traversal.time_budget_seconds"

    def to_record(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "enabled": self.enabled,
            "scope": self.scope,
            "time_budget_seconds": self.time_budget_seconds,
            "vehicle_configuration": self.vehicle_configuration,
            "allow_forward_road_only": self.allow_forward_road_only,
            "allow_reverse_parking": self.allow_reverse_parking,
            "position_tolerance_m": self.position_tolerance_m,
            "heading_tolerance_degrees": self.heading_tolerance_degrees,
            "sample_step_m": self.sample_step_m,
            "max_heading_step_degrees": self.max_heading_step_degrees,
            "algorithm_version": self.algorithm_version,
            "source_maneuvering": dict(self.source_maneuvering),
            "budget_source": self.budget_source,
        }


@dataclass(frozen=True)
class TraversalState:
    state_id: str
    kind: str
    pose: VehiclePose
    travel_direction: str
    aisle_id: str | None = None
    entrance_id: str | None = None
    stall_id: str | None = None
    occupancy_region: str = "road"

    def to_record(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "kind": self.kind,
            "pose": _pose_record(self.pose),
            "travel_direction": self.travel_direction,
            "aisle_id": self.aisle_id,
            "entrance_id": self.entrance_id,
            "stall_id": self.stall_id,
            "occupancy_region": self.occupancy_region,
        }


@dataclass(frozen=True)
class RoadTransition:
    transition_id: str
    family: str
    from_state: TraversalState
    to_state: TraversalState
    segments: tuple[MotionSegment, ...]
    road_relation: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "family": self.family,
            "from_state_id": self.from_state.state_id,
            "to_state_id": self.to_state.state_id,
            "road_relation": self.road_relation,
            "segments": [_segment_record(item) for item in self.segments],
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class TransitionEvidence:
    executed: bool
    status: str
    from_pose: VehiclePose | None
    to_pose: VehiclePose | None
    valid: bool | None
    reason: str | None
    collision_object: str | None
    duration_seconds: float
    envelope_area: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "status": self.status,
            "valid": self.valid,
            "reason": self.reason,
            "from_pose": _pose_record(self.from_pose),
            "to_pose": _pose_record(self.to_pose),
            "collision_object": self.collision_object,
            "duration_seconds": self.duration_seconds,
            "envelope_area": self.envelope_area,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class StallJourneyEvidence:
    stall_id: str
    status: str
    valid: bool | None
    reason: str | None
    entrance_id: str | None
    exit_id: str | None
    inbound_transition_ids: tuple[str, ...]
    parking_family: str | None
    outbound_transition_ids: tuple[str, ...]
    continuity_valid: bool | None
    reverse_distance: float
    path_length: float
    parked_pose: VehiclePose | None
    collision_object: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "stall_id": self.stall_id,
            "status": self.status,
            "valid": self.valid,
            "reason": self.reason,
            "entrance_id": self.entrance_id,
            "exit_id": self.exit_id,
            "inbound_transition_ids": list(self.inbound_transition_ids),
            "parking_family": self.parking_family,
            "outbound_transition_ids": list(self.outbound_transition_ids),
            "continuity_valid": self.continuity_valid,
            "reverse_distance": self.reverse_distance,
            "path_length": self.path_length,
            "parked_pose": _pose_record(self.parked_pose),
            "collision_object": self.collision_object,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RoadTraversalResult:
    algorithm_version: str
    layout_identity: str
    policy: TraversalPolicy
    requested: bool
    executed: bool
    status: str
    valid: bool | None
    reason: str | None
    stall_coverage: int
    stall_count: int
    time_budget_seconds: float
    elapsed_seconds: float
    budget_source: str
    journeys: tuple[StallJourneyEvidence, ...] = ()
    failures: tuple[dict[str, Any], ...] = ()
    unsupported_families: tuple[str, ...] = UNSUPPORTED_ROAD_FAMILIES
    supported_road_families: tuple[str, ...] = SUPPORTED_ROAD_FAMILIES
    supported_parking_families: tuple[str, ...] = SUPPORTED_PARKING_FAMILIES
    source_layout_id: str | None = None
    result_layout_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.algorithm_version,
            "layout_identity": self.layout_identity,
            "requested": self.requested,
            "executed": self.executed,
            "status": self.status,
            "valid": self.valid,
            "reason": self.reason,
            "scope": self.policy.scope,
            "stall_coverage": self.stall_coverage,
            "stall_count": self.stall_count,
            "time_budget_seconds": self.time_budget_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "budget_source": self.budget_source,
            "policy": self.policy.to_record(),
            "journeys": [item.to_record() for item in self.journeys],
            "failures": [dict(item) for item in self.failures],
            "supported_road_families": list(self.supported_road_families),
            "supported_parking_families": list(self.supported_parking_families),
            "unsupported_families": list(self.unsupported_families),
            "source_layout_id": self.source_layout_id,
            "result_layout_id": self.result_layout_id,
        }


def parse_road_traversal_mapping(constraints: dict[str, Any] | None) -> TraversalPolicy:
    """Parse and validate ``constraints.road_traversal``. Raises ``ValueError`` on input errors."""

    raw_constraints = constraints if isinstance(constraints, dict) else {}
    source_maneuvering = raw_constraints.get("maneuvering", {})
    if source_maneuvering is None:
        source_maneuvering = {}
    if not isinstance(source_maneuvering, dict):
        raise ValueError("constraints.maneuvering must be an object")

    if "road_traversal" not in raw_constraints:
        return _not_requested_policy(source_maneuvering)

    raw = raw_constraints.get("road_traversal")
    if raw is None:
        raise ValueError("constraints.road_traversal must be an object")
    if not isinstance(raw, dict):
        raise ValueError("constraints.road_traversal must be an object")

    unknown = sorted(set(raw) - KNOWN_ROAD_TRAVERSAL_KEYS)
    if unknown:
        raise ValueError(f"constraints.road_traversal has unknown keys: {', '.join(unknown)}")

    if "enabled" in raw and not isinstance(raw["enabled"], bool):
        raise ValueError("constraints.road_traversal.enabled must be a boolean")
    enabled = bool(raw.get("enabled", False))
    if not enabled:
        return _not_requested_policy(source_maneuvering, enabled=False)

    scope = raw.get("scope", ALLOWED_SCOPE)
    if not isinstance(scope, str) or scope != ALLOWED_SCOPE:
        raise ValueError("constraints.road_traversal.scope must be 'site_interior'")

    budget = raw.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS)
    if isinstance(budget, bool) or not isinstance(budget, int | float):
        raise ValueError("constraints.road_traversal.time_budget_seconds must be a positive finite number")
    budget_value = float(budget)
    if not math.isfinite(budget_value) or budget_value <= 0.0:
        raise ValueError("constraints.road_traversal.time_budget_seconds must be a positive finite number")

    return TraversalPolicy(
        requested=True,
        enabled=True,
        scope=scope,
        time_budget_seconds=budget_value,
        vehicle_configuration="rigid",
        allow_forward_road_only=True,
        allow_reverse_parking=True,
        position_tolerance_m=POSITION_TOLERANCE_M,
        heading_tolerance_degrees=HEADING_TOLERANCE_DEGREES,
        sample_step_m=SAMPLE_STEP_M,
        max_heading_step_degrees=MAX_HEADING_STEP_DEGREES,
        algorithm_version=ALGORITHM_VERSION,
        source_maneuvering=dict(source_maneuvering),
    )


def parse_traversal_policy(site: SiteSpec) -> TraversalPolicy:
    return parse_road_traversal_mapping(site.constraints)


def vehicle_support_reason(vehicle: VehicleSpec | None) -> str | None:
    if vehicle is None:
        return "design_vehicle_missing"
    if is_articulated_vehicle(vehicle):
        return "articulated_vehicle_unsupported"
    configuration = str(vehicle.configuration).strip().lower()
    if configuration not in {"", "rigid"}:
        return "vehicle_configuration_unsupported"
    if vehicle.wheelbase is None or not math.isfinite(vehicle.wheelbase) or vehicle.wheelbase <= 0.0:
        return "vehicle_wheelbase_missing_or_invalid"
    if vehicle.min_turning_radius is None or not math.isfinite(vehicle.min_turning_radius) or vehicle.min_turning_radius <= 0.0:
        return "vehicle_turning_radius_missing_or_invalid"
    if not math.isfinite(vehicle.length) or vehicle.length <= 0.0:
        return "invalid_vehicle_length"
    if not math.isfinite(vehicle.width) or vehicle.width <= 0.0:
        return "invalid_vehicle_width"
    return None


def wrap_heading_delta(degrees: float) -> float:
    value = (float(degrees) + 180.0) % 360.0 - 180.0
    if value <= -180.0:
        return 180.0
    return value


def headings_close(left: float, right: float, tolerance_degrees: float = HEADING_TOLERANCE_DEGREES) -> bool:
    return abs(wrap_heading_delta(left - right)) <= tolerance_degrees


def poses_joinable(
    left: VehiclePose | None,
    right: VehiclePose | None,
    *,
    position_tolerance_m: float = POSITION_TOLERANCE_M,
    heading_tolerance_degrees: float = HEADING_TOLERANCE_DEGREES,
) -> bool:
    if left is None or right is None:
        return False
    distance = math.hypot(left.x - right.x, left.y - right.y)
    return distance <= position_tolerance_m and headings_close(
        left.heading_degrees, right.heading_degrees, heading_tolerance_degrees
    )


def layout_traversal_identity(layout: LayoutResult, policy: TraversalPolicy) -> str:
    vehicle = layout.site.vehicle
    payload = {
        "algorithm_version": policy.algorithm_version,
        "scope": policy.scope,
        "requested": policy.requested,
        "position_tolerance_m": policy.position_tolerance_m,
        "heading_tolerance_degrees": policy.heading_tolerance_degrees,
        "boundary": _points_payload(layout.site.boundary),
        "obstacles": [_points_payload(item) for item in layout.site.obstacles],
        "entrances": [
            {
                "id": item.id,
                "center": list(item.center),
                "width": item.width,
                "heading_degrees": item.heading_degrees,
                "allowed_movements": list(item.allowed_movements),
                "mode": item.mode,
            }
            for item in layout.site.entrances
        ],
        "aisles": [
            {
                "id": aisle.id,
                "role": aisle.role,
                "polygon": _points_payload(aisle.polygon),
                "angle_degrees": aisle.angle_degrees,
                "directionality": aisle.directionality,
                "connected_to_entrance_id": aisle.connected_to_entrance_id,
                "parent_aisle_id": aisle.parent_aisle_id,
                "connected_aisle_ids": list(aisle.connected_aisle_ids),
            }
            for aisle in layout.aisles
        ],
        "stalls": [
            {
                "id": stall.id,
                "polygon": _points_payload(stall.polygon),
                "angle_degrees": stall.angle_degrees,
                "served_by_aisle_id": stall.served_by_aisle_id,
                "stall_type_id": stall.stall_type_id,
            }
            for stall in layout.stalls
        ],
        "vehicle": None
        if vehicle is None
        else {
            "id": vehicle.id,
            "length": vehicle.length,
            "width": vehicle.width,
            "wheelbase": vehicle.wheelbase,
            "min_turning_radius": vehicle.min_turning_radius,
            "turning_radius_reference": vehicle.turning_radius_reference,
            "track_width": vehicle.track_width,
            "front_overhang": vehicle.front_overhang,
            "rear_overhang": vehicle.rear_overhang,
            "swept_path_margin": vehicle.swept_path_margin,
            "max_reverse_distance": vehicle.max_reverse_distance,
            "configuration": vehicle.configuration,
            "hitch_offset": vehicle.hitch_offset,
            "trailer": None if vehicle.trailer is None else {"length": vehicle.trailer.length, "width": vehicle.trailer.width},
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def road_traversal_satisfies_request(result: dict[str, Any] | RoadTraversalResult | None) -> bool:
    """True when the road check does not block publication."""

    record = result.to_record() if isinstance(result, RoadTraversalResult) else result
    if not record:
        return False
    if not record.get("requested"):
        return True
    return record.get("status") == "passed" and record.get("valid") is True


def publication_requires_road_evidence(site: SiteSpec) -> bool:
    return parse_traversal_policy(site).requested


def _not_requested_policy(source_maneuvering: dict[str, Any], *, enabled: bool = False) -> TraversalPolicy:
    return TraversalPolicy(
        requested=False,
        enabled=enabled,
        scope=ALLOWED_SCOPE,
        time_budget_seconds=DEFAULT_TIME_BUDGET_SECONDS,
        vehicle_configuration="rigid",
        allow_forward_road_only=True,
        allow_reverse_parking=True,
        position_tolerance_m=POSITION_TOLERANCE_M,
        heading_tolerance_degrees=HEADING_TOLERANCE_DEGREES,
        sample_step_m=SAMPLE_STEP_M,
        max_heading_step_degrees=MAX_HEADING_STEP_DEGREES,
        algorithm_version=ALGORITHM_VERSION,
        source_maneuvering=dict(source_maneuvering),
    )


def _pose_record(pose: VehiclePose | None) -> dict[str, float] | None:
    if pose is None:
        return None
    return {"x": pose.x, "y": pose.y, "heading_degrees": pose.heading_degrees}


def _segment_record(segment: MotionSegment) -> dict[str, Any]:
    return {
        "distance": segment.distance,
        "steering_angle_degrees": segment.steering_angle_degrees,
        "label": segment.label,
        "direction": segment.direction,
    }


def _points_payload(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[round(float(x), 9), round(float(y), 9)] for x, y in points]
