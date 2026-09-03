"""Compose and recheck interior stall journeys from finite road and parking templates."""

from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, replace
from typing import Any

from openparkcad.models import LayoutResult, ParkingStall
from openparkcad.parking_motion_adapter import ParkingMotion, parking_motion_for_stall
from openparkcad.phase1_support import entry_capable_entrances, exit_capable_entrances
from openparkcad.road_traversal_models import (
    ALGORITHM_VERSION,
    RoadTraversalResult,
    StallJourneyEvidence,
    TraversalPolicy,
    TraversalState,
    layout_traversal_identity,
    parse_traversal_policy,
    poses_joinable,
    publication_requires_road_evidence,
    road_traversal_satisfies_request,
    vehicle_support_reason,
    wrap_heading_delta,
)
from openparkcad.road_transitions import (
    OccupancySet,
    RoadTransition,
    build_occupancy,
    connect_states,
    entrance_inner_pose,
    evaluate_motion,
    make_state,
    sample_aisle_states,
)
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph
from openparkcad.vehicle_kinematics import MotionSegment, VehiclePose, simulate_bicycle_path

_NEIGHBOR_DISTANCE_M = 22.0
_MAX_EXPANSIONS = 8000


@dataclass
class _Graph:
    states: dict[str, TraversalState]
    outgoing: dict[str, list[tuple[str, RoadTransition, float]]]


