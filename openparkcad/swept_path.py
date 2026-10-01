"""Conservative vehicle footprints and swept-path collision checks."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from shapely.affinity import rotate, translate
from shapely.geometry import GeometryCollection, LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openparkcad.models import ParkingAisle, ParkingStall, Point, VehicleSpec, is_articulated_vehicle
from openparkcad.vehicle_kinematics import (
    KinematicResult,
    MotionSegment,
    VehiclePose,
    arc_motion,
    rear_axle_turning_radius,
    simulate_bicycle_path,
    straight_motion,
)

_GEOMETRY_EPSILON = 1e-9


@dataclass(frozen=True)
class SweptPathResult:
    """Kinematic trace plus its conservative body envelope and validation."""

    valid: bool
    reason: str | None
    kinematics: KinematicResult
    envelope: BaseGeometry = field(default_factory=GeometryCollection)
    collision_geometry: BaseGeometry = field(default_factory=GeometryCollection)
    colliding_obstacle_indices: tuple[int, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self, *, include_trajectory: bool = True, include_geometry: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible result suitable for validation reports."""

        record = self.kinematics.to_record(include_trajectory=include_trajectory)
        record.update(
            {
                "valid": self.valid,
                "reason": self.reason,
                "swept_area": self.envelope.area,
                "collision_area": self.collision_geometry.area,
                "colliding_obstacle_indices": list(self.colliding_obstacle_indices),
                "details": {**record.get("details", {}), **self.details},
            }
        )
        if include_geometry:
            record["envelope"] = mapping(self.envelope)
            record["collision_geometry"] = mapping(self.collision_geometry)
        return record


@dataclass(frozen=True)
class VehicleFootprintResolution:
    """Resolved body overhangs and any assumptions used."""

    valid: bool
    reason: str | None
    front_overhang: float | None
    rear_overhang: float | None
    assumptions: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "front_overhang": self.front_overhang,
            "rear_overhang": self.rear_overhang,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class ReverseIn90Template:
    """A deterministic one-arc-plus-straight reverse-in maneuver."""

    valid: bool
    reason: str | None
    start_pose: VehiclePose | None = None
    final_pose: VehiclePose | None = None
    segments: tuple[MotionSegment, ...] = ()
    variant: str | None = None
    front_edge: tuple[Point, Point] | None = None
    rear_axle_turn_radius: float | None = None
    envelope: BaseGeometry = field(default_factory=GeometryCollection)
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "start_pose": _pose_record(self.start_pose),
            "final_pose": _pose_record(self.final_pose),
            "segments": [
                {
                    "distance": segment.distance,
                    "steering_angle_degrees": segment.steering_angle_degrees,
                    "label": segment.label,
                }
                for segment in self.segments
            ],
            "variant": self.variant,
            "front_edge": self.front_edge,
            "rear_axle_turn_radius": self.rear_axle_turn_radius,
            "swept_area": self.envelope.area,
            "details": dict(self.details),
        }


def vehicle_footprint(
    vehicle: VehicleSpec,
    pose: VehiclePose,
    *,
    include_margin: bool = True,
    rear_overhang: float | None = None,
) -> ShapelyPolygon:
    """Build the vehicle body polygon at one rear-axle-centre pose.

    The safety margin expands every side of the body.  When no rear overhang is
    supplied, the total non-wheelbase length is divided evenly between front
    and rear.
    """

    resolution = resolve_vehicle_overhangs(vehicle, rear_overhang=rear_overhang)
    if not resolution.valid or resolution.front_overhang is None or resolution.rear_overhang is None:
        raise ValueError(resolution.reason or "Vehicle overhangs cannot be resolved")
    if not all(math.isfinite(value) for value in (pose.x, pose.y, pose.heading_degrees)):
        raise ValueError("Vehicle pose must contain finite coordinates and heading")
    margin = vehicle.swept_path_margin if include_margin else 0.0
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError("Swept-path margin cannot be negative")

    assert vehicle.wheelbase is not None
    rear_x = -resolution.rear_overhang - margin
    front_x = vehicle.wheelbase + resolution.front_overhang + margin
    half_width = vehicle.width / 2.0 + margin
    local = ShapelyPolygon(
        [
            (rear_x, -half_width),
            (front_x, -half_width),
            (front_x, half_width),
            (rear_x, half_width),
        ]
    )
    rotated = rotate(local, pose.heading_degrees, origin=(0.0, 0.0), use_radians=False)
    return translate(rotated, xoff=pose.x, yoff=pose.y)


def resolve_vehicle_overhangs(
    vehicle: VehicleSpec,
    *,
    rear_overhang: float | None = None,
) -> VehicleFootprintResolution:
    """Resolve explicit or derivable front/rear body overhangs."""

    if not math.isfinite(vehicle.length) or vehicle.length <= 0.0:
        return VehicleFootprintResolution(False, "invalid_vehicle_length", None, None)
    if not math.isfinite(vehicle.width) or vehicle.width <= 0.0:
        return VehicleFootprintResolution(False, "invalid_vehicle_width", None, None)
    if vehicle.wheelbase is None or not math.isfinite(vehicle.wheelbase) or vehicle.wheelbase <= 0.0:
        return VehicleFootprintResolution(False, "vehicle_wheelbase_missing_or_invalid", None, None)
    remaining = vehicle.length - vehicle.wheelbase
    if remaining < -_GEOMETRY_EPSILON:
        return VehicleFootprintResolution(False, "vehicle_wheelbase_exceeds_length", None, None)

    explicit_front = vehicle.front_overhang
    explicit_rear = rear_overhang if rear_overhang is not None else vehicle.rear_overhang
    assumptions: list[str] = []
    if explicit_front is None and explicit_rear is None:
        explicit_front = remaining / 2.0
        explicit_rear = remaining / 2.0
        assumptions.append("front_and_rear_overhang_split_evenly")
    elif explicit_front is None:
        assert explicit_rear is not None
        explicit_front = remaining - explicit_rear
        assumptions.append("front_overhang_derived_from_vehicle_length")
    elif explicit_rear is None:
        explicit_rear = remaining - explicit_front
        assumptions.append("rear_overhang_derived_from_vehicle_length")

    if not math.isfinite(explicit_front) or not math.isfinite(explicit_rear):
        return VehicleFootprintResolution(False, "vehicle_overhang_not_finite", None, None)
    if explicit_front < 0.0 or explicit_rear < 0.0:
        return VehicleFootprintResolution(False, "vehicle_overhang_negative", None, None)
    if not math.isclose(explicit_front + explicit_rear, remaining, abs_tol=1e-6):
        return VehicleFootprintResolution(
            False,
            "vehicle_overhangs_inconsistent_with_length_and_wheelbase",
            explicit_front,
            explicit_rear,
        )
    return VehicleFootprintResolution(
        True,
        None,
        explicit_front,
        explicit_rear,
        tuple(assumptions),
    )


def conservative_swept_envelope(
    vehicle: VehicleSpec,
    poses: Sequence[VehiclePose],
    *,
    rear_overhang: float | None = None,
) -> BaseGeometry:
    """Union conservative between-sample body sweeps.

    The convex hull of each consecutive footprint pair intentionally
    over-approximates the short arc between them.  This prevents sampling gaps
    from becoming false-negative collision checks.
    """

    if not poses:
        return GeometryCollection()
    footprints = [vehicle_footprint(vehicle, pose, rear_overhang=rear_overhang) for pose in poses]
    if len(footprints) == 1:
        return footprints[0]
    slabs = [unary_union((first, second)).convex_hull for first, second in zip(footprints, footprints[1:])]
    return unary_union(slabs)


