"""Through-corridor generation family: a straight entry-to-exit spine."""

from __future__ import annotations

import math
from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.layout_geometry import available_area, main_aisle_polygon, polygon_points
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.phase1_candidates import place_main_family_stalls
from openparkcad.phase1_support import entry_capable_entrances, exit_capable_entrances

FAMILY_ID = "through_corridor"
MAX_OFFICIAL_STALLS = 1


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
    stalls = place_main_family_stalls(
        site,
        site.main_stall or site.stall,
        usable,
        entry,
        heading,
        start_u,
        end_u,
        served_by_aisle_id=main.id,
    )
    kept: list[ParkingStall] = []
    for stall in stalls:
        poly = ShapelyPolygon(stall.polygon)
        if not (poly.within(usable.buffer(1e-6)) or usable.contains(poly.centroid)):
            continue
        kept.append(stall)
    if len(kept) > MAX_OFFICIAL_STALLS:
        aisle_center = ShapelyPolygon(aisle_poly).centroid
        kept = sorted(kept, key=lambda stall: ShapelyPolygon(stall.polygon).centroid.distance(aisle_center))[:MAX_OFFICIAL_STALLS]
    stalls = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=main.id,
            stall_type_id=site.stall.id,
            aisle_side=stall.aisle_side,
        )
        for index, stall in enumerate(kept, start=1)
    ]
    if not stalls:
        return None
    return LayoutResult(
        site=site,
        stalls=stalls,
        aisles=[main, exit_aisle],
        generation_mode="phase1_through_corridor",
        main_entrance_id=entry.id,
        selected_heading_degrees=heading,
        selected_stall_type_id=site.stall.id,
    )


def maybe_through_corridor(site: SiteSpec, current: LayoutResult, finalize, valid) -> LayoutResult:
    if not through_corridor_enabled(site):
        return current
    built = build_through_corridor_layout(site)
    if built is None:
        return current
    finalized = finalize(built)
    if valid(finalized) and (not valid(current) or finalized.stall_count > current.stall_count):
        return finalized
    road = finalized.road_traversal_validation or {}
    passed_ids = {
        item.get("stall_id")
        for item in road.get("journeys") or []
        if isinstance(item, dict) and item.get("status") == "passed" and item.get("valid") is True
    }
    reduced_stalls = [stall for stall in finalized.stalls if stall.id in passed_ids]
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
    )
    reduced = finalize(reduced)
    if valid(reduced) and (not valid(current) or reduced.stall_count > current.stall_count):
        return reduced
    return current