def validate_road_traversal(
    layout: LayoutResult,
    *,
    deadline: float | None = None,
    outer_deadline: float | None = None,
    source_layout_id: str | None = None,
    result_layout_id: str | None = None,
) -> dict[str, Any]:
    """Run or skip the interior road check for one concrete layout."""

    policy = parse_traversal_policy(layout.site)
    identity = layout_traversal_identity(layout, policy)
    started = time.perf_counter()
    active_deadline, budget_source, budget = _resolve_deadline(policy, deadline, outer_deadline, started)
    if not policy.requested:
        return RoadTraversalResult(
            algorithm_version=ALGORITHM_VERSION,
            layout_identity=identity,
            policy=policy,
            requested=False,
            executed=False,
            status="not_requested",
            valid=None,
            reason=None,
            stall_coverage=0,
            stall_count=len(layout.stalls),
            time_budget_seconds=budget,
            elapsed_seconds=time.perf_counter() - started,
            budget_source=budget_source,
            source_layout_id=source_layout_id,
            result_layout_id=result_layout_id or _layout_result_id(layout),
        ).to_record()

    support = vehicle_support_reason(layout.site.vehicle)
    if support is not None:
        status = "unsupported"
        return _finished(
            policy,
            identity,
            started,
            budget,
            budget_source,
            status=status,
            valid=None,
            reason=support,
            stall_count=len(layout.stalls),
            journeys=(),
            failures=({"reason": support, "classification": status},),
            source_layout_id=source_layout_id,
            result_layout_id=result_layout_id or _layout_result_id(layout),
        )

    vehicle = layout.site.vehicle
    assert vehicle is not None
    if not layout.stalls or not layout.aisles:
        return _finished(
            policy,
            identity,
            started,
            budget,
            budget_source,
            status="failed",
            valid=False,
            reason="no_layout_to_validate",
            stall_count=len(layout.stalls),
            journeys=(),
            failures=({"reason": "no_layout_to_validate", "classification": "failed"},),
            source_layout_id=source_layout_id,
            result_layout_id=result_layout_id or _layout_result_id(layout),
        )
    occupancy = build_occupancy(layout)
    if _budget_hit(active_deadline):
        return _incomplete(policy, identity, started, budget, budget_source, len(layout.stalls), "time_budget_exhausted", source_layout_id, result_layout_id or _layout_result_id(layout))

    parking_motions = {
        stall.id: parking_motion_for_stall(layout, stall, occupancy, policy, vehicle) for stall in layout.stalls
    }
    graph = _build_pose_graph(layout, occupancy, policy, vehicle, parking_motions, active_deadline)
    if graph is None:
        return _incomplete(policy, identity, started, budget, budget_source, len(layout.stalls), "time_budget_exhausted", source_layout_id, result_layout_id or _layout_result_id(layout))

    journeys: list[StallJourneyEvidence] = []
    failures: list[dict[str, Any]] = []
    incomplete = False
    for stall in layout.stalls:
        if _budget_hit(active_deadline):
            incomplete = True
            journeys.append(
                StallJourneyEvidence(
                    stall_id=stall.id,
                    status="incomplete",
                    valid=None,
                    reason="time_budget_exhausted",
                    entrance_id=None,
                    exit_id=None,
                    inbound_transition_ids=(),
                    parking_family=parking_motions[stall.id].family,
                    outbound_transition_ids=(),
                    continuity_valid=None,
                    reverse_distance=0.0,
                    path_length=0.0,
                    parked_pose=None,
                )
            )
            continue
        journey = _journey_for_stall(
            layout,
            stall,
            occupancy,
            policy,
            vehicle,
            graph,
            parking_motions[stall.id],
            active_deadline,
        )
        journeys.append(journey)
        if journey.status == "incomplete":
            incomplete = True
        elif journey.valid is not True:
            failures.append(
                {
                    "stall_id": stall.id,
                    "status": journey.status,
                    "reason": journey.reason,
                    "collision_object": journey.collision_object,
                }
            )

    elapsed = time.perf_counter() - started
    coverage = sum(1 for item in journeys if item.status == "passed" and item.valid is True)
    if incomplete:
        status = "incomplete"
        valid: bool | None = None
        reason = "time_budget_exhausted"
    elif any(item.status == "unsupported" for item in journeys):
        status = "unsupported"
        valid = None
        reason = next(item.reason for item in journeys if item.status == "unsupported")
    elif coverage == len(layout.stalls):
        status = "passed"
        valid = True
        reason = None
    else:
        status = "failed"
        valid = False
        reason = failures[0]["reason"] if failures else "no_supported_route_found"

    return RoadTraversalResult(
        algorithm_version=ALGORITHM_VERSION,
        layout_identity=identity,
        policy=policy,
        requested=True,
        executed=True,
        status=status,
        valid=valid,
        reason=reason,
        stall_coverage=coverage,
        stall_count=len(layout.stalls),
        time_budget_seconds=budget,
        elapsed_seconds=elapsed,
        budget_source=budget_source,
        journeys=tuple(journeys),
        failures=tuple(failures),
        source_layout_id=source_layout_id,
        result_layout_id=result_layout_id or _layout_result_id(layout),
    ).to_record()


def apply_road_traversal(
    layout: LayoutResult,
    *,
    deadline: float | None = None,
    outer_deadline: float | None = None,
    source_layout_id: str | None = None,
    result_layout_id: str | None = None,
) -> LayoutResult:
    record = validate_road_traversal(
        layout,
        deadline=deadline,
        outer_deadline=outer_deadline,
        source_layout_id=source_layout_id,
        result_layout_id=result_layout_id,
    )
    return replace(layout, road_traversal_validation=record)


def road_traversal_satisfied(layout: LayoutResult) -> bool:
    policy = parse_traversal_policy(layout.site)
    if not policy.requested:
        return True
    record = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else None
    if not record:
        return False
    expected = layout_traversal_identity(layout, policy)
    if record.get("layout_identity") != expected:
        return False
    return road_traversal_satisfies_request(record)


def journey_trajectories(layout: LayoutResult) -> list[tuple[str, list[tuple[float, float]]]]:
    record = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    journeys = record.get("journeys") if isinstance(record.get("journeys"), list) else []
    traces: list[tuple[str, list[tuple[float, float]]]] = []
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        details = journey.get("details") if isinstance(journey.get("details"), dict) else {}
        raw = details.get("trajectory") if isinstance(details.get("trajectory"), list) else []
        points = [
            (float(item["x"]), float(item["y"]))
            for item in raw
            if isinstance(item, dict) and "x" in item and "y" in item
        ]
        if points:
            traces.append((str(journey.get("stall_id") or "journey"), points))
    return traces