def validate_swept_path(
    vehicle: VehicleSpec,
    start_pose: VehiclePose,
    segments: list[MotionSegment] | tuple[MotionSegment, ...],
    *,
    boundary: BaseGeometry | Sequence[Point] | None = None,
    obstacles: Iterable[BaseGeometry | Sequence[Point]] = (),
    sample_step: float = 0.25,
    max_heading_step_degrees: float = 2.0,
    require_min_turning_radius: bool = True,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    rear_overhang: float | None = None,
) -> SweptPathResult:
    """Simulate, envelope, and validate a low-speed maneuver.

    Boundary contact is allowed, while obstacle contact is treated as a
    collision.  ``swept_path_margin`` is already embedded in every footprint.
    """

    kinematics = simulate_bicycle_path(
        vehicle,
        start_pose,
        segments,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_min_turning_radius=require_min_turning_radius,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not kinematics.valid:
        return SweptPathResult(False, kinematics.reason, kinematics, details=dict(kinematics.details))

    footprint_resolution = resolve_vehicle_overhangs(vehicle, rear_overhang=rear_overhang)
    footprint_details = {"footprint_resolution": footprint_resolution.to_record()}
    try:
        envelope = conservative_swept_envelope(vehicle, kinematics.poses, rear_overhang=rear_overhang)
    except ValueError as error:
        return SweptPathResult(
            False,
            "vehicle_footprint_invalid",
            kinematics,
            details={"message": str(error), **footprint_details},
        )

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return SweptPathResult(
            False,
            "invalid_geometry_tolerance",
            kinematics,
            envelope,
            details={"geometry_tolerance": geometry_tolerance, **footprint_details},
        )

    try:
        boundary_geometry = _as_polygon(boundary, "boundary") if boundary is not None else None
    except (TypeError, ValueError) as error:
        return SweptPathResult(
            False,
            "invalid_boundary_geometry",
            kinematics,
            envelope,
            details={"message": str(error), **footprint_details},
        )
    if boundary_geometry is not None:
        accepted_boundary = boundary_geometry.buffer(float(geometry_tolerance))
        if not accepted_boundary.covers(envelope):
            collision = envelope.difference(accepted_boundary)
            return SweptPathResult(
                False,
                "swept_path_outside_boundary",
                kinematics,
                envelope,
                collision,
                details={"outside_area": collision.area, **footprint_details},
            )

    try:
        obstacle_geometries = [_as_polygon(item, f"obstacles[{index}]") for index, item in enumerate(obstacles)]
    except (TypeError, ValueError) as error:
        return SweptPathResult(
            False,
            "invalid_obstacle_geometry",
            kinematics,
            envelope,
            details={"message": str(error), **footprint_details},
        )
    colliding_indices = tuple(
        index
        for index, obstacle in enumerate(obstacle_geometries)
        if envelope.intersects(obstacle.buffer(float(geometry_tolerance)))
    )
    if colliding_indices:
        collision = envelope.intersection(unary_union([obstacle_geometries[index] for index in colliding_indices]))
        return SweptPathResult(
            False,
            "swept_path_intersects_obstacle",
            kinematics,
            envelope,
            collision,
            colliding_indices,
            {"obstacle_count": len(colliding_indices), **footprint_details},
        )

    return SweptPathResult(True, None, kinematics, envelope, details=footprint_details)


def reverse_in_90_template(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> ReverseIn90Template:
    """Construct an audited reverse-in template for a perpendicular stall.

    Supported geometry is intentionally narrow and fail-closed: a straight
    stall front edge must touch the serving aisle, the vehicle body must fit in
    the final stall, and one of the two aisle-tangent approaches must keep the
    conservative envelope inside the supplied drivable area plus the target
    stall.  The maneuver is one 90-degree constant-radius reverse arc followed
    by a straight reverse segment.
    """

    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
        active_drivable = (
            _as_polygon(drivable_area, "drivable_area") if drivable_area is not None else aisle_geometry
        )
        explicit_reference_area = (
            _as_polygon(allowed_reference_area, "allowed_reference_area")
            if allowed_reference_area is not None
            else None
        )
    except (TypeError, ValueError) as error:
        return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return ReverseIn90Template(
            False,
            "invalid_geometry_tolerance",
            details={"geometry_tolerance": geometry_tolerance},
        )

    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return ReverseIn90Template(False, "stall_front_edge_not_connected_to_serving_aisle")
    basis = _stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return ReverseIn90Template(False, "unsupported_stall_geometry_for_reverse_in_90")
    midpoint, tangent, inward, stall_depth = basis
    parking_basis = _parallelogram_stall_basis(stall_geometry, edge, geometry_tolerance)
    if parking_basis is not None:
        parking_inward = parking_basis[2]
        aligned = abs(parking_inward[0] * inward[0] + parking_inward[1] * inward[1])
        if aligned < 0.999:
            return ReverseIn90Template(
                False,
                "stall_not_perpendicular_to_serving_aisle",
                front_edge=_edge_points(edge),
                details={"parking_axis_alignment": aligned},
            )
    crossing_rule = str(centerline_crossing).strip().lower()
    if crossing_rule not in {"allowed", "permitted", "maneuver_only", "forbidden"}:
        return ReverseIn90Template(
            False,
            "unsupported_centerline_crossing_rule",
            front_edge=_edge_points(edge),
            details={"centerline_crossing": centerline_crossing},
        )
    reference_area = None
    if crossing_rule == "forbidden":
        aisle_side_reference = (
            explicit_reference_area
            if explicit_reference_area is not None
            else _stall_side_reference_area(aisle_geometry, tangent, inward)
        )
        reference_area = unary_union((aisle_side_reference, stall_geometry))

    footprint_resolution = resolve_vehicle_overhangs(vehicle)
    if (
        not footprint_resolution.valid
        or footprint_resolution.front_overhang is None
        or footprint_resolution.rear_overhang is None
        or vehicle.wheelbase is None
    ):
        return ReverseIn90Template(
            False,
            footprint_resolution.reason or "vehicle_footprint_invalid",
            front_edge=_edge_points(edge),
            details={"footprint_resolution": footprint_resolution.to_record()},
        )
    if vehicle.width > edge.length + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_wide_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_width": vehicle.width, "stall_front_width": edge.length},
        )
    if vehicle.length > stall_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_long_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_length": vehicle.length, "stall_depth": stall_depth},
        )

    radius_resolution = rear_axle_turning_radius(
        vehicle,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not radius_resolution.valid or radius_resolution.rear_axle_radius is None:
        return ReverseIn90Template(
            False,
            radius_resolution.reason or "minimum_turning_radius_unresolved",
            front_edge=_edge_points(edge),
            details={"turning_radius_resolution": radius_resolution.to_record()},
        )

    front_extent = vehicle.wheelbase + footprint_resolution.front_overhang
    maximum_axle_depth = stall_depth - footprint_resolution.rear_overhang
    if front_extent > maximum_axle_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_body_cannot_land_inside_stall",
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={
                "minimum_rear_axle_depth": front_extent,
                "maximum_rear_axle_depth": maximum_axle_depth,
                "footprint_resolution": footprint_resolution.to_record(),
            },
        )
    target_depth = (front_extent + maximum_axle_depth) / 2.0
    target_heading = _heading_degrees((-inward[0], -inward[1]))
    target_point = (
        midpoint[0] + inward[0] * target_depth,
        midpoint[1] + inward[1] * target_depth,
    )
    target_pose = VehiclePose(target_point[0], target_point[1], target_heading)
    target_body = vehicle_footprint(vehicle, target_pose, include_margin=False)
    if not stall_geometry.buffer(geometry_tolerance).covers(target_body):
        return ReverseIn90Template(
            False,
            "reverse_in_90_final_pose_outside_stall",
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"target_depth": target_depth},
        )

    return _search_reverse_in_arc_candidates(
        vehicle=vehicle,
        stall_geometry=stall_geometry,
        active_drivable=active_drivable,
        reference_area=reference_area,
        explicit_reference_area=explicit_reference_area,
        midpoint=midpoint,
        inward=inward,
        tangent=tangent,
        target_depth=target_depth,
        target_heading=target_heading,
        target_pose=target_pose,
        radius_resolution=radius_resolution,
        footprint_resolution=footprint_resolution,
        edge=edge,
        stall_depth=stall_depth,
        stall_front_width=edge.length,
        heading_accept=lambda change: math.isclose(abs(change), 90.0, abs_tol=1e-5),
        heading_reject_reason="stall_not_perpendicular_to_serving_aisle",
        template_version="reverse_in_90_bicycle_v1",
        arc_label="reverse_90_arc",
        simulation_fail_reason="reverse_in_90_simulation_failed",
        narrow_aisle_reason="serving_aisle_too_narrow_for_reverse_in_90",
        not_constructible_reason="reverse_in_90_template_not_constructible",
        extra_assumptions=("approach_heading_change_is_90_degrees",),
        crossing_rule=crossing_rule,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
    )


