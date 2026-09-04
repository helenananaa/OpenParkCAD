"""Through-corridor generation family: a straight entry-to-exit spine."""

from __future__ import annotations

import math
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
        polygon=aisle_poly,
        angle_degrees=heading,
        role="exit",
        connected_to_entrance_id=exit_entrance.id,
        parent_aisle_id=main.id,
        directionality="two_way",
    )
    usable = available_area(site)
    start_u = site.aisle_width * 0.25
    end_u = length - site.aisle_width * 0.25
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
    stalls = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=main.id,
            stall_type_id=site.stall.id,
        )
        for index, stall in enumerate(stalls, start=1)
        if ShapelyPolygon(stall.polygon).within(usable.buffer(1e-6)) or usable.contains(ShapelyPolygon(stall.polygon).centroid)
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
    if not valid(current) and finalized.stall_count > current.stall_count:
        return finalized
    return current
