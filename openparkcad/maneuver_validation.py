from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from shapely.geometry import LineString, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import area_overlaps, available_area, polygon_points
from openparkcad.models import (
    LayoutResult,
    ParkingStall,
    SiteSpec,
    StallSpec,
    VehicleSpec,
    is_articulated_vehicle,
)
from openparkcad.phase1_support import angled_module_angle, fixed_aisle_class
from openparkcad.site_constraints import constraint_conflicts, site_usable_area
from openparkcad.swept_path import resolve_vehicle_overhangs, validate_stall_swept_path
from openparkcad.vehicle_kinematics import (
    articulated_off_tracking,
    rear_axle_turning_radius,
    resolve_articulated_geometry,
)


@dataclass(frozen=True)
class ManeuverContext:
    site: SiteSpec
    aisle_by_id: dict[str, ShapelyPolygon]
    drivable: Any
    usable: Any
    swept_usable: Any
    parent_aisle_ids: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ManeuverRule:
    id: str
    family: str
    status: str
    evaluate: Callable[[ManeuverContext, ParkingStall], dict[str, Any]]


@dataclass(frozen=True)
class VehicleCheckPolicy:
    require_turning_radius: bool
    require_swept_path: bool
    require_reverse_distance: bool
    maximum_reverse_distance: float | None
    configuration_error: str | None = None
    declared_turning_radius: bool = False
    declared_swept_path: bool = False

    @property
    def requested(self) -> bool:
        return self.require_turning_radius or self.require_swept_path or self.require_reverse_distance


def apply_maneuver_filter(layout: LayoutResult) -> LayoutResult:
    validation = validate_maneuvers(layout)
    invalid_ids = {item["stall_id"] for item in validation["invalid_stalls"]}
    if not invalid_ids:
        object.__setattr__(layout, "maneuver_validation", validation)
        return layout

    kept_stalls = [stall for stall in layout.stalls if stall.id not in invalid_ids]
    object.__setattr__(layout, "stalls", _renumber_stalls(kept_stalls))
    post_validation = validate_maneuvers(layout)
    post_validation["filtered_stall_ids"] = sorted(invalid_ids)
    post_validation["filtered_stall_count"] = len(invalid_ids)
    post_validation["pre_filter_invalid_stalls"] = validation["invalid_stalls"]
    post_validation["pre_filter_vehicle_validation"] = validation.get("vehicle_validation", {})
    pre_vehicle = validation.get("vehicle_validation", {})
    if (
        not kept_stalls
        and isinstance(pre_vehicle, dict)
        and int(pre_vehicle.get("invalid_stall_count", 0)) > 0
    ):
        rejected_vehicle = dict(pre_vehicle)
        rejected_vehicle["result_scope"] = "all_generated_stalls_rejected"
        post_validation["vehicle_validation"] = rejected_vehicle
        post_validation["valid"] = False
    object.__setattr__(layout, "maneuver_validation", post_validation)
    return layout


def validate_maneuvers(layout: LayoutResult) -> dict[str, Any]:
    aisle_by_id = {aisle.id: ShapelyPolygon(aisle.polygon) for aisle in layout.aisles}
    drivable = unary_union(list(aisle_by_id.values())) if aisle_by_id else ShapelyPolygon()
    usable = available_area(layout.site)
    context = ManeuverContext(
        site=layout.site,
        aisle_by_id=aisle_by_id,
        drivable=drivable,
        usable=usable,
        swept_usable=site_usable_area(layout.site, "swept_path"),
        parent_aisle_ids={aisle.id: aisle.parent_aisle_id for aisle in layout.aisles},
    )
    invalid: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    rule_counts: dict[str, int] = {}
    vehicle_checks: list[dict[str, Any]] = []
    vehicle_rule_counts: dict[str, int] = {}
    vehicle_policy = _vehicle_check_policy(layout.site)

    for stall in layout.stalls:
        rule = _rule_for_stall(layout.site, stall)
        envelope_result = rule.evaluate(context, stall)
        if envelope_result["envelope"] is None:
            _increment_rule_count(rule_counts, rule.id)
            invalid.append(
                {
                    "stall_id": stall.id,
                    "stall_type_id": stall.stall_type_id,
                    "served_by_aisle_id": stall.served_by_aisle_id,
                    "rule_id": rule.id,
                    "rule_status": rule.status,
                    "reason": envelope_result["reason"],
                }
            )
            continue

        record = _maneuver_record(context, stall, rule.id, rule.status, envelope_result)
        failure_reason = _maneuver_failure_reason(record, envelope_result)
        fallback_result = _perpendicular_l_shape_fallback(context, stall, rule, envelope_result, failure_reason)
        if fallback_result is not None:
            fallback_record = _maneuver_record(
                context,
                stall,
                str(fallback_result["rule_id"]),
                rule.status,
                fallback_result,
            )
            fallback_reason = _maneuver_failure_reason(fallback_record, fallback_result)
            if fallback_reason is None:
                record = fallback_record
                envelope_result = fallback_result
                failure_reason = None

        vehicle_check = _validate_vehicle_maneuver(context, stall, vehicle_policy)
        if vehicle_check is not None:
            record["vehicle_validation"] = vehicle_check
            vehicle_checks.append(vehicle_check)
            vehicle_rule_id = str(vehicle_check.get("rule_id", "unknown"))
            _increment_rule_count(vehicle_rule_counts, vehicle_rule_id)
            if failure_reason is None and not vehicle_check.get("valid", False):
                failure_reason = str(vehicle_check.get("reason") or "vehicle_maneuver_invalid")

        _increment_rule_count(rule_counts, str(record["rule_id"]))
        envelopes.append(record)
        if failure_reason is not None:
            invalid.append({**record, "reason": failure_reason})

    vehicle_validation = _vehicle_validation_summary(layout.site, vehicle_policy, vehicle_checks)
    return {
        "version": "v0.3-maneuver-validation-1",
        "valid": not invalid and vehicle_validation["valid"],
        "checked_stalls": len(layout.stalls),
        "invalid_stalls": invalid,
        "access_depth": _access_depth(layout.site),
        "minimum_coverage_ratio": _minimum_coverage_ratio(layout.site),
        "turn_buffer_length": _turn_buffer_length(layout.site),
        "minimum_turn_coverage_ratio": _minimum_turn_coverage_ratio(layout.site),
        "rule_counts": rule_counts,
        "rule_support": _rule_support_report(layout.site),
        "envelopes": envelopes,
        "vehicle_validation": vehicle_validation,
        "vehicle_rule_counts": vehicle_rule_counts,
    }