_T_END_REASON_MAP = {
    "unsupported_stall_geometry_for_reverse_in_90": "unsupported_stall_geometry_for_reverse_in_t_end",
    "reverse_in_90_final_pose_outside_stall": "reverse_in_t_end_final_pose_outside_stall",
    "reverse_in_90_simulation_failed": "reverse_in_t_end_simulation_failed",
    "reverse_in_90_template_not_constructible": "reverse_in_t_end_template_not_constructible",
    "serving_aisle_too_narrow_for_reverse_in_90": "serving_aisle_too_narrow_for_reverse_in_t_end",
}


def reverse_in_t_end_template(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> ReverseIn90Template:
    """Reverse straight from the dead-end court into a T-end bay.

    The parked heading faces the serving turnaround. The path is a straight
    reverse along the bay axis from the convex court of the turnaround, optional
    parent aisle, and target bay. If that cannot be constructed, the template
    falls back to the perpendicular-90 reverse-in on the same court.
    """

    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
        extra = _as_polygon(drivable_area, "drivable_area") if drivable_area is not None else None
        court_parts = [aisle_geometry, stall_geometry]
        if extra is not None:
            court_parts.append(extra)
        court = unary_union(court_parts)
        if court.is_empty or not court.is_valid:
            court = aisle_geometry
        else:
            court = court.convex_hull
    except (TypeError, ValueError) as error:
        return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})

    straight = _t_end_straight_reverse(
        vehicle,
        stall_geometry,
        aisle_geometry,
        court,
        extra,
        centerline_crossing=centerline_crossing,
        allowed_reference_area=allowed_reference_area,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
    )
    if straight.valid:
        return _label_t_end_template(straight, "t_end_straight_reverse_from_dead_end_court")

    fallback = reverse_in_90_template(
        vehicle,
        stall,
        serving_aisle,
        drivable_area=court,
        centerline_crossing=centerline_crossing,
        allowed_reference_area=allowed_reference_area,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
    )
    labeled = _label_t_end_template(fallback, "t_end_falls_back_to_perpendicular_reverse_in_on_court")
    if labeled.valid or straight.start_pose is None:
        return labeled
    return _label_t_end_template(straight, "t_end_straight_reverse_from_dead_end_court")


def _label_t_end_template(result: ReverseIn90Template, extra_assumption: str) -> ReverseIn90Template:
    details = dict(result.details)
    details["template_version"] = "reverse_in_t_end_bicycle_v1"
    assumptions = list(details.get("geometry_assumptions") or [])
    for item in ("t_end_end_bay_facing_turnaround", extra_assumption):
        if item not in assumptions:
            assumptions.append(item)
    details["geometry_assumptions"] = assumptions
    reason = result.reason
    if reason in _T_END_REASON_MAP:
        reason = _T_END_REASON_MAP[reason]
    return replace(result, reason=reason, details=details)


def _t_end_straight_reverse(
    vehicle: VehicleSpec,
    stall_geometry: BaseGeometry,
    aisle_geometry: BaseGeometry,
    court: BaseGeometry,
    extra: BaseGeometry | None,
    *,
    centerline_crossing: str,
    allowed_reference_area: BaseGeometry | Sequence[Point] | None,
    require_explicit_track_width: bool,
    geometry_tolerance: float,
    sample_step: float,
    max_heading_step_degrees: float,
) -> ReverseIn90Template:
    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return ReverseIn90Template(
            False,
            "invalid_geometry_tolerance",
            details={"geometry_tolerance": geometry_tolerance},
        )
    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return ReverseIn90Template(False, "stall_front_edge_not_connected_to_serving_aisle")
    basis = _stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return ReverseIn90Template(False, "unsupported_stall_geometry_for_reverse_in_t_end")
    midpoint, tangent, inward, stall_depth = basis
    crossing_rule = str(centerline_crossing).strip().lower()
    if crossing_rule not in {"allowed", "permitted", "maneuver_only", "forbidden"}:
        return ReverseIn90Template(
            False,
            "unsupported_centerline_crossing_rule",
            front_edge=_edge_points(edge),
            details={"centerline_crossing": centerline_crossing},
        )
    explicit_reference_area = None
    if allowed_reference_area is not None:
        try:
            explicit_reference_area = _as_polygon(allowed_reference_area, "allowed_reference_area")
        except (TypeError, ValueError) as error:
            return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})
    reference_area = None
    if crossing_rule == "forbidden":
        aisle_side_reference = (
            explicit_reference_area
            if explicit_reference_area is not None
            else _stall_side_reference_area(aisle_geometry, tangent, inward)
        )
        reference_area = unary_union((aisle_side_reference, stall_geometry, court))

    footprint_resolution = resolve_vehicle_overhangs(vehicle)
    if (
        not footprint_resolution.valid
        or footprint_resolution.front_overhang is None
        or footprint_resolution.rear_overhang is None
        or vehicle.wheelbase is None
    ):
        return ReverseIn90Template(
            False,
            footprint_resolution.reason or "vehicle_footprint_invalid",
            front_edge=_edge_points(edge),
            details={"footprint_resolution": footprint_resolution.to_record()},
        )
    if vehicle.width > edge.length + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_wide_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_width": vehicle.width, "stall_front_width": edge.length},
        )
    if vehicle.length > stall_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_long_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_length": vehicle.length, "stall_depth": stall_depth},
        )
    front_extent = vehicle.wheelbase + footprint_resolution.front_overhang
    maximum_axle_depth = stall_depth - footprint_resolution.rear_overhang
    if front_extent > maximum_axle_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_body_cannot_land_inside_stall",
            front_edge=_edge_points(edge),
            details={
                "minimum_rear_axle_depth": front_extent,
                "maximum_rear_axle_depth": maximum_axle_depth,
                "footprint_resolution": footprint_resolution.to_record(),
            },
        )
    target_depth = (front_extent + maximum_axle_depth) / 2.0
    target_heading = _heading_degrees((-inward[0], -inward[1]))
    target_pose = VehiclePose(
        midpoint[0] + inward[0] * target_depth,
        midpoint[1] + inward[1] * target_depth,
        target_heading,
    )
    target_body = vehicle_footprint(vehicle, target_pose, include_margin=False)
    if not stall_geometry.buffer(geometry_tolerance).covers(target_body):
        return ReverseIn90Template(
            False,
            "reverse_in_t_end_final_pose_outside_stall",
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            details={"target_depth": target_depth},
        )

    radius_resolution = rear_axle_turning_radius(vehicle, require_explicit_track_width=require_explicit_track_width)
    margin = max(vehicle.swept_path_margin, 0.0)
    allowed_area = unary_union((court, stall_geometry.buffer(margin)))
    start_area = aisle_geometry
    if extra is not None:
        start_area = unary_union((aisle_geometry, extra))
    max_reverse = vehicle.max_reverse_distance if vehicle.max_reverse_distance is not None else stall_depth + 12.0
    candidates: list[tuple[float, float, ReverseIn90Template]] = []
    failures: list[tuple[str, dict[str, Any]]] = []
    for reverse_distance in _template_arc_end_depths(max(target_depth + 0.5, min(max_reverse, 12.0))):
        if reverse_distance + geometry_tolerance < target_depth:
            continue
        if vehicle.max_reverse_distance is not None and reverse_distance > vehicle.max_reverse_distance + 1e-9:
            continue
        start_pose = VehiclePose(
            target_pose.x - inward[0] * reverse_distance,
            target_pose.y - inward[1] * reverse_distance,
            target_heading,
        )
        if not start_area.buffer(max(geometry_tolerance, 0.05)).covers(ShapelyPoint(start_pose.x, start_pose.y)):
            failures.append(("t_end_start_not_in_serving_aisle", {"reverse_distance": reverse_distance}))
            continue
        commands = (straight_motion(reverse_distance, reverse=True, label="reverse_into_t_end"),)
        simulation = simulate_bicycle_path(
            vehicle,
            start_pose,
            commands,
            sample_step=sample_step,
            max_heading_step_degrees=max_heading_step_degrees,
            require_explicit_track_width=require_explicit_track_width,
        )
        if not simulation.valid or simulation.final_pose is None:
            failures.append((simulation.reason or "reverse_in_t_end_simulation_failed", simulation.details))
            continue
        envelope = conservative_swept_envelope(vehicle, simulation.poses)
        outside = envelope.difference(allowed_area.buffer(geometry_tolerance))
        trajectory = LineString([(pose.x, pose.y) for pose in simulation.poses])
        reference_outside_length = 0.0
        if reference_area is not None:
            reference_outside_length = trajectory.difference(reference_area.buffer(geometry_tolerance)).length
        within_reference_area = reference_outside_length <= _GEOMETRY_EPSILON
        within_drivable_area = outside.area <= _GEOMETRY_EPSILON
        if not within_reference_area:
            failure_reason = "rear_axle_crosses_forbidden_aisle_centerline"
        elif not within_drivable_area:
            failure_reason = "serving_aisle_too_narrow_for_reverse_in_t_end"
        else:
            failure_reason = None
        candidate = ReverseIn90Template(
            within_reference_area and within_drivable_area,
            failure_reason,
            start_pose,
            simulation.final_pose,
            commands,
            "straight_reverse",
            _edge_points(edge),
            radius_resolution.rear_axle_radius if radius_resolution.valid else None,
            envelope,
            {
                "template_version": "reverse_in_t_end_bicycle_v1",
                "heading_change_degrees": 0.0,
                "target_rear_axle_depth": target_depth,
                "reverse_distance": simulation.reverse_distance,
                "path_length": simulation.path_length,
                "outside_drivable_area": outside.area,
                "centerline_crossing": crossing_rule,
                "reference_path_outside_length": reference_outside_length,
                "turning_radius_resolution": radius_resolution.to_record(),
                "footprint_resolution": footprint_resolution.to_record(),
                "stall_depth": stall_depth,
                "stall_front_width": edge.length,
                "sampling": {
                    "maximum_distance_step": sample_step,
                    "maximum_heading_step_degrees": max_heading_step_degrees,
                },
                "geometry_assumptions": [
                    "front_edge_connected_to_serving_aisle",
                    "straight_reverse_along_bay_axis",
                    "final_unmargined_vehicle_body_must_fit_target_stall",
                    "start_rear_axle_in_serving_or_parent_aisle",
                    "swept_margin_allowed_within_buffered_target_stall",
                    "t_end_court_is_convex_hull_of_turnaround_parent_and_bay",
                ],
            },
        )
        candidates.append((reference_outside_length, outside.area, candidate))
        if candidate.valid:
            break
    if not candidates:
        reason, details = failures[0] if failures else ("reverse_in_t_end_template_not_constructible", {})
        return ReverseIn90Template(
            False,
            reason,
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            details={"failures": [{"reason": item[0], "details": item[1]} for item in failures], **details},
        )
    candidates.sort(key=lambda item: (0 if item[2].valid else 1, item[0], item[1], item[2].details.get("reverse_distance", 99)))
    return candidates[0][2]


