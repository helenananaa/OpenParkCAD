"""Materialize a LayoutResult from a RoadSkeleton so existing road checks can run."""

from __future__ import annotations

from dataclasses import replace

import math

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.road_skeleton import RoadSkeleton
from openparkcad.road_skeleton_geometry import derive_segment_polygon
from openparkcad.road_traversal import validate_road_traversal

ROLE_MAP = {
    "parking_aisle": "branch",
    "cross_aisle": "main",
    "turnaround": "turnaround",
    "spine": "main",
    "main": "main",
    "exit": "exit",
}


def layout_from_skeleton(site: SiteSpec, skeleton: RoadSkeleton, stalls: list[ParkingStall] | None = None) -> LayoutResult:
    aisles: list[ParkingAisle] = []
    parents: dict[str, str] = {}
    connected: dict[str, list[str]] = {segment.id: [] for segment in skeleton.segments}
    for movement in skeleton.movements:
        if movement.from_segment_id != movement.to_segment_id:
            connected.setdefault(movement.from_segment_id, []).append(movement.to_segment_id)
            if movement.from_segment_id.startswith("S-CROSS") or movement.from_segment_id == "S-THROAT":
                parents[movement.to_segment_id] = movement.from_segment_id
    entrance_id = skeleton.entrance_ids[0] if skeleton.entrance_ids else None
    for segment in skeleton.segments:
        polygon = list(derive_segment_polygon(segment).exterior.coords)[:-1]
        role = ROLE_MAP.get(segment.role, segment.role)
        aisle = ParkingAisle(
            id=segment.id,
            polygon=polygon,
            angle_degrees=0.0,
            role=role,
            connected_to_entrance_id=entrance_id if segment.id in {"S-CROSS-ENTRY", "S-THROAT"} else None,
            parent_aisle_id=parents.get(segment.id),
            connected_aisle_ids=tuple(dict.fromkeys(connected.get(segment.id, []))),
            directionality=segment.directionality,
        )
        object.__setattr__(aisle, "angle_degrees", _heading(aisle))
        aisles.append(aisle)
    meta = dict(site.metadata or {})
    meta["skeleton_id"] = skeleton.skeleton_id
    meta["skeleton_family"] = skeleton.family
    return LayoutResult(
        site=replace(site, metadata=meta),
        stalls=list(stalls or []),
        aisles=aisles,
        generation_mode="parallel_ladder_shadow",
        main_entrance_id=entrance_id,
        graph_validation={},
        maneuver_validation={},
        site_constraint_validation={},
        engineering_validation={},
        operational_quality={},
        road_traversal_validation={},
    )


def _heading(aisle: ParkingAisle) -> float:
    if len(aisle.polygon) < 2:
        return 0.0
    rectangle = ShapelyPolygon(aisle.polygon).minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    length_01 = math.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
    length_12 = math.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1])
    if length_01 >= length_12:
        dx, dy = coords[1][0] - coords[0][0], coords[1][1] - coords[0][1]
    else:
        dx, dy = coords[2][0] - coords[1][0], coords[2][1] - coords[1][1]
    return math.degrees(math.atan2(dy, dx)) % 180.0


def validate_skeleton_road_traversal(site: SiteSpec, skeleton: RoadSkeleton, stalls: list[ParkingStall] | None = None) -> dict:
    unsupported = [
        movement
        for movement in skeleton.movements
        if movement.allowed and movement.movement_kind not in {"straight", "left", "right", "enter", "exit"}
    ]
    layout = layout_from_skeleton(site, skeleton, stalls)
    if unsupported:
        return {
            "requested": True,
            "executed": True,
            "status": "unsupported",
            "valid": None,
            "reason": "junction_movement_unsupported",
            "layout_identity": f"skeleton:{skeleton.skeleton_id}",
            "unsupported_movements": [movement.id for movement in unsupported],
        }
    report = validate_road_traversal(layout)
    vehicle = site.vehicle
    vehicle_key = (
        f"{vehicle.id}:{vehicle.min_turning_radius}:{vehicle.length}" if vehicle is not None else "none"
    )
    report["layout_identity"] = (
        f"{report.get('layout_identity')}|skeleton:{skeleton.skeleton_id}|vehicle:{vehicle_key}|algo:road-traversal-1"
    )
    return report
