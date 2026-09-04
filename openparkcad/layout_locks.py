"""Hard generation/selection locks for project objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall

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
        directionality=aisle.directionality,
        project_object_id=aisle.id,
    )


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
