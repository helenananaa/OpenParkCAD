"""Adapt a current LayoutResult into a RoadSkeleton without inventing connections."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, SiteSpec
from openparkcad.road_skeleton import (
    RoadMovement,
    RoadNode,
    RoadSegment,
    RoadSkeleton,
    SkeletonIssue,
    make_movement,
    make_node,
    make_segment,
    make_skeleton,
)
from openparkcad.road_skeleton_geometry import derive_segment_polygon
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph

LEGACY_ADAPTER_VERSION = "legacy-adapter-1"
NODE_SNAP_TOLERANCE = 0.45
AREA_ABS_TOLERANCE = 16.0
AREA_REL_TOLERANCE = 0.2
ROLE_MAP = {
    "main": "main",
    "jog": "jog",
    "branch": "branch",
    "connector": "connector",
    "exit": "exit",
    "passing_bay": "passing_bay",
    "turnaround": "turnaround",
    "aisle": "parking_aisle",
}


@dataclass(frozen=True)
class LegacyAdapterResult:
    skeleton: RoadSkeleton | None
    aisle_to_segment: Mapping[str, str]
    segment_to_aisle: Mapping[str, str]
    comparison: dict[str, Any]
    issues: tuple[SkeletonIssue, ...]
    graph_valid: bool | None


def adapt_layout_to_skeleton(layout: LayoutResult, *, strict: bool = False) -> LegacyAdapterResult:
    """Shadow adapter: explicit relations only, official geometry stays on the current path."""

    empty = LegacyAdapterResult(
        skeleton=None,
        aisle_to_segment={},
        segment_to_aisle={},
        comparison={"version": LEGACY_ADAPTER_VERSION, "aisles": []},
        issues=(),
        graph_valid=None,
    )
    if not layout.aisles:
        return empty

    try:
        segments, aisle_to_segment, node_seeds = _segments_from_aisles(layout)
        nodes, replacements = _merge_nodes(node_seeds)
        segments = _rewrite_segment_nodes(segments, replacements)
        movements = _movements_from_declared_links(layout, aisle_to_segment, segments, nodes)
        entrance_ids = tuple(
            aisle.connected_to_entrance_id
            for aisle in layout.aisles
            if aisle.connected_to_entrance_id
        )
        skeleton = make_skeleton(
            family="legacy",
            nodes=nodes,
            segments=segments,
            movements=movements,
            entrance_ids=tuple(dict.fromkeys(entrance_ids)),
            source={
                "adapter": LEGACY_ADAPTER_VERSION,
                "generation_mode": layout.generation_mode,
                "aisle_ids": [aisle.id for aisle in layout.aisles],
            },
            site=layout.site,
            strict=strict,
        )
    except (ValueError, TypeError) as exc:
        return LegacyAdapterResult(
            skeleton=None,
            aisle_to_segment={},
            segment_to_aisle={},
            comparison={"version": LEGACY_ADAPTER_VERSION, "error": str(exc)},
            issues=(),
            graph_valid=None,
        )

    segment_to_aisle = {segment_id: aisle_id for aisle_id, segment_id in aisle_to_segment.items()}
    comparison = _compare_geometry(layout, skeleton, aisle_to_segment)
    graph = build_traffic_graph(layout)
    graph_report = validate_traffic_graph(graph, layout)
    comparison["traffic_graph_valid"] = bool(graph_report.get("valid"))
    comparison["invented_movements"] = False
    return LegacyAdapterResult(
        skeleton=skeleton,
        aisle_to_segment=aisle_to_segment,
        segment_to_aisle=segment_to_aisle,
        comparison=comparison,
        issues=(),
        graph_valid=bool(graph_report.get("valid")),
    )


def _segments_from_aisles(
    layout: LayoutResult,
) -> tuple[list[RoadSegment], dict[str, str], list[RoadNode]]:
    segments: list[RoadSegment] = []
    mapping: dict[str, str] = {}
    nodes: list[RoadNode] = []
    for aisle in layout.aisles:
        centerline, width = _aisle_centerline(aisle, layout.site)
        if len(centerline) < 2 or width <= 0:
            continue
        segment_id = f"seg-{aisle.id}"
        start_id, end_id = f"node-{aisle.id}-a", f"node-{aisle.id}-b"
        start_kind, end_kind = "terminal", "terminal"
        start_source = None
        end_source = None
        if aisle.role == "turnaround":
            start_kind = end_kind = "turnaround"
        if aisle.connected_to_entrance_id:
            entrance = next(
                (item for item in layout.site.entrances if item.id == aisle.connected_to_entrance_id),
                None,
            )
            if entrance is not None:
                if _distance(centerline[0], entrance.center) <= _distance(centerline[-1], entrance.center):
                    start_kind, start_source = "entrance_port", entrance.id
                else:
                    end_kind, end_source = "entrance_port", entrance.id
        nodes.append(make_node(start_id, start_kind, centerline[0], source_id=start_source))
        nodes.append(make_node(end_id, end_kind, centerline[-1], source_id=end_source))
        segments.append(
            make_segment(
                segment_id,
                ROLE_MAP.get(aisle.role, "parking_aisle"),
                start_id,
                end_id,
                centerline,
                width,
                aisle.directionality or "two_way",
                parking_sides=_parking_sides(aisle),
                source={"legacy_aisle_id": aisle.id, "legacy_role": aisle.role},
            )
        )
        mapping[aisle.id] = segment_id
    return segments, mapping, nodes


def _aisle_centerline(aisle: ParkingAisle, site: SiteSpec) -> tuple[tuple[tuple[float, float], ...], float]:
    if len(aisle.polygon) < 3:
        return ((), 0.0)
    poly = ShapelyPolygon(aisle.polygon)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return ((), 0.0)
    width = float(site.aisle_width)
    eroded = poly.buffer(-(width / 2.0 - 0.08), join_style="mitre")
    if not eroded.is_empty:
        if eroded.geom_type == "MultiPolygon":
            eroded = max(eroded.geoms, key=lambda item: item.area)
        line = _polyline_along(eroded, width)
        if line is not None and len(line) >= 2 and _distance(line[0], line[-1]) > 1e-4:
            return (line, width)
    return _mrr_centerline(poly, width)


def _mrr_centerline(poly: ShapelyPolygon, width: float) -> tuple[tuple[tuple[float, float], ...], float]:
    rectangle = poly.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:-1]
    if len(coords) < 4:
        return ((), 0.0)
    length_01 = _distance(coords[0], coords[1])
    length_12 = _distance(coords[1], coords[2])
    if length_01 >= length_12:
        start = _mid(coords[0], coords[3])
        end = _mid(coords[1], coords[2])
    else:
        start = _mid(coords[0], coords[1])
        end = _mid(coords[3], coords[2])
    length = _distance(start, end)
    if length <= 1e-6:
        return ((), 0.0)
    inset = min(width / 2.0, max(length / 2.0 - 1e-4, 0.0))
    if inset > 0:
        ux = (end[0] - start[0]) / length
        uy = (end[1] - start[1]) / length
        start = (start[0] + ux * inset, start[1] + uy * inset)
        end = (end[0] - ux * inset, end[1] - uy * inset)
    if _distance(start, end) <= 1e-6:
        return ((), 0.0)
    return ((start, end), width)


def _polyline_along(slim: ShapelyPolygon, width: float) -> tuple[tuple[float, float], ...] | None:
    coords = list(slim.simplify(0.15).exterior.coords)[:-1]
    if len(coords) < 2:
        centroid = slim.centroid
        return ((centroid.x, centroid.y), (centroid.x + 1e-3, centroid.y))
    best_i, best_j, best_d = 0, 1, 0.0
    for index, left in enumerate(coords):
        for other, right in enumerate(coords[index + 1 :], start=index + 1):
            distance = _distance(left, right)
            if distance > best_d:
                best_i, best_j, best_d = index, other, distance
    start, end = coords[best_i], coords[best_j]
    elbow = max(coords, key=lambda point: _point_segment_distance(point, start, end))
    if _point_segment_distance(elbow, start, end) < max(width * 0.35, 1.0):
        return (start, end)
    return (start, elbow, end)


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    vx, vy = end[0] - start[0], end[1] - start[1]
    length_sq = vx * vx + vy * vy
    if length_sq <= 1e-12:
        return _distance(point, start)
    t = max(0.0, min(1.0, ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / length_sq))
    return _distance(point, (start[0] + t * vx, start[1] + t * vy))


def _parking_sides(aisle: ParkingAisle) -> tuple[str, ...]:
    if aisle.role in {"turnaround", "exit", "passing_bay", "jog"}:
        return ("none",)
    return ("left", "right")


def _merge_nodes(nodes: list[RoadNode]) -> tuple[list[RoadNode], dict[str, str]]:
    replacements: dict[str, str] = {node.id: node.id for node in nodes}
    kept: list[RoadNode] = []
    for node in nodes:
        match = next((item for item in kept if _distance(item.point, node.point) <= NODE_SNAP_TOLERANCE), None)
        if match is None:
            kept.append(node)
            continue
        replacements[node.id] = match.id
        if node.kind == "entrance_port" and match.kind != "entrance_port":
            kept[kept.index(match)] = make_node(
                match.id,
                "entrance_port",
                match.point,
                heading_degrees=match.heading_degrees,
                source_id=node.source_id or match.source_id,
            )
    return kept, replacements


def _rewrite_segment_nodes(segments: list[RoadSegment], replacements: Mapping[str, str]) -> list[RoadSegment]:
    rewritten: list[RoadSegment] = []
    for segment in segments:
        rewritten.append(
            make_segment(
                segment.id,
                segment.role,
                replacements.get(segment.start_node_id, segment.start_node_id),
                replacements.get(segment.end_node_id, segment.end_node_id),
                segment.centerline,
                segment.width,
                segment.directionality,
                parking_sides=segment.parking_sides,
                source=segment.source,
            )
        )
    return rewritten


def _declared_pairs(layout: LayoutResult) -> list[tuple[ParkingAisle, ParkingAisle, str]]:
    by_id = {aisle.id: aisle for aisle in layout.aisles}
    pairs: list[tuple[ParkingAisle, ParkingAisle, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(left: ParkingAisle, right: ParkingAisle, kind: str) -> None:
        key = tuple(sorted((left.id, right.id)))
        if key in seen:
            return
        if not _polygons_touch(left, right):
            return
        seen.add(key)
        pairs.append((left, right, kind))

    for aisle in layout.aisles:
        if aisle.parent_aisle_id and aisle.parent_aisle_id in by_id:
            add(by_id[aisle.parent_aisle_id], aisle, "parent")
        for other_id in aisle.connected_aisle_ids:
            if other_id in by_id:
                add(aisle, by_id[other_id], "connected")
    return pairs


def _polygons_touch(left: ParkingAisle, right: ParkingAisle) -> bool:
    try:
        a = ShapelyPolygon(left.polygon)
        b = ShapelyPolygon(right.polygon)
    except (TypeError, ValueError):
        return False
    if a.is_empty or b.is_empty:
        return False
    return a.distance(b) <= 1e-7


def _movements_from_declared_links(
    layout: LayoutResult,
    aisle_to_segment: Mapping[str, str],
    segments: list[RoadSegment],
    nodes: list[RoadNode],
) -> list[RoadMovement]:
    by_seg = {segment.id: segment for segment in segments}
    movements: list[RoadMovement] = []
    for left, right, kind in _declared_pairs(layout):
        left_id = aisle_to_segment.get(left.id)
        right_id = aisle_to_segment.get(right.id)
        if left_id not in by_seg or right_id not in by_seg:
            continue
        via = _via_node(by_seg[left_id], by_seg[right_id], nodes, left, right)
        if via is None:
            continue
        two_way = (left.directionality or "two_way") != "one_way"
        movements.append(make_movement(f"mov-{left_id}-{right_id}-{via.id}", left_id, right_id, via.id, "left" if kind == "parent" else "straight"))
        if two_way:
            movements.append(
                make_movement(f"mov-{right_id}-{left_id}-{via.id}", right_id, left_id, via.id, "right" if kind == "parent" else "straight")
            )
    for aisle in layout.aisles:
        if not aisle.connected_to_entrance_id:
            continue
        segment_id = aisle_to_segment.get(aisle.id)
        if segment_id not in by_seg:
            continue
        port = next((node for node in nodes if node.kind == "entrance_port" and node.source_id == aisle.connected_to_entrance_id), None)
        if port is None:
            continue
        movements.append(make_movement(f"mov-enter-{segment_id}", segment_id, segment_id, port.id, "enter"))
    return movements


def _via_node(
    left: RoadSegment,
    right: RoadSegment,
    nodes: list[RoadNode],
    left_aisle: ParkingAisle,
    right_aisle: ParkingAisle,
) -> RoadNode | None:
    shared = {left.start_node_id, left.end_node_id} & {right.start_node_id, right.end_node_id}
    if shared:
        node_id = next(iter(shared))
        return next((node for node in nodes if node.id == node_id), None)
    contact = ShapelyPolygon(left_aisle.polygon).intersection(ShapelyPolygon(right_aisle.polygon))
    if contact.is_empty:
        return None
    point = (float(contact.centroid.x), float(contact.centroid.y))
    existing = next((node for node in nodes if _distance(node.point, point) <= NODE_SNAP_TOLERANCE), None)
    if existing is not None:
        return existing
    created = make_node(f"node-j-{left.id}-{right.id}", "junction", point)
    nodes.append(created)
    return created


def _compare_geometry(
    layout: LayoutResult,
    skeleton: RoadSkeleton,
    aisle_to_segment: Mapping[str, str],
) -> dict[str, Any]:
    by_seg = {segment.id: segment for segment in skeleton.segments}
    rows: list[dict[str, Any]] = []
    for aisle in layout.aisles:
        segment_id = aisle_to_segment.get(aisle.id)
        if segment_id not in by_seg:
            continue
        original = ShapelyPolygon(aisle.polygon)
        derived = derive_segment_polygon(by_seg[segment_id])
        if not original.is_valid:
            original = original.buffer(0)
        delta = original.symmetric_difference(derived).area
        scale = max(original.area, derived.area, 1.0)
        rows.append(
            {
                "aisle_id": aisle.id,
                "segment_id": segment_id,
                "role": aisle.role,
                "mapped_role": by_seg[segment_id].role,
                "width": by_seg[segment_id].width,
                "directionality": by_seg[segment_id].directionality,
                "symmetric_difference_area": float(delta),
                "within_tolerance": delta <= max(AREA_ABS_TOLERANCE, AREA_REL_TOLERANCE * scale),
            }
        )
    return {
        "version": LEGACY_ADAPTER_VERSION,
        "aisles": rows,
        "all_within_tolerance": all(row["within_tolerance"] for row in rows) if rows else True,
    }


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _mid(left: tuple[float, float], right: tuple[float, float]) -> tuple[float, float]:
    return ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
