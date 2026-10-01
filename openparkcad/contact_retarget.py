"""Retarget official stalls on the contact band to classified accessible/EV types."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import area_overlaps, available_area, polygon_points
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.phase1_support import angled_module_angle
from openparkcad.site_constraints import (
    DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE,
    DEFAULT_EV_CHARGER_TOUCH_TOLERANCE,
    ConstraintGeometry,
    _constraint_tolerance,
    _geometry_reaches_routes,
    _hard_charger_features,
    _hard_routes,
    stall_type_capabilities,
)

_FRONT_TOUCH = 0.2
_EPSILON = 1e-6
_CONTIGUOUS_GAP = 0.05
_ALONG_ALIGN = 0.9
_HEADING_PERP = 0.35
_MOUNT_ROLES = frozenset({"main", "branch", "jog", "aisle"})


def apply_contact_retarget(layout: LayoutResult) -> LayoutResult:
    """Convert route/charger-adjacent stalls to classified types to meet quotas.

    Same-family only. Does not invent a new aisle. When classified stalls are
    a different size, a same-side contact run may be repacked as a strip.
    Wider single replacements may still drop overlapping same-side neighbors.
    Remaining shortfall may be filled with perpendicular, parallel, or angled
    classified bays on empty contact-band pavement along an existing
    main/branch/jog aisle.
    """
    stalls = list(layout.stalls)
    changed = False
    for capability, quota_key, targets, tolerance_key, default_tol in (
        (
            "accessible",
            "accessible_min",
            _hard_routes(layout.site, {"accessible_route"}),
            "accessible_route_touch_tolerance",
            DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE,
        ),
        (
            "ev",
            "ev_min",
            _hard_charger_features(layout.site),
            "ev_charger_touch_tolerance",
            DEFAULT_EV_CHARGER_TOUCH_TOLERANCE,
        ),
    ):
        need = int(layout.site.parking_quotas.get(quota_key, 0) or 0)
        if need <= 0 or not targets:
            continue
        spec = classified_stall_spec(layout.site, capability)
        if spec is None:
            continue
        tolerance, error = _constraint_tolerance(layout.site, tolerance_key, default_tol)
        if error or tolerance is None:
            continue
        if _capability_count(layout.site, stalls, capability) >= need:
            continue
        aisle_by_id = {
            aisle.id: ShapelyPolygon(aisle.polygon)
            for aisle in layout.aisles
            if aisle.polygon
        }
        usable = available_area(layout.site, "stall")
        specs = _specs_by_id(layout.site)
        guard = 0
        while _capability_count(layout.site, stalls, capability) < need and guard < need + max(len(stalls), 1) + 2:
            guard += 1
            shortfall = need - _capability_count(layout.site, stalls, capability)
            packed = _pack_contact_strip(
                stalls,
                spec,
                capability,
                layout.site,
                aisle_by_id,
                usable,
                targets,
                tolerance,
                shortfall,
            )
            if packed:
                changed = True
                continue
            order = _retarget_order(layout.site, stalls, spec, capability, targets, tolerance, aisle_by_id)
            placed = False
            for index in order:
                replacement, drop = _try_replace_stall(
                    stalls[index],
                    spec,
                    specs.get(stalls[index].stall_type_id or layout.site.stall.id),
                    aisle_by_id,
                    usable,
                    stalls,
                    index,
                    capability,
                    layout.site,
                )
                if replacement is None:
                    continue
                stalls[index] = replacement
                for drop_index in sorted(set(drop), reverse=True):
                    del stalls[drop_index]
                changed = True
                placed = True
                break
            if placed:
                continue
            filled = _fill_empty_contact_frontage(
                stalls,
                spec,
                layout.site,
                layout.aisles,
                aisle_by_id,
                usable,
                targets,
                tolerance,
                shortfall,
            )
            if filled:
                changed = True
                continue
            break
    if not changed:
        return layout
    object.__setattr__(layout, "stalls", _renumber(stalls))
    return layout


def contact_retarget_requested(site: SiteSpec) -> bool:
    if int(site.parking_quotas.get("accessible_min", 0) or 0) > 0:
        if classified_stall_spec(site, "accessible") is not None and _hard_routes(site, {"accessible_route"}):
            return True
    if int(site.parking_quotas.get("ev_min", 0) or 0) > 0:
        if classified_stall_spec(site, "ev") is not None and _hard_charger_features(site):
            return True
    return False


def classified_stall_spec(site: SiteSpec, capability: str) -> StallSpec | None:
    chosen: StallSpec | None = None
    for spec in (site.stall, site.main_stall, site.branch_stall, *site.stall_candidates):
        if spec is None or capability not in stall_type_capabilities(spec):
            continue
        if chosen is None or len(stall_type_capabilities(spec)) > len(stall_type_capabilities(chosen)):
            chosen = spec
    return chosen


def _capability_count(site: SiteSpec, stalls: list[ParkingStall], capability: str) -> int:
    specs = _specs_by_id(site)
    count = 0
    for stall in stalls:
        spec = specs.get(stall.stall_type_id or site.stall.id)
        if capability in stall_type_capabilities(spec):
            count += 1
    return count


def _specs_by_id(site: SiteSpec) -> dict[str, StallSpec]:
    specs: dict[str, StallSpec] = {}
    for spec in (site.stall, site.main_stall, site.branch_stall, *site.stall_candidates):
        if spec is not None:
            specs[spec.id] = spec
    return specs


def _retarget_order(
    site: SiteSpec,
    stalls: list[ParkingStall],
    spec: StallSpec,
    capability: str,
    targets: list[ConstraintGeometry],
    tolerance: float,
    aisle_by_id: dict[str, ShapelyPolygon],
) -> list[int]:
    specs = _specs_by_id(site)
    ranked: list[tuple[float, int]] = []
    for index, stall in enumerate(stalls):
        current = specs.get(stall.stall_type_id or site.stall.id)
        if current is not None and capability in stall_type_capabilities(current):
            continue
        if current is not None and current.family != spec.family:
            continue
        polygon = ShapelyPolygon(stall.polygon)
        if not _geometry_reaches_routes(polygon, targets, tolerance):
            continue
        if stall.served_by_aisle_id not in aisle_by_id:
            continue
        distance = min(polygon.distance(item.base_geometry) for item in targets)
        ranked.append((distance, index))
    ranked.sort()
    return [index for _distance, index in ranked]


def _pack_contact_strip(
    stalls: list[ParkingStall],
    spec: StallSpec,
    capability: str,
    site: SiteSpec,
    aisle_by_id: dict[str, ShapelyPolygon],
    usable,
    targets: list[ConstraintGeometry],
    tolerance: float,
    shortfall: int,
) -> bool:
    """Repack one contiguous same-side contact run into classified stalls.

    Only used when the classified stall is a different size and the run has
    enough frontage for at least two classified bays. Mutates ``stalls``.
    """
    if shortfall < 2:
        return False
    order = _retarget_order(site, stalls, spec, capability, targets, tolerance, aisle_by_id)
    groups: dict[tuple[str | None, str | None], list[int]] = {}
    for index in order:
        stall = stalls[index]
        groups.setdefault((stall.served_by_aisle_id, stall.aisle_side), []).append(index)
    specs = _specs_by_id(site)
    for (aisle_id, side), indices in groups.items():
        if aisle_id not in aisle_by_id or not indices:
            continue
        aisle = aisle_by_id[aisle_id]
        frames: list[tuple[float, int, tuple[float, float], tuple[float, float], tuple[float, float], float]] = []
        for index in indices:
            frame = _front_frame(ShapelyPolygon(stalls[index].polygon), aisle)
            if frame is None:
                continue
            origin, along, outward, front_length = frame
            if frames:
                ref_along = frames[0][3]
                ref_outward = frames[0][4]
                along_dot = along[0] * ref_along[0] + along[1] * ref_along[1]
                if along_dot < 0.0:
                    origin = (origin[0] + along[0] * front_length, origin[1] + along[1] * front_length)
                    along = ref_along
                    along_dot = 1.0
                if along_dot < _ALONG_ALIGN:
                    continue
                if outward[0] * ref_outward[0] + outward[1] * ref_outward[1] < 0.0:
                    continue
            station = origin[0] * along[0] + origin[1] * along[1]
            frames.append((station, index, origin, along, outward, front_length))
        if not frames:
            continue
        frames.sort()
        runs: list[list[tuple[float, int, tuple[float, float], tuple[float, float], tuple[float, float], float]]] = [
            [frames[0]]
        ]
        for frame in frames[1:]:
            prev = runs[-1][-1]
            if frame[0] <= prev[0] + prev[5] + _CONTIGUOUS_GAP:
                runs[-1].append(frame)
            else:
                runs.append([frame])
        for run in runs:
            if _pack_contiguous_run(
                stalls,
                spec,
                capability,
                site,
                specs,
                aisle_by_id[aisle_id],
                aisle_id,
                side,
                usable,
                targets,
                tolerance,
                shortfall,
                run,
            ):
                return True
    return False


def _pack_contiguous_run(
    stalls: list[ParkingStall],
    spec: StallSpec,
    capability: str,
    site: SiteSpec,
    specs: dict[str, StallSpec],
    aisle: ShapelyPolygon,
    aisle_id: str | None,
    side: str | None,
    usable,
    targets: list[ConstraintGeometry],
    tolerance: float,
    shortfall: int,
    run: list[tuple[float, int, tuple[float, float], tuple[float, float], tuple[float, float], float]],
) -> bool:
    current = specs.get(stalls[run[0][1]].stall_type_id or site.stall.id)
    if (
        current is not None
        and abs(current.width - spec.width) <= 1e-6
        and abs(current.length - spec.length) <= 1e-6
    ):
        return False
    origin = run[0][2]
    along = run[0][3]
    outward = run[0][4]
    frontage = run[-1][0] + run[-1][5] - run[0][0]
    fit = int(frontage / spec.width + _EPSILON)
    to_place = min(shortfall, max(fit, 0))
    if to_place < 2:
        return False
    new_stalls: list[ParkingStall] = []
    new_polys: list[ShapelyPolygon] = []
    angle = stalls[run[0][1]].angle_degrees
    for step in range(to_place):
        start = (
            origin[0] + along[0] * spec.width * step,
            origin[1] + along[1] * spec.width * step,
        )
        poly = ShapelyPolygon(
            [
                start,
                (start[0] + along[0] * spec.width, start[1] + along[1] * spec.width),
                (
                    start[0] + along[0] * spec.width + outward[0] * spec.length,
                    start[1] + along[1] * spec.width + outward[1] * spec.length,
                ),
                (start[0] + outward[0] * spec.length, start[1] + outward[1] * spec.length),
            ]
        )
        if poly.is_empty or not poly.is_valid:
            break
        if not usable.covers(poly.buffer(-_EPSILON)) and not usable.covers(poly):
            break
        if area_overlaps(poly, aisle):
            break
        if not _geometry_reaches_routes(poly, targets, tolerance):
            break
        new_stalls.append(
            ParkingStall(
                id=f"P-STRIP-{step}",
                polygon=polygon_points(poly),
                angle_degrees=angle,
                served_by_aisle_id=aisle_id,
                aisle_side=side,
                stall_type_id=spec.id,
            )
        )
        new_polys.append(poly)
    if len(new_stalls) < 2:
        return False
    strip = unary_union(new_polys)
    drop: list[int] = []
    for other_index, other in enumerate(stalls):
        other_poly = ShapelyPolygon(other.polygon)
        if not area_overlaps(strip, other_poly):
            continue
        if other.served_by_aisle_id != aisle_id or other.aisle_side != side:
            return False
        other_spec = specs.get(other.stall_type_id or site.stall.id)
        if capability in stall_type_capabilities(other_spec):
            return False
        drop.append(other_index)
    if not drop:
        return False
    keep = [ShapelyPolygon(item.polygon) for i, item in enumerate(stalls) if i not in set(drop)]
    occupied = unary_union(keep) if keep else ShapelyPolygon()
    if not occupied.is_empty and area_overlaps(strip, occupied):
        return False
    for drop_index in sorted(set(drop), reverse=True):
        del stalls[drop_index]
    stalls.extend(new_stalls)
    return True


def _fill_empty_contact_frontage(
    stalls: list[ParkingStall],
    spec: StallSpec,
    site: SiteSpec,
    aisles: list[ParkingAisle],
    aisle_by_id: dict[str, ShapelyPolygon],
    usable,
    targets: list[ConstraintGeometry],
    tolerance: float,
    shortfall: int,
) -> bool:
    """Place classified bays on empty pavement along existing aisles."""
    plan = _mount_plan(spec)
    if shortfall <= 0 or plan is None:
        return False
    occupied = unary_union([ShapelyPolygon(stall.polygon) for stall in stalls]) if stalls else ShapelyPolygon()
    aisle_union = unary_union(list(aisle_by_id.values())) if aisle_by_id else ShapelyPolygon()
    candidates: list[tuple[float, str, float, str, float, ShapelyPolygon]] = []
    for aisle in aisles:
        if aisle.role not in _MOUNT_ROLES or aisle.id not in aisle_by_id:
            continue
        aisle_poly = aisle_by_id[aisle.id]
        heading = math.radians(aisle.angle_degrees)
        hx, hy = math.cos(heading), math.sin(heading)
        left = (-hy, hx)
        centroid = aisle_poly.centroid
        coords = list(aisle_poly.exterior.coords)
        for start, end in zip(coords, coords[1:]):
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if length + _EPSILON < plan["span"]:
                continue
            tx, ty = (end[0] - start[0]) / length, (end[1] - start[1]) / length
            nx, ny = -ty, tx
            mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if (centroid.x - mid[0]) * nx + (centroid.y - mid[1]) * ny > 0.0:
                nx, ny = -nx, -ny
            if abs(nx * hx + ny * hy) > _HEADING_PERP:
                continue
            side = "left" if nx * left[0] + ny * left[1] >= 0.0 else "right"
            if not _side_accepts_family(site, stalls, aisle.id, side, spec.family):
                continue
            along_x, along_y = tx, ty
            origin_start = start
            if plan["kind"] == "angled" and along_x * hx + along_y * hy < 0.0:
                origin_start = end
                along_x, along_y = -tx, -ty
            offset = 0.0
            while offset + plan["span"] <= length + _EPSILON:
                origin = (origin_start[0] + along_x * offset, origin_start[1] + along_y * offset)
                poly, stall_angle = _mount_polygon(
                    plan, origin, (along_x, along_y), (nx, ny), aisle.angle_degrees, side
                )
                if poly.is_empty or not poly.is_valid:
                    offset += plan["pitch"]
                    continue
                if not usable.covers(poly.buffer(-_EPSILON)) and not usable.covers(poly):
                    offset += plan["pitch"]
                    continue
                if not aisle_union.is_empty and area_overlaps(poly, aisle_union):
                    offset += plan["pitch"]
                    continue
                if not occupied.is_empty and area_overlaps(poly, occupied):
                    offset += plan["pitch"]
                    continue
                if not _geometry_reaches_routes(poly, targets, tolerance):
                    offset += plan["pitch"]
                    continue
                distance = min(poly.distance(item.base_geometry) for item in targets)
                station = origin[0] * along_x + origin[1] * along_y
                candidates.append((distance, aisle.id, station, side, stall_angle, poly))
                offset += plan["pitch"]
    if not candidates:
        return False
    placed: list[ParkingStall] = []
    placed_polys: list[ShapelyPolygon] = []
    for _distance, aisle_id, _station, side, angle, poly in sorted(candidates):
        if len(placed) >= shortfall:
            break
        if any(area_overlaps(poly, other) for other in placed_polys):
            continue
        placed.append(
            ParkingStall(
                id=f"P-FILL-{len(placed)}",
                polygon=polygon_points(poly),
                angle_degrees=angle,
                served_by_aisle_id=aisle_id,
                aisle_side=side,
                stall_type_id=spec.id,
            )
        )
        placed_polys.append(poly)
    if not placed:
        return False
    stalls.extend(placed)
    return True


def _mount_plan(spec: StallSpec) -> dict[str, float | str] | None:
    if _perpendicular_mount_allowed(spec):
        return {"kind": "box", "pitch": spec.width, "span": spec.width, "along": spec.width, "outward": spec.length}
    if spec.family == "parallel":
        return {"kind": "box", "pitch": spec.length, "span": spec.length, "along": spec.length, "outward": spec.width}
    if spec.family != "angled":
        return None
    angle = angled_module_angle(spec.allowed_angles)
    if angle is None:
        return None
    theta = math.radians(angle)
    sine = math.sin(theta)
    if abs(sine) <= _EPSILON:
        return None
    front_pitch = spec.width / sine
    forward_shift = spec.length * math.cos(theta)
    lateral_depth = spec.length * sine
    return {
        "kind": "angled",
        "pitch": front_pitch,
        "span": front_pitch + forward_shift,
        "front_pitch": front_pitch,
        "forward_shift": forward_shift,
        "lateral_depth": lateral_depth,
        "module_angle": angle,
    }


def _mount_polygon(
    plan: dict[str, float | str],
    origin: tuple[float, float],
    along: tuple[float, float],
    outward: tuple[float, float],
    aisle_angle: float,
    side: str | None,
) -> tuple[ShapelyPolygon, float]:
    if plan["kind"] == "box":
        return (
            _classified_polygon(origin, along, outward, float(plan["along"]), float(plan["outward"])),
            aisle_angle,
        )
    poly = _angled_classified_polygon(
        origin,
        along,
        outward,
        float(plan["front_pitch"]),
        float(plan["forward_shift"]),
        float(plan["lateral_depth"]),
    )
    direction = 1.0 if side == "left" else -1.0
    return poly, aisle_angle + direction * float(plan["module_angle"])


def _angled_classified_polygon(
    origin: tuple[float, float],
    along: tuple[float, float],
    outward: tuple[float, float],
    front_pitch: float,
    forward_shift: float,
    lateral_depth: float,
) -> ShapelyPolygon:
    return ShapelyPolygon(
        [
            origin,
            (origin[0] + along[0] * front_pitch, origin[1] + along[1] * front_pitch),
            (
                origin[0] + along[0] * (front_pitch + forward_shift) + outward[0] * lateral_depth,
                origin[1] + along[1] * (front_pitch + forward_shift) + outward[1] * lateral_depth,
            ),
            (
                origin[0] + along[0] * forward_shift + outward[0] * lateral_depth,
                origin[1] + along[1] * forward_shift + outward[1] * lateral_depth,
            ),
        ]
    )


def _perpendicular_mount_allowed(spec: StallSpec) -> bool:
    if spec.family != "perpendicular":
        return False
    if not spec.allowed_angles:
        return True
    return any(abs((angle % 180.0) - 90.0) <= 1e-6 for angle in spec.allowed_angles)


def _side_accepts_family(
    site: SiteSpec,
    stalls: list[ParkingStall],
    aisle_id: str | None,
    side: str | None,
    family: str,
) -> bool:
    specs = _specs_by_id(site)
    for stall in stalls:
        if stall.served_by_aisle_id != aisle_id or stall.aisle_side != side:
            continue
        other = specs.get(stall.stall_type_id or site.stall.id)
        if other is not None and other.family != family:
            return False
    return True


def _classified_polygon(
    origin: tuple[float, float],
    along: tuple[float, float],
    outward: tuple[float, float],
    along_size: float,
    outward_size: float,
) -> ShapelyPolygon:
    return ShapelyPolygon(
        [
            origin,
            (origin[0] + along[0] * along_size, origin[1] + along[1] * along_size),
            (
                origin[0] + along[0] * along_size + outward[0] * outward_size,
                origin[1] + along[1] * along_size + outward[1] * outward_size,
            ),
            (origin[0] + outward[0] * outward_size, origin[1] + outward[1] * outward_size),
        ]
    )


def _try_replace_stall(
    stall: ParkingStall,
    spec: StallSpec,
    current_spec: StallSpec | None,
    aisle_by_id: dict[str, ShapelyPolygon],
    usable,
    stalls: list[ParkingStall],
    index: int,
    capability: str,
    site: SiteSpec,
) -> tuple[ParkingStall | None, list[int]]:
    aisle = aisle_by_id.get(stall.served_by_aisle_id or "")
    if aisle is None:
        return None, []
    current = ShapelyPolygon(stall.polygon)
    if (
        current_spec is not None
        and abs(current_spec.width - spec.width) <= 1e-6
        and abs(current_spec.length - spec.length) <= 1e-6
    ):
        replacement = ParkingStall(
            id=stall.id,
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=spec.id,
        )
        return replacement, []
    frame = _front_frame(current, aisle)
    if frame is None:
        return None, []
    origin, along, outward, _front_length = frame
    replacement_poly = ShapelyPolygon(
        [
            origin,
            (origin[0] + along[0] * spec.width, origin[1] + along[1] * spec.width),
            (
                origin[0] + along[0] * spec.width + outward[0] * spec.length,
                origin[1] + along[1] * spec.width + outward[1] * spec.length,
            ),
            (origin[0] + outward[0] * spec.length, origin[1] + outward[1] * spec.length),
        ]
    )
    if replacement_poly.is_empty or not replacement_poly.is_valid:
        return None, []
    if not usable.covers(replacement_poly.buffer(-_EPSILON)):
        if not usable.covers(replacement_poly):
            return None, []
    if area_overlaps(replacement_poly, aisle):
        return None, []
    specs = _specs_by_id(site)
    drop: list[int] = []
    for other_index, other in enumerate(stalls):
        if other_index == index:
            continue
        other_poly = ShapelyPolygon(other.polygon)
        if not area_overlaps(replacement_poly, other_poly):
            continue
        if other.served_by_aisle_id != stall.served_by_aisle_id or other.aisle_side != stall.aisle_side:
            return None, []
        other_spec = specs.get(other.stall_type_id or site.stall.id)
        if capability in stall_type_capabilities(other_spec):
            return None, []
        drop.append(other_index)
    keep = [ShapelyPolygon(item.polygon) for i, item in enumerate(stalls) if i != index and i not in set(drop)]
    occupied = unary_union(keep) if keep else ShapelyPolygon()
    if not occupied.is_empty and area_overlaps(replacement_poly, occupied):
        return None, []
    replacement = ParkingStall(
        id=stall.id,
        polygon=polygon_points(replacement_poly),
        angle_degrees=stall.angle_degrees,
        served_by_aisle_id=stall.served_by_aisle_id,
        aisle_side=stall.aisle_side,
        stall_type_id=spec.id,
    )
    return replacement, drop


def _front_frame(
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], float] | None:
    coords = list(stall.exterior.coords)
    best: tuple[tuple[float, float], tuple[float, float]] | None = None
    best_length = 0.0
    for start, end in zip(coords, coords[1:]):
        edge = LineString([start, end])
        midpoint = ShapelyPoint(((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0))
        if aisle.distance(midpoint) > _FRONT_TOUCH:
            continue
        length = edge.length
        if length > best_length:
            best_length = length
            best = (start, end)
    if best is None or best_length <= _EPSILON:
        return None
    (x0, y0), (x1, y1) = best
    along_x, along_y = x1 - x0, y1 - y0
    length = math.hypot(along_x, along_y)
    tx, ty = along_x / length, along_y / length
    nx, ny = -ty, tx
    mid = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    centroid = stall.centroid
    if (centroid.x - mid[0]) * nx + (centroid.y - mid[1]) * ny < 0.0:
        nx, ny = -nx, -ny
        x0, y0 = x1, y1
        tx, ty = -tx, -ty
    return (x0, y0), (tx, ty), (nx, ny), best_length


def _renumber(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=stall.stall_type_id,
        )
        for index, stall in enumerate(stalls, start=1)
    ]
