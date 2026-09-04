"""Hard generation/selection locks for project objects."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec

SUPPORTED_LOCK_KINDS = ("entrance", "main_aisle", "stall_group")
_GEOM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class LayoutLock:
    lock_id: str
    kind: str
    object_id: str
    geometry: list[tuple[float, float]] | None
    heading_degrees: float | None = None
    width: float | None = None
    directionality: str | None = None
    stall_type_id: str | None = None
    project_object_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "lock_id": self.lock_id,
            "kind": self.kind,
            "object_id": self.object_id,
            "project_object_id": self.project_object_id or self.object_id,
            "geometry": [list(point) for point in self.geometry] if self.geometry else None,
            "heading_degrees": self.heading_degrees,
            "width": self.width,
            "directionality": self.directionality,
            "stall_type_id": self.stall_type_id,
        }


def parse_lock(raw: dict[str, Any]) -> LayoutLock:
    kind = str(raw.get("kind") or "")
    if kind not in SUPPORTED_LOCK_KINDS:
        raise ValueError(f"unsupported lock kind {kind!r}")
    geometry = raw.get("geometry")
    points = None
    if geometry is not None:
        points = [(float(x), float(y)) for x, y in geometry]
    return LayoutLock(
        lock_id=str(raw.get("lock_id") or raw.get("id") or raw.get("object_id")),
        kind=kind,
        object_id=str(raw.get("object_id")),
        geometry=points,
        heading_degrees=float(raw["heading_degrees"]) if raw.get("heading_degrees") is not None else None,
        width=float(raw["width"]) if raw.get("width") is not None else None,
        directionality=str(raw["directionality"]) if raw.get("directionality") is not None else None,
        stall_type_id=str(raw["stall_type_id"]) if raw.get("stall_type_id") is not None else None,
        project_object_id=str(raw.get("project_object_id") or raw.get("object_id")),
    )


def lock_from_aisle(aisle: ParkingAisle, *, lock_id: str | None = None) -> LayoutLock:
    if aisle.role not in {"main", "aisle"}:
        raise ValueError("main_aisle locks require a main aisle")
    return LayoutLock(
        lock_id=lock_id or f"lock-{aisle.id}",
        kind="main_aisle",
        object_id=aisle.id,
        geometry=list(aisle.polygon),
        heading_degrees=aisle.angle_degrees,
        directionality=aisle.directionality,
        project_object_id=aisle.id,
    )


def lock_from_entrance(entrance: EntranceSpec, *, lock_id: str | None = None) -> LayoutLock:
    return LayoutLock(
        lock_id=lock_id or f"lock-entrance-{entrance.id}",
        kind="entrance",
        object_id=entrance.id,
        geometry=[entrance.center],
        heading_degrees=entrance.heading_degrees,
        width=entrance.width,
        project_object_id=entrance.id,
    )


def generation_locks_from_site(site: SiteSpec) -> list[LayoutLock]:
    raw = (site.constraints or {}).get("generation_locks") or []
    return [parse_lock(item) for item in raw]


def site_with_generation_locks(site: SiteSpec, locks: list[LayoutLock]) -> SiteSpec:
    constraints = dict(site.constraints or {})
    constraints["generation_locks"] = [lock.to_record() for lock in locks]
    return replace(site, constraints=constraints)


def apply_entrance_locks(site: SiteSpec, locks: list[LayoutLock]) -> SiteSpec:
    entrance_locks = {lock.object_id: lock for lock in locks if lock.kind == "entrance"}
    if not entrance_locks:
        return site
    updated: list[EntranceSpec] = []
    for entrance in site.entrances:
        lock = entrance_locks.get(entrance.id)
        if lock is None:
            updated.append(entrance)
            continue
        center = lock.geometry[0] if lock.geometry else entrance.center
        updated.append(
            replace(
                entrance,
                center=center,
                heading_degrees=lock.heading_degrees if lock.heading_degrees is not None else entrance.heading_degrees,
                width=lock.width if lock.width is not None else entrance.width,
            )
        )
    return replace(site, entrances=updated)


def prepare_site_locks(site: SiteSpec, locks: list[LayoutLock] | None) -> tuple[SiteSpec, list[LayoutLock]]:
    merged = list(locks or []) + generation_locks_from_site(site)
    if not merged:
        return site, []
    prepared = site_with_generation_locks(apply_entrance_locks(site, merged), merged)
    return prepared, merged


def generate_with_locks(
    site: SiteSpec,
    locks: list[LayoutLock],
    unconstrained: Callable[[SiteSpec], LayoutResult],
    finalize: Callable[[LayoutResult], LayoutResult],
) -> LayoutResult:
    """Pin locked geometry while regenerating the rest; do not post-draw moved objects."""

    main_lock = next((lock for lock in locks if lock.kind == "main_aisle"), None)
    if main_lock is not None:
        built = build_layout_from_main_lock(site, main_lock, locks)
        if built is None:
            return finalize(LayoutResult(site=site, stalls=[], aisles=[], generation_mode="locked_main_aisle"))
        return finalize(built)
    layout = unconstrained(site)
    overlaid = overlay_locked_stalls(layout, locks)
    if overlaid is layout:
        return layout
    return finalize(overlaid)


def build_layout_from_main_lock(site: SiteSpec, main_lock: LayoutLock, locks: list[LayoutLock]) -> LayoutResult | None:
    if not main_lock.geometry:
        return None
    from openparkcad.layout_geometry import available_area, polygon_points, turnaround_polygon
    from openparkcad.phase1_candidates import place_main_family_stalls
    from openparkcad.phase1_support import entry_capable_entrances
    from openparkcad.road_transitions import aisle_centerline

    entries = entry_capable_entrances(site)
    if not entries:
        return None
    heading = main_lock.heading_degrees if main_lock.heading_degrees is not None else _heading_from_polygon(main_lock.geometry)
    directionality = main_lock.directionality or "two_way"
    main = ParkingAisle(
        id=main_lock.object_id,
        polygon=list(main_lock.geometry),
        angle_degrees=heading,
        role="main",
        connected_to_entrance_id=entries[0].id,
        connected_aisle_ids=("A-TURNAROUND",),
        directionality=directionality,
    )
    centerline = aisle_centerline(main)
    if centerline is None or centerline.length < site.aisle_width:
        return None
    start = centerline.interpolate(0.0, normalized=True)
    geom_entrance = EntranceSpec(
        id=entries[0].id,
        mode=entries[0].mode,
        center=(float(start.x), float(start.y)),
        width=entries[0].width,
        heading_degrees=heading,
        allowed_movements=entries[0].allowed_movements,
    )
    usable = available_area(site)
    start_u = site.aisle_width * 0.25
    end_u = centerline.length - site.aisle_width * 0.25
    stalls = place_main_family_stalls(
        site,
        site.main_stall or site.stall,
        usable,
        geom_entrance,
        heading,
        start_u,
        end_u,
        served_by_aisle_id=main.id,
    )
    turnaround = ParkingAisle(
        id="A-TURNAROUND",
        polygon=polygon_points(turnaround_polygon(site, geom_entrance, heading, centerline.length)),
        angle_degrees=heading,
        role="turnaround",
        parent_aisle_id=main.id,
        directionality=directionality,
    )
    layout = LayoutResult(
        site=site,
        stalls=stalls,
        aisles=[main, turnaround],
        generation_mode="locked_main_aisle",
        main_entrance_id=entries[0].id,
        selected_heading_degrees=heading,
        selected_stall_type_id=site.stall.id,
    )
    return overlay_locked_stalls(layout, locks)


def overlay_locked_stalls(layout: LayoutResult, locks: list[LayoutLock]) -> LayoutResult:
    group_locks = [lock for lock in locks if lock.kind == "stall_group" and lock.geometry]
    if not group_locks:
        return layout
    stalls = list(layout.stalls)
    parent_id = layout.aisles[0].id if layout.aisles else None
    for lock in group_locks:
        if any(not _polygon_mismatch(lock.geometry, stall.polygon) for stall in stalls):
            continue
        stalls.append(
            ParkingStall(
                id=lock.object_id,
                polygon=list(lock.geometry),
                angle_degrees=0.0,
                served_by_aisle_id=parent_id,
                stall_type_id=lock.stall_type_id,
            )
        )
    if stalls == layout.stalls:
        return layout
    return replace(layout, stalls=stalls)


def lock_site_conflicts(layout: LayoutResult, locks: list[LayoutLock]) -> list[dict[str, Any]]:
    from openparkcad.layout_geometry import available_area

    conflicts: list[dict[str, Any]] = []
    usable = available_area(layout.site)
    for lock in locks:
        if lock.kind not in {"main_aisle", "stall_group"} or not lock.geometry:
            continue
        try:
            poly = ShapelyPolygon(lock.geometry)
        except Exception:
            continue
        if poly.is_empty:
            continue
        leftover = poly.difference(usable)
        if leftover.area > _GEOM_TOLERANCE:
            conflicts.append(
                {
                    "lock_id": lock.lock_id,
                    "reason": "locked_geometry_conflicts_with_site",
                    "object_id": lock.object_id,
                }
            )
    return conflicts


def _heading_from_polygon(points: list[tuple[float, float]]) -> float:
    best_length = -1.0
    heading = 0.0
    count = len(points)
    for index in range(count):
        start = points[index]
        end = points[(index + 1) % count]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length > best_length:
            best_length = length
            heading = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    return heading


def lock_from_stalls(stalls: list[ParkingStall], *, lock_id: str, project_object_id: str) -> LayoutLock:
    if not stalls:
        raise ValueError("stall_group lock requires at least one stall")
    union_points: list[tuple[float, float]] = []
    for stall in stalls:
        union_points.extend(stall.polygon)
    return LayoutLock(
        lock_id=lock_id,
        kind="stall_group",
        object_id=project_object_id,
        geometry=list(stalls[0].polygon),
        stall_type_id=stalls[0].stall_type_id,
        project_object_id=project_object_id,
    )


def locks_satisfied(layout: LayoutResult, locks: list[LayoutLock]) -> tuple[bool, list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    aisles = {aisle.id: aisle for aisle in layout.aisles}
    stalls = {stall.id: stall for stall in layout.stalls}
    for lock in locks:
        if lock.kind == "entrance":
            entrance = next((item for item in layout.site.entrances if item.id == lock.object_id), None)
            if entrance is None:
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_entrance_missing", "object_id": lock.object_id})
                continue
            if lock.heading_degrees is not None and abs(entrance.heading_degrees - lock.heading_degrees) > 1e-6:
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_entrance_heading_changed", "object_id": lock.object_id})
            if lock.width is not None and abs(entrance.width - lock.width) > 1e-6:
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_entrance_width_changed", "object_id": lock.object_id})
            if lock.geometry and _points_mismatch(lock.geometry[:1], [entrance.center]):
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_entrance_moved", "object_id": lock.object_id})
        elif lock.kind == "main_aisle":
            aisle = aisles.get(lock.object_id) or next((item for item in layout.aisles if item.role == "main"), None)
            if aisle is None or lock.geometry is None or _polygon_mismatch(lock.geometry, aisle.polygon):
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_main_aisle_geometry_changed", "object_id": lock.object_id})
            elif lock.directionality and aisle.directionality != lock.directionality:
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_main_aisle_direction_changed", "object_id": lock.object_id})
        elif lock.kind == "stall_group":
            stall = stalls.get(lock.object_id)
            if stall is None:
                # group identity is stable; match by geometry
                match = next((item for item in layout.stalls if lock.geometry and not _polygon_mismatch(lock.geometry, item.polygon)), None)
                if match is None:
                    conflicts.append({"lock_id": lock.lock_id, "reason": "locked_stall_group_moved_or_missing", "object_id": lock.object_id})
            elif lock.geometry and _polygon_mismatch(lock.geometry, stall.polygon):
                conflicts.append({"lock_id": lock.lock_id, "reason": "locked_stall_group_moved_or_missing", "object_id": lock.object_id})
        else:
            conflicts.append({"lock_id": lock.lock_id, "reason": "unsupported_lock_kind", "object_id": lock.object_id})
    return not conflicts, conflicts


def _polygon_mismatch(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> bool:
    try:
        a = ShapelyPolygon(left)
        b = ShapelyPolygon(right)
    except Exception:
        return True
    if a.is_empty or b.is_empty:
        return True
    return a.symmetric_difference(b).area > _GEOM_TOLERANCE


def _points_mismatch(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> bool:
    if len(left) != len(right):
        return True
    return any(abs(a[0] - b[0]) > _GEOM_TOLERANCE or abs(a[1] - b[1]) > _GEOM_TOLERANCE for a, b in zip(left, right))