def reverse_in_angled_template(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> ReverseIn90Template:
    """Construct an audited reverse-in template for an acute-angled stall.

    Supported geometry is a parallelogram whose long sides are the parking
    axis. The maneuver is one constant-radius reverse arc whose heading change
    equals the acute stall-to-aisle angle, then a straight reverse along that
    axis. Obtuse approaches (heading change >= 90) are not tried. The parked
    unmargined body may occupy the stall/aisle throat because the front bumper
    is not parallel to the stall front edge; the rear axle must remain inside
    the stall.
    """

    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
        active_drivable = (
            _as_polygon(drivable_area, "drivable_area") if drivable_area is not None else aisle_geometry
        )
        explicit_reference_area = (
            _as_polygon(allowed_reference_area, "allowed_reference_area")
            if allowed_reference_area is not None
            else None
        )
    except (TypeError, ValueError) as error:
        return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return ReverseIn90Template(
            False,
            "invalid_geometry_tolerance",
            details={"geometry_tolerance": geometry_tolerance},
        )

    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return ReverseIn90Template(False, "stall_front_edge_not_connected_to_serving_aisle")
    basis = _parallelogram_stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return ReverseIn90Template(False, "unsupported_stall_geometry_for_reverse_in_angled")
    midpoint, tangent, inward, stall_depth, stall_width = basis
    crossing_rule = str(centerline_crossing).strip().lower()
    if crossing_rule not in {"allowed", "permitted", "maneuver_only", "forbidden"}:
        return ReverseIn90Template(
            False,
            "unsupported_centerline_crossing_rule",
            front_edge=_edge_points(edge),
            details={"centerline_crossing": centerline_crossing},
        )
    reference_area = None
    if crossing_rule == "forbidden":
        aisle_side_reference = (
            explicit_reference_area
            if explicit_reference_area is not None
            else _stall_side_reference_area(aisle_geometry, tangent, inward)
        )
        reference_area = unary_union((aisle_side_reference, stall_geometry))

    footprint_resolution = resolve_vehicle_overhangs(vehicle)
    if (
        not footprint_resolution.valid
        or footprint_resolution.front_overhang is None
        or footprint_resolution.rear_overhang is None
        or vehicle.wheelbase is None
    ):
        return ReverseIn90Template(
            False,
            footprint_resolution.reason or "vehicle_footprint_invalid",
            front_edge=_edge_points(edge),
            details={"footprint_resolution": footprint_resolution.to_record()},
        )
    if vehicle.width > stall_width + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_wide_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_width": vehicle.width, "stall_width": stall_width},
        )
    if vehicle.length > stall_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_long_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_length": vehicle.length, "stall_depth": stall_depth},
        )

    radius_resolution = rear_axle_turning_radius(
        vehicle,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not radius_resolution.valid or radius_resolution.rear_axle_radius is None:
        return ReverseIn90Template(
            False,
            radius_resolution.reason or "minimum_turning_radius_unresolved",
            front_edge=_edge_points(edge),
            details={"turning_radius_resolution": radius_resolution.to_record()},
        )

    target_heading = _heading_degrees((-inward[0], -inward[1]))
    parked_area = unary_union((stall_geometry, aisle_geometry)).buffer(geometry_tolerance)
    landing = _angled_reverse_in_landing(
        vehicle,
        stall_geometry,
        parked_area,
        midpoint,
        inward,
        target_heading,
        stall_depth,
        geometry_tolerance,
    )
    if landing is None:
        return ReverseIn90Template(
            False,
            "reverse_in_angled_final_pose_outside_stall",
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"stall_depth": stall_depth, "stall_width": stall_width},
        )
    target_depth, target_pose = landing

    return _search_reverse_in_arc_candidates(
        vehicle=vehicle,
        stall_geometry=stall_geometry,
        active_drivable=active_drivable,
        reference_area=reference_area,
        explicit_reference_area=explicit_reference_area,
        midpoint=midpoint,
        inward=inward,
        tangent=tangent,
        target_depth=target_depth,
        target_heading=target_heading,
        target_pose=target_pose,
        radius_resolution=radius_resolution,
        footprint_resolution=footprint_resolution,
        edge=edge,
        stall_depth=stall_depth,
        stall_front_width=edge.length,
        heading_accept=lambda change: 1e-5 < abs(change) < 90.0 - 1e-5,
        heading_reject_reason="stall_not_acute_angled_to_serving_aisle",
        template_version="reverse_in_angled_bicycle_v1",
        arc_label="reverse_angled_arc",
        simulation_fail_reason="reverse_in_angled_simulation_failed",
        narrow_aisle_reason="serving_aisle_too_narrow_for_reverse_in_angled",
        not_constructible_reason="reverse_in_angled_template_not_constructible",
        extra_assumptions=(
            "parking_axis_follows_parallelogram_sides",
            "approach_heading_change_is_acute_and_below_90_degrees",
            "final_unmargined_body_may_occupy_serving_aisle_throat",
        ),
        extra_details={"stall_width": stall_width},
        crossing_rule=crossing_rule,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
    )