def _perpendicular_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    site = context.site
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=stall_spec.width,
        access_depth=_access_depth(site),
        turn_buffer_length=_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_turn_coverage_ratio(site),
    )


def _angled_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    site = context.site
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=_angled_front_edge_width(stall_spec),
        access_depth=_angled_access_depth(site, stall_spec),
        turn_buffer_length=_angled_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_angled_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_angled_turn_coverage_ratio(site),
    )


def _parallel_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    """Conservative parallel-parking proxy: traffic-side strip plus longitudinal buffer.

    This is not an S-curve reverse-parallel path. It only checks that a rectangle
    from the aisle-facing long edge into the serving aisle remains mostly
    drivable and inside usable site geometry.
    """
    site = context.site
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=stall_spec.length,
        access_depth=_parallel_access_depth(site, stall_spec),
        turn_buffer_length=_parallel_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_parallel_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_parallel_turn_coverage_ratio(site),
    )


def _t_end_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    """T-end stalls face the dead-end turnaround; reuse the perpendicular front proxy."""
    site = context.site
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=stall_spec.width,
        access_depth=_access_depth(site),
        turn_buffer_length=_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_turn_coverage_ratio(site),
    )


def _front_access_envelope(
    context: ManeuverContext,
    stall: ParkingStall,
    target_edge_width: float,
    access_depth: float,
    turn_buffer_length: float,
    minimum_coverage_ratio: float,
    minimum_turn_coverage_ratio: float,
) -> dict[str, Any]:
    if not stall.served_by_aisle_id:
        return {"envelope": None, "reason": "stall_has_no_serving_aisle"}
    aisle = context.aisle_by_id.get(stall.served_by_aisle_id)
    if aisle is None:
        return {"envelope": None, "reason": "serving_aisle_missing"}

    stall_polygon = ShapelyPolygon(stall.polygon)
    edge = _front_edge(stall_polygon, aisle, target_edge_width)
    if edge is None:
        return {"envelope": None, "reason": "front_access_edge_not_found"}

    envelope = _edge_access_rectangle(edge, stall_polygon, aisle, access_depth, extra_along_edge=0.0)
    if envelope is None or envelope.area <= 1e-9:
        return {"envelope": None, "reason": "access_envelope_not_possible"}
    turn_proxy = _edge_access_rectangle(
        edge,
        stall_polygon,
        aisle,
        access_depth,
        extra_along_edge=turn_buffer_length,
    )
    if turn_proxy is None or turn_proxy.area <= 1e-9:
        return {"envelope": None, "reason": "turning_sweep_not_possible"}
    return {
        "envelope": envelope,
        "turn_proxy": turn_proxy,
        "access_depth": access_depth,
        "turn_buffer_length": turn_buffer_length,
        "minimum_coverage_ratio": minimum_coverage_ratio,
        "minimum_turn_coverage_ratio": minimum_turn_coverage_ratio,
        "front_edge": edge,
        "stall_polygon": stall_polygon,
        "serving_aisle": aisle,
        "maneuver_variant": "full_rectangle",
        "reason": "ok",
    }


def _unsupported_maneuver_rule(reason: str) -> Callable[[ManeuverContext, ParkingStall], dict[str, Any]]:
    def evaluate(_context: ManeuverContext, _stall: ParkingStall) -> dict[str, Any]:
        return {"envelope": None, "reason": reason}

    return evaluate


def _rule_for_stall(site: SiteSpec, stall: ParkingStall) -> ManeuverRule:
    stall_spec = _stall_spec_for_stall(site, stall)
    family = stall_spec.family
    if stall.aisle_side == "end" or family == "t_end":
        return ManeuverRule(
            id="t_end_proxy",
            family="t_end",
            status="active",
            evaluate=_t_end_access_envelope,
        )
    if family == "perpendicular" and _angle_supported_for_perpendicular(stall_spec, stall):
        return ManeuverRule(
            id="perpendicular_90_proxy",
            family=family,
            status="active",
            evaluate=_perpendicular_access_envelope,
        )
    if family == "perpendicular":
        return ManeuverRule(
            id="perpendicular_non_90_future",
            family=family,
            status="future",
            evaluate=_unsupported_maneuver_rule("perpendicular_non_90_maneuver_rule_not_implemented"),
        )
    if family == "angled":
        return ManeuverRule(
            id="angled_proxy",
            family=family,
            status="active",
            evaluate=_angled_access_envelope,
        )
    if family == "parallel":
        return ManeuverRule(
            id="parallel_proxy",
            family=family,
            status="active",
            evaluate=_parallel_access_envelope,
        )
    return ManeuverRule(
        id="unknown_family_future",
        family=family,
        status="future",
        evaluate=_unsupported_maneuver_rule("stall_family_maneuver_rule_not_implemented"),
    )


