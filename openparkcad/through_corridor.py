"""Through-corridor generation family: a straight entry-to-exit spine."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.layout_geometry import available_area, main_aisle_polygon, polygon_points
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.phase1_candidates import place_main_family_stalls
from openparkcad.phase1_support import entry_capable_entrances, exit_capable_entrances

FAMILY_ID = "through_corridor"


def through_corridor_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get("enable_through_corridor", False)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no", "off"}
    return bool(raw)


def build_through_corridor_layout(site: SiteSpec) -> LayoutResult | None:
    entries = entry_capable_entrances(site)
    if not entries:
        return None
    entry = entries[0]
    exit_entrance = next((item for item in exit_capable_entrances(site) if item.id != entry.id), None)
    if exit_entrance is None:
        return None
    dx = exit_entrance.center[0] - entry.center[0]
    dy = exit_entrance.center[1] - entry.center[1]
    length = math.hypot(dx, dy)
    if length < site.aisle_width + site.stall.length:
        return None
    heading = math.degrees(math.atan2(dy, dx))
    aisle_poly = polygon_points(main_aisle_polygon(site, entry, heading, 0.0, length))
    exit_start = max(site.aisle_width, length - site.aisle_width)
    exit_poly = polygon_points(main_aisle_polygon(site, entry, heading, exit_start, length))
    main = ParkingAisle(
        id="A-THROUGH",
        polygon=aisle_poly,
        angle_degrees=heading,
        role="main",
        connected_to_entrance_id=entry.id,
        connected_aisle_ids=("A-THROUGH-EXIT",),
        directionality="two_way",
    )
    exit_aisle = ParkingAisle(
        id="A-THROUGH-EXIT",
        polygon=exit_poly,
        angle_degrees=heading,
        role="exit",
        connected_to_entrance_id=exit_entrance.id,
        parent_aisle_id=main.id,
        directionality="two_way",
    )
    usable = available_area(site)
    start_u = site.aisle_width + site.stall.length * 0.5
    end_u = length - site.aisle_width - site.stall.length * 0.5
    placed = place_main_family_stalls(
        site,
        site.main_stall or site.stall,
        usable,
        entry,
        heading,
        start_u,
        end_u,
        served_by_aisle_id=main.id,
    )
    candidates = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=main.id,
            stall_type_id=site.stall.id,
            aisle_side=stall.aisle_side,
        )
        for index, stall in enumerate(placed, start=1)
    ]
    kept: list[ParkingStall] = []
    dropped: list[dict[str, Any]] = []
    for stall in candidates:
        poly = ShapelyPolygon(stall.polygon)
        if not (poly.within(usable.buffer(1e-6)) or usable.contains(poly.centroid)):
            dropped.append(_drop_record(stall, "outside_usable_area"))
            continue
        kept.append(stall)
    if not kept:
        return None
    return LayoutResult(
        site=site,
        stalls=kept,
        aisles=[main, exit_aisle],
        generation_mode="phase1_through_corridor",
        main_entrance_id=entry.id,
        selected_heading_degrees=heading,
        selected_stall_type_id=site.stall.id,
        through_corridor_report=_selection_report(candidates, kept, dropped),
    )


def maybe_through_corridor(site: SiteSpec, current: LayoutResult, finalize, valid) -> LayoutResult:
    if not through_corridor_enabled(site):
        return current
    built = build_through_corridor_layout(site)
    if built is None:
        return current
    selected = _with_compatible_stalls(site, built)
    if not selected.stalls:
        return current
    finalized = _carry_report(selected, finalize(selected), "constraint_or_maneuver_filter")
    if valid(finalized) and (not valid(current) or finalized.stall_count > current.stall_count):
        return finalized
    road = finalized.road_traversal_validation or {}
    passed_ids = {
        item.get("stall_id")
        for item in road.get("journeys") or []
        if isinstance(item, dict) and item.get("status") == "passed" and item.get("valid") is True
    }
    reduced_stalls = [stall for stall in finalized.stalls if stall.id in passed_ids]
    rejected = [stall for stall in finalized.stalls if stall.id not in passed_ids]
    if not reduced_stalls or len(reduced_stalls) == len(finalized.stalls):
        return current
    reduced = LayoutResult(
        site=finalized.site,
        stalls=reduced_stalls,
        aisles=list(finalized.aisles),
        generation_mode=finalized.generation_mode,
        main_entrance_id=finalized.main_entrance_id,
        selected_heading_degrees=finalized.selected_heading_degrees,
        selected_stall_type_id=finalized.selected_stall_type_id,
        through_corridor_report=_append_drops(finalized.through_corridor_report, rejected, "road_traversal_not_passed"),
    )
    reduced = _carry_report(reduced, finalize(reduced), "constraint_or_maneuver_filter")
    if valid(reduced) and (not valid(current) or reduced.stall_count > current.stall_count):
        return reduced
    return current


def _drop_record(stall: ParkingStall, reason: str) -> dict[str, Any]:
    return {
        "stall_id": stall.id,
        "reason": reason,
        "polygon": [list(point) for point in stall.polygon],
    }


def _selection_report(
    candidates: list[ParkingStall],
    kept: list[ParkingStall],
    dropped: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "family": FAMILY_ID,
        "candidate_ids": [stall.id for stall in candidates],
        "candidate_polygons": [[list(point) for point in stall.polygon] for stall in candidates],
        "kept_ids": [stall.id for stall in kept],
        "dropped": list(dropped),
    }


def _append_drops(report: dict[str, Any] | None, stalls: list[ParkingStall], reason: str) -> dict[str, Any]:
    payload = dict(report or {})
    dropped_ids = {stall.id for stall in stalls}
    dropped = list(payload.get("dropped") or [])
    dropped.extend(_drop_record(stall, reason) for stall in stalls)
    payload["dropped"] = dropped
    payload["kept_ids"] = [item for item in (payload.get("kept_ids") or []) if item not in dropped_ids]
    return payload


def _compatible_official_stalls(site: SiteSpec, stalls: list[ParkingStall]) -> tuple[list[ParkingStall], list[dict[str, Any]]]:
    min_gap = float(site.stall.width)
    kept: list[ParkingStall] = []
    dropped: list[dict[str, Any]] = []
    kept_side: str | None = None
    for stall in stalls:
        if kept_side is None and stall.aisle_side:
            kept_side = stall.aisle_side
        if kept_side is not None and stall.aisle_side and stall.aisle_side != kept_side:
            dropped.append(_drop_record(stall, "opposite_side_spine_conflict"))
            continue
        poly = ShapelyPolygon(stall.polygon)
        if any(poly.distance(ShapelyPolygon(other.polygon)) < min_gap - 1e-9 for other in kept):
            dropped.append(_drop_record(stall, "adjacent_stall_clearance"))
            continue
        kept.append(stall)
    return kept, dropped


def _with_compatible_stalls(site: SiteSpec, layout: LayoutResult) -> LayoutResult:
    kept, dropped = _compatible_official_stalls(site, layout.stalls)
    if not dropped:
        return layout
    report = dict(layout.through_corridor_report or {})
    report["dropped"] = list(report.get("dropped") or []) + dropped
    report["kept_ids"] = [stall.id for stall in kept]
    return replace(layout, stalls=kept, through_corridor_report=report)


def _polygon_key(polygon) -> tuple[tuple[float, float], ...]:
    return tuple((round(float(x), 6), round(float(y), 6)) for x, y in polygon)


def _carry_report(before: LayoutResult, after: LayoutResult, reason: str) -> LayoutResult:
    after_keys = {_polygon_key(stall.polygon) for stall in after.stalls}
    missing = [stall for stall in before.stalls if _polygon_key(stall.polygon) not in after_keys]
    report = dict(after.through_corridor_report or before.through_corridor_report or {})
    if missing:
        report = _append_drops(report, missing, reason)
    if report == after.through_corridor_report:
        return after
    return replace(after, through_corridor_report=report)