def reverse_parallel_template(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> ReverseIn90Template:
    """Construct an audited two-arc reverse-parallel (S-curve) maneuver.

    Supported geometry is a rectangle whose long edge touches the serving aisle.
    The path is two equal-radius reverse arcs of opposite steering that restore
    the approach heading. Obtuse or perpendicular reverse-in is not tried.
    """

    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
        active_drivable = (
            _as_polygon(drivable_area, "drivable_area") if drivable_area is not None else aisle_geometry
        )
        explicit_reference_area = (
            _as_polygon(allowed_reference_area, "allowed_reference_area")
            if allowed_reference_area is not None
            else None
        )
    except (TypeError, ValueError) as error:
        return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return ReverseIn90Template(
            False,
            "invalid_geometry_tolerance",
            details={"geometry_tolerance": geometry_tolerance},
        )

    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return ReverseIn90Template(False, "stall_front_edge_not_connected_to_serving_aisle")
    basis = _stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return ReverseIn90Template(False, "unsupported_stall_geometry_for_parallel_reverse")
    midpoint, tangent, inward, stall_width = basis
    parking_basis = _parallelogram_stall_basis(stall_geometry, edge, geometry_tolerance)
    if parking_basis is not None:
        parking_inward = parking_basis[2]
        aligned = abs(parking_inward[0] * inward[0] + parking_inward[1] * inward[1])
        if aligned < 0.999:
            return ReverseIn90Template(
                False,
                "stall_not_parallel_to_serving_aisle",
                front_edge=_edge_points(edge),
                details={"parking_axis_alignment": aligned},
            )
    stall_length = edge.length
    crossing_rule = str(centerline_crossing).strip().lower()
    if crossing_rule not in {"allowed", "permitted", "maneuver_only", "forbidden"}:
        return ReverseIn90Template(
            False,
            "unsupported_centerline_crossing_rule",
            front_edge=_edge_points(edge),
            details={"centerline_crossing": centerline_crossing},
        )
    reference_area = None
    if crossing_rule == "forbidden":
        aisle_side_reference = (
            explicit_reference_area
            if explicit_reference_area is not None
            else _stall_side_reference_area(aisle_geometry, tangent, inward)
        )
        reference_area = unary_union((aisle_side_reference, stall_geometry))

    footprint_resolution = resolve_vehicle_overhangs(vehicle)
    if (
        not footprint_resolution.valid
        or footprint_resolution.front_overhang is None
        or footprint_resolution.rear_overhang is None
        or vehicle.wheelbase is None
    ):
        return ReverseIn90Template(
            False,
            footprint_resolution.reason or "vehicle_footprint_invalid",
            front_edge=_edge_points(edge),
            details={"footprint_resolution": footprint_resolution.to_record()},
        )
    if vehicle.width > stall_width + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_wide_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_width": vehicle.width, "stall_width": stall_width},
        )
    if vehicle.length > stall_length + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_long_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_length": vehicle.length, "stall_length": stall_length},
        )

    radius_resolution = rear_axle_turning_radius(
        vehicle,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not radius_resolution.valid or radius_resolution.rear_axle_radius is None:
        return ReverseIn90Template(
            False,
            radius_resolution.reason or "minimum_turning_radius_unresolved",
            front_edge=_edge_points(edge),
            details={"turning_radius_resolution": radius_resolution.to_record()},
        )

    front_extent = vehicle.wheelbase + footprint_resolution.front_overhang
    axle_along = (footprint_resolution.rear_overhang - front_extent) / 2.0
    axle_inward = stall_width / 2.0
    aisle_inward = (
        (aisle_geometry.centroid.x - midpoint[0]) * inward[0]
        + (aisle_geometry.centroid.y - midpoint[1]) * inward[1]
    )
    lateral = axle_inward - aisle_inward
    parameters = _parallel_s_curve_parameters(lateral, radius_resolution.rear_axle_radius)
    if parameters is None:
        return ReverseIn90Template(
            False,
            "parallel_s_curve_not_constructible",
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"lateral_shift": lateral, "aisle_inward": aisle_inward, "stall_inward": axle_inward},
        )
    arc_radius, heading_change = parameters

    margin = max(vehicle.swept_path_margin, 0.0)
    stall_side_band = translate(aisle_geometry, xoff=inward[0] * stall_width, yoff=inward[1] * stall_width)
    allowed_area = unary_union((active_drivable, stall_geometry.buffer(margin), stall_side_band))
    candidates: list[tuple[float, float, int, int, ReverseIn90Template]] = []
    failures: list[tuple[str, dict[str, Any]]] = []
    tangent_variants = (
        (tangent, "along_front_edge"),
        ((-tangent[0], -tangent[1]), "against_front_edge"),
    )
    for heading_index, (heading_vec, variant) in enumerate(tangent_variants):
        heading = _heading_degrees(heading_vec)
        target_pose = VehiclePose(
            midpoint[0] + heading_vec[0] * axle_along + inward[0] * axle_inward,
            midpoint[1] + heading_vec[1] * axle_along + inward[1] * axle_inward,
            heading,
        )
        target_body = vehicle_footprint(vehicle, target_pose, include_margin=False)
        if not stall_geometry.buffer(geometry_tolerance).covers(target_body):
            failures.append(("parallel_final_pose_outside_stall", {"variant": variant}))
            continue
        for sign_index, sign in enumerate((1.0, -1.0)):
            commands = (
                arc_motion(
                    vehicle,
                    sign * heading_change,
                    radius=arc_radius,
                    reverse=True,
                    label="parallel_entry_arc",
                ),
                arc_motion(
                    vehicle,
                    -sign * heading_change,
                    radius=arc_radius,
                    reverse=True,
                    label="parallel_align_arc",
                ),
            )
            origin = simulate_bicycle_path(
                vehicle,
                VehiclePose(0.0, 0.0, heading),
                commands,
                sample_step=sample_step,
                max_heading_step_degrees=max_heading_step_degrees,
                require_explicit_track_width=require_explicit_track_width,
            )
            if not origin.valid or origin.final_pose is None:
                failures.append((origin.reason or "parallel_s_curve_simulation_failed", origin.details))
                continue
            start_pose = VehiclePose(
                target_pose.x - origin.final_pose.x,
                target_pose.y - origin.final_pose.y,
                heading,
            )
            if not aisle_geometry.buffer(max(geometry_tolerance, 0.05)).covers(ShapelyPoint(start_pose.x, start_pose.y)):
                failures.append(
                    (
                        "parallel_start_not_in_serving_aisle",
                        {"variant": variant, "sign": sign, "start_pose": _pose_record(start_pose)},
                    )
                )
                continue
            simulation = simulate_bicycle_path(
                vehicle,
                start_pose,
                commands,
                sample_step=sample_step,
                max_heading_step_degrees=max_heading_step_degrees,
                require_explicit_track_width=require_explicit_track_width,
            )
            if not simulation.valid or simulation.final_pose is None:
                failures.append((simulation.reason or "parallel_s_curve_simulation_failed", simulation.details))
                continue
            envelope = conservative_swept_envelope(vehicle, simulation.poses)
            outside = envelope.difference(allowed_area.buffer(geometry_tolerance))
            trajectory = LineString([(pose.x, pose.y) for pose in simulation.poses])
            reference_outside_length = 0.0
            if reference_area is not None:
                reference_outside_length = trajectory.difference(reference_area.buffer(geometry_tolerance)).length
            within_reference_area = reference_outside_length <= _GEOMETRY_EPSILON
            within_drivable_area = outside.area <= _GEOMETRY_EPSILON
            if not within_reference_area:
                failure_reason = "rear_axle_crosses_forbidden_aisle_centerline"
            elif not within_drivable_area:
                failure_reason = "serving_aisle_too_narrow_for_parallel_reverse"
            else:
                failure_reason = None
            candidate = ReverseIn90Template(
                within_reference_area and within_drivable_area,
                failure_reason,
                start_pose,
                simulation.final_pose,
                commands,
                f"{variant}_{'ccw_first' if sign > 0 else 'cw_first'}",
                _edge_points(edge),
                arc_radius,
                envelope,
                {
                    "template_version": "parallel_reverse_s_curve_bicycle_v1",
                    "heading_change_degrees": heading_change,
                    "s_curve_radius": arc_radius,
                    "lateral_shift": lateral,
                    "turning_radius_resolution": radius_resolution.to_record(),
                    "footprint_resolution": footprint_resolution.to_record(),
                    "stall_width": stall_width,
                    "stall_length": stall_length,
                    "reverse_distance": simulation.reverse_distance,
                    "path_length": simulation.path_length,
                    "outside_drivable_area": outside.area,
                    "centerline_crossing": crossing_rule,
                    "reference_path_outside_length": reference_outside_length,
                    "sampling": {
                        "maximum_distance_step": sample_step,
                        "maximum_heading_step_degrees": max_heading_step_degrees,
                    },
                    "geometry_assumptions": [
                        "long_edge_connected_to_serving_aisle",
                        "two_equal_radius_opposite_reverse_arcs",
                        "parked_heading_parallel_to_serving_aisle",
                        "final_unmargined_vehicle_body_must_fit_target_stall",
                        "start_rear_axle_in_serving_aisle",
                        "s_curve_may_occupy_stall_side_band_along_aisle",
                        "swept_margin_allowed_within_buffered_target_stall",
                        (
                            "explicit_allowed_reference_area"
                            if explicit_reference_area is not None
                            else "forbidden_centerline_uses_aisle_centroid_half_plane"
                        )
                        if crossing_rule == "forbidden"
                        else "centerline_crossing_allowed_for_maneuver",
                    ],
                },
            )
            candidates.append(
                (
                    reference_outside_length,
                    outside.area,
                    heading_index,
                    sign_index,
                    candidate,
                )
            )
            if candidate.valid:
                break

    if not candidates:
        reason, details = failures[0] if failures else ("parallel_s_curve_not_constructible", {})
        return ReverseIn90Template(
            False,
            reason,
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=arc_radius,
            details={"failures": [{"reason": item[0], "details": item[1]} for item in failures], **details},
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][4]