def road_traversal_publication_error(layout: LayoutResult) -> str | None:
    site = getattr(layout, "site", None)
    if site is None:
        return None
    if not publication_requires_road_evidence(site):
        return None
    record = getattr(layout, "road_traversal_validation", None)
    record = record if isinstance(record, dict) else None
    if not record:
        return "requested road_traversal evidence is missing"
    if record.get("layout_identity") != layout_traversal_identity(layout, parse_traversal_policy(site)):
        return "road_traversal evidence does not match layout identity"
    if record.get("status") == "passed" and record.get("valid") is True:
        return None
    status = record.get("status") or "missing"
    reason = record.get("reason")
    detail = f" ({reason})" if reason else ""
    return f"road traversal {status}{detail}"


def _build_pose_graph(
    layout: LayoutResult,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    vehicle,
    parking_motions: dict[str, ParkingMotion],
    deadline: float | None,
) -> _Graph | None:
    states: dict[str, TraversalState] = {}
    outgoing: dict[str, list[tuple[str, RoadTransition, float]]] = {}

    def add_state(state: TraversalState) -> TraversalState:
        states[state.state_id] = state
        outgoing.setdefault(state.state_id, [])
        return state

    for entrance in entry_capable_entrances(layout.site):
        pose, reason = entrance_inner_pose(entrance, vehicle, occupancy, for_exit=False)
        if pose is None:
            continue
        add_state(make_state("entrance_inner", pose, entrance_id=entrance.id))
    for entrance in exit_capable_entrances(layout.site):
        pose, reason = entrance_inner_pose(entrance, vehicle, occupancy, for_exit=True)
        if pose is None:
            continue
        add_state(make_state("exit_inner", pose, entrance_id=entrance.id))

    aisle_states: dict[str, list[TraversalState]] = {}
    for aisle in layout.aisles:
        sampled = [add_state(item) for item in sample_aisle_states(aisle, vehicle)]
        aisle_states[aisle.id] = sampled

    parking_nodes: dict[str, ParkingMotion] = {}
    for motion in parking_motions.values():
        if not motion.valid or motion.start_state is None or motion.exit_state is None or motion.parked_state is None:
            continue
        add_state(motion.start_state)
        add_state(motion.parked_state)
        add_state(motion.exit_state)
        parking_nodes[motion.stall_id] = motion
        inbound = _parking_transition(motion, inbound=True)
        outbound = _parking_transition(motion, inbound=False)
        _add_edge(outgoing, inbound, inbound.to_state.state_id, _path_cost(inbound.segments))
        _add_edge(outgoing, outbound, outbound.to_state.state_id, _path_cost(outbound.segments))

    if _budget_hit(deadline):
        return None

    state_list = list(states.values())
    for aisle_id, sampled in aisle_states.items():
        by_heading: dict[int, list[TraversalState]] = {}
        for item in sampled:
            key = int(round(item.pose.heading_degrees / 5.0) * 5)
            by_heading.setdefault(key, []).append(item)
        for group in by_heading.values():
            ordered = sorted(group, key=lambda item: (item.pose.x, item.pose.y, item.state_id))
            for left, right in zip(ordered, ordered[1:]):
                if _budget_hit(deadline):
                    return None
                _try_add_connection(vehicle, left, right, occupancy, policy, outgoing, family_hint="straight_forward")
                _try_add_connection(vehicle, right, left, occupancy, policy, outgoing, family_hint="straight_forward")

    aisles_by_id = {aisle.id: aisle for aisle in layout.aisles}
    for aisle in layout.aisles:
        related_ids = set(aisle.connected_aisle_ids)
        if aisle.parent_aisle_id:
            related_ids.add(aisle.parent_aisle_id)
        for other_id in related_ids:
            if other_id not in aisles_by_id:
                continue
            for start in aisle_states.get(aisle.id, []):
                for end in aisle_states.get(other_id, []):
                    if math.hypot(start.pose.x - end.pose.x, start.pose.y - end.pose.y) > _NEIGHBOR_DISTANCE_M:
                        continue
                    if _budget_hit(deadline):
                        return None
                    family = _junction_family(aisle.role, aisles_by_id[other_id].role, start.pose, end.pose)
                    _try_add_connection(vehicle, start, end, occupancy, policy, outgoing, family_hint=family)

    for state in list(states.values()):
        if state.kind != "entrance_inner":
            continue
        for candidate in state_list:
            if candidate.kind not in {"road", "parking_start"}:
                continue
            if math.hypot(state.pose.x - candidate.pose.x, state.pose.y - candidate.pose.y) > 20.0:
                continue
            if _budget_hit(deadline):
                return None
            _try_add_connection(
                vehicle, state, candidate, occupancy, policy, outgoing, family_hint="entrance_throat_to_main"
            )
    for state in list(states.values()):
        if state.kind != "exit_inner":
            continue
        for candidate in state_list:
            if candidate.kind not in {"road", "parking_exit"}:
                continue
            if math.hypot(state.pose.x - candidate.pose.x, state.pose.y - candidate.pose.y) > 20.0:
                continue
            if _budget_hit(deadline):
                return None
            _try_add_connection(
                vehicle, candidate, state, occupancy, policy, outgoing, family_hint="main_to_exit_throat"
            )

    for motion in parking_nodes.values():
        assert motion.start_state is not None and motion.exit_state is not None
        for candidate in state_list:
            if candidate.kind != "road":
                continue
            if motion.start_state.aisle_id and candidate.aisle_id not in {motion.start_state.aisle_id, None}:
                if math.hypot(candidate.pose.x - motion.start_state.pose.x, candidate.pose.y - motion.start_state.pose.y) > _NEIGHBOR_DISTANCE_M:
                    continue
            if math.hypot(candidate.pose.x - motion.start_state.pose.x, candidate.pose.y - motion.start_state.pose.y) <= _NEIGHBOR_DISTANCE_M:
                _try_add_connection(vehicle, candidate, motion.start_state, occupancy, policy, outgoing)
            if math.hypot(candidate.pose.x - motion.exit_state.pose.x, candidate.pose.y - motion.exit_state.pose.y) <= _NEIGHBOR_DISTANCE_M:
                _try_add_connection(vehicle, motion.exit_state, candidate, occupancy, policy, outgoing)

    return _Graph(states=states, outgoing=outgoing)


