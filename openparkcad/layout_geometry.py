from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon, box
from shapely.ops import unary_union

from openparkcad.models import EntranceSpec, Polygon, SiteSpec
from openparkcad.site_constraints import ConstraintPurpose, site_usable_area


def available_area(site: SiteSpec, purpose: ConstraintPurpose = "all"):
    """Return site geometry after the active hard exclusions for ``purpose``."""

    return site_usable_area(site, purpose)


def main_aisle_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return unary_union(
        [
            main_aisle_polygon(site, entrance, heading_degrees, start, length),
            turnaround_polygon(site, entrance, heading_degrees, length),
        ]
    )


def branch_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    return unary_union(
        [
            branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, length),
            branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, length),
        ]
    )


def main_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return local_box_to_world(start, -site.aisle_width / 2, length, site.aisle_width / 2, entrance, heading_degrees)


def turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, length: float):
    return local_box_to_world(
        length - site.aisle_width,
        -site.aisle_width,
        length,
        site.aisle_width,
        entrance,
        heading_degrees,
    )


def entrance_throat_polygon(
    site: SiteSpec,
    real_entrance: EntranceSpec,
    geom_entrance: EntranceSpec,
    heading_degrees: float,
    start: float,
):
    """Connect a laterally shifted aisle frame back to the real entrance center.

    When the geometry frame is unshifted, return an empty polygon so the main
    aisle body alone owns the entrance connection.
    """
    if (
        abs(real_entrance.center[0] - geom_entrance.center[0]) <= 1e-9
        and abs(real_entrance.center[1] - geom_entrance.center[1]) <= 1e-9
    ):
        return ShapelyPolygon()

    # Build throat in the real-entrance local frame spanning toward the shifted frame origin.
    dx = geom_entrance.center[0] - real_entrance.center[0]
    dy = geom_entrance.center[1] - real_entrance.center[1]
    heading = math.radians(heading_degrees)
    # local u along heading, v perpendicular
    local_u = dx * math.cos(heading) + dy * math.sin(heading)
    local_v = -dx * math.sin(heading) + dy * math.cos(heading)
    u_min = max(start, 0.0)
    u_max = max(u_min + site.aisle_width, local_u + site.aisle_width / 2, start + site.aisle_width)
    v_min = min(0.0, local_v) - site.aisle_width / 2
    v_max = max(0.0, local_v) + site.aisle_width / 2
    return local_box_to_world(u_min, v_min, u_max, v_max, real_entrance, heading_degrees)


def branch_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width / 2, -site.aisle_width / 2, branch_u + site.aisle_width / 2, length)
    else:
        local = (branch_u - site.aisle_width / 2, -length, branch_u + site.aisle_width / 2, site.aisle_width / 2)
    return local_box_to_world(*local, entrance, heading_degrees)


def branch_turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width, length - site.aisle_width, branch_u + site.aisle_width, length)
    else:
        local = (branch_u - site.aisle_width, -length, branch_u + site.aisle_width, -length + site.aisle_width)
    return local_box_to_world(*local, entrance, heading_degrees)


def local_box_to_world(u1: float, v1: float, u2: float, v2: float, entrance: EntranceSpec, heading_degrees: float):
    geometry = box(u1, v1, u2, v2)
    rotated = affinity.rotate(geometry, heading_degrees, origin=(0, 0), use_radians=False)
    return affinity.translate(rotated, xoff=entrance.center[0], yoff=entrance.center[1])


def normalized_local_box_to_world(local: tuple[float, float, float, float], entrance: EntranceSpec, heading_degrees: float):
    u1, v1, u2, v2 = local
    return local_box_to_world(min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2), entrance, heading_degrees)


def area_overlaps(a, b) -> bool:
    return a.intersection(b).area > 1e-6


def polygon_points(poly) -> Polygon:
    polygons = _iter_polygons(poly)
    if polygons:
        largest = max(polygons, key=lambda item: item.area)
        coords = list(largest.exterior.coords[:-1])
    else:
        coords = list(poly.exterior.coords[:-1])
    return [(float(x), float(y)) for x, y in coords]


def _iter_polygons(geometry) -> list[ShapelyPolygon]:
    if isinstance(geometry, ShapelyPolygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        return [item for item in geometry.geoms if isinstance(item, ShapelyPolygon)]
    return []