def _rule_support_report(site: SiteSpec) -> dict[str, str]:
    active_families = {stall.family for stall in _active_stall_specs(site)}
    return {
        "perpendicular_90_proxy": "active" if "perpendicular" in active_families else "available",
        "perpendicular_90_l_shape_proxy": (
            "active"
            if "perpendicular" in active_families and _l_shape_fallback_enabled(site)
            else "available"
        ),
        "perpendicular_non_90": "future",
        "angled_proxy": "active" if "angled" in active_families else "available",
        "parallel_proxy": "active" if "parallel" in active_families else "available",
        "t_end_proxy": (
            "active"
            if "t_end" in active_families or _boolean_setting(site.optimization.get("enable_t_end_caps", False))
            else "available"
        ),
    }


def _angle_supported_for_perpendicular(stall_spec: StallSpec, stall: ParkingStall) -> bool:
    _ = stall
    if not _angle_allowed(90.0, stall_spec.allowed_angles):
        return False
    return True


def _angle_supported_for_angled(stall_spec: StallSpec, stall: ParkingStall) -> bool:
    _ = stall
    return angled_module_angle(stall_spec.allowed_angles) is not None


def _angle_allowed(angle: float, allowed_angles: tuple[float, ...]) -> bool:
    normalized = angle % 180
    return any(abs(normalized - (allowed % 180)) <= 1e-6 for allowed in allowed_angles)