def validate_stall_swept_path(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    boundary: BaseGeometry | Sequence[Point] | None = None,
    obstacles: Iterable[BaseGeometry | Sequence[Point]] = (),
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
    stall_family: str | None = None,
) -> SweptPathResult:
    """Build and spatially validate a supported reverse-in bicycle template."""

    if is_articulated_vehicle(vehicle):
        kinematics = KinematicResult(
            False,
            "articulated_vehicle_template_not_supported",
            (),
            details={"vehicle_class": "articulated"},
        )
        return SweptPathResult(
            False,
            "articulated_vehicle_template_not_supported",
            kinematics,
            details={"vehicle_class": "articulated"},
        )

    template_kwargs = {
        "drivable_area": drivable_area,
        "centerline_crossing": centerline_crossing,
        "allowed_reference_area": allowed_reference_area,
        "require_explicit_track_width": require_explicit_track_width,
        "geometry_tolerance": geometry_tolerance,
        "sample_step": sample_step,
        "max_heading_step_degrees": max_heading_step_degrees,
    }
    if stall_family == "angled":
        template = reverse_in_angled_template(vehicle, stall, serving_aisle, **template_kwargs)
    elif stall_family == "parallel":
        template = reverse_parallel_template(vehicle, stall, serving_aisle, **template_kwargs)
    elif stall_family == "t_end":
        template = reverse_in_t_end_template(vehicle, stall, serving_aisle, **template_kwargs)
    elif stall_family == "perpendicular":
        template = reverse_in_90_template(vehicle, stall, serving_aisle, **template_kwargs)
    elif _stall_uses_angled_reverse_in(stall, serving_aisle, geometry_tolerance):
        template = reverse_in_angled_template(vehicle, stall, serving_aisle, **template_kwargs)
    elif _stall_uses_parallel_reverse(stall, serving_aisle, geometry_tolerance):
        template = reverse_parallel_template(vehicle, stall, serving_aisle, **template_kwargs)
    else:
        template = reverse_in_90_template(vehicle, stall, serving_aisle, **template_kwargs)
    template_record = template.to_record()
    if template.start_pose is None or not template.segments:
        kinematics = KinematicResult(
            False,
            template.reason,
            (),
            details={"template": template_record},
        )
        return SweptPathResult(
            False,
            template.reason,
            kinematics,
            template.envelope,
            details={"template": template_record},
        )
    if not template.valid:
        kinematics = simulate_bicycle_path(
            vehicle,
            template.start_pose,
            template.segments,
            sample_step=sample_step,
            max_heading_step_degrees=max_heading_step_degrees,
            require_explicit_track_width=require_explicit_track_width,
        )
        return SweptPathResult(
            False,
            template.reason,
            kinematics,
            template.envelope,
            details={"template": template_record},
        )

    result = validate_swept_path(
        vehicle,
        template.start_pose,
        template.segments,
        boundary=boundary,
        obstacles=obstacles,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
    )
    return SweptPathResult(
        result.valid,
        result.reason,
        result.kinematics,
        result.envelope,
        result.collision_geometry,
        result.colliding_obstacle_indices,
        {**result.details, "template": template_record},
    )


