"""Bounded conflict-driven repair of a fixed ladder's occupied parking stalls.

Learn actual swept-motion conflicts, select a compatible subset, then recheck
every retained stall's complete journey. No vehicle, pavement, stall geometry,
hard constraint or requested check is relaxed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import time
from typing import Any

from openparkcad.layout_locks import generation_locks_from_site, locks_satisfied, lock_site_conflicts, _polygon_mismatch
from openparkcad.models import LayoutResult
from openparkcad.parking_motion_adapter import parking_motion_for_stall
from openparkcad.road_network_config import RepairConfig
from openparkcad.road_transitions import build_occupancy
from openparkcad.road_traversal_models import layout_traversal_identity, parse_traversal_policy, vehicle_support_reason

VERSION = "ladder-conflict-repair-1"


def repair_ladder_layout(
    layout: LayoutResult, config: RepairConfig, *, backend: str = "greedy", deadline: float | None = None,
) -> tuple[LayoutResult, dict[str, Any]]:
    from openparkcad.generator import _finalize_candidate, _layout_valid

    started = time.perf_counter()
    stop_at = min(deadline if deadline is not None else float("inf"), started + config.time_budget_seconds)
    policy = parse_traversal_policy(layout.site)
    report: dict[str, Any] = {
        "version": VERSION, "requested": config.enabled, "executed": False,
        "status": "not_requested", "accepted": False, "scope": "fixed_skeleton_stall_subset",
        "input_layout_identity": layout_traversal_identity(layout, policy),
        "input_stall_count": layout.stall_count, "min_retained_stalls": config.min_retained_stalls,
        "rounds": [], "learned_conflicts": [], "removed_stalls": [],
        "solver_scope": "learned_pair_conflicts_only_not_site_global_optimum",
    }
    current = layout
    edges: set[tuple[str, str]] = set()
    original = {stall.id: stall for stall in layout.stalls}

    def finish(status: str, reason: str | None = None):
        report.update(status=status, reason=reason, accepted=status == "passed",
                      elapsed_seconds=time.perf_counter() - started,
                      output_stall_count=current.stall_count,
                      output_layout_identity=layout_traversal_identity(current, policy),
                      learned_conflicts=[list(pair) for pair in sorted(edges)])
        kept = {stall.id for stall in current.stalls}
        report["removed_stalls"] = [
            {"stall_id": stall_id, "reason": "occupied_stall_motion_conflict",
             "conflicts_with_retained": sorted(b if a == stall_id else a for a, b in edges
                                               if (a == stall_id and b in kept) or (b == stall_id and a in kept))}
            for stall_id in sorted(original.keys() - kept)
        ]
        return current, report

    if not config.enabled:
        return finish("not_requested")
    report["executed"] = True
    if layout.generation_mode != "parallel_ladder" or not policy.requested:
        return finish("unsupported", "requires_parallel_ladder_and_requested_road_traversal")
    support = vehicle_support_reason(layout.site.vehicle)
    if support:
        return finish("unsupported", support)
    if len(original) != layout.stall_count or layout.stall_count < config.min_retained_stalls:
        return finish("failed", "invalid_or_insufficient_stall_catalog")
    locks = generation_locks_from_site(layout.site)
    pinned = {stall.id for stall in layout.stalls for lock in locks
              if lock.kind == "stall_group" and lock.geometry and not _polygon_mismatch(lock.geometry, stall.polygon)}
    report["pinned_stall_ids"] = sorted(pinned)
    if not locks_satisfied(layout, locks)[0] or lock_site_conflicts(layout, locks):
        return finish("failed", "initial_lock_conflict")

    for round_number in range(config.max_rounds):
        if time.perf_counter() >= stop_at:
            return finish("incomplete", "repair_time_budget_exhausted")
        occupancy = build_occupancy(current)
        failures = []
        for stall in current.stalls:
            if time.perf_counter() >= stop_at:
                return finish("incomplete", "repair_time_budget_exhausted")
            motion = parking_motion_for_stall(current, stall, occupancy, policy, current.site.vehicle)
            if not motion.valid:
                blockers = motion.details.get("blocking_stall_ids") or []
                failures.append({"stall_id": stall.id, "reason": motion.reason,
                                 "blocking_stall_ids": list(blockers),
                                 "hard_obstacle_collision": motion.details.get("hard_obstacle_collision", False)})
        step: dict[str, Any] = {"round": round_number, "stall_count": current.stall_count,
                                "parking_passed": current.stall_count - len(failures), "failures": failures}
        report["rounds"].append(step)
        if not failures:
            current = _finalize_candidate(deepcopy(current), deadline=stop_at)
            road = current.road_traversal_validation or {}
            exact_geometry = (current.aisles == layout.aisles and all(s == original.get(s.id) for s in current.stalls)
                              and len(current.stalls) == step["stall_count"])
            report["final_validation"] = {
                "geometry_preserved": exact_geometry, "road_status": road.get("status"),
                "stall_coverage": road.get("stall_coverage"), "stall_count": current.stall_count,
                "engineering_valid": (current.engineering_validation or {}).get("valid"),
            }
            if time.perf_counter() >= stop_at or road.get("status") == "incomplete":
                return finish("incomplete", "repair_time_budget_exhausted")
            if (not exact_geometry or current.stall_count < config.min_retained_stalls or not _layout_valid(current)
                    or not locks_satisfied(current, locks)[0] or lock_site_conflicts(current, locks)):
                return finish("failed", "full_layout_validation_failed")
            return finish("passed")

        for failure in failures:
            if (failure["reason"] != "swept_path_intersects_obstacle" or not failure["blocking_stall_ids"]
                    or failure["hard_obstacle_collision"]):
                return finish("failed", "conflict_not_repairable_by_stall_selection")
            for blocker in failure["blocking_stall_ids"]:
                if blocker not in original or blocker == failure["stall_id"]:
                    return finish("failed", "unresolved_collision_identity")
                edges.add(tuple(sorted((failure["stall_id"], blocker))))
        selected, selector = _select_stalls(sorted(original), edges, pinned, backend, stop_at)
        step["selector"] = selector
        step["proposed_stall_count"] = len(selected)
        if time.perf_counter() >= stop_at or selector["status"] == "incomplete":
            return finish("incomplete", "repair_time_budget_exhausted")
        if not selected or len(selected) < config.min_retained_stalls:
            return finish("failed", "retention_floor_or_lock_conflict")
        if selected == {s.id for s in current.stalls}:
            return finish("failed", "no_repair_progress")
        # Always use the original catalog; learned constraints accumulate, but
        # a prior greedy deletion does not permanently exclude a better subset.
        current = replace(layout, stalls=[s for s in layout.stalls if s.id in selected])
    return finish("incomplete", "repair_round_budget_exhausted")


def _select_stalls(ids: list[str], edges: set[tuple[str, str]], pinned: set[str], backend: str, deadline: float):
    meta: dict[str, Any] = {"requested": backend, "actual": "greedy", "status": "heuristic",
                            "objective": None, "objective_bound": None}
    if backend == "cpsat":
        try:
            from ortools.sat.python import cp_model
        except ImportError:
            meta["fallback_reason"] = "ortools_missing"
        else:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return set(), {**meta, "actual": "cpsat", "status": "incomplete"}
            model = cp_model.CpModel()
            variables = {key: model.NewBoolVar(key) for key in ids}
            for a, b in sorted(edges):
                model.Add(variables[a] + variables[b] <= 1)
            for key in sorted(pinned):
                model.Add(variables[key] == 1)
            model.Maximize(sum(variables.values()))
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 17
            solver.parameters.max_time_in_seconds = remaining
            status = solver.Solve(model)
            meta.update(actual="cpsat", status={cp_model.OPTIMAL: "optimal", cp_model.FEASIBLE: "feasible",
                                               cp_model.INFEASIBLE: "infeasible", cp_model.UNKNOWN: "incomplete"}.get(status, "model_invalid"))
            if status == cp_model.MODEL_INVALID:
                raise RuntimeError("repair CP-SAT model is invalid")
            if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                return set(), meta
            chosen = {key for key in ids if solver.Value(variables[key])}
            meta.update(objective=solver.ObjectiveValue(), objective_bound=solver.BestObjectiveBound())
            return chosen, meta
    elif backend != "greedy":
        raise ValueError(f"unsupported repair selector backend {backend!r}")
    adjacency = {key: set() for key in ids}
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    if any(adjacency[key] & pinned for key in pinned):
        return set(), {**meta, "status": "infeasible_locked_conflicts"}
    chosen = set(pinned)
    available = set(ids) - pinned
    for key in pinned:
        available -= adjacency[key]
    while available:
        if time.perf_counter() >= deadline:
            return set(), {**meta, "status": "incomplete"}
        key = min(available, key=lambda item: (len(adjacency[item] & available), item))
        chosen.add(key)
        available -= adjacency[key] | {key}
    meta["objective"] = len(chosen)
    return chosen, meta
