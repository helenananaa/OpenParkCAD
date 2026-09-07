"""Per-skeleton parking modules for parallel_ladder (v0.5 N6). 90-degree family only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from shapely.geometry import Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.models import EntranceSpec, ParkingStall, SiteSpec
from openparkcad.phase1_candidates import place_main_family_stalls
from openparkcad.road_skeleton import RoadSkeleton
from openparkcad.road_skeleton_geometry import derive_segment_polygon
from openparkcad.site_constraints import site_usable_area

SUPPORTED_STALL_FAMILY = "perpendicular"
MODULE_VERSION = "ladder-modules-1"


@dataclass(frozen=True)
class LadderModule:
    module_id: str
    skeleton_id: str
    segment_id: str
    side: str
    stalls: tuple[ParkingStall, ...]
    depends_on_segment_id: str


@dataclass
class LadderModuleCatalog:
    modules: list[LadderModule]
    conflicts: list[tuple[str, str, str]]
    requested_backend: str
    actual_backend: str
    selected_ids: list[str]
    selected_stalls: list[ParkingStall]
    provenance: dict[str, Any]


def build_and_select_ladder_modules(
    site: SiteSpec,
    skeleton: RoadSkeleton,
    *,
    backend: str = "greedy",
) -> LadderModuleCatalog:
    if (site.stall.family or "perpendicular") != SUPPORTED_STALL_FAMILY and 90.0 not in (site.stall.allowed_angles or (90.0,)):
        return LadderModuleCatalog(
            modules=[],
            conflicts=[],
            requested_backend=backend,
            actual_backend="unsupported",
            selected_ids=[],
            selected_stalls=[],
            provenance={"family": site.stall.family, "status": "unsupported"},
        )
    usable = site_usable_area(site, "stall")
    modules: list[LadderModule] = []
    for segment in skeleton.segments:
        if segment.role != "parking_aisle":
            continue
        modules.extend(_modules_for_segment(site, skeleton, segment, usable))
    conflicts = _conflicts(site, skeleton, modules)
    requested = backend
    actual = backend
    selected = _greedy_select(modules, conflicts)
    selector_meta: dict[str, Any] = {"requested": requested, "actual": actual, "status": "optimal_greedy", "gap": None, "objective": None, "objective_bound": None}
    if backend == "cpsat":
        try:
            cpsat_ids, cpsat_meta = _cpsat_select(modules, conflicts)
            if cpsat_ids is not None:
                selected = cpsat_ids
                actual = "cpsat"
                selector_meta = {"requested": requested, "actual": actual, **cpsat_meta}
            else:
                actual = "greedy"
                selector_meta["actual"] = "greedy"
                selector_meta["backend_fallback_reason"] = "cpsat_infeasible_or_unavailable"
        except Exception as exc:
            actual = "greedy"
            selector_meta["actual"] = "greedy"
            selector_meta["backend_fallback_reason"] = str(exc)
    by_id = {module.module_id: module for module in modules}
    stalls = [stall for module_id in selected for stall in by_id[module_id].stalls]
    return LadderModuleCatalog(
        modules=modules,
        conflicts=conflicts,
        requested_backend=requested,
        actual_backend=actual,
        selected_ids=selected,
        selected_stalls=stalls,
        provenance={
            "version": MODULE_VERSION,
            "skeleton_id": skeleton.skeleton_id,
            "module_count": len(modules),
            "conflict_count": len(conflicts),
            "selected_count": len(selected),
            "official_stall_count": len(stalls),
            "scope": "skeleton_catalog",
            "selector": selector_meta,
        },
    )


def _modules_for_segment(site: SiteSpec, skeleton: RoadSkeleton, segment, usable) -> list[LadderModule]:
    start, end = segment.centerline[0], segment.centerline[-1]
    heading = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    virtual = EntranceSpec(
        id=f"virt-{segment.id}",
        mode="shared",
        center=start,
        width=segment.width,
        heading_degrees=heading,
    )
    stalls = place_main_family_stalls(
        site,
        site.stall,
        usable,
        virtual,
        heading,
        0.0,
        length,
        served_by_aisle_id=segment.id,
        v_center=0.0,
    )
    junction_points = [node.point for node in skeleton.nodes if node.kind in {"junction", "entrance_port"}]
    kept = []
    for stall in stalls:
        poly = ShapelyPolygon(stall.polygon)
        if any(poly.distance(ShapelyPoint(point)) < segment.width / 2.0 for point in junction_points):
            continue
        kept.append(
            ParkingStall(
                id=f"{segment.id}-{stall.id}",
                polygon=stall.polygon,
                angle_degrees=stall.angle_degrees,
                served_by_aisle_id=segment.id,
                aisle_side=stall.aisle_side,
                stall_type_id=stall.stall_type_id,
            )
        )
    chunk = 4
    modules = []
    for index in range(0, len(kept), chunk):
        group = kept[index : index + chunk]
        if not group:
            continue
        side = group[0].aisle_side or "left"
        module_id = f"mod-{skeleton.skeleton_id[:8]}-{segment.id}-{side}-{index}"
        modules.append(
            LadderModule(
                module_id=module_id,
                skeleton_id=skeleton.skeleton_id,
                segment_id=segment.id,
                side=side,
                stalls=tuple(group),
                depends_on_segment_id=segment.id,
            )
        )
    return modules


def _conflicts(site: SiteSpec, skeleton: RoadSkeleton, modules: list[LadderModule]) -> list[tuple[str, str, str]]:
    from shapely.ops import unary_union

    pairs: list[tuple[str, str, str]] = []
    roads = {segment.id: derive_segment_polygon(segment) for segment in skeleton.segments}

    def unary_module(module: LadderModule):
        return unary_union([ShapelyPolygon(stall.polygon) for stall in module.stalls])

    polys = {module.module_id: unary_module(module) for module in modules}
    ids = [module.module_id for module in modules]
    by_id = {module.module_id: module for module in modules}
    for i, left_id in enumerate(ids):
        for right_id in ids[i + 1 :]:
            overlap = polys[left_id].intersection(polys[right_id]).area
            if overlap > 1e-3:
                kind = "back_to_back" if by_id[left_id].segment_id != by_id[right_id].segment_id else "geometry_overlap"
                pairs.append((left_id, right_id, kind))
        for segment_id, road in roads.items():
            if segment_id == by_id[left_id].depends_on_segment_id:
                continue
            if polys[left_id].intersection(road).area > 1e-3:
                pairs.append((left_id, f"road:{segment_id}", "junction_or_road"))
    return pairs


def _greedy_select(modules: list[LadderModule], conflicts: list[tuple[str, str, str]]) -> list[str]:
    blocked = {frozenset((a, b)) for a, b, _kind in conflicts if not str(b).startswith("road:")}
    road_block = {a for a, b, _kind in conflicts if str(b).startswith("road:")}
    ranked = sorted(modules, key=lambda module: (-len(module.stalls), module.module_id))
    chosen: list[str] = []
    for module in ranked:
        if module.module_id in road_block:
            continue
        if any(frozenset((module.module_id, other)) in blocked for other in chosen):
            continue
        chosen.append(module.module_id)
    return chosen


def _cpsat_select(modules: list[LadderModule], conflicts: list[tuple[str, str, str]]) -> tuple[list[str] | None, dict[str, Any]]:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None, {"status": "unavailable", "backend_fallback_reason": "ortools_missing"}

    model = cp_model.CpModel()
    variables = {module.module_id: model.NewBoolVar(module.module_id) for module in modules}
    for left, right, _kind in conflicts:
        if str(right).startswith("road:"):
            model.Add(variables[left] == 0)
            continue
        if left in variables and right in variables:
            model.Add(variables[left] + variables[right] <= 1)
    model.Maximize(sum(len(module.stalls) * variables[module.module_id] for module in modules))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    status = solver.Solve(model)
    status_name = {cp_model.OPTIMAL: "optimal", cp_model.FEASIBLE: "feasible"}.get(status, "not_solved")
    meta = {
        "status": status_name,
        "objective": float(solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "objective_bound": float(solver.BestObjectiveBound()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "gap": float(solver.BestObjectiveBound() - solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "scope": "skeleton_catalog_not_site_global",
    }
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, meta
    selected = [module.module_id for module in modules if solver.Value(variables[module.module_id]) == 1]
    return selected, meta
