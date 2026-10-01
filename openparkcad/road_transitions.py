"""Finite forward road templates: straight, throat, turns, dogleg, and U-turn."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, VehicleSpec
from openparkcad.road_traversal_models import (
    POSITION_TOLERANCE_M,
    RoadTransition,
    TransitionEvidence,
    TraversalPolicy,
    TraversalState,
    headings_close,
    poses_joinable,
    wrap_heading_delta,
)
from openparkcad.swept_path import (
    resolve_vehicle_overhangs,
    validate_swept_path,
    vehicle_footprint,
)
from openparkcad.vehicle_kinematics import (
    VehiclePose,
    arc_motion,
    rear_axle_turning_radius,
    simulate_bicycle_path,
    straight_motion,
)

_GEOMETRY_EPSILON = 1e-9
_TURN_HEADING_TOLERANCE = 8.0
_DOGLEG_ALPHAS = (5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 45.0)
_RADIUS_FACTORS = (1.0, 1.25, 1.5, 2.0)
_STATION_STEP_M = 2.0


@dataclass(frozen=True)
class OccupancySet:
    site: BaseGeometry
    road_pavement: BaseGeometry
    hard_obstacles: BaseGeometry
    stall_faces: dict[str, BaseGeometry]
    aisle_polygons: dict[str, BaseGeometry]
    entrance_throats: dict[str, BaseGeometry]

    def road_allowed(self) -> BaseGeometry:
        parts = [self.road_pavement, *self.entrance_throats.values()]
        return unary_union(parts)

    def parking_allowed(self, stall_id: str, serving_aisle_id: str | None, *, margin: float = 0.0) -> BaseGeometry:
        parts = [self.road_pavement]
        stall = self.stall_faces.get(stall_id)
        if stall is not None:
            parts.append(stall.buffer(max(margin, 0.0)))
        if serving_aisle_id and serving_aisle_id in self.aisle_polygons:
            parts.append(self.aisle_polygons[serving_aisle_id])
        return unary_union(parts)

    def obstacles_excluding_stall(self, stall_id: str | None) -> list[BaseGeometry]:
        parts: list[BaseGeometry] = []
        if not self.hard_obstacles.is_empty:
            parts.append(self.hard_obstacles)
        for item_id, geometry in self.stall_faces.items():
            if stall_id is None or item_id != stall_id:
                parts.append(geometry)
        return parts


def heading_unit(heading_degrees: float) -> tuple[float, float]:
    radians = math.radians(heading_degrees)
    return (math.cos(radians), math.sin(radians))


def farthest_body_radius(vehicle: VehicleSpec) -> float:
    resolution = resolve_vehicle_overhangs(vehicle)
    if not resolution.valid or resolution.front_overhang is None or resolution.rear_overhang is None:
        raise ValueError(resolution.reason or "Vehicle overhangs cannot be resolved")
    if vehicle.wheelbase is None:
        raise ValueError("vehicle_wheelbase_missing_or_invalid")
    margin = max(vehicle.swept_path_margin, 0.0)
    half_width = vehicle.width / 2.0 + margin
    front = vehicle.wheelbase + resolution.front_overhang + margin
    rear = resolution.rear_overhang + margin
    return math.hypot(max(front, rear), half_width)


def heading_step_sagitta(body_radius: float, heading_step_degrees: float) -> float:
    if body_radius <= 0.0:
        return 0.0
    return float(body_radius) * (1.0 - math.cos(math.radians(abs(heading_step_degrees)) / 2.0))


def build_occupancy(layout: LayoutResult) -> OccupancySet:
    site = ShapelyPolygon(layout.site.boundary)
    aisle_polygons = {
        aisle.id: ShapelyPolygon(aisle.polygon)
        for aisle in layout.aisles
        if aisle.role != "passing_bay" and len(aisle.polygon) >= 3
    }
    road_pavement = unary_union(list(aisle_polygons.values())) if aisle_polygons else ShapelyPolygon()
    stall_faces = {stall.id: ShapelyPolygon(stall.polygon) for stall in layout.stalls if len(stall.polygon) >= 3}
    obstacle_parts = [ShapelyPolygon(item) for item in layout.site.obstacles if len(item) >= 3]
    hard_obstacles = unary_union(obstacle_parts) if obstacle_parts else ShapelyPolygon()
    throats = {entrance.id: entrance_throat_polygon(entrance) for entrance in layout.site.entrances}
    return OccupancySet(
        site=site,
        road_pavement=road_pavement,
        hard_obstacles=hard_obstacles,
        stall_faces=stall_faces,
        aisle_polygons=aisle_polygons,
        entrance_throats=throats,
    )


def entrance_throat_polygon(entrance: EntranceSpec, depth: float | None = None) -> BaseGeometry:
    ux, uy = heading_unit(entrance.heading_degrees)
    nx, ny = -uy, ux
    half = entrance.width / 2.0
    inward = depth if depth is not None else max(entrance.width, 8.0)
    center = entrance.center
    outer_left = (center[0] + nx * half - ux * 0.5, center[1] + ny * half - uy * 0.5)
    outer_right = (center[0] - nx * half - ux * 0.5, center[1] - ny * half - uy * 0.5)
    inner_left = (center[0] + nx * half + ux * inward, center[1] + ny * half + uy * inward)
    inner_right = (center[0] - nx * half + ux * inward, center[1] - ny * half + uy * inward)
    return ShapelyPolygon([outer_left, outer_right, inner_right, inner_left])


def entrance_inner_pose(
    entrance: EntranceSpec,
    vehicle: VehicleSpec,
    occupancy: OccupancySet,
    *,
    for_exit: bool = False,
) -> tuple[VehiclePose | None, str | None]:
    resolution = resolve_vehicle_overhangs(vehicle)
    if not resolution.valid or resolution.rear_overhang is None or resolution.front_overhang is None:
        return None, resolution.reason or "vehicle_footprint_invalid"
    if vehicle.width + 2.0 * max(vehicle.swept_path_margin, 0.0) > entrance.width + 1e-6:
        return None, "entrance_throat_too_narrow"
    margin = max(vehicle.swept_path_margin, 0.0)
    ix, iy = heading_unit(entrance.heading_degrees)
    if for_exit:
        heading = wrap_heading_delta(entrance.heading_degrees + 180.0)
        inset = resolution.front_overhang + float(vehicle.wheelbase or 0.0) + margin + 0.05
        pose = VehiclePose(entrance.center[0] + ix * inset, entrance.center[1] + iy * inset, heading)
    else:
        heading = wrap_heading_delta(entrance.heading_degrees)
        inset = resolution.rear_overhang + margin + 0.05
        pose = VehiclePose(entrance.center[0] + ix * inset, entrance.center[1] + iy * inset, heading)
    try:
        body = vehicle_footprint(vehicle, pose)
    except ValueError as error:
        return None, str(error)
    allowed = occupancy.site.buffer(1e-7)
    if not allowed.covers(body):
        return None, "entrance_inner_body_outside_site"
    throat = occupancy.entrance_throats.get(entrance.id)
    if throat is not None and not throat.buffer(0.05).intersects(body):
        return None, "entrance_inner_not_in_throat"
    return pose, None


def make_state(
    kind: str,
    pose: VehiclePose,
    *,
    aisle_id: str | None = None,
    entrance_id: str | None = None,
    stall_id: str | None = None,
    travel_direction: str = "forward",
    occupancy_region: str = "road",
) -> TraversalState:
    pose_key = f"{pose.x:.3f}:{pose.y:.3f}:{pose.heading_degrees:.3f}"
    parts = [kind]
    if entrance_id:
        parts.append(entrance_id)
    if aisle_id:
        parts.append(aisle_id)
    if stall_id:
        parts.append(stall_id)
    parts.append(pose_key)
    parts.append(travel_direction)
    return TraversalState(
        state_id=":".join(parts),
        kind=kind,
        pose=pose,
        travel_direction=travel_direction,
        aisle_id=aisle_id,
        entrance_id=entrance_id,
        stall_id=stall_id,
        occupancy_region=occupancy_region,
    )


def evaluate_motion(
    vehicle: VehicleSpec,
    start: VehiclePose,
    segments: list | tuple,
    *,
    allowed: BaseGeometry,
    obstacles: list[BaseGeometry],
    policy: TraversalPolicy,
    family: str,
) -> TransitionEvidence:
    started = time.perf_counter()
    sagitta = heading_step_sagitta(farthest_body_radius(vehicle), policy.max_heading_step_degrees)
    grown_obstacles = [item.buffer(sagitta) if sagitta > 0.0 else item for item in obstacles if not item.is_empty]
    result = validate_swept_path(
        vehicle,
        start,
        segments,
        boundary=allowed,
        obstacles=grown_obstacles,
        sample_step=policy.sample_step_m,
        max_heading_step_degrees=policy.max_heading_step_degrees,
    )
    collision = None
    if result.reason == "swept_path_intersects_obstacle":
        collision = "obstacle_or_occupied_stall"
    elif result.reason == "swept_path_outside_boundary":
        collision = "drivable_boundary"
    elapsed = time.perf_counter() - started
    return TransitionEvidence(
        executed=True,
        status="passed" if result.valid else "failed",
        from_pose=start,
        to_pose=result.kinematics.final_pose,
        valid=result.valid,
        reason=None if result.valid else (result.reason or family),
        collision_object=collision,
        duration_seconds=elapsed,
        envelope_area=float(result.envelope.area) if result.envelope is not None else None,
        details={
            "family": family,
            "sagitta_buffer_m": sagitta,
            "sample_step_m": policy.sample_step_m,
            "max_heading_step_degrees": policy.max_heading_step_degrees,
            "swept_path_margin": vehicle.swept_path_margin,
            "colliding_obstacle_indices": list(result.colliding_obstacle_indices),
        },
    )


def try_straight(
    vehicle: VehicleSpec,
    start: TraversalState,
    end: TraversalState,
    *,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    family: str,
    stall_id: str | None = None,
) -> tuple[RoadTransition, TransitionEvidence] | None:
    dx = end.pose.x - start.pose.x
    dy = end.pose.y - start.pose.y
    distance = math.hypot(dx, dy)
    if distance <= POSITION_TOLERANCE_M:
        if poses_joinable(start.pose, end.pose, position_tolerance_m=policy.position_tolerance_m, heading_tolerance_degrees=policy.heading_tolerance_degrees):
            segments = (straight_motion(0.0, label="degenerate_join"),)
            evidence = evaluate_motion(
                vehicle,
                start.pose,
                segments,
                allowed=_allowed_for(start, occupancy, stall_id),
                obstacles=occupancy.obstacles_excluding_stall(stall_id),
                policy=policy,
                family=family,
            )
            return _transition(family, start, end, segments, "same_pose"), evidence
        return None
    ux, uy = heading_unit(start.pose.heading_degrees)
    along = dx * ux + dy * uy
    lateral = abs(dx * (-uy) + dy * ux)
    if along <= _GEOMETRY_EPSILON:
        return None
    if not headings_close(start.pose.heading_degrees, end.pose.heading_degrees, policy.heading_tolerance_degrees):
        return None
    expected = (start.pose.x + ux * along, start.pose.y + uy * along)
    if math.hypot(expected[0] - end.pose.x, expected[1] - end.pose.y) > policy.position_tolerance_m:
        return None
    if lateral > policy.position_tolerance_m:
        return None
    segments = (straight_motion(along, label=family),)
    evidence = evaluate_motion(
        vehicle,
        start.pose,
        segments,
        allowed=_allowed_for(start, occupancy, stall_id),
        obstacles=occupancy.obstacles_excluding_stall(stall_id),
        policy=policy,
        family=family,
    )
    if not evidence.valid or not poses_joinable(
        evidence.to_pose,
        end.pose,
        position_tolerance_m=policy.position_tolerance_m,
        heading_tolerance_degrees=policy.heading_tolerance_degrees,
    ):
        return None
    return _transition(family, start, end, segments, "aligned_forward"), evidence


def try_arc_turn(
    vehicle: VehicleSpec,
    start: TraversalState,
    end: TraversalState,
    *,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    family: str,
    stall_id: str | None = None,
) -> tuple[RoadTransition, TransitionEvidence] | None:
    delta = wrap_heading_delta(end.pose.heading_degrees - start.pose.heading_degrees)
    ux, uy = heading_unit(start.pose.heading_degrees)
    side = ux * (end.pose.y - start.pose.y) - uy * (end.pose.x - start.pose.x)
    if abs(abs(delta) - 180.0) <= _TURN_HEADING_TOLERANCE:
        delta = 180.0 if side >= 0.0 else -180.0
    abs_delta = abs(delta)
    if abs(abs_delta - 90.0) > _TURN_HEADING_TOLERANCE and abs(abs_delta - 180.0) > _TURN_HEADING_TOLERANCE:
        return None
    resolution = rear_axle_turning_radius(vehicle)
    if not resolution.valid or resolution.rear_axle_radius is None:
        return None
    radii = [resolution.rear_axle_radius * factor for factor in _RADIUS_FACTORS]
    chord = math.hypot(end.pose.x - start.pose.x, end.pose.y - start.pose.y)
    if abs_delta > _GEOMETRY_EPSILON and chord > _GEOMETRY_EPSILON:
        implied = chord / (2.0 * math.sin(math.radians(abs_delta) / 2.0))
        if implied > 0.0 and math.isfinite(implied):
            radii.insert(0, implied)
    for radius in radii:
        turn_sign = 1.0 if delta > 0 else -1.0
        along = radius * math.sin(math.radians(abs_delta))
        lateral = turn_sign * radius * (1.0 - math.cos(math.radians(abs_delta)))
        predicted_x = start.pose.x + along * ux - lateral * uy
        predicted_y = start.pose.y + along * uy + lateral * ux
        if math.hypot(predicted_x - end.pose.x, predicted_y - end.pose.y) > policy.position_tolerance_m:
            continue
        try:
            segment = arc_motion(vehicle, delta, radius=radius, label=family)
        except ValueError:
            continue
        simulation = simulate_bicycle_path(
            vehicle,
            start.pose,
            [segment],
            sample_step=policy.sample_step_m,
            max_heading_step_degrees=policy.max_heading_step_degrees,
        )
        if not simulation.valid or simulation.final_pose is None:
            continue
        if not poses_joinable(
            simulation.final_pose,
            end.pose,
            position_tolerance_m=policy.position_tolerance_m,
            heading_tolerance_degrees=policy.heading_tolerance_degrees,
        ):
            continue
        evidence = evaluate_motion(
            vehicle,
            start.pose,
            (segment,),
            allowed=_allowed_for(start, occupancy, stall_id),
            obstacles=occupancy.obstacles_excluding_stall(stall_id),
            policy=policy,
            family=family,
        )
        if not evidence.valid:
            continue
        return _transition(family, start, end, (segment,), "constant_radius_turn", {"radius": radius, "heading_change": delta}), evidence
    return None


def try_approach_turn(
    vehicle: VehicleSpec,
    start: TraversalState,
    end: TraversalState,
    *,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    family: str,
    stall_id: str | None = None,
) -> tuple[RoadTransition, TransitionEvidence] | None:
    """Join orthogonal road stations with a forward straight/arc/straight.

    Uniformly sampled stations need not be tangent points of one circular arc.
    Solve the two straight lengths in the start frame; never bridge an endpoint
    gap or shorten the vehicle's minimum radius to make a connection fit.
    """
    delta = wrap_heading_delta(end.pose.heading_degrees - start.pose.heading_degrees)
    if abs(abs(delta) - 90.0) > 1e-6:
        return None
    ux, uy = heading_unit(start.pose.heading_degrees)
    dx, dy = end.pose.x - start.pose.x, end.pose.y - start.pose.y
    along = dx * ux + dy * uy
    lateral = (dx * -uy + dy * ux) * (1.0 if delta > 0 else -1.0)
    resolution = rear_axle_turning_radius(vehicle)
    if not resolution.valid or resolution.rear_axle_radius is None:
        return None
    for factor in _RADIUS_FACTORS:
        radius = resolution.rear_axle_radius * factor
        approach, departure = along - radius, lateral - radius
        if min(approach, departure) < -_GEOMETRY_EPSILON:
            continue
        segments = (
            straight_motion(max(approach, 0.0), label="turn_approach"),
            arc_motion(vehicle, delta, radius=radius, label=family),
            straight_motion(max(departure, 0.0), label="turn_departure"),
        )
        evidence = evaluate_motion(
            vehicle, start.pose, segments,
            allowed=_allowed_for(start, occupancy, stall_id),
            obstacles=occupancy.obstacles_excluding_stall(stall_id),
            policy=policy, family=family,
        )
        if evidence.valid and poses_joinable(
            evidence.to_pose, end.pose,
            position_tolerance_m=policy.position_tolerance_m,
            heading_tolerance_degrees=policy.heading_tolerance_degrees,
        ):
            return _transition(family, start, end, segments, "straight_arc_straight", {"radius": radius}), evidence
    return None


def try_dogleg(
    vehicle: VehicleSpec,
    start: TraversalState,
    end: TraversalState,
    *,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    family: str = "dogleg_single_jog",
    stall_id: str | None = None,
) -> tuple[RoadTransition, TransitionEvidence] | None:
    heading_delta = wrap_heading_delta(end.pose.heading_degrees - start.pose.heading_degrees)
    if abs(heading_delta) > 50.0:
        return None
    ux, uy = heading_unit(start.pose.heading_degrees)
    dx = end.pose.x - start.pose.x
    dy = end.pose.y - start.pose.y
    along = dx * ux + dy * uy
    left = -dx * uy + dy * ux
    if along <= 0.2:
        return None
    resolution = rear_axle_turning_radius(vehicle)
    if not resolution.valid or resolution.rear_axle_radius is None:
        return None
    radii = [resolution.rear_axle_radius * factor for factor in _RADIUS_FACTORS]
    alphas = _DOGLEG_ALPHAS if family == "dogleg_single_jog" else (20.0, 30.0)
    sign = 1.0 if left >= 0.0 else -1.0
    for alpha in alphas:
        for radius in radii:
            if family == "dogleg_single_jog":
                lateral_from_arcs = 2.0 * radius * (1.0 - math.cos(math.radians(alpha)))
                remaining = abs(left) - lateral_from_arcs
                if remaining < -0.05:
                    continue
                sin_a = math.sin(math.radians(alpha))
                cos_a = math.cos(math.radians(alpha))
                if sin_a <= _GEOMETRY_EPSILON:
                    continue
                middle = remaining / sin_a
                if middle < -0.05:
                    continue
                middle = max(middle, 0.0)
                jog_long = 2.0 * radius * sin_a + middle * cos_a
                extra = along - jog_long
                if extra < -0.05:
                    continue
                extra = max(extra, 0.0)
                try:
                    segments = (
                        straight_motion(extra, label="dogleg_approach"),
                        arc_motion(vehicle, sign * alpha, radius=radius, label="dogleg_enter"),
                        straight_motion(middle, label="dogleg_middle"),
                        arc_motion(vehicle, heading_delta - sign * alpha, radius=radius, label="dogleg_exit"),
                    )
                except ValueError:
                    continue
            else:
                half = alpha
                try:
                    segments = (
                        arc_motion(vehicle, sign * half, radius=radius, label="dogleg_first"),
                        straight_motion(max(along * 0.25, 0.5), label="dogleg_a"),
                        arc_motion(vehicle, -sign * half, radius=radius, label="dogleg_mid"),
                        straight_motion(max(along * 0.25, 0.5), label="dogleg_b"),
                        arc_motion(vehicle, heading_delta, radius=radius, label="dogleg_last"),
                    )
                except ValueError:
                    continue
            simulation = simulate_bicycle_path(
                vehicle,
                start.pose,
                list(segments),
                sample_step=policy.sample_step_m,
                max_heading_step_degrees=policy.max_heading_step_degrees,
            )
            if not simulation.valid or simulation.final_pose is None:
                continue
            if not poses_joinable(
                simulation.final_pose,
                end.pose,
                position_tolerance_m=policy.position_tolerance_m,
                heading_tolerance_degrees=policy.heading_tolerance_degrees,
            ):
                continue
            evidence = evaluate_motion(
                vehicle,
                start.pose,
                segments,
                allowed=_allowed_for(start, occupancy, stall_id),
                obstacles=occupancy.obstacles_excluding_stall(stall_id),
                policy=policy,
                family=family,
            )
            if not evidence.valid:
                continue
            return _transition(family, start, end, segments, "dogleg", {"alpha": alpha, "radius": radius}), evidence
    return None


def connect_states(
    vehicle: VehicleSpec,
    start: TraversalState,
    end: TraversalState,
    *,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    family_hint: str | None = None,
    stall_id: str | None = None,
) -> tuple[RoadTransition, TransitionEvidence] | None:
    if start.travel_direction != "forward" or end.travel_direction != "forward":
        return None
    heading_delta = abs(wrap_heading_delta(end.pose.heading_degrees - start.pose.heading_degrees))
    ux, uy = heading_unit(start.pose.heading_degrees)
    dx = end.pose.x - start.pose.x
    dy = end.pose.y - start.pose.y
    along = dx * ux + dy * uy
    lateral = abs(dx * (-uy) + dy * ux)
    families: list[str] = []
    if family_hint:
        families.append(family_hint)
    if heading_delta <= max(policy.heading_tolerance_degrees, 1.0) and lateral <= policy.position_tolerance_m and along > 0:
        families.append("straight_forward")
    if 80.0 <= heading_delta <= 100.0:
        families.append("main_branch_left_turn" if wrap_heading_delta(end.pose.heading_degrees - start.pose.heading_degrees) > 0 else "main_branch_right_turn")
        families.append("exit_turn")
    if 160.0 <= heading_delta <= 200.0:
        families.append("turnaround_u_turn")
        families.append("u_connector")
    if heading_delta <= 50.0 and along > 0.4 and 0.02 < lateral < 4.0:
        families.append("dogleg_single_jog")
    seen: set[str] = set()
    turn_attempted = False
    for family in families:
        if family in seen:
            continue
        seen.add(family)
        if family in {"straight_forward", "entrance_throat_to_main", "main_to_exit_throat"}:
            found = try_straight(vehicle, start, end, occupancy=occupancy, policy=policy, family=family, stall_id=stall_id)
            if found:
                return found
        elif family in {
            "main_branch_left_turn",
            "main_branch_right_turn",
            "exit_turn",
            "turnaround_u_turn",
            "u_connector",
        }:
            if turn_attempted:
                continue
            turn_attempted = True
            found = try_arc_turn(vehicle, start, end, occupancy=occupancy, policy=policy, family=family, stall_id=stall_id)
            if found is None:
                found = try_approach_turn(vehicle, start, end, occupancy=occupancy, policy=policy, family=family, stall_id=stall_id)
            if found:
                return found
        elif family in {"dogleg_single_jog", "dogleg_double_jog"}:
            found = try_dogleg(
                vehicle,
                start,
                end,
                occupancy=occupancy,
                policy=policy,
                family=family,
                stall_id=stall_id,
            )
            if found:
                return found
    return None


def aisle_centerline(aisle: ParkingAisle) -> LineString | None:
    polygon = ShapelyPolygon(aisle.polygon)
    if polygon.is_empty or polygon.area <= 0.0:
        return None
    ux, uy = heading_unit(aisle.angle_degrees)
    centroid = polygon.centroid
    span = 1000.0
    line = LineString(
        [
            (centroid.x - ux * span, centroid.y - uy * span),
            (centroid.x + ux * span, centroid.y + uy * span),
        ]
    )
    clipped = line.intersection(polygon)
    if clipped.is_empty:
        return None
    if clipped.geom_type == "LineString":
        return clipped
    if clipped.geom_type == "MultiLineString":
        return max(clipped.geoms, key=lambda item: item.length)
    return None


def sample_aisle_states(aisle: ParkingAisle, vehicle: VehicleSpec) -> list[TraversalState]:
    centerline = aisle_centerline(aisle)
    if centerline is None or centerline.length < 0.2:
        return []
    count = max(2, int(centerline.length / _STATION_STEP_M) + 1)
    states: list[TraversalState] = []
    headings = [aisle.angle_degrees]
    if aisle.directionality != "one_way":
        headings.append(aisle.angle_degrees + 180.0)
    for index in range(count):
        fraction = index / (count - 1)
        point = centerline.interpolate(fraction, normalized=True)
        for heading in headings:
            pose = VehiclePose(point.x, point.y, heading)
            try:
                body = vehicle_footprint(vehicle, pose)
            except ValueError:
                continue
            if not ShapelyPolygon(aisle.polygon).buffer(0.05).covers(body.centroid):
                continue
            states.append(make_state("road", pose, aisle_id=aisle.id, travel_direction="forward"))
    return states


def _transition(
    family: str,
    start: TraversalState,
    end: TraversalState,
    segments: tuple,
    relation: str,
    parameters: dict[str, Any] | None = None,
) -> RoadTransition:
    return RoadTransition(
        transition_id=f"{family}:{start.state_id}->{end.state_id}",
        family=family,
        from_state=start,
        to_state=end,
        segments=tuple(segments),
        road_relation=relation,
        parameters=parameters or {},
    )


def _allowed_for(state: TraversalState, occupancy: OccupancySet, stall_id: str | None) -> BaseGeometry:
    if state.occupancy_region == "parking" and stall_id:
        return occupancy.parking_allowed(stall_id, state.aisle_id, margin=0.4)
    return occupancy.road_allowed()


def point_on_aisle(aisle: ParkingAisle, pose: VehiclePose) -> bool:
    return ShapelyPolygon(aisle.polygon).buffer(0.2).covers(ShapelyPoint(pose.x, pose.y))