def _search_reverse_in_arc_candidates(
    *,
    vehicle: VehicleSpec,
    stall_geometry: BaseGeometry,
    active_drivable: BaseGeometry,
    reference_area: BaseGeometry | None,
    explicit_reference_area: BaseGeometry | None,
    midpoint: Point,
    inward: Point,
    tangent: Point,
    target_depth: float,
    target_heading: float,
    target_pose: VehiclePose,
    radius_resolution,
    footprint_resolution,
    edge: LineString,
    stall_depth: float,
    stall_front_width: float,
    heading_accept: Callable[[float], bool],
    heading_reject_reason: str,
    template_version: str,
    arc_label: str,
    simulation_fail_reason: str,
    narrow_aisle_reason: str,
    not_constructible_reason: str,
    extra_assumptions: tuple[str, ...],
    crossing_rule: str,
    sample_step: float,
    max_heading_step_degrees: float,
    require_explicit_track_width: bool,
    geometry_tolerance: float,
    extra_details: dict[str, Any] | None = None,
) -> ReverseIn90Template:
    margin = max(vehicle.swept_path_margin, 0.0)
    allowed_area = unary_union((active_drivable, stall_geometry.buffer(margin)))
    candidates: list[tuple[float, float, float, int, ReverseIn90Template]] = []
    failures: list[tuple[str, dict[str, Any]]] = []
    tangent_variants = ((tangent, "front_edge_start_to_end"), ((-tangent[0], -tangent[1]), "front_edge_end_to_start"))
    for approach_tangent, variant in tangent_variants:
        approach_heading = _heading_degrees(approach_tangent)
        heading_change = _signed_heading_delta(approach_heading, target_heading)
        if not heading_accept(heading_change):
            failures.append((heading_reject_reason, {"heading_change": heading_change}))
            continue
        turn = arc_motion(
            vehicle,
            heading_change,
            radius=radius_resolution.rear_axle_radius,
            reverse=True,
            label=arc_label,
        )
        arc_from_origin = simulate_bicycle_path(
            vehicle,
            VehiclePose(0.0, 0.0, approach_heading),
            [turn],
            sample_step=sample_step,
            max_heading_step_degrees=max_heading_step_degrees,
            require_explicit_track_width=require_explicit_track_width,
        )
        if not arc_from_origin.valid or arc_from_origin.final_pose is None:
            failures.append((arc_from_origin.reason or "reverse_arc_simulation_failed", arc_from_origin.details))
            continue
        arc_end = arc_from_origin.final_pose
        for arc_end_depth in _template_arc_end_depths(target_depth):
            arc_target = (
                midpoint[0] + inward[0] * arc_end_depth,
                midpoint[1] + inward[1] * arc_end_depth,
            )
            start_pose = VehiclePose(
                arc_target[0] - arc_end.x,
                arc_target[1] - arc_end.y,
                approach_heading,
            )
            commands = (
                turn,
                straight_motion(target_depth - arc_end_depth, reverse=True, label="reverse_into_stall"),
            )
            simulation = simulate_bicycle_path(
                vehicle,
                start_pose,
                commands,
                sample_step=sample_step,
                max_heading_step_degrees=max_heading_step_degrees,
                require_explicit_track_width=require_explicit_track_width,
            )
            if not simulation.valid or simulation.final_pose is None:
                failures.append((simulation.reason or simulation_fail_reason, simulation.details))
                continue
            envelope = conservative_swept_envelope(vehicle, simulation.poses)
            outside = envelope.difference(allowed_area.buffer(geometry_tolerance))
            trajectory = LineString([(pose.x, pose.y) for pose in simulation.poses])
            reference_outside_length = 0.0
            if reference_area is not None:
                reference_outside_length = trajectory.difference(reference_area.buffer(geometry_tolerance)).length
            within_reference_area = reference_outside_length <= _GEOMETRY_EPSILON
            within_drivable_area = outside.area <= _GEOMETRY_EPSILON
            if not within_reference_area:
                failure_reason = "rear_axle_crosses_forbidden_aisle_centerline"
            elif not within_drivable_area:
                failure_reason = narrow_aisle_reason
            else:
                failure_reason = None
            candidate_details = {
                "template_version": template_version,
                "heading_change_degrees": heading_change,
                "turning_radius_resolution": radius_resolution.to_record(),
                "footprint_resolution": footprint_resolution.to_record(),
                "target_rear_axle_depth": target_depth,
                "arc_end_depth": arc_end_depth,
                "arc_end_depth_search_step": min(0.25, target_depth) if target_depth > 0.0 else 0.0,
                "stall_depth": stall_depth,
                "stall_front_width": stall_front_width,
                "reverse_distance": simulation.reverse_distance,
                "path_length": simulation.path_length,
                "outside_drivable_area": outside.area,
                "centerline_crossing": crossing_rule,
                "reference_path_outside_length": reference_outside_length,
                "sampling": {
                    "maximum_distance_step": sample_step,
                    "maximum_heading_step_degrees": max_heading_step_degrees,
                },
                "geometry_assumptions": [
                    "front_edge_connected_to_serving_aisle",
                    "single_constant_radius_reverse_arc_then_reverse_straight",
                    "arc_may_finish_after_crossing_the_stall_front_edge",
                    "final_unmargined_vehicle_body_must_fit_target_stall",
                    "swept_margin_allowed_within_buffered_target_stall",
                    *(extra_assumptions or ()),
                    (
                        "explicit_allowed_reference_area"
                        if explicit_reference_area is not None
                        else "forbidden_centerline_uses_aisle_centroid_half_plane"
                    )
                    if crossing_rule == "forbidden"
                    else "centerline_crossing_allowed_for_maneuver",
                ],
                **(extra_details or {}),
            }
            candidate = ReverseIn90Template(
                within_reference_area and within_drivable_area,
                failure_reason,
                start_pose,
                simulation.final_pose,
                commands,
                variant,
                _edge_points(edge),
                radius_resolution.rear_axle_radius,
                envelope,
                candidate_details,
            )
            candidates.append(
                (
                    reference_outside_length,
                    outside.area,
                    arc_end_depth,
                    0 if variant == "front_edge_start_to_end" else 1,
                    candidate,
                )
            )
            if candidate.valid:
                break

    if not candidates:
        reason, details = failures[0] if failures else (not_constructible_reason, {})
        return ReverseIn90Template(
            False,
            reason,
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"failures": [{"reason": item[0], "details": item[1]} for item in failures], **details},
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][4]


def _parallel_s_curve_parameters(lateral: float, min_radius: float) -> tuple[float, float] | None:
    if not math.isfinite(lateral) or lateral <= _GEOMETRY_EPSILON:
        return None
    if not math.isfinite(min_radius) or min_radius <= _GEOMETRY_EPSILON:
        return None
    if lateral <= 2.0 * min_radius + _GEOMETRY_EPSILON:
        cosine = 1.0 - lateral / (2.0 * min_radius)
        cosine = min(1.0, max(-1.0, cosine))
        theta = math.degrees(math.acos(cosine))
        if theta < 0.5:
            return None
        return min_radius, theta
    radius = lateral / 2.0
    if radius + 1e-9 < min_radius:
        return None
    return radius, 90.0


def _stall_uses_parallel_reverse(
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    geometry_tolerance: float,
) -> bool:
    if _stall_uses_angled_reverse_in(stall, serving_aisle, geometry_tolerance):
        return False
    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
    except (TypeError, ValueError):
        return False
    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return False
    basis = _stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return False
    depth = basis[3]
    return edge.length > depth + 0.25


def _stall_uses_angled_reverse_in(
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    geometry_tolerance: float,
) -> bool:
    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
    except (TypeError, ValueError):
        return False
    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return False
    basis = _parallelogram_stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return False
    _, tangent, inward, _, _ = basis
    heading_change = abs(_signed_heading_delta(_heading_degrees(tangent), _heading_degrees((-inward[0], -inward[1]))))
    acute = heading_change if heading_change <= 90.0 else 180.0 - heading_change
    return 1e-5 < acute < 90.0 - 1e-5


def _as_polygon(raw: BaseGeometry | Sequence[Point], label: str) -> BaseGeometry:
    geometry = raw if isinstance(raw, BaseGeometry) else ShapelyPolygon(raw)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= _GEOMETRY_EPSILON:
        raise ValueError(f"{label} must be a non-empty valid area geometry")
    return geometry


def _parking_geometry(
    raw: ParkingStall | ParkingAisle | BaseGeometry | Sequence[Point],
    label: str,
) -> BaseGeometry:
    if isinstance(raw, ParkingStall | ParkingAisle):
        return _as_polygon(raw.polygon, label)
    return _as_polygon(raw, label)