def _front_edge(stall: ShapelyPolygon, aisle: ShapelyPolygon, target_width: float) -> LineString | None:
    coords = list(stall.exterior.coords)
    candidates: list[tuple[float, float, float, LineString]] = []
    for start, end in zip(coords, coords[1:]):
        edge = LineString([start, end])
        length = edge.length
        distance = edge.distance(aisle)
        if length <= 1e-9:
            continue
        if distance <= 1e-5 or area_overlaps(edge.buffer(1e-5, cap_style=2), aisle):
            candidates.append((distance, abs(length - target_width), -length, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _maneuver_record(
    context: ManeuverContext,
    stall: ParkingStall,
    rule_id: str,
    rule_status: str,
    envelope_result: dict[str, Any],
) -> dict[str, Any]:
    envelope = envelope_result["envelope"]
    turn_proxy = envelope_result["turn_proxy"]
    record = {
        "stall_id": stall.id,
        "stall_type_id": stall.stall_type_id,
        "served_by_aisle_id": stall.served_by_aisle_id,
        "rule_id": rule_id,
        "rule_status": rule_status,
        "drivable_coverage_ratio": _coverage_ratio(envelope, context.drivable),
        "usable_coverage_ratio": _coverage_ratio(envelope, context.usable),
        "turn_drivable_coverage_ratio": _coverage_ratio(turn_proxy, context.drivable),
        "turn_usable_coverage_ratio": _coverage_ratio(turn_proxy, context.usable),
        "depth": float(envelope_result["access_depth"]),
        "turn_buffer_length": float(envelope_result["turn_buffer_length"]),
    }
    for key in ("base_rule_id", "maneuver_variant", "fallback_from_reason"):
        if key in envelope_result:
            record[key] = envelope_result[key]
    return record


def _maneuver_failure_reason(record: dict[str, Any], envelope_result: dict[str, Any]) -> str | None:
    min_ratio = float(envelope_result["minimum_coverage_ratio"])
    min_turn_ratio = float(envelope_result["minimum_turn_coverage_ratio"])
    if float(record["drivable_coverage_ratio"]) + 1e-9 < min_ratio:
        return "access_envelope_not_in_drivable_aisle"
    if float(record["usable_coverage_ratio"]) + 1e-9 < min_ratio:
        return "access_envelope_hits_boundary_or_obstacle"
    if float(record["turn_drivable_coverage_ratio"]) + 1e-9 < min_turn_ratio:
        return "turning_sweep_not_in_drivable_aisle"
    if float(record["turn_usable_coverage_ratio"]) + 1e-9 < min_turn_ratio:
        return "turning_sweep_hits_boundary_or_obstacle"
    return None


def _perpendicular_l_shape_fallback(
    context: ManeuverContext,
    stall: ParkingStall,
    rule: ManeuverRule,
    envelope_result: dict[str, Any],
    failure_reason: str | None,
) -> dict[str, Any] | None:
    if rule.id != "perpendicular_90_proxy":
        return None
    if failure_reason not in {"turning_sweep_not_in_drivable_aisle", "turning_sweep_hits_boundary_or_obstacle"}:
        return None
    if not _l_shape_fallback_enabled(context.site):
        return None

    edge = envelope_result.get("front_edge")
    stall_polygon = envelope_result.get("stall_polygon")
    aisle = envelope_result.get("serving_aisle")
    if not isinstance(edge, LineString) or not isinstance(stall_polygon, ShapelyPolygon) or not isinstance(aisle, ShapelyPolygon):
        return None

    successful: list[tuple[float, float, dict[str, Any]]] = []
    for side in ("start", "end"):
        turn_proxy = _edge_access_l_shape(
            edge,
            stall_polygon,
            aisle,
            float(envelope_result["access_depth"]),
            float(envelope_result["turn_buffer_length"]),
            side,
        )
        if turn_proxy is None or turn_proxy.area <= 1e-9:
            continue
        candidate = {
            **envelope_result,
            "turn_proxy": turn_proxy,
            "rule_id": "perpendicular_90_l_shape_proxy",
            "base_rule_id": rule.id,
            "maneuver_variant": f"l_shape_{side}",
            "fallback_from_reason": failure_reason,
        }
        record = _maneuver_record(context, stall, "perpendicular_90_l_shape_proxy", rule.status, candidate)
        if _maneuver_failure_reason(record, candidate) is None:
            successful.append(
                (
                    float(record["turn_drivable_coverage_ratio"]),
                    float(record["turn_usable_coverage_ratio"]),
                    candidate,
                )
            )

    if not successful:
        return None
    successful.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return successful[0][2]


def _increment_rule_count(rule_counts: dict[str, int], rule_id: str) -> None:
    rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1


def _edge_access_rectangle(
    edge: LineString,
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
    depth: float,
    extra_along_edge: float,
) -> ShapelyPolygon | None:
    basis = _edge_access_basis(edge, stall, aisle, depth)
    if basis is None:
        return None
    p1, p2, tangent, normal = basis
    extra = max(extra_along_edge, 0.0)
    q1 = (p1[0] - tangent[0] * extra, p1[1] - tangent[1] * extra)
    q2 = (p2[0] + tangent[0] * extra, p2[1] + tangent[1] * extra)
    q3 = (q2[0] + normal[0] * depth, q2[1] + normal[1] * depth)
    q4 = (q1[0] + normal[0] * depth, q1[1] + normal[1] * depth)
    return ShapelyPolygon([q1, q2, q3, q4])


def _edge_access_l_shape(
    edge: LineString,
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
    depth: float,
    extra_along_edge: float,
    side: str,
):
    base = _edge_access_rectangle(edge, stall, aisle, depth, extra_along_edge=0.0)
    if base is None or base.area <= 1e-9:
        return None
    if extra_along_edge <= 1e-9:
        return base

    extension = _edge_access_side_extension(edge, stall, aisle, depth, extra_along_edge, side)
    if extension is None or extension.area <= 1e-9:
        return None
    return unary_union([base, extension])


def _edge_access_side_extension(
    edge: LineString,
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
    depth: float,
    extra_along_edge: float,
    side: str,
) -> ShapelyPolygon | None:
    basis = _edge_access_basis(edge, stall, aisle, depth)
    if basis is None:
        return None
    p1, p2, tangent, normal = basis
    extra = max(extra_along_edge, 0.0)
    if side == "start":
        q1 = (p1[0] - tangent[0] * extra, p1[1] - tangent[1] * extra)
        q2 = p1
    elif side == "end":
        q1 = p2
        q2 = (p2[0] + tangent[0] * extra, p2[1] + tangent[1] * extra)
    else:
        return None
    q3 = (q2[0] + normal[0] * depth, q2[1] + normal[1] * depth)
    q4 = (q1[0] + normal[0] * depth, q1[1] + normal[1] * depth)
    return ShapelyPolygon([q1, q2, q3, q4])


def _edge_access_basis(
    edge: LineString,
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
    depth: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    coords = list(edge.coords)
    if len(coords) < 2 or depth <= 0:
        return None
    p1 = coords[0]
    p2 = coords[-1]
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None

    normals = [(-dy / length, dx / length), (dy / length, -dx / length)]
    midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    aisle_center = aisle.centroid
    stall_center = stall.centroid
    target = (aisle_center.x - midpoint[0], aisle_center.y - midpoint[1])
    away_from_stall = (midpoint[0] - stall_center.x, midpoint[1] - stall_center.y)

    normal = max(
        normals,
        key=lambda item: (
            item[0] * target[0] + item[1] * target[1],
            item[0] * away_from_stall[0] + item[1] * away_from_stall[1],
        ),
    )
    return p1, p2, (dx / length, dy / length), normal


def _coverage_ratio(envelope: ShapelyPolygon, area) -> float:
    if envelope.area <= 1e-9:
        return 0.0
    return min(envelope.intersection(area).area / envelope.area, 1.0)


def _access_depth(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_access_depth", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


def _angled_access_depth(site: SiteSpec, stall_spec: StallSpec) -> float:
    angle = _active_angled_angle(stall_spec)
    default = site.aisle_width * max(math.sin(math.radians(angle)), 0.5)
    raw = site.optimization.get("maneuver_angled_access_depth", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _angled_front_edge_width(stall_spec: StallSpec) -> float:
    angle = _active_angled_angle(stall_spec)
    return stall_spec.width / max(math.sin(math.radians(angle)), 1e-6)


def _turn_buffer_length(site: SiteSpec, stall_spec: StallSpec | None = None) -> float:
    active_stall = stall_spec or site.stall
    default = min(site.aisle_width / 2, active_stall.width)
    if site.vehicle:
        default = min(site.aisle_width / 2, max(site.vehicle.width, active_stall.width) + site.vehicle.swept_path_margin)
    raw = site.optimization.get("maneuver_turn_buffer_length", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _angled_turn_buffer_length(site: SiteSpec, stall_spec: StallSpec) -> float:
    default = min(_turn_buffer_length(site, stall_spec), stall_spec.width / 2)
    raw = site.optimization.get("maneuver_angled_turn_buffer_length", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _parallel_access_depth(site: SiteSpec, stall_spec: StallSpec) -> float:
    # Parallel pull-in needs a lateral strip into the aisle, not full reverse-in depth.
    default = min(site.aisle_width * 0.55, max(stall_spec.width * 1.25, 2.5))
    if site.vehicle:
        default = min(site.aisle_width * 0.65, max(site.vehicle.width + site.vehicle.swept_path_margin + 0.5, default))
    raw = site.optimization.get("maneuver_parallel_access_depth", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _parallel_turn_buffer_length(site: SiteSpec, stall_spec: StallSpec) -> float:
    # Longitudinal room for a conservative parallel-park approach/exit strip.
    # Keep this modest: large buffers clip past aisle ends and over-reject valid stalls.
    default = min(stall_spec.length * 0.2, max(stall_spec.width * 0.5, 1.0))
    if site.vehicle:
        default = min(stall_spec.length * 0.25, max(site.vehicle.width * 0.5, default))
    raw = site.optimization.get("maneuver_parallel_turn_buffer_length", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _minimum_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_access_coverage_ratio", 0.95)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.95
    return min(max(value, 0.0), 1.0)


def _minimum_angled_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_angled_access_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _minimum_parallel_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_parallel_access_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _minimum_turn_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_turn_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _l_shape_fallback_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get("maneuver_l_shape_fallback", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no", "off"}
    return bool(raw)


def _minimum_angled_turn_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_angled_turn_coverage_ratio", _minimum_turn_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_turn_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _minimum_parallel_turn_coverage_ratio(site: SiteSpec) -> float:
    # Slightly looser than full-rectangle reverse-in proxies because the parallel
    # buffer is a longitudinal strip that often clips the aisle endcaps.
    default = min(_minimum_turn_coverage_ratio(site), 0.9)
    raw = site.optimization.get("maneuver_parallel_turn_coverage_ratio", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 0.0), 1.0)


def _active_angled_angle(stall_spec: StallSpec) -> float:
    for angle in stall_spec.allowed_angles:
        normalized = angle % 180
        if 1e-6 < normalized < 180 - 1e-6 and abs(normalized - 90.0) > 1e-6:
            return normalized
    return 60.0


def _vehicle_check_policy(site: SiteSpec) -> VehicleCheckPolicy:
    raw = site.constraints.get("maneuvering", {})
    maneuvering = raw if isinstance(raw, dict) else {}
    require_turning = _boolean_setting(maneuvering.get("require_turning_radius_check", False))
    require_swept = _boolean_setting(maneuvering.get("require_swept_path_check", False))
    configured_limit = maneuvering.get("max_reverse_distance")
    vehicle_limit = site.vehicle.max_reverse_distance if site.vehicle else None
    limits: list[float] = []
    for value in (configured_limit, vehicle_limit):
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return VehicleCheckPolicy(
                require_turning_radius=True,
                require_swept_path=require_swept,
                require_reverse_distance=True,
                maximum_reverse_distance=None,
                configuration_error="invalid_maximum_reverse_distance",
                declared_turning_radius=require_turning,
                declared_swept_path=require_swept,
            )
        if not math.isfinite(parsed) or parsed <= 0.0:
            return VehicleCheckPolicy(
                require_turning_radius=True,
                require_swept_path=require_swept,
                require_reverse_distance=True,
                maximum_reverse_distance=None,
                configuration_error="invalid_maximum_reverse_distance",
                declared_turning_radius=require_turning,
                declared_swept_path=require_swept,
            )
        limits.append(parsed)
    return VehicleCheckPolicy(
        require_turning_radius=require_turning or require_swept or bool(limits),
        require_swept_path=require_swept,
        require_reverse_distance=bool(limits),
        maximum_reverse_distance=min(limits) if limits else None,
        declared_turning_radius=require_turning,
        declared_swept_path=require_swept,
    )


def _validate_vehicle_maneuver(
    context: ManeuverContext,
    stall: ParkingStall,
    policy: VehicleCheckPolicy,
) -> dict[str, Any] | None:
    if not policy.requested:
        return None
    base: dict[str, Any] = {
        "stall_id": stall.id,
        "served_by_aisle_id": stall.served_by_aisle_id,
        "requested": {
            "turning_radius": policy.require_turning_radius,
            "swept_path": policy.require_swept_path,
            "reverse_distance": policy.require_reverse_distance,
        },
        "declared_requests": {
            "turning_radius": policy.declared_turning_radius,
            "swept_path": policy.declared_swept_path,
            "reverse_distance": policy.require_reverse_distance,
        },
        "maximum_reverse_distance": policy.maximum_reverse_distance,
    }
    if policy.configuration_error:
        return {**base, "valid": False, "reason": policy.configuration_error, "rule_id": "vehicle_input_v1"}
    vehicle = context.site.vehicle
    if vehicle is None:
        return {**base, "valid": False, "reason": "design_vehicle_missing", "rule_id": "vehicle_input_v1"}

    configuration = str(vehicle.configuration or "rigid").strip().lower() or "rigid"
    if configuration not in {"rigid", "articulated"} and vehicle.trailer is None:
        return {
            **base,
            "valid": False,
            "reason": "vehicle_configuration_not_supported",
            "rule_id": "vehicle_input_v1",
            "vehicle_configuration": configuration,
        }

    stall_spec = _stall_spec_for_stall(context.site, stall)
    family = stall_spec.family
    is_t_end = stall.aisle_side == "end" or family == "t_end"
    if is_articulated_vehicle(vehicle):
        effective_articulated = replace(vehicle, max_reverse_distance=policy.maximum_reverse_distance)
        if policy.require_swept_path:
            return {
                **base,
                "valid": False,
                "reason": "articulated_vehicle_template_not_supported",
                "rule_id": "vehicle_template_dispatch_v1",
                "stall_family": "t_end" if is_t_end else family,
                "vehicle_class": "articulated",
            }
        return _articulated_analytic_vehicle_check(
            context.site,
            stall_spec,
            effective_articulated,
            base,
            stall_family="t_end" if is_t_end else family,
        )

    supports_exact = is_t_end or (
        (family == "perpendicular" and _angle_supported_for_perpendicular(stall_spec, stall))
        or (family == "angled" and _angle_supported_for_angled(stall_spec, stall))
        or family == "parallel"
    )
    supports_analytic = supports_exact or family == "parallel" or is_t_end
    if not supports_analytic:
        return {
            **base,
            "valid": False,
            "reason": "vehicle_maneuver_template_not_supported_for_stall",
            "rule_id": "vehicle_template_dispatch_v1",
            "stall_family": "t_end" if is_t_end else family,
        }

    effective_vehicle = replace(vehicle, max_reverse_distance=policy.maximum_reverse_distance)
    if policy.require_swept_path:
        if not supports_exact:
            return {
                **base,
                "valid": False,
                "reason": "vehicle_maneuver_template_not_supported_for_stall",
                "rule_id": "vehicle_template_dispatch_v1",
                "stall_family": "t_end" if is_t_end else family,
            }
        return _exact_vehicle_check(
            context,
            stall,
            effective_vehicle,
            base,
            stall_family="t_end" if is_t_end else family,
        )
    if family == "parallel" and not is_t_end:
        return _parallel_analytic_vehicle_check(context.site, stall_spec, effective_vehicle, base)
    if is_t_end:
        return _t_end_analytic_vehicle_check(context.site, stall_spec, effective_vehicle, base)
    if family == "angled":
        return _angled_analytic_vehicle_check(context.site, stall_spec, effective_vehicle, base)
    return _analytic_vehicle_check(context.site, stall_spec, effective_vehicle, base)


def _exact_vehicle_check(
    context: ManeuverContext,
    stall: ParkingStall,
    vehicle: VehicleSpec,
    base: dict[str, Any],
    *,
    stall_family: str,
) -> dict[str, Any]:
    default_rule = {
        "angled": "reverse_in_angled_bicycle_v1",
        "parallel": "parallel_reverse_s_curve_bicycle_v1",
        "t_end": "reverse_in_t_end_bicycle_v1",
    }.get(stall_family, "reverse_in_90_bicycle_v1")
    if not stall.served_by_aisle_id or stall.served_by_aisle_id not in context.aisle_by_id:
        return {
            **base,
            "valid": False,
            "reason": "serving_aisle_missing",
            "rule_id": default_rule,
        }
    sample_step = _positive_setting(context.site.optimization.get("vehicle_swept_sample_step"), 0.35)
    heading_step = _positive_setting(
        context.site.optimization.get("vehicle_swept_heading_step_degrees"),
        2.0,
    )
    crossing = _centerline_crossing_rule(context.site)
    serving = context.aisle_by_id[stall.served_by_aisle_id]
    drivable = context.drivable
    if stall_family == "t_end":
        parts = [serving]
        parent_id = context.parent_aisle_ids.get(stall.served_by_aisle_id)
        if parent_id and parent_id in context.aisle_by_id:
            parts.append(context.aisle_by_id[parent_id])
        drivable = unary_union(parts)
    result = validate_stall_swept_path(
        vehicle,
        stall,
        serving,
        boundary=context.swept_usable,
        drivable_area=drivable,
        centerline_crossing=crossing,
        require_explicit_track_width=True,
        sample_step=sample_step,
        max_heading_step_degrees=heading_step,
        stall_family=stall_family,
    )
    conflicts = []
    if not result.envelope.is_empty:
        conflicts = constraint_conflicts(context.site, result.envelope, "swept_path")
    valid = result.valid and not conflicts
    reason = result.reason
    if result.valid and conflicts:
        reason = "swept_path_hits_site_constraint"
    include_trajectory = _boolean_setting(
        context.site.diagnostics.get("include_vehicle_trajectories", False)
    )
    template_details = ((result.details.get("template") or {}).get("details") or {})
    rule_id = str(template_details.get("template_version") or default_rule)
    return {
        **base,
        "valid": valid,
        "reason": reason,
        "rule_id": rule_id,
        "status": "active_exact",
        "stall_family": stall_family,
        "centerline_crossing": crossing,
        "sample_step": sample_step,
        "max_heading_step_degrees": heading_step,
        "site_constraint_conflicts": conflicts,
        "entry": result.to_record(
            include_trajectory=include_trajectory,
            include_geometry=False,
        ),
        "exit": {
            "valid": valid,
            "method": "time_reverse_of_validated_reverse_in_path",
        },
    }


def _analytic_vehicle_check(
    site: SiteSpec,
    stall_spec: StallSpec,
    vehicle: VehicleSpec,
    base: dict[str, Any],
) -> dict[str, Any]:
    radius = rear_axle_turning_radius(vehicle, require_explicit_track_width=True)
    footprint = resolve_vehicle_overhangs(vehicle)
    reason: str | None = None
    if not radius.valid or radius.rear_axle_radius is None:
        reason = radius.reason or "minimum_turning_radius_unresolved"
    elif not footprint.valid:
        reason = footprint.reason or "vehicle_footprint_invalid"
    elif vehicle.width > stall_spec.width + 1e-9:
        reason = "vehicle_too_wide_for_stall"
    elif vehicle.length > stall_spec.length + 1e-9:
        reason = "vehicle_too_long_for_stall"

    quarter_arc = math.pi * radius.rear_axle_radius / 2.0 if radius.rear_axle_radius else None
    reverse_upper_bound = quarter_arc + stall_spec.length if quarter_arc is not None else None
    if (
        reason is None
        and vehicle.max_reverse_distance is not None
        and reverse_upper_bound is not None
        and reverse_upper_bound > vehicle.max_reverse_distance + 1e-9
    ):
        reason = "maximum_reverse_distance_exceeded_by_conservative_bound"
    return {
        **base,
        "valid": reason is None,
        "reason": reason,
        "rule_id": "turning_radius_analytic_v1",
        "status": "active_conservative",
        "turning_radius_resolution": radius.to_record(),
        "footprint_resolution": footprint.to_record(),
        "quarter_turn_arc_distance": quarter_arc,
        "reverse_distance_upper_bound": reverse_upper_bound,
        "centerline_crossing": _centerline_crossing_rule(site),
        "centerline_check": "requires_swept_path_check",
        "aisle_end_check": "covered_by_access_proxy_and_turnaround_graph",
    }


def _parallel_analytic_vehicle_check(
    site: SiteSpec,
    stall_spec: StallSpec,
    vehicle: VehicleSpec,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Conservative parallel vehicle audit: fit + reverse bound, no swept path.

    Parallel reverse distance is bounded by a simple multi-point-style estimate
    (vehicle length + stall overhang allowance), not a reverse-in quarter arc.
    """
    radius = rear_axle_turning_radius(vehicle, require_explicit_track_width=True)
    footprint = resolve_vehicle_overhangs(vehicle)
    reason: str | None = None
    if not radius.valid or radius.rear_axle_radius is None:
        reason = radius.reason or "minimum_turning_radius_unresolved"
    elif not footprint.valid:
        reason = footprint.reason or "vehicle_footprint_invalid"
    elif vehicle.width > stall_spec.width + 1e-9:
        reason = "vehicle_too_wide_for_stall"
    elif vehicle.length > stall_spec.length + 1e-9:
        reason = "vehicle_too_long_for_stall"

    # Parallel park typically reverse-aligns within roughly one vehicle length plus
    # a short correction; keep a conservative multiple of vehicle length.
    reverse_upper_bound = vehicle.length * 1.25 if vehicle.length > 0 else None
    if (
        reason is None
        and vehicle.max_reverse_distance is not None
        and reverse_upper_bound is not None
        and reverse_upper_bound > vehicle.max_reverse_distance + 1e-9
    ):
        reason = "maximum_reverse_distance_exceeded_by_conservative_bound"
    return {
        **base,
        "valid": reason is None,
        "reason": reason,
        "rule_id": "parallel_vehicle_analytic_v1",
        "status": "active_conservative",
        "stall_family": "parallel",
        "turning_radius_resolution": radius.to_record(),
        "footprint_resolution": footprint.to_record(),
        "reverse_distance_upper_bound": reverse_upper_bound,
        "reverse_model": "parallel_multi_point_proxy_length_bound",
        "centerline_crossing": _centerline_crossing_rule(site),
        "centerline_check": "requires_swept_path_check",
        "path_template": "analytic fit and reverse bound; exact S-curve available when swept_path is requested",
    }


def _angled_analytic_vehicle_check(
    site: SiteSpec,
    stall_spec: StallSpec,
    vehicle: VehicleSpec,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Conservative angled reverse-in bound: scaled arc plus stall length."""
    record = _analytic_vehicle_check(site, stall_spec, vehicle, base)
    angle = angled_module_angle(stall_spec.allowed_angles)
    radius = record.get("turning_radius_resolution", {})
    rear_radius = radius.get("rear_axle_radius") if isinstance(radius, dict) else None
    arc = (
        math.radians(angle) * float(rear_radius)
        if angle is not None and isinstance(rear_radius, int | float)
        else None
    )
    reverse_upper_bound = None if arc is None else arc + stall_spec.length
    reason = record.get("reason")
    if reason == "maximum_reverse_distance_exceeded_by_conservative_bound":
        reason = None
    if (
        reason is None
        and vehicle.max_reverse_distance is not None
        and reverse_upper_bound is not None
        and reverse_upper_bound > vehicle.max_reverse_distance + 1e-9
    ):
        reason = "maximum_reverse_distance_exceeded_by_conservative_bound"
    return {
        **record,
        "valid": reason is None,
        "reason": reason,
        "rule_id": "angled_vehicle_analytic_v1",
        "stall_family": "angled",
        "stall_angle_degrees": angle,
        "quarter_turn_arc_distance": arc,
        "reverse_distance_upper_bound": reverse_upper_bound,
        "path_template": "analytic reverse-in bound; exact template available when swept_path is requested",
    }


def _t_end_analytic_vehicle_check(
    site: SiteSpec,
    stall_spec: StallSpec,
    vehicle: VehicleSpec,
    base: dict[str, Any],
) -> dict[str, Any]:
    """T-end uses the same conservative reverse-in bound as perpendicular-90."""
    record = _analytic_vehicle_check(site, stall_spec, vehicle, base)
    return {
        **record,
        "rule_id": "t_end_vehicle_analytic_v1",
        "stall_family": "t_end",
        "path_template": "analytic reverse-in bound; exact template available when swept_path is requested",
    }


def _articulated_analytic_vehicle_check(
    site: SiteSpec,
    stall_spec: StallSpec,
    vehicle: VehicleSpec,
    base: dict[str, Any],
    *,
    stall_family: str,
) -> dict[str, Any]:
    """Conservative articulated audit: combination fit, off-tracking, reverse bound.

    Exact bicycle stall templates are never used. This bound does not prove a
    spatial tractor-trailer path.
    """
    geometry = resolve_articulated_geometry(vehicle)
    radius = rear_axle_turning_radius(vehicle, require_explicit_track_width=True)
    off_tracking = articulated_off_tracking(
        vehicle,
        aisle_width=site.aisle_width,
        require_explicit_track_width=True,
    )
    reason: str | None = None
    if not geometry.valid:
        reason = geometry.reason or "articulated_vehicle_geometry_incomplete"
    elif not radius.valid or radius.rear_axle_radius is None:
        reason = radius.reason or "minimum_turning_radius_unresolved"
    elif geometry.combination_width is not None and geometry.combination_width > stall_spec.width + 1e-9:
        reason = "vehicle_too_wide_for_stall"
    elif geometry.combination_length is not None and geometry.combination_length > stall_spec.length + 1e-9:
        reason = "vehicle_too_long_for_stall"
    elif not off_tracking.valid:
        reason = off_tracking.reason or "articulated_off_tracking_unresolved"

    reverse_upper_bound = None
    if radius.rear_axle_radius is not None and geometry.trailer_length is not None:
        reverse_upper_bound = math.pi * radius.rear_axle_radius / 2.0 + geometry.trailer_length
    if (
        reason is None
        and vehicle.max_reverse_distance is not None
        and reverse_upper_bound is not None
        and reverse_upper_bound > vehicle.max_reverse_distance + 1e-9
    ):
        reason = "maximum_reverse_distance_exceeded_by_conservative_bound"
    return {
        **base,
        "valid": reason is None,
        "reason": reason,
        "rule_id": "articulated_vehicle_analytic_v1",
        "status": "active_conservative",
        "stall_family": stall_family,
        "vehicle_class": "articulated",
        "turning_radius_resolution": radius.to_record(),
        "articulated_geometry": geometry.to_record(),
        "articulated_off_tracking": off_tracking.to_record(),
        "reverse_distance_upper_bound": reverse_upper_bound,
        "reverse_model": "tractor_quarter_arc_plus_trailer_length",
        "centerline_crossing": _centerline_crossing_rule(site),
        "centerline_check": "requires_swept_path_check",
        "path_template": (
            "conservative combination fit, steady-state trailer off-tracking, and tractor-arc-plus-trailer "
            "reverse bound; no exact articulated swept-path template"
        ),
    }


def _vehicle_validation_summary(
    site: SiteSpec,
    policy: VehicleCheckPolicy,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    invalid = [item for item in checks if not item.get("valid", False)]
    global_reason = policy.configuration_error
    if policy.requested and site.vehicle is None:
        global_reason = "design_vehicle_missing"
    elif policy.requested and not checks and global_reason is None:
        global_reason = "no_vehicle_checks_executed"
    valid = not invalid and global_reason is None
    vehicle = site.vehicle
    return {
        "version": "v0.3-vehicle-maneuver-1",
        "valid": valid,
        "status": "not_requested" if not policy.requested else ("active" if valid else "active_failed"),
        "reason": global_reason,
        "requested": {
            "turning_radius": policy.require_turning_radius,
            "swept_path": policy.require_swept_path,
            "reverse_distance": policy.require_reverse_distance,
        },
        "declared_requests": {
            "turning_radius": policy.declared_turning_radius,
            "swept_path": policy.declared_swept_path,
            "reverse_distance": policy.require_reverse_distance,
        },
        "design_vehicle": _vehicle_spec_record(vehicle),
        "maximum_reverse_distance": policy.maximum_reverse_distance,
        "checked_stalls": len(checks),
        "invalid_stall_count": len(invalid),
        "invalid_stall_ids": [str(item.get("stall_id")) for item in invalid],
        "checks": checks,
    }


def _vehicle_spec_record(vehicle: VehicleSpec | None) -> dict[str, Any] | None:
    if vehicle is None:
        return None
    trailer = None
    if vehicle.trailer is not None:
        trailer = {
            "length": vehicle.trailer.length,
            "width": vehicle.trailer.width,
            "wheelbase": vehicle.trailer.wheelbase,
            "track_width": vehicle.trailer.track_width,
            "front_overhang": vehicle.trailer.front_overhang,
            "rear_overhang": vehicle.trailer.rear_overhang,
        }
    return {
        "id": vehicle.id,
        "length": vehicle.length,
        "width": vehicle.width,
        "wheelbase": vehicle.wheelbase,
        "min_turning_radius": vehicle.min_turning_radius,
        "turning_radius_reference": vehicle.turning_radius_reference,
        "track_width": vehicle.track_width,
        "front_overhang": vehicle.front_overhang,
        "rear_overhang": vehicle.rear_overhang,
        "swept_path_margin": vehicle.swept_path_margin,
        "max_reverse_distance": vehicle.max_reverse_distance,
        "configuration": vehicle.configuration,
        "hitch_offset": vehicle.hitch_offset,
        "trailer": trailer,
        "articulated": is_articulated_vehicle(vehicle),
    }


def _centerline_crossing_rule(site: SiteSpec) -> str:
    aisle_class = fixed_aisle_class(site)
    if aisle_class is None or aisle_class.centerline_crossing == "not_applicable":
        return "allowed"
    return aisle_class.centerline_crossing


def _boolean_setting(raw: object) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "false", "0", "no", "off"}
    return bool(raw)


def _positive_setting(raw: object, default: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0.0 else default


def _renumber_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=polygon_points(ShapelyPolygon(stall.polygon)),
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=stall.stall_type_id,
        )
        for index, stall in enumerate(stalls, start=1)
    ]


def _stall_spec_for_stall(site: SiteSpec, stall: ParkingStall) -> StallSpec:
    active = {spec.id: spec for spec in _active_stall_specs(site)}
    if stall.stall_type_id and stall.stall_type_id in active:
        return active[stall.stall_type_id]
    return site.stall


def _active_stall_specs(site: SiteSpec) -> tuple[StallSpec, ...]:
    specs: list[StallSpec] = []
    for spec in (site.main_stall, site.branch_stall, site.stall, *site.stall_candidates):
        if spec is not None and spec.id not in {item.id for item in specs}:
            specs.append(spec)
    return tuple(specs)