def _try_add_connection(
    vehicle,
    start: TraversalState,
    end: TraversalState,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    outgoing: dict[str, list[tuple[str, RoadTransition, float]]],
    family_hint: str | None = None,
) -> None:
    if start.state_id == end.state_id:
        return
    found = connect_states(vehicle, start, end, occupancy=occupancy, policy=policy, family_hint=family_hint)
    if found is None:
        return
    transition, evidence = found
    if not evidence.valid:
        return
    _add_edge(outgoing, transition, end.state_id, _path_cost(transition.segments))


def _add_edge(
    outgoing: dict[str, list[tuple[str, RoadTransition, float]]],
    transition: RoadTransition,
    to_id: str,
    cost: float,
) -> None:
    outgoing.setdefault(transition.from_state.state_id, []).append((to_id, transition, cost))


def _journey_for_stall(
    layout: LayoutResult,
    stall: ParkingStall,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    vehicle,
    graph: _Graph,
    parking: ParkingMotion,
    deadline: float | None,
) -> StallJourneyEvidence:
    if parking.details.get("unsupported"):
        return StallJourneyEvidence(
            stall_id=stall.id,
            status="unsupported",
            valid=None,
            reason=parking.reason,
            entrance_id=None,
            exit_id=None,
            inbound_transition_ids=(),
            parking_family=parking.family,
            outbound_transition_ids=(),
            continuity_valid=None,
            reverse_distance=0.0,
            path_length=0.0,
            parked_pose=None,
        )
    if not parking.valid or parking.start_state is None or parking.exit_state is None:
        return StallJourneyEvidence(
            stall_id=stall.id,
            status="failed",
            valid=False,
            reason=parking.reason or "no_supported_route_found",
            entrance_id=None,
            exit_id=None,
            inbound_transition_ids=(),
            parking_family=parking.family,
            outbound_transition_ids=(),
            continuity_valid=False,
            reverse_distance=0.0,
            path_length=0.0,
            parked_pose=None,
            collision_object=parking.details.get("collision_object") if isinstance(parking.details.get("collision_object"), str) else None,
        )

    if not _graph_prefilter_allows(layout, stall):
        return StallJourneyEvidence(
            stall_id=stall.id,
            status="failed",
            valid=False,
            reason="no_supported_route_found",
            entrance_id=None,
            exit_id=None,
            inbound_transition_ids=(),
            parking_family=parking.family,
            outbound_transition_ids=(),
            continuity_valid=False,
            reverse_distance=0.0,
            path_length=0.0,
            parked_pose=None,
            details={"prefilter": "traffic_graph_unreachable"},
        )

    starts = [state for state in graph.states.values() if state.kind == "entrance_inner"]
    exits = [state for state in graph.states.values() if state.kind == "exit_inner"]
    inbound = _shortest_path(graph, [item.state_id for item in starts], parking.start_state.state_id, deadline)
    if inbound is None:
        status = "incomplete" if _budget_hit(deadline) else "failed"
        return StallJourneyEvidence(
            stall_id=stall.id,
            status=status,
            valid=None if status == "incomplete" else False,
            reason="time_budget_exhausted" if status == "incomplete" else "no_supported_route_found",
            entrance_id=None,
            exit_id=None,
            inbound_transition_ids=(),
            parking_family=parking.family,
            outbound_transition_ids=(),
            continuity_valid=False,
            reverse_distance=parking.reverse_distance,
            path_length=0.0,
            parked_pose=parking.parked_state.pose if parking.parked_state else None,
            details={"phase": "inbound"},
        )
    outbound = _shortest_path(graph, [parking.exit_state.state_id], {item.state_id for item in exits}, deadline)
    if outbound is None:
        status = "incomplete" if _budget_hit(deadline) else "failed"
        return StallJourneyEvidence(
            stall_id=stall.id,
            status=status,
            valid=None if status == "incomplete" else False,
            reason="time_budget_exhausted" if status == "incomplete" else "no_supported_route_found",
            entrance_id=inbound[0].from_state.entrance_id if inbound else None,
            exit_id=None,
            inbound_transition_ids=tuple(item.transition_id for item in inbound),
            parking_family=parking.family,
            outbound_transition_ids=(),
            continuity_valid=False,
            reverse_distance=parking.reverse_distance,
            path_length=_path_cost([segment for item in inbound for segment in item.segments]),
            parked_pose=parking.parked_state.pose if parking.parked_state else None,
            details={"phase": "outbound"},
        )

    recheck = _recheck_journey(vehicle, occupancy, policy, stall, inbound, parking, outbound)
    entrance_id = next((item.from_state.entrance_id for item in inbound if item.from_state.entrance_id), None)
    exit_id = next((item.to_state.entrance_id for item in reversed(outbound) if item.to_state.entrance_id), None)
    path_length = _path_cost(
        [segment for item in inbound for segment in item.segments]
        + list(parking.inbound_segments)
        + list(parking.outbound_segments)
        + [segment for item in outbound for segment in item.segments]
    )
    if recheck["valid"] is True:
        return StallJourneyEvidence(
            stall_id=stall.id,
            status="passed",
            valid=True,
            reason=None,
            entrance_id=entrance_id,
            exit_id=exit_id,
            inbound_transition_ids=tuple(item.transition_id for item in inbound),
            parking_family=parking.family,
            outbound_transition_ids=tuple(item.transition_id for item in outbound),
            continuity_valid=True,
            reverse_distance=parking.reverse_distance,
            path_length=path_length,
            parked_pose=parking.parked_state.pose if parking.parked_state else None,
            details=recheck,
        )
    return StallJourneyEvidence(
        stall_id=stall.id,
        status="failed",
        valid=False,
        reason=recheck.get("reason") or "journey_recheck_failed",
        entrance_id=entrance_id,
        exit_id=exit_id,
        inbound_transition_ids=tuple(item.transition_id for item in inbound),
        parking_family=parking.family,
        outbound_transition_ids=tuple(item.transition_id for item in outbound),
        continuity_valid=False,
        reverse_distance=parking.reverse_distance,
        path_length=path_length,
        parked_pose=parking.parked_state.pose if parking.parked_state else None,
        collision_object=recheck.get("collision_object"),
        details=recheck,
    )


