"""Derive road polygons from skeleton centerlines. Report A / verify B is forbidden."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon
from shapely.geometry.base import BaseGeometry

from openparkcad.road_skeleton import RoadSegment, RoadSkeleton, segment_by_id

ROAD_SKELETON_GEOMETRY_VERSION = "road-skeleton-geometry-1"
# shapely 2 named styles: cap round=1, join round=1
CAP_STYLE = "round"
JOIN_STYLE = "round"
BUFFER_RESOLUTION = 16
CONTACT_TOLERANCE = 1e-4


def centerline_linestring(segment: RoadSegment) -> LineString:
    points = list(segment.centerline)
    if len(points) == 1:
        points = [points[0], points[0]]
    return LineString(points)


def derive_segment_polygon(segment: RoadSegment) -> BaseGeometry:
    """Buffer the official centerline. Callers must not substitute another polygon."""

    distinct = []
    for point in segment.centerline:
        if not distinct or (abs(point[0] - distinct[-1][0]) > 1e-12 or abs(point[1] - distinct[-1][1]) > 1e-12):
            distinct.append(point)
    if len(distinct) < 2:
        return ShapelyPolygon()
    line = centerline_linestring(segment)
    return line.buffer(
        segment.width / 2.0,
        cap_style=CAP_STYLE,
        join_style=JOIN_STYLE,
        quad_segs=BUFFER_RESOLUTION,
    )


def geometry_strategy() -> dict[str, Any]:
    return {
        "version": ROAD_SKELETON_GEOMETRY_VERSION,
        "construction": "centerline_buffer",
        "cap_style": CAP_STYLE,
        "join_style": JOIN_STYLE,
        "quad_segs": BUFFER_RESOLUTION,
        "offset": "half_width",
    }


def derive_skeleton_polygons(skeleton: RoadSkeleton) -> dict[str, BaseGeometry]:
    return {segment.id: derive_segment_polygon(segment) for segment in skeleton.segments}


def segment_touches(
    left: RoadSegment,
    right: RoadSegment,
    *,
    tolerance: float = CONTACT_TOLERANCE,
) -> bool:
    if left.id == right.id:
        return True
    a = derive_segment_polygon(left)
    b = derive_segment_polygon(right)
    return bool(a.buffer(tolerance).intersects(b))


def point_on_segment_pavement(
    segment: RoadSegment,
    point: tuple[float, float],
    *,
    tolerance: float = CONTACT_TOLERANCE,
) -> bool:
    pavement = derive_segment_polygon(segment)
    return bool(pavement.buffer(tolerance).covers(ShapelyPoint(point)))


def derived_geometry_report(skeleton: RoadSkeleton) -> dict[str, Any]:
    polygons = derive_skeleton_polygons(skeleton)
    return {
        "version": ROAD_SKELETON_GEOMETRY_VERSION,
        "strategy": geometry_strategy(),
        "segments": {
            segment_id: {
                "area": float(geometry.area),
                "wkt": geometry.wkt,
            }
            for segment_id, geometry in polygons.items()
        },
    }


def segments_for_ids(skeleton: RoadSkeleton, ids: tuple[str, ...]) -> tuple[RoadSegment, ...]:
    lookup: Mapping[str, RoadSegment] = segment_by_id(skeleton)
    return tuple(lookup[item] for item in ids if item in lookup)