def _stall_front_edge(stall: BaseGeometry, aisle: BaseGeometry, tolerance: float) -> LineString | None:
    if not isinstance(stall, ShapelyPolygon):
        return None
    coords = list(stall.exterior.coords)
    candidates: list[tuple[float, float, float, LineString]] = []
    contact_area = aisle.buffer(max(tolerance, 1e-6))
    for start, end in zip(coords, coords[1:]):
        edge = LineString((start, end))
        if edge.length <= _GEOMETRY_EPSILON:
            continue
        contact_length = edge.intersection(contact_area).length
        contact_ratio = contact_length / edge.length
        if contact_ratio + 1e-6 < 0.5:
            continue
        candidates.append((-contact_ratio, edge.distance(aisle), edge.length, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _stall_basis(
    stall: BaseGeometry,
    edge: LineString,
    tolerance: float,
) -> tuple[Point, Point, Point, float] | None:
    edge_coords = list(edge.coords)
    if len(edge_coords) < 2:
        return None
    start, end = edge_coords[0], edge_coords[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= _GEOMETRY_EPSILON:
        return None
    tangent = (dx / length, dy / length)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    centroid_vector = (stall.centroid.x - midpoint[0], stall.centroid.y - midpoint[1])
    along_edge = centroid_vector[0] * tangent[0] + centroid_vector[1] * tangent[1]
    normal_vector = (
        centroid_vector[0] - along_edge * tangent[0],
        centroid_vector[1] - along_edge * tangent[1],
    )
    normal_length = math.hypot(*normal_vector)
    if normal_length <= max(tolerance, _GEOMETRY_EPSILON):
        return None
    inward = (normal_vector[0] / normal_length, normal_vector[1] / normal_length)
    projections = [
        (point[0] - midpoint[0]) * inward[0] + (point[1] - midpoint[1]) * inward[1]
        for point in list(stall.exterior.coords)[:-1]
    ]
    if min(projections) < -max(tolerance, 1e-6):
        return None
    depth = max(projections)
    if depth <= _GEOMETRY_EPSILON:
        return None
    return midpoint, tangent, inward, depth


def _parallelogram_stall_basis(
    stall: BaseGeometry,
    edge: LineString,
    tolerance: float,
) -> tuple[Point, Point, Point, float, float] | None:
    """Return front-edge midpoint, tangent, parking inward, length, and width."""

    if not isinstance(stall, ShapelyPolygon):
        return None
    coords = list(stall.exterior.coords)[:-1]
    if len(coords) < 4:
        return None
    edge_coords = list(edge.coords)
    if len(edge_coords) < 2:
        return None
    start, end = edge_coords[0], edge_coords[-1]

    def _nearest_index(point: Point) -> int:
        return min(
            range(len(coords)),
            key=lambda index: math.hypot(coords[index][0] - point[0], coords[index][1] - point[1]),
        )

    start_index = _nearest_index(start)
    end_index = _nearest_index(end)
    if start_index == end_index:
        return None
    count = len(coords)

    def _other_vertex(index: int, forbidden: int) -> Point | None:
        for delta in (-1, 1):
            neighbor = (index + delta) % count
            if neighbor != forbidden:
                return coords[neighbor]
        return None

    start_other = _other_vertex(start_index, end_index)
    end_other = _other_vertex(end_index, start_index)
    if start_other is None or end_other is None:
        return None
    side_a = (start_other[0] - coords[start_index][0], start_other[1] - coords[start_index][1])
    side_b = (end_other[0] - coords[end_index][0], end_other[1] - coords[end_index][1])
    length_a = math.hypot(*side_a)
    length_b = math.hypot(*side_b)
    if length_a <= _GEOMETRY_EPSILON or length_b <= _GEOMETRY_EPSILON:
        return None
    unit_a = (side_a[0] / length_a, side_a[1] / length_a)
    unit_b = (side_b[0] / length_b, side_b[1] / length_b)
    cross = unit_a[0] * unit_b[1] - unit_a[1] * unit_b[0]
    aligned = unit_a[0] * unit_b[0] + unit_a[1] * unit_b[1]
    if abs(cross) > 0.05 or aligned < 0.95:
        return None
    inward = ((unit_a[0] + unit_b[0]) / 2.0, (unit_a[1] + unit_b[1]) / 2.0)
    inward_length = math.hypot(*inward)
    if inward_length <= _GEOMETRY_EPSILON:
        return None
    inward = (inward[0] / inward_length, inward[1] / inward_length)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    edge_length = math.hypot(dx, dy)
    if edge_length <= _GEOMETRY_EPSILON:
        return None
    tangent = (dx / edge_length, dy / edge_length)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    centroid_vector = (stall.centroid.x - midpoint[0], stall.centroid.y - midpoint[1])
    if centroid_vector[0] * inward[0] + centroid_vector[1] * inward[1] < 0.0:
        inward = (-inward[0], -inward[1])
    stall_length = (length_a + length_b) / 2.0
    perp = (-inward[1], inward[0])
    stall_width = abs((end[0] - start[0]) * perp[0] + (end[1] - start[1]) * perp[1])
    if stall_length <= max(tolerance, _GEOMETRY_EPSILON) or stall_width <= max(tolerance, _GEOMETRY_EPSILON):
        return None
    return midpoint, tangent, inward, stall_length, stall_width


def _heading_degrees(vector: Point) -> float:
    return math.degrees(math.atan2(vector[1], vector[0])) % 360.0


def _signed_heading_delta(start: float, end: float) -> float:
    return (end - start + 180.0) % 360.0 - 180.0


def _edge_points(edge: LineString) -> tuple[Point, Point]:
    coords = list(edge.coords)
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


def _angled_reverse_in_landing(
    vehicle: VehicleSpec,
    stall_geometry: BaseGeometry,
    parked_area: BaseGeometry,
    midpoint: Point,
    inward: Point,
    target_heading: float,
    stall_depth: float,
    geometry_tolerance: float,
) -> tuple[float, VehiclePose] | None:
    """Pick the deepest rear-axle pose whose body fits stall ∪ aisle throat."""

    stall_with_tol = stall_geometry.buffer(geometry_tolerance)
    for target_depth in reversed(_template_arc_end_depths(stall_depth)):
        target_pose = VehiclePose(
            midpoint[0] + inward[0] * target_depth,
            midpoint[1] + inward[1] * target_depth,
            target_heading,
        )
        if not stall_with_tol.covers(ShapelyPoint(target_pose.x, target_pose.y)):
            continue
        body = vehicle_footprint(vehicle, target_pose, include_margin=False)
        if parked_area.covers(body):
            return target_depth, target_pose
    return None


def _template_arc_end_depths(target_depth: float) -> tuple[float, ...]:
    if target_depth <= _GEOMETRY_EPSILON:
        return (0.0,)
    step = min(0.25, target_depth)
    count = math.floor(target_depth / step)
    values = [index * step for index in range(count + 1)]
    if not math.isclose(values[-1], target_depth, abs_tol=_GEOMETRY_EPSILON):
        values.append(target_depth)
    return tuple(values)


def _stall_side_reference_area(aisle: BaseGeometry, tangent: Point, inward: Point) -> BaseGeometry:
    min_x, min_y, max_x, max_y = aisle.bounds
    extent = max(max_x - min_x, max_y - min_y, 1.0) * 10.0
    center = (aisle.centroid.x, aisle.centroid.y)
    half_plane = ShapelyPolygon(
        [
            (center[0] - tangent[0] * extent, center[1] - tangent[1] * extent),
            (center[0] + tangent[0] * extent, center[1] + tangent[1] * extent),
            (
                center[0] + tangent[0] * extent + inward[0] * extent,
                center[1] + tangent[1] * extent + inward[1] * extent,
            ),
            (
                center[0] - tangent[0] * extent + inward[0] * extent,
                center[1] - tangent[1] * extent + inward[1] * extent,
            ),
        ]
    )
    return aisle.intersection(half_plane)


def _pose_record(pose: VehiclePose | None) -> dict[str, float] | None:
    if pose is None:
        return None
    return {"x": pose.x, "y": pose.y, "heading_degrees": pose.heading_degrees}