def _shortest_path(
    graph: _Graph,
    starts: list[str] | set[str],
    goals: str | set[str],
    deadline: float | None,
) -> list[RoadTransition] | None:
    goal_ids = {goals} if isinstance(goals, str) else set(goals)
    start_ids = [item for item in starts if item in graph.states]
    if not start_ids or not goal_ids.intersection(graph.states):
        return None
    heap: list[tuple[float, int, str]] = []
    counter = 0
    best: dict[str, tuple[float, str | None, RoadTransition | None]] = {}
    for start in sorted(start_ids):
        heapq.heappush(heap, (0.0, counter, start))
        best[start] = (0.0, None, None)
        counter += 1
    expansions = 0
    while heap:
        if _budget_hit(deadline) or expansions >= _MAX_EXPANSIONS:
            return None
        cost, _, current = heapq.heappop(heap)
        if best[current][0] < cost:
            continue
        if current in goal_ids:
            return _reconstruct(best, current)
        expansions += 1
        edges = sorted(graph.outgoing.get(current, []), key=lambda item: (item[2], item[0], item[1].transition_id))
        for to_id, transition, edge_cost in edges:
            if transition.segments and any(segment.distance < -1e-9 for segment in transition.segments):
                if transition.from_state.occupancy_region != "parking":
                    continue
            new_cost = cost + edge_cost
            previous = best.get(to_id)
            if previous is None or new_cost + 1e-12 < previous[0]:
                best[to_id] = (new_cost, current, transition)
                counter += 1
                heapq.heappush(heap, (new_cost, counter, to_id))
    return None


