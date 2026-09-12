"""Adapt existing stall swept-path templates into composable parking/exit motions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from shapely.ops import unary_union

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, VehicleSpec
from openparkcad.road_traversal_models import TraversalPolicy, TraversalState, poses_joinable
from openparkcad.road_transitions import OccupancySet, evaluate_motion, make_state
from openparkcad.swept_path import (
    ReverseIn90Template,
    reverse_in_90_template,
    reverse_in_angled_template,
    reverse_in_t_end_template,
    reverse_parallel_template,
)
from openparkcad.vehicle_kinematics import MotionSegment, simulate_bicycle_path

_TEMPLATE_BUILDERS: dict[str, Callable[..., ReverseIn90Template]] = {
    "perpendicular": reverse_in_90_template,
    "angled": reverse_in_angled_template,
    "parallel": reverse_parallel_template,
    "t_end": reverse_in_t_end_template,
}


def _collision_sources(occupancy: OccupancySet, stall_id: str, evidence) -> dict[str, Any]:
    # Match the exact obstacle ordering used by obstacles_excluding_stall and
    # evaluate_motion. A static obstacle must never become a removable stall.
    sources: list[str | None] = []
    if not occupancy.hard_obstacles.is_empty:
        sources.append(None)
    sources.extend(key for key, geom in occupancy.stall_faces.items() if key != stall_id and not geom.is_empty)
    indices = evidence.details.get("colliding_obstacle_indices") or []
    return {
        "blocking_stall_ids": sorted({sources[i] for i in indices if 0 <= i < len(sources) and sources[i] is not None}),
        "hard_obstacle_collision": any(i < 0 or i >= len(sources) or sources[i] is None for i in indices),
    }


@dataclass(frozen=True)
class ParkingMotion:
    valid: bool
    family: str | None
    reason: str | None
    stall_id: str
    start_state: TraversalState | None
    parked_state: TraversalState | None
    exit_state: TraversalState | None
    inbound_segments: tuple[MotionSegment, ...]
    outbound_segments: tuple[MotionSegment, ...]
    reverse_distance: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "family": self.family,
            "reason": self.reason,
            "stall_id": self.stall_id,
            "start_pose": None if self.start_state is None else {
                "x": self.start_state.pose.x,
                "y": self.start_state.pose.y,
                "heading_degrees": self.start_state.pose.heading_degrees,
            },
            "parked_pose": None if self.parked_state is None else {
                "x": self.parked_state.pose.x,
                "y": self.parked_state.pose.y,
                "heading_degrees": self.parked_state.pose.heading_degrees,
            },
            "reverse_distance": self.reverse_distance,
            "details": dict(self.details),
        }


def stall_family_for(layout: LayoutResult, stall: ParkingStall) -> str:
    stall_type_id = stall.stall_type_id
    if stall_type_id:
        for spec in layout.site.stall_candidates or ():
            if spec.id == stall_type_id:
                return spec.family
        if layout.site.stall.id == stall_type_id:
            return layout.site.stall.family
    if stall.served_by_aisle_id:
        aisle = next((item for item in layout.aisles if item.id == stall.served_by_aisle_id), None)
        if aisle is not None and aisle.role == "turnaround":
            return "t_end"
    return layout.site.stall.family


def reverse_motion_segments(segments: tuple[MotionSegment, ...] | list[MotionSegment]) -> tuple[MotionSegment, ...]:
    reversed_segments: list[MotionSegment] = []
    for segment in reversed(tuple(segments)):
        reversed_segments.append(
            MotionSegment(
                distance=-segment.distance,
                steering_angle_degrees=segment.steering_angle_degrees,
                label=f"exit:{segment.label}" if segment.label else "exit",
            )
        )
    return tuple(reversed_segments)


def parking_motion_for_stall(
    layout: LayoutResult,
    stall: ParkingStall,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    vehicle: VehicleSpec,
) -> ParkingMotion:
    family = stall_family_for(layout, stall)
    builder = _TEMPLATE_BUILDERS.get(family)
    if builder is None:
        return ParkingMotion(
            valid=False,
            family=family,
            reason="parking_family_unsupported",
            stall_id=stall.id,
            start_state=None,
            parked_state=None,
            exit_state=None,
            inbound_segments=(),
            outbound_segments=(),
            reverse_distance=0.0,
            details={"unsupported": True},
        )
    aisle = _serving_aisle(layout, stall)
    if aisle is None:
        return _invalid(stall.id, family, "stall_missing_serving_aisle")
    serving_geometry = occupancy.aisle_polygons.get(aisle.id)
    stall_geometry = occupancy.stall_faces.get(stall.id)
    if serving_geometry is None or stall_geometry is None:
        return _invalid(stall.id, family, "stall_or_aisle_geometry_missing")
    drivable = unary_union((serving_geometry, stall_geometry, occupancy.road_pavement))
    template = builder(
        vehicle,
        stall,
        aisle,
        drivable_area=drivable,
        sample_step=policy.sample_step_m,
        max_heading_step_degrees=policy.max_heading_step_degrees,
    )
    if not template.valid or template.start_pose is None or template.final_pose is None:
        return _invalid(stall.id, family, template.reason or "parking_template_not_constructible", {"template": template.to_record()})

    inbound = tuple(template.segments)
    inbound_evidence = evaluate_motion(
        vehicle,
        template.start_pose,
        inbound,
        allowed=occupancy.parking_allowed(stall.id, aisle.id, margin=max(vehicle.swept_path_margin, 0.3)),
        obstacles=occupancy.obstacles_excluding_stall(stall.id),
        policy=policy,
        family=f"parking_{family}",
    )
    if not inbound_evidence.valid:
        return _invalid(
            stall.id,
            family,
            inbound_evidence.reason or "parking_inbound_collision",
            {"collision_object": inbound_evidence.collision_object, "template": template.to_record(),
             **_collision_sources(occupancy, stall.id, inbound_evidence)},
        )
    if not poses_joinable(inbound_evidence.to_pose, template.final_pose):
        return _invalid(stall.id, family, "parking_inbound_pose_mismatch")

    outbound = reverse_motion_segments(inbound)
    outbound_sim = simulate_bicycle_path(
        vehicle,
        template.final_pose,
        list(outbound),
        sample_step=policy.sample_step_m,
        max_heading_step_degrees=policy.max_heading_step_degrees,
    )
    if not outbound_sim.valid or outbound_sim.final_pose is None:
        return _invalid(stall.id, family, outbound_sim.reason or "parking_exit_simulation_failed")
    if not poses_joinable(outbound_sim.final_pose, template.start_pose):
        return _invalid(stall.id, family, "parking_exit_does_not_restore_start_pose")

    outbound_evidence = evaluate_motion(
        vehicle,
        template.final_pose,
        outbound,
        allowed=occupancy.parking_allowed(stall.id, aisle.id, margin=max(vehicle.swept_path_margin, 0.3)),
        obstacles=occupancy.obstacles_excluding_stall(stall.id),
        policy=policy,
        family=f"parking_exit_{family}",
    )
    if not outbound_evidence.valid:
        return _invalid(
            stall.id,
            family,
            outbound_evidence.reason or "parking_exit_collision",
            {"collision_object": outbound_evidence.collision_object,
             **_collision_sources(occupancy, stall.id, outbound_evidence)},
        )

    reverse_distance = sum(abs(segment.distance) for segment in inbound if segment.distance < 0.0)
    if vehicle.max_reverse_distance is not None and reverse_distance > vehicle.max_reverse_distance + 1e-6:
        return _invalid(
            stall.id,
            family,
            "parking_reverse_distance_exceeded",
            {"reverse_distance": reverse_distance, "max_reverse_distance": vehicle.max_reverse_distance},
        )

    start_state = make_state(
        "parking_start",
        template.start_pose,
        aisle_id=aisle.id,
        stall_id=stall.id,
        occupancy_region="parking",
    )
    parked_state = make_state(
        "parked",
        template.final_pose,
        aisle_id=aisle.id,
        stall_id=stall.id,
        occupancy_region="parking",
        travel_direction="reverse" if inbound and inbound[0].distance < 0 else "forward",
    )
    exit_state = make_state(
        "parking_exit",
        outbound_sim.final_pose,
        aisle_id=aisle.id,
        stall_id=stall.id,
        occupancy_region="road",
    )
    return ParkingMotion(
        valid=True,
        family=family,
        reason=None,
        stall_id=stall.id,
        start_state=start_state,
        parked_state=parked_state,
        exit_state=exit_state,
        inbound_segments=inbound,
        outbound_segments=outbound,
        reverse_distance=reverse_distance,
        details={
            "template_variant": template.variant,
            "parking_reverse_distance": reverse_distance,
            "exit_reverse_distance": sum(abs(segment.distance) for segment in outbound if segment.distance < 0.0),
            "inbound_path_length": sum(abs(segment.distance) for segment in inbound),
            "outbound_path_length": sum(abs(segment.distance) for segment in outbound),
        },
    )


def _serving_aisle(layout: LayoutResult, stall: ParkingStall) -> ParkingAisle | None:
    if stall.served_by_aisle_id:
        found = next((item for item in layout.aisles if item.id == stall.served_by_aisle_id), None)
        if found is not None:
            return found
    return layout.aisles[0] if layout.aisles else None


def _invalid(stall_id: str, family: str | None, reason: str, details: dict[str, Any] | None = None) -> ParkingMotion:
    return ParkingMotion(
        valid=False,
        family=family,
        reason=reason,
        stall_id=stall_id,
        start_state=None,
        parked_state=None,
        exit_state=None,
        inbound_segments=(),
        outbound_segments=(),
        reverse_distance=0.0,
        details=details or {},
    )
