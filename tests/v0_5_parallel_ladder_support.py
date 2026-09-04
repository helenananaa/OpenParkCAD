"""Frozen v0.5 parallel-ladder case identities and testable predicates.

These helpers are the N1 contract later N4–N7 tests drive. They inspect shipped
layout objects; they do not reimplement generation or invent official scores.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, SiteSpec, site_from_dict

REPO_ROOT = Path(__file__).resolve().parents[1]
HEADING_TOLERANCE_DEGREES = 8.0
LATERAL_CLUSTER_GAP = 3.0
CROSS_CONTACT_TOLERANCE = 0.05

# Human-reference parking-aisle count for the wide rectangle. N-T01's generator
# floor is 2; this constant is not a site-independent stall-count KPI.
RECT_HUMAN_PARKING_AISLES = 3

CASES: dict[str, dict[str, Any]] = {
    "N-T01": {
        "path": "examples/parallel_ladder_rect_site.json",
        "kind": "positive",
        "split": "development",
        "source": "synthetic",
        "scenario": "wide_rectangle_both_ends",
        "current_failure": "single_spine_not_three_parallel_parking_aisles_with_both_end_cross",
        "solved_when": "at_least_two_parallel_parking_aisles_and_a_real_cross_aisle",
    },
    "N-T02": {
        "path": "examples/parallel_ladder_l_site.json",
        "kind": "positive",
        "split": "holdout",
        "source": "synthetic",
        "scenario": "l_shape_second_wing",
        "current_failure": "single_spine_leaves_second_wing_unused",
        "solved_when": "at_least_one_skeleton_uses_the_second_wing",
    },
    "N-T03": {
        "path": "tests/fixtures/v0_5/parallel_ladder_tight_reject.json",
        "kind": "hard_reject",
        "split": "development",
        "source": "synthetic",
        "scenario": "too_narrow_for_two_aisles",
        "current_failure": "no_geometric_corridor_for_two_hard_width_aisles",
        "solved_when": "reject_without_shrinking_hard_aisle_width",
    },
    "N-T04": {
        "path": "tests/fixtures/v0_5/parallel_ladder_turn_reject.json",
        "kind": "hard_reject",
        "split": "holdout",
        "source": "synthetic",
        "scenario": "graph_contact_t_turn_envelope_blocked",
        "current_failure": "intended_t_has_graph_contact_but_vehicle_envelope_hits_obstacle",
        "solved_when": "do_not_publish_pseudo_valid_t_roads_or_road_traversal_passed",
    },
}


def case_path(case_id: str) -> Path:
    return REPO_ROOT / str(CASES[case_id]["path"])


def load_case_payload(case_id: str) -> dict[str, Any]:
    return json.loads(case_path(case_id).read_text(encoding="utf-8"))


def load_case_site(case_id: str) -> SiteSpec:
    return site_from_dict(load_case_payload(case_id))


def case_source(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") or {}
    v05 = metadata.get("v0_5") or {}
    return str(v05.get("source") or metadata.get("fixture_provenance") or "")


def heading_delta(a: float, b: float) -> float:
    delta = abs((float(a) - float(b)) % 180.0)
    return min(delta, 180.0 - delta)


def headings_parallel(a: float, b: float, *, tolerance: float = HEADING_TOLERANCE_DEGREES) -> bool:
    return heading_delta(a, b) <= tolerance


def headings_perpendicular(a: float, b: float, *, tolerance: float = HEADING_TOLERANCE_DEGREES) -> bool:
    return abs(heading_delta(a, b) - 90.0) <= tolerance


def _polygon(aisle: ParkingAisle) -> ShapelyPolygon:
    return ShapelyPolygon(aisle.polygon)


def _centroid(aisle: ParkingAisle) -> tuple[float, float]:
    centroid = _polygon(aisle).centroid
    return (float(centroid.x), float(centroid.y))


def aisle_centerline_heading(aisle: ParkingAisle) -> float:
    """Heading of the longer axis of the aisle polygon, not stall ``angle_degrees``."""

    rectangle = _polygon(aisle).minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    edges: list[tuple[float, float]] = []
    for start, end in zip(coords, coords[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        edges.append((length, math.degrees(math.atan2(dy, dx)) % 180.0))
    if not edges:
        return float(aisle.angle_degrees)
    edges.sort(key=lambda item: item[0], reverse=True)
    return edges[0][1]


def lateral_offset(point: tuple[float, float], heading_degrees: float) -> float:
    radians = math.radians(heading_degrees)
    # Perpendicular to heading: (-sin, cos) · point
    return (-math.sin(radians) * point[0]) + (math.cos(radians) * point[1])


def longitudinal_offset(point: tuple[float, float], heading_degrees: float) -> float:
    radians = math.radians(heading_degrees)
    return (math.cos(radians) * point[0]) + (math.sin(radians) * point[1])


def parking_aisles(layout: LayoutResult) -> list[ParkingAisle]:
    return [
        aisle
        for aisle in layout.aisles
        if str(aisle.role).lower() in {"aisle", "main", "jog", "branch", "parking_aisle"}
    ]


def parallel_parking_aisles(
    layout: LayoutResult,
    heading_degrees: float,
    *,
    tolerance: float = HEADING_TOLERANCE_DEGREES,
) -> list[ParkingAisle]:
    return [
        aisle
        for aisle in parking_aisles(layout)
        if headings_parallel(aisle_centerline_heading(aisle), heading_degrees, tolerance=tolerance)
    ]


def distinct_parallel_parking_aisle_count(
    layout: LayoutResult,
    heading_degrees: float,
    *,
    gap: float = LATERAL_CLUSTER_GAP,
) -> int:
    offsets = sorted(
        lateral_offset(_centroid(aisle), heading_degrees)
        for aisle in parallel_parking_aisles(layout, heading_degrees)
    )
    if not offsets:
        return 0
    clusters = 1
    last = offsets[0]
    for value in offsets[1:]:
        if value - last > gap:
            clusters += 1
        last = value
    return clusters


def connecting_cross_aisles(layout: LayoutResult, parking_heading: float) -> list[ParkingAisle]:
    parking = parallel_parking_aisles(layout, parking_heading)
    if len(parking) < 2:
        return []
    found: list[ParkingAisle] = []
    for aisle in layout.aisles:
        if not headings_perpendicular(aisle_centerline_heading(aisle), parking_heading):
            continue
        poly = _polygon(aisle)
        contacts = [
            other
            for other in parking
            if poly.distance(_polygon(other)) <= CROSS_CONTACT_TOLERANCE
        ]
        if len(contacts) >= 2:
            found.append(aisle)
    return found


def has_real_cross_aisle(layout: LayoutResult, parking_heading: float) -> bool:
    return bool(connecting_cross_aisles(layout, parking_heading))


def both_end_cross_aisles(layout: LayoutResult, parking_heading: float) -> bool:
    parking = parallel_parking_aisles(layout, parking_heading)
    crosses = connecting_cross_aisles(layout, parking_heading)
    if len(parking) < 2 or len(crosses) < 2:
        return False
    extents = [longitudinal_offset(_centroid(aisle), parking_heading) for aisle in parking]
    low, high = min(extents), max(extents)
    span = max(high - low, 1.0)
    cross_long = [longitudinal_offset(_centroid(aisle), parking_heading) for aisle in crosses]
    near_low = any(abs(value - low) <= 0.35 * span for value in cross_long)
    near_high = any(abs(value - high) <= 0.35 * span for value in cross_long)
    return near_low and near_high


def l_wings(site: SiteSpec) -> tuple[ShapelyPolygon, ShapelyPolygon]:
    """West/north primary wing and east secondary wing for the frozen L-site."""

    west = ShapelyPolygon([(0.0, 0.0), (36.0, 0.0), (36.0, 72.0), (0.0, 72.0)])
    east = ShapelyPolygon([(36.0, 0.0), (90.0, 0.0), (90.0, 28.0), (36.0, 28.0)])
    return west, east


def aisle_covers_polygon(layout: LayoutResult, region: ShapelyPolygon, *, min_area: float = 8.0) -> bool:
    for aisle in layout.aisles:
        overlap = _polygon(aisle).intersection(region)
        if not overlap.is_empty and float(overlap.area) >= min_area:
            return True
    return False


def uses_second_l_wing(layout: LayoutResult, site: SiteSpec) -> bool:
    _west, east = l_wings(site)
    return aisle_covers_polygon(layout, east)


def site_width(site: SiteSpec) -> float:
    xs = [point[0] for point in site.boundary]
    return max(xs) - min(xs)


def hard_aisle_width(site: SiteSpec) -> float:
    return float(site.aisle_width)


def two_aisle_clear_width(site: SiteSpec) -> float:
    return 2.0 * hard_aisle_width(site)


def aisle_polygon_width(aisle: ParkingAisle) -> float:
    poly = _polygon(aisle)
    if poly.is_empty:
        return 0.0
    heading = math.radians(aisle_centerline_heading(aisle))
    perp = (-math.sin(heading), math.cos(heading))
    projections = [(point[0] * perp[0] + point[1] * perp[1]) for point in aisle.polygon]
    return max(projections) - min(projections)


def published_aisles_respect_hard_width(layout: LayoutResult, site: SiteSpec, *, slack: float = 0.2) -> bool:
    minimum = hard_aisle_width(site) - slack
    return all(aisle_polygon_width(aisle) + 1e-6 >= minimum for aisle in layout.aisles)


def rectangle_current_is_insufficient(layout: LayoutResult, site: SiteSpec) -> bool:
    heading = float(site.entrances[0].heading_degrees)
    parallel = distinct_parallel_parking_aisle_count(layout, heading)
    return parallel < 2 or not has_real_cross_aisle(layout, heading) or not both_end_cross_aisles(layout, heading)


def rectangle_is_solved(layout: LayoutResult, site: SiteSpec) -> bool:
    heading = float(site.entrances[0].heading_degrees)
    return (
        distinct_parallel_parking_aisle_count(layout, heading) >= 2
        and has_real_cross_aisle(layout, heading)
    )


def l_shape_current_is_insufficient(layout: LayoutResult, site: SiteSpec) -> bool:
    return not uses_second_l_wing(layout, site)


def l_shape_is_solved(layout: LayoutResult, site: SiteSpec) -> bool:
    return uses_second_l_wing(layout, site)


def tight_site_cannot_fit_two_aisles(site: SiteSpec) -> bool:
    return site_width(site) + 1e-6 < two_aisle_clear_width(site)


def turn_reject_obstacle_blocks_intended_t(site: SiteSpec) -> bool:
    payload_ids = {spec.id for spec in site.obstacle_specs}
    return "t-fillet-block" in payload_ids