def _reconstruct(best: dict[str, tuple[float, str | None, RoadTransition | None]], goal: str) -> list[RoadTransition]:
    path: list[RoadTransition] = []
    current = goal
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        _cost, previous, transition = best[current]
        if transition is None or previous is None:
            break
        path.append(transition)
        current = previous
    path.reverse()
    return path


def _recheck_journey(
    vehicle,
    occupancy: OccupancySet,
    policy: TraversalPolicy,
    stall: ParkingStall,
    inbound: list[RoadTransition],
    parking: ParkingMotion,
    outbound: list[RoadTransition],
) -> dict[str, Any]:
    pieces: list[tuple[VehiclePose, tuple[MotionSegment, ...], str]] = []
    for item in inbound:
        pieces.append((item.from_state.pose, item.segments, "road"))
    assert parking.start_state is not None and parking.parked_state is not None and parking.exit_state is not None
    pieces.append((parking.start_state.pose, parking.inbound_segments, "parking"))
    pieces.append((parking.parked_state.pose, parking.outbound_segments, "parking"))
    for item in outbound:
        pieces.append((item.from_state.pose, item.segments, "road"))

    cursor: VehiclePose | None = None
    total_reverse = 0.0
    trajectory: list[dict[str, float]] = []
    for start, segments, region in pieces:
        if cursor is not None and not poses_joinable_relaxed(cursor, start, policy):
            return {"valid": False, "reason": "journey_pose_discontinuity", "break_pose": {"x": start.x, "y": start.y}}
        allowed = (
            occupancy.parking_allowed(stall.id, stall.served_by_aisle_id, margin=max(vehicle.swept_path_margin, 0.3))
            if region == "parking"
            else occupancy.road_allowed()
        )
        kinematics = simulate_bicycle_path(
            vehicle,
            start,
            list(segments),
            sample_step=policy.sample_step_m,
            max_heading_step_degrees=policy.max_heading_step_degrees,
        )
        evidence = evaluate_motion(
            vehicle,
            start,
            segments,
            allowed=allowed,
            obstacles=occupancy.obstacles_excluding_stall(stall.id if region == "parking" else None),
            policy=policy,
            family=f"recheck_{region}",
        )
        if not evidence.valid:
            return {
                "valid": False,
                "reason": evidence.reason or "journey_recheck_failed",
                "collision_object": evidence.collision_object,
                "region": region,
            }
        for pose in kinematics.poses:
            trajectory.append({"x": pose.x, "y": pose.y, "heading_degrees": pose.heading_degrees, "region": region})
        total_reverse += sum(abs(segment.distance) for segment in segments if segment.distance < 0.0)
        cursor = evidence.to_pose
    parked = simulate_bicycle_path(
        vehicle,
        parking.start_state.pose,
        list(parking.inbound_segments),
        sample_step=policy.sample_step_m,
        max_heading_step_degrees=policy.max_heading_step_degrees,
    )
    if not parked.valid or parked.final_pose is None:
        return {"valid": False, "reason": parked.reason or "parked_pose_recheck_failed"}
    if parking.parked_state and not poses_joinable_relaxed(parked.final_pose, parking.parked_state.pose, policy):
        return {"valid": False, "reason": "parked_pose_mismatch"}
    return {
        "valid": True,
        "reason": None,
        "rechecked": True,
        "reverse_distance": total_reverse,
        "trajectory": trajectory,
        "aisle_ids": sorted({item.from_state.aisle_id for item in inbound + outbound if item.from_state.aisle_id}),
    }


def poses_joinable_relaxed(left: VehiclePose, right: VehiclePose, policy: TraversalPolicy) -> bool:
    return poses_joinable(
        left,
        right,
        position_tolerance_m=policy.position_tolerance_m,
        heading_tolerance_degrees=policy.heading_tolerance_degrees,
    )


def _graph_prefilter_allows(layout: LayoutResult, stall: ParkingStall) -> bool:
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    unreachable = set(validation.get("unreachable_stalls") or [])
    no_exit = set(validation.get("stalls_without_exit_path") or [])
    return stall.id not in unreachable and stall.id not in no_exit


def _parking_transition(motion: ParkingMotion, *, inbound: bool) -> RoadTransition:
    assert motion.start_state is not None and motion.parked_state is not None and motion.exit_state is not None
    if inbound:
        return RoadTransition(
            transition_id=f"parking-in:{motion.stall_id}",
            family=f"parking_{motion.family}",
            from_state=motion.start_state,
            to_state=motion.parked_state,
            segments=motion.inbound_segments,
            road_relation="parking_inbound",
        )
    return RoadTransition(
        transition_id=f"parking-out:{motion.stall_id}",
        family=f"parking_exit_{motion.family}",
        from_state=motion.parked_state,
        to_state=motion.exit_state,
        segments=motion.outbound_segments,
        road_relation="parking_outbound",
    )


def _junction_family(from_role: str, to_role: str, start: VehiclePose, end: VehiclePose) -> str:
    delta = abs(wrap_heading_delta(end.heading_degrees - start.heading_degrees))
    if {from_role, to_role} & {"turnaround"} and delta > 120:
        return "turnaround_u_turn"
    if {from_role, to_role} & {"connector"}:
        return "u_connector"
    if {from_role, to_role} & {"jog"}:
        return "dogleg_single_jog"
    if {from_role, to_role} & {"exit"}:
        return "exit_turn"
    if {from_role, to_role} & {"branch"}:
        cross = wrap_heading_delta(end.heading_degrees - start.heading_degrees)
        return "main_branch_left_turn" if cross > 0 else "main_branch_right_turn"
    if abs(delta - 90.0) <= 20.0:
        return "main_branch_left_turn" if wrap_heading_delta(end.heading_degrees - start.heading_degrees) > 0 else "main_branch_right_turn"
    return "dogleg_single_jog"


def _path_cost(segments) -> float:
    return float(sum(abs(segment.distance) for segment in segments))


def _resolve_deadline(
    policy: TraversalPolicy,
    deadline: float | None,
    outer_deadline: float | None,
    started: float,
) -> tuple[float | None, str, float]:
    candidates: list[tuple[float, str]] = []
    if policy.requested:
        candidates.append((started + policy.time_budget_seconds, "constraints.road_traversal.time_budget_seconds"))
    if deadline is not None:
        candidates.append((deadline, "caller_deadline"))
    if outer_deadline is not None:
        candidates.append((outer_deadline, "outer_search_budget"))
    if not candidates:
        return None, policy.budget_source, policy.time_budget_seconds
    chosen_deadline, source = min(candidates, key=lambda item: item[0])
    return chosen_deadline, source, max(chosen_deadline - started, 0.0)


def _budget_hit(deadline: float | None) -> bool:
    return deadline is not None and time.perf_counter() >= deadline


def _layout_result_id(layout: LayoutResult) -> str:
    aisle_ids = ",".join(aisle.id for aisle in layout.aisles)
    stall_ids = ",".join(stall.id for stall in layout.stalls)
    return f"{layout.generation_mode}:{aisle_ids}:{stall_ids}"


def _finished(
    policy: TraversalPolicy,
    identity: str,
    started: float,
    budget: float,
    budget_source: str,
    *,
    status: str,
    valid: bool | None,
    reason: str | None,
    stall_count: int,
    journeys: tuple[StallJourneyEvidence, ...],
    failures: tuple[dict[str, Any], ...],
    source_layout_id: str | None,
    result_layout_id: str | None,
) -> dict[str, Any]:
    return RoadTraversalResult(
        algorithm_version=ALGORITHM_VERSION,
        layout_identity=identity,
        policy=policy,
        requested=True,
        executed=True,
        status=status,
        valid=valid,
        reason=reason,
        stall_coverage=sum(1 for item in journeys if item.valid is True),
        stall_count=stall_count,
        time_budget_seconds=budget,
        elapsed_seconds=time.perf_counter() - started,
        budget_source=budget_source,
        journeys=journeys,
        failures=failures,
        source_layout_id=source_layout_id,
        result_layout_id=result_layout_id,
    ).to_record()


def _incomplete(
    policy: TraversalPolicy,
    identity: str,
    started: float,
    budget: float,
    budget_source: str,
    stall_count: int,
    reason: str,
    source_layout_id: str | None,
    result_layout_id: str | None,
) -> dict[str, Any]:
    return _finished(
        policy,
        identity,
        started,
        budget,
        budget_source,
        status="incomplete",
        valid=None,
        reason=reason,
        stall_count=stall_count,
        journeys=(),
        failures=({"reason": reason, "classification": "incomplete"},),
        source_layout_id=source_layout_id,
        result_layout_id=result_layout_id,
    )
