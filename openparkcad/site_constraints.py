from __future__ import annotations

import math
from dataclasses import dataclass
from re import split
from typing import Any, Literal

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openparkcad.models import EntranceSpec, LayoutResult, ParkingStall, SiteAreaSpec, SiteSpec, StallSpec

ConstraintPurpose = Literal["all", "stall", "aisle", "swept_path"]
_CONCRETE_PURPOSES = frozenset({"stall", "aisle", "swept_path"})
_ADVISORY_PRIORITIES = frozenset({"advisory", "draw_only", "future", "soft"})
_AUTHORITY_TIERS = ("advisory", "project_policy", "jurisdictional")
_AREA_TOLERANCE = 1e-6
DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE = 1.5
DEFAULT_EMERGENCY_ROUTE_TOUCH_TOLERANCE = 1.0
DEFAULT_EV_CHARGER_TOUCH_TOLERANCE = 2.0
_EMERGENCY_ROUTE_SOURCES = frozenset({"fire_lane", "access_route", "emergency_access_route"})
_CHARGER_KINDS = frozenset({"charging_post", "ev_charger", "charger"})
_RESERVED_ACCESSIBLE_CONNECT_TOKENS = frozenset(
    {
        "accessible-stalls",
        "accessible_stalls",
        "accessible-stall",
        "accessible_stall",
        "stalls",
        "stall",
        "parking",
    }
)


@dataclass(frozen=True)
class ConstraintGeometry:
    id: str
    source: str
    kind: str
    base_geometry: BaseGeometry
    exclusion_geometry: BaseGeometry
    clearance: float
    purposes: frozenset[str]
    affects: tuple[str, ...] = ()
    authority: str = "project_policy"
    priority: str = "hard"
    required: bool = False

    @property
    def hard(self) -> bool:
        return (
            self.authority.strip().lower() != "advisory"
            and self.priority.strip().lower() not in _ADVISORY_PRIORITIES
        )


def declared_constraint_geometries(
    site: SiteSpec,
    *,
    include_advisory: bool = False,
) -> list[ConstraintGeometry]:
    """Normalize input declarations into deterministic hard-exclusion geometry.

    Purposes are explicit: ``stall`` is marked-space placement, ``aisle`` is
    ordinary circulation geometry, and ``swept_path`` is vehicle maneuver
    clearance. ``all`` is accepted by query helpers and means the union of all
    three scopes.
    """

    declarations: list[ConstraintGeometry] = []
    declarations.extend(_obstacle_geometries(site))
    declarations.extend(_reserved_area_geometries(site))
    declarations.extend(_site_feature_geometries(site))
    declarations.extend(_route_geometries(site))
    if include_advisory:
        return declarations
    return [item for item in declarations if item.hard]


def site_exclusion_geometry(site: SiteSpec, purpose: ConstraintPurpose = "all") -> BaseGeometry:
    purpose = _normalize_purpose(purpose)
    geometries = [
        item.exclusion_geometry
        for item in declared_constraint_geometries(site)
        if _matches_purpose(item, purpose)
    ]
    return unary_union(geometries) if geometries else GeometryCollection()


def site_usable_area(site: SiteSpec, purpose: ConstraintPurpose = "all") -> BaseGeometry:
    boundary = _valid_polygon(site.boundary, "site.boundary")
    usable = boundary.buffer(-site.margin, join_style="mitre")
    exclusions = site_exclusion_geometry(site, purpose)
    return usable.difference(exclusions) if not exclusions.is_empty else usable


def constraint_conflicts(
    site: SiteSpec,
    geometry: BaseGeometry,
    purpose: ConstraintPurpose,
) -> list[dict[str, Any]]:
    purpose = _normalize_purpose(purpose)
    conflicts: list[dict[str, Any]] = []
    for item in declared_constraint_geometries(site):
        if not _matches_purpose(item, purpose):
            continue
        overlap_area = geometry.intersection(item.exclusion_geometry).area
        if overlap_area <= _AREA_TOLERANCE:
            continue
        conflicts.append(
            {
                "constraint_id": item.id,
                "source": item.source,
                "kind": item.kind,
                "purpose": purpose,
                "overlap_area": float(overlap_area),
                "clearance": item.clearance,
                "authority": item.authority,
                "priority": item.priority,
            }
        )
    return conflicts


def validate_site_constraints(layout: LayoutResult) -> dict[str, Any]:
    errors: list[str] = []
    definition_report = validate_site_constraint_definitions(layout.site)
    errors.extend(definition_report["errors"])

    stall_conflicts = _layout_object_conflicts(layout, "stall") if definition_report["valid"] else []
    aisle_conflicts = _layout_object_conflicts(layout, "aisle") if definition_report["valid"] else []
    for conflict in [*stall_conflicts, *aisle_conflicts]:
        errors.append(str(conflict["message"]))

    quota_report = validate_parking_quotas(layout)
    errors.extend(str(item) for item in quota_report["errors"])
    usability_report = validate_route_usability(layout)
    errors.extend(str(item) for item in usability_report["errors"])
    active_constraints: list[ConstraintGeometry] = []
    all_constraints: list[ConstraintGeometry] = []
    if definition_report["valid"]:
        active_constraints = declared_constraint_geometries(layout.site)
        all_constraints = declared_constraint_geometries(layout.site, include_advisory=True)
    else:
        try:
            all_constraints = declared_constraint_geometries(layout.site, include_advisory=True)
        except ValueError:
            pass

    return {
        "version": "v0.3-site-constraints",
        "valid": not errors,
        "errors": errors,
        "scope": {
            "stall": "marked stall footprints",
            "aisle": "ordinary vehicle circulation footprints",
            "swept_path": "vehicle maneuver and swept-path footprints",
        },
        "active_constraint_count": len(active_constraints),
        "active_constraint_ids": [item.id for item in active_constraints],
        "required_constraint_ids": [item.id for item in all_constraints if item.required],
        "authority": _authority_summary(layout.site, all_constraints),
        "definition_validation": definition_report,
        "conflicts": {
            "stalls": stall_conflicts,
            "aisles": aisle_conflicts,
        },
        "quota": quota_report,
        "route_usability": usability_report,
    }


def validate_site_constraint_definitions(site: SiteSpec) -> dict[str, Any]:
    errors: list[str] = []
    boundary: BaseGeometry | None = None
    try:
        boundary = _valid_polygon(site.boundary, "site.boundary")
    except ValueError as exc:
        errors.append(str(exc))

    declarations: list[ConstraintGeometry] = []
    try:
        declarations = declared_constraint_geometries(site, include_advisory=True)
    except ValueError as exc:
        errors.append(str(exc))

    if boundary is not None:
        for item in declarations:
            if not item.hard:
                continue
            if not boundary.buffer(_AREA_TOLERANCE).covers(item.base_geometry):
                errors.append(
                    f"{item.source} '{item.id}' extends outside the site boundary"
                )

    invalid_authorities = sorted(
        {
            item.authority
            for item in declarations
            if item.authority.strip().lower() not in _AUTHORITY_TIERS
        }
    )
    if invalid_authorities:
        errors.append(
            "unsupported constraint authority: " + ", ".join(invalid_authorities)
        )
    if any(item.authority.strip().lower() == "jurisdictional" for item in declarations):
        metadata = _jurisdictional_metadata_status(site)
        if not metadata["valid"]:
            errors.append(
                "jurisdictional constraints need standards metadata: "
                + ", ".join(metadata["missing"])
            )

    pedestrian_data = site.pedestrian_and_emergency
    errors.extend(_missing_required_route_geometry_errors(pedestrian_data))
    if pedestrian_data.get("emergency_access_required", False) and not _has_hard_route_geometry(
        pedestrian_data,
        ("fire_lanes", "access_routes", "emergency_access_routes"),
    ):
        errors.append(
            "pedestrian_and_emergency.emergency_access_required needs a hard fire/access route geometry"
        )
    if site.parking_quotas.get("accessible_min", 0) > 0 and not _has_hard_route_geometry(
        pedestrian_data,
        ("accessible_routes",),
    ):
        errors.append("parking.quotas.accessible_min needs a hard accessible route geometry")

    return {
        "valid": not errors,
        "errors": errors,
        "declared_count": len(declarations),
        "hard_count": sum(1 for item in declarations if item.hard),
        "authority": _authority_summary(site, declarations),
    }


def validate_parking_quotas(layout: LayoutResult) -> dict[str, Any]:
    requested = {
        "accessible_min": int(layout.site.parking_quotas.get("accessible_min", 0)),
        "ev_min": int(layout.site.parking_quotas.get("ev_min", 0)),
    }
    recognized_keys = set(requested)
    errors = [
        f"parking quota '{key}' is not enforceable in v0.3"
        for key, value in layout.site.parking_quotas.items()
        if key not in recognized_keys and value > 0
    ]

    actual = {"accessible": 0, "ev": 0}
    matching_stall_ids: dict[str, list[str]] = {"accessible": [], "ev": []}
    specs = _stall_specs_by_id(layout.site)
    for stall in layout.stalls:
        stall_type_id = stall.stall_type_id or layout.site.stall.id
        spec = specs.get(stall_type_id)
        capabilities = stall_type_capabilities(spec) if spec else frozenset()
        for capability in actual:
            if capability in capabilities:
                actual[capability] += 1
                matching_stall_ids[capability].append(stall.id)

    shortfall = {
        "accessible": max(0, requested["accessible_min"] - actual["accessible"]),
        "ev": max(0, requested["ev_min"] - actual["ev"]),
    }
    if shortfall["accessible"]:
        errors.append(
            "accessible parking quota requires "
            f"{requested['accessible_min']} stalls, layout provides {actual['accessible']}"
        )
    if shortfall["ev"]:
        errors.append(
            f"EV parking quota requires {requested['ev_min']} stalls, layout provides {actual['ev']}"
        )

    return {
        "valid": not errors,
        "required": requested,
        "actual": actual,
        "shortfall": shortfall,
        "matching_stall_ids": matching_stall_ids,
        "errors": errors,
        "assignment_mode": "count_explicit_stall_type_classifications",
    }


DEFAULT_ACCESSIBLE_CONTACT_WEIGHT = 100.0
DEFAULT_EV_CONTACT_WEIGHT = 100.0


def classified_contact_adjustment(
    site: SiteSpec,
    stall_type_id: str | None,
    stall_polygons: list,
) -> tuple[float, str | None]:
    """Per-stall shadow bonus, or a rejection reason if classified contact fails."""
    spec = _stall_specs_by_id(site).get(stall_type_id or site.stall.id)
    capabilities = stall_type_capabilities(spec)
    extra = 0.0
    if "accessible" in capabilities and int(site.parking_quotas.get("accessible_min", 0) or 0) > 0:
        routes = _hard_routes(site, {"accessible_route"})
        if routes:
            tolerance, error = _constraint_tolerance(
                site, "accessible_route_touch_tolerance", DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE
            )
            if error or tolerance is None:
                return 0.0, error
            if not _polygons_all_reach(stall_polygons, routes, tolerance):
                return 0.0, "module_does_not_reach_accessible_route"
            extra += _contact_weight(site, "accessible_contact", DEFAULT_ACCESSIBLE_CONTACT_WEIGHT)
    if "ev" in capabilities and int(site.parking_quotas.get("ev_min", 0) or 0) > 0:
        chargers = _hard_charger_features(site)
        if chargers:
            tolerance, error = _constraint_tolerance(
                site, "ev_charger_touch_tolerance", DEFAULT_EV_CHARGER_TOUCH_TOLERANCE
            )
            if error or tolerance is None:
                return 0.0, error
            if not _polygons_all_reach(stall_polygons, chargers, tolerance):
                return 0.0, "module_does_not_reach_charger"
            extra += _contact_weight(site, "ev_contact", DEFAULT_EV_CONTACT_WEIGHT)
    return extra, None


def _contact_weight(site: SiteSpec, key: str, default: float) -> float:
    overrides = site.optimization.get("weights", {})
    if not isinstance(overrides, dict) or key not in overrides:
        return default
    try:
        return float(overrides[key])
    except (TypeError, ValueError):
        return default


def _polygons_all_reach(polygons: list, targets: list[ConstraintGeometry], tolerance: float) -> bool:
    if not polygons:
        return False
    for polygon in polygons:
        geometry = ShapelyPolygon(polygon) if not hasattr(polygon, "geom_type") else polygon
        if not _geometry_reaches_routes(geometry, targets, tolerance):
            return False
    return True


def apply_contact_filter(layout: LayoutResult) -> LayoutResult:
    """Drop classified accessible/EV stalls that cannot reach required contact geometry.

    Runs on official and preview layouts before site-constraint validation so
    quota and usability see only reachable stalls. Not a mix-type placer.
    """
    drop_ids: list[str] = []
    specs = _stall_specs_by_id(layout.site)
    accessible_needed = int(layout.site.parking_quotas.get("accessible_min", 0) or 0) > 0
    ev_needed = int(layout.site.parking_quotas.get("ev_min", 0) or 0) > 0
    routes = _hard_routes(layout.site, {"accessible_route"}) if accessible_needed else []
    chargers = _hard_charger_features(layout.site) if ev_needed else []
    acc_tol, acc_err = _constraint_tolerance(
        layout.site, "accessible_route_touch_tolerance", DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE
    )
    ev_tol, ev_err = _constraint_tolerance(
        layout.site, "ev_charger_touch_tolerance", DEFAULT_EV_CHARGER_TOUCH_TOLERANCE
    )
    if acc_err or ev_err:
        return layout
    for stall in layout.stalls:
        spec = specs.get(stall.stall_type_id or layout.site.stall.id)
        capabilities = stall_type_capabilities(spec)
        if accessible_needed and routes and "accessible" in capabilities:
            if acc_tol is None or not _geometry_reaches_routes(ShapelyPolygon(stall.polygon), routes, acc_tol):
                drop_ids.append(stall.id)
                continue
        if ev_needed and chargers and "ev" in capabilities:
            if ev_tol is None or not _geometry_reaches_routes(ShapelyPolygon(stall.polygon), chargers, ev_tol):
                drop_ids.append(stall.id)
    if not drop_ids:
        return layout
    drop_set = set(drop_ids)
    kept = [stall for stall in layout.stalls if stall.id not in drop_set]
    object.__setattr__(layout, "stalls", _renumber_contact_stalls(kept))
    return layout


def _renumber_contact_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
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


def validate_route_usability(layout: LayoutResult) -> dict[str, Any]:
    """Geometric stall-to-route and route-to-entrance contact. Not slope/width/ADA."""

    accessible = _accessible_route_usability(layout)
    continuity = _accessible_route_continuity(layout, accessible)
    dimensions = _accessible_route_dimensions(layout.site)
    emergency = _emergency_access_connectivity(layout)
    ev_charger = _ev_charger_usability(layout)
    errors = (
        list(accessible.get("errors", []))
        + list(continuity.get("errors", []))
        + list(dimensions.get("errors", []))
        + list(emergency.get("errors", []))
        + list(ev_charger.get("errors", []))
    )
    return {
        "version": "v0.4-route-usability-4",
        "valid": not errors,
        "errors": errors,
        "accessible_route": accessible,
        "accessible_route_continuity": continuity,
        "accessible_route_dimensions": dimensions,
        "emergency_access": emergency,
        "ev_charger": ev_charger,
        "scope": (
            "project-policy geometric contact between classified accessible stalls and hard "
            "accessible routes, connected accessible-route pieces and declared connects "
            "destinations, polyline_buffer min_width versus declared width, hard fire/access "
            "routes and entrance gates, and classified EV stalls and placed charging posts; "
            "declared max_slope fail-closes because elevation is not modeled. Not ADA, "
            "apparatus, or electrical certification"
        ),
    }


def _accessible_route_usability(layout: LayoutResult) -> dict[str, Any]:
    requested = int(layout.site.parking_quotas.get("accessible_min", 0)) > 0
    tolerance, config_error = _constraint_tolerance(
        layout.site,
        "accessible_route_touch_tolerance",
        DEFAULT_ACCESSIBLE_ROUTE_TOUCH_TOLERANCE,
    )
    quota = validate_parking_quotas(layout)
    stall_ids = list(quota.get("matching_stall_ids", {}).get("accessible", []))
    routes = _hard_routes(layout.site, {"accessible_route"})
    base = {
        "requested": requested,
        "touch_tolerance": tolerance,
        "route_ids": [item.id for item in routes],
        "checked_stall_ids": stall_ids,
        "reachable_stall_ids": [],
        "unreachable_stall_ids": [],
        "errors": [],
        "reason": None,
    }
    if config_error:
        return {
            **base,
            "status": "active_failed",
            "reason": config_error,
            "errors": [config_error],
        }
    if not requested:
        return {**base, "status": "not_requested"}
    if not routes:
        return {
            **base,
            "status": "active_failed",
            "reason": "accessible_route_geometry_missing",
            "errors": [],
        }
    stall_by_id = {stall.id: stall for stall in layout.stalls}
    reachable: list[str] = []
    unreachable: list[str] = []
    assert tolerance is not None
    for stall_id in stall_ids:
        stall = stall_by_id.get(stall_id)
        if stall is None or not _geometry_reaches_routes(ShapelyPolygon(stall.polygon), routes, tolerance):
            unreachable.append(stall_id)
        else:
            reachable.append(stall_id)
    errors: list[str] = []
    reason = None
    if unreachable:
        reason = "accessible_stall_does_not_reach_accessible_route"
        errors.append(
            "accessible stalls do not reach a hard accessible route within "
            f"{tolerance} m: {', '.join(unreachable)}"
        )
    return {
        **base,
        "status": "active_failed" if errors else "active",
        "reachable_stall_ids": reachable,
        "unreachable_stall_ids": unreachable,
        "reason": reason,
        "errors": errors,
    }


def _accessible_route_continuity(
    layout: LayoutResult,
    stall_usability: dict[str, Any],
) -> dict[str, Any]:
    """Connected accessible-route pieces and declared connects destinations.

    Not slope, width, or a pedestrian graph through the aisle network.
    """
    requested = bool(stall_usability.get("requested"))
    tolerance = stall_usability.get("touch_tolerance")
    routes = _hard_routes(layout.site, {"accessible_route"})
    connects = _accessible_route_connect_tokens(layout.site)
    base = {
        "requested": requested,
        "touch_tolerance": tolerance,
        "route_ids": [item.id for item in routes],
        "connects": connects,
        "components": [],
        "serving_route_ids": [],
        "destination_ids": [],
        "missing_connect_ids": [],
        "unreached_destination_ids": [],
        "errors": [],
        "reason": None,
    }
    if stall_usability.get("status") == "active_failed" and stall_usability.get("reason", "").startswith("invalid_"):
        return {**base, "status": "active_failed", "reason": stall_usability.get("reason"), "errors": []}
    if not requested:
        return {**base, "status": "not_requested"}
    if not routes:
        return {
            **base,
            "status": "active_failed",
            "reason": "accessible_route_geometry_missing",
            "errors": [],
        }
    if not isinstance(tolerance, int | float):
        return {**base, "status": "active_failed", "reason": "invalid_accessible_route_touch_tolerance", "errors": []}

    components = _route_components(routes, float(tolerance))
    stall_by_id = {stall.id: stall for stall in layout.stalls}
    serving_ids: list[str] = []
    for stall_id in stall_usability.get("reachable_stall_ids", []):
        stall = stall_by_id.get(str(stall_id))
        if stall is None:
            continue
        for route in routes:
            if route.id in serving_ids:
                continue
            if _geometry_reaches_geometry(ShapelyPolygon(stall.polygon), route.base_geometry, float(tolerance)):
                serving_ids.append(route.id)
    serving_component_keys = {
        index
        for index, component in enumerate(components)
        if any(route_id in component for route_id in serving_ids)
    }
    destinations, missing = _resolve_accessible_connect_targets(layout.site, connects)
    serving_routes = [item for item in routes if item.id in serving_ids] or list(routes)
    unreached = [
        dest_id
        for dest_id, dest_geom in destinations
        if not any(_geometry_reaches_geometry(route.base_geometry, dest_geom, float(tolerance)) for route in serving_routes)
    ]
    errors: list[str] = []
    reason = None
    if missing:
        reason = "accessible_route_connects_target_missing"
        errors.append("accessible_routes.connects names unknown geometry: " + ", ".join(missing))
    elif len(serving_component_keys) > 1:
        reason = "accessible_route_network_disconnected"
        errors.append(
            "classified accessible stalls reach disconnected accessible-route pieces: "
            + "; ".join(",".join(components[index]) for index in sorted(serving_component_keys))
        )
    elif unreached:
        reason = "accessible_route_does_not_reach_destination"
        errors.append(
            "accessible-route network does not reach declared connects destinations within "
            f"{tolerance} m: {', '.join(unreached)}"
        )
    return {
        **base,
        "status": "active_failed" if errors else "active",
        "components": components,
        "serving_route_ids": serving_ids,
        "destination_ids": [item[0] for item in destinations],
        "missing_connect_ids": missing,
        "unreached_destination_ids": unreached,
        "reason": reason,
        "errors": errors,
    }


def _accessible_route_connect_tokens(site: SiteSpec) -> list[str]:
    tokens: list[str] = []
    raw_items = site.pedestrian_and_emergency.get("accessible_routes", [])
    if not isinstance(raw_items, list):
        return tokens
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw = item.get("connects", [])
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, list | tuple):
            values = list(raw)
        else:
            continue
        for value in values:
            token = str(value).strip()
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def _resolve_accessible_connect_targets(
    site: SiteSpec,
    tokens: list[str],
) -> tuple[list[tuple[str, BaseGeometry]], list[str]]:
    destinations: list[tuple[str, BaseGeometry]] = []
    missing: list[str] = []
    for token in tokens:
        if _normalize_connect_token(token) in _RESERVED_ACCESSIBLE_CONNECT_TOKENS:
            continue
        geometry = _named_site_geometry(site, token)
        if geometry is None or geometry.is_empty:
            missing.append(token)
        else:
            destinations.append((token, geometry))
    return destinations, missing


def _normalize_connect_token(token: str) -> str:
    return token.strip().lower().replace(" ", "-").replace("_", "-")


def _named_site_geometry(site: SiteSpec, token: str) -> BaseGeometry | None:
    for feature in site.site_features:
        if isinstance(feature, dict) and str(feature.get("id", "")) == token and "geometry" in feature:
            try:
                return _declared_geometry(feature.get("geometry"), f"site_features[{token}].geometry")
            except ValueError:
                return None
    for spec in (*site.reserved_areas, *site.obstacle_specs):
        if spec.id == token:
            try:
                return _declared_geometry(spec.geometry, f"{spec.id}.geometry")
            except ValueError:
                return None
    for entrance in site.entrances:
        if entrance.id == token:
            return _entrance_gate_geometry(entrance)
    for source in ("accessible_route", "pedestrian_route", "fire_lane", "access_route", "emergency_access_route"):
        for route in _hard_routes(site, {source}):
            if route.id == token:
                return route.base_geometry
    return None


def _route_components(routes: list[ConstraintGeometry], tolerance: float) -> list[list[str]]:
    parent = list(range(len(routes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, start in enumerate(routes):
        for right in range(left + 1, len(routes)):
            if _geometry_reaches_geometry(start.base_geometry, routes[right].base_geometry, tolerance):
                root_left = find(left)
                root_right = find(right)
                if root_left != root_right:
                    parent[root_right] = root_left
    groups: dict[int, list[str]] = {}
    for index, route in enumerate(routes):
        groups.setdefault(find(index), []).append(route.id)
    return [groups[key] for key in sorted(groups)]


def _accessible_route_dimensions(site: SiteSpec) -> dict[str, Any]:
    """Audit declared min_width; fail-closed on max_slope (no elevation model)."""
    items = _hard_accessible_route_items(site)
    requested = any("min_width" in item or "max_slope" in item for item in items)
    base = {
        "requested": requested,
        "checked_route_ids": [],
        "min_width_ok_ids": [],
        "too_narrow_ids": [],
        "width_unresolved_ids": [],
        "slope_declared_ids": [],
        "errors": [],
        "reason": None,
    }
    if not requested:
        return {**base, "status": "not_requested"}
    errors: list[str] = []
    reason: str | None = None
    for item in items:
        route_id = str(item.get("id") or "accessible-route")
        if "min_width" in item or "max_slope" in item:
            base["checked_route_ids"].append(route_id)
        if "min_width" in item:
            min_width, min_error = _positive_route_number(item.get("min_width"), "min_width")
            if min_error:
                reason = reason or min_error
                errors.append(f"accessible route '{route_id}' has {min_error}")
            else:
                width, width_error = _declared_polyline_width(item)
                if width_error:
                    base["width_unresolved_ids"].append(route_id)
                    reason = reason or width_error
                    errors.append(f"accessible route '{route_id}' {width_error}")
                elif width is not None and min_width is not None and width + 1e-9 < min_width:
                    base["too_narrow_ids"].append(route_id)
                    reason = reason or "accessible_route_narrower_than_min_width"
                    errors.append(
                        f"accessible route '{route_id}' width {width} m is below min_width {min_width} m"
                    )
                else:
                    base["min_width_ok_ids"].append(route_id)
        if "max_slope" in item:
            _, slope_error = _nonnegative_route_number(item.get("max_slope"), "max_slope")
            if slope_error:
                reason = reason or slope_error
                errors.append(f"accessible route '{route_id}' has {slope_error}")
            else:
                base["slope_declared_ids"].append(route_id)
                reason = reason or "accessible_route_slope_check_unsupported"
                errors.append(
                    f"accessible route '{route_id}' declares max_slope but elevation is not modeled"
                )
    return {
        **base,
        "status": "active_failed" if errors else "active",
        "reason": reason,
        "errors": errors,
    }


def _hard_accessible_route_items(site: SiteSpec) -> list[dict[str, Any]]:
    raw_items = site.pedestrian_and_emergency.get("accessible_routes", [])
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("enabled", True) or "geometry" not in item:
            continue
        priority = str(item.get("priority", "hard")).strip().lower()
        authority = str(item.get("authority", "project_policy")).strip().lower()
        if authority == "advisory" or priority in _ADVISORY_PRIORITIES:
            continue
        items.append(item)
    return items


def _declared_polyline_width(item: dict[str, Any]) -> tuple[float | None, str | None]:
    geometry = item.get("geometry")
    if not isinstance(geometry, dict):
        return None, "accessible_route_width_not_auditable_for_geometry"
    if str(geometry.get("type", "")).strip().lower() != "polyline_buffer":
        return None, "accessible_route_width_not_auditable_for_geometry"
    if "width" not in geometry:
        return None, "accessible_route_width_not_auditable_for_geometry"
    return _positive_route_number(geometry.get("width"), "geometry_width")


def _positive_route_number(raw: object, label: str) -> tuple[float | None, str | None]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"invalid_accessible_route_{label}"
    if not math.isfinite(value) or value <= 0.0:
        return None, f"invalid_accessible_route_{label}"
    return value, None


def _nonnegative_route_number(raw: object, label: str) -> tuple[float | None, str | None]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"invalid_accessible_route_{label}"
    if not math.isfinite(value) or value < 0.0:
        return None, f"invalid_accessible_route_{label}"
    return value, None


def _emergency_access_connectivity(layout: LayoutResult) -> dict[str, Any]:
    requested = bool(layout.site.pedestrian_and_emergency.get("emergency_access_required", False))
    tolerance, config_error = _constraint_tolerance(
        layout.site,
        "emergency_route_touch_tolerance",
        DEFAULT_EMERGENCY_ROUTE_TOUCH_TOLERANCE,
    )
    routes = _hard_routes(layout.site, _EMERGENCY_ROUTE_SOURCES)
    base = {
        "requested": requested,
        "touch_tolerance": tolerance,
        "route_ids": [item.id for item in routes],
        "connected_route_ids": [],
        "unconnected_route_ids": [],
        "connected_entrance_ids": [],
        "errors": [],
        "reason": None,
    }
    if config_error:
        return {
            **base,
            "status": "active_failed",
            "reason": config_error,
            "errors": [config_error],
        }
    if not requested:
        return {**base, "status": "not_requested"}
    if not routes:
        return {
            **base,
            "status": "active_failed",
            "reason": "emergency_access_geometry_missing",
            "errors": [],
        }
    assert tolerance is not None
    gates = [(entrance.id, _entrance_gate_geometry(entrance)) for entrance in layout.site.entrances]
    connected_routes: list[str] = []
    unconnected: list[str] = []
    connected_entrances: set[str] = set()
    for route in routes:
        matched = [
            entrance_id
            for entrance_id, gate in gates
            if _geometry_reaches_geometry(route.base_geometry, gate, tolerance)
        ]
        if matched:
            connected_routes.append(route.id)
            connected_entrances.update(matched)
        else:
            unconnected.append(route.id)
    errors: list[str] = []
    reason = None
    if not gates:
        reason = "emergency_access_entrance_missing"
        errors.append("emergency_access_required needs at least one entrance for route connectivity")
    elif not connected_routes:
        reason = "emergency_route_does_not_reach_entrance"
        errors.append(
            "hard fire/access routes do not reach an entrance gate within "
            f"{tolerance} m: {', '.join(unconnected)}"
        )
    return {
        **base,
        "status": "active_failed" if errors else "active",
        "connected_route_ids": connected_routes,
        "unconnected_route_ids": unconnected,
        "connected_entrance_ids": sorted(connected_entrances),
        "reason": reason,
        "errors": errors,
    }


def _ev_charger_usability(layout: LayoutResult) -> dict[str, Any]:
    quota_requested = int(layout.site.parking_quotas.get("ev_min", 0)) > 0
    chargers = _hard_charger_features(layout.site)
    requested = quota_requested and bool(chargers)
    tolerance, config_error = _constraint_tolerance(
        layout.site,
        "ev_charger_touch_tolerance",
        DEFAULT_EV_CHARGER_TOUCH_TOLERANCE,
    )
    quota = validate_parking_quotas(layout)
    stall_ids = list(quota.get("matching_stall_ids", {}).get("ev", []))
    base = {
        "requested": requested,
        "touch_tolerance": tolerance,
        "charger_ids": [item.id for item in chargers],
        "checked_stall_ids": stall_ids,
        "reachable_stall_ids": [],
        "unreachable_stall_ids": [],
        "errors": [],
        "reason": None,
    }
    if not requested:
        return {**base, "status": "not_requested"}
    if config_error:
        return {
            **base,
            "status": "active_failed",
            "reason": config_error,
            "errors": [config_error],
        }
    stall_by_id = {stall.id: stall for stall in layout.stalls}
    reachable: list[str] = []
    unreachable: list[str] = []
    assert tolerance is not None
    for stall_id in stall_ids:
        stall = stall_by_id.get(stall_id)
        if stall is None or not _geometry_reaches_routes(ShapelyPolygon(stall.polygon), chargers, tolerance):
            unreachable.append(stall_id)
        else:
            reachable.append(stall_id)
    errors: list[str] = []
    reason = None
    if unreachable:
        reason = "ev_stall_does_not_reach_charger"
        errors.append(
            "EV stalls do not reach a placed charging post within "
            f"{tolerance} m: {', '.join(unreachable)}"
        )
    return {
        **base,
        "status": "active_failed" if errors else "active",
        "reachable_stall_ids": reachable,
        "unreachable_stall_ids": unreachable,
        "reason": reason,
        "errors": errors,
    }


def accessible_route_dimensions_requested(site: SiteSpec) -> bool:
    return any("min_width" in item or "max_slope" in item for item in _hard_accessible_route_items(site))


def has_hard_accessible_route_geometry(site: SiteSpec) -> bool:
    return bool(_hard_routes(site, {"accessible_route"}))


def has_hard_charger_geometry(site: SiteSpec) -> bool:
    return bool(_hard_charger_features(site))


def _hard_charger_features(site: SiteSpec) -> list[ConstraintGeometry]:
    chargers: list[ConstraintGeometry] = []
    for index, feature in enumerate(site.site_features, start=1):
        if not isinstance(feature, dict) or not feature.get("enabled", True):
            continue
        kind = str(feature.get("type", "")).strip().lower()
        if kind not in _CHARGER_KINDS or "geometry" not in feature:
            continue
        try:
            geometry = _declared_geometry(feature.get("geometry"), f"site_features[{index}].geometry")
            clearance = _nonnegative_float(feature.get("clearance", 0.0), f"site_features[{index}].clearance")
        except ValueError:
            continue
        item = _make_constraint(
            constraint_id=str(feature.get("id", f"charger-{index}")),
            source="site_feature",
            kind=kind,
            geometry=geometry,
            clearance=clearance,
            purposes=frozenset(),
            affects=tuple(str(value) for value in feature.get("affects", [])),
            authority=str(feature.get("authority", "project_policy")),
            priority=str(feature.get("priority", "hard")),
        )
        if item.hard:
            chargers.append(item)
    return chargers


def _hard_routes(site: SiteSpec, sources: set[str]) -> list[ConstraintGeometry]:
    try:
        declarations = declared_constraint_geometries(site)
    except ValueError:
        return []
    return [item for item in declarations if item.source in sources and item.hard]


def _geometry_reaches_routes(geometry: BaseGeometry, routes: list[ConstraintGeometry], tolerance: float) -> bool:
    return any(_geometry_reaches_geometry(geometry, route.base_geometry, tolerance) for route in routes)


def _geometry_reaches_geometry(left: BaseGeometry, right: BaseGeometry, tolerance: float) -> bool:
    if left.is_empty or right.is_empty:
        return False
    reach = left.buffer(tolerance) if tolerance > 0.0 else left
    return bool(reach.intersects(right))


def _entrance_gate_geometry(entrance: EntranceSpec) -> BaseGeometry:
    heading = math.radians(entrance.heading_degrees)
    perp_x = -math.sin(heading)
    perp_y = math.cos(heading)
    half = max(float(entrance.width), 0.0) / 2.0
    cx, cy = entrance.center
    return LineString(
        [
            (cx - perp_x * half, cy - perp_y * half),
            (cx + perp_x * half, cy + perp_y * half),
        ]
    ).buffer(0.25)


def _constraint_tolerance(site: SiteSpec, key: str, default: float) -> tuple[float | None, str | None]:
    raw = site.constraints.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"invalid_{key}"
    if not math.isfinite(value) or value < 0.0:
        return None, f"invalid_{key}"
    return value, None


def stall_type_capabilities(spec: StallSpec | None) -> frozenset[str]:
    if spec is None:
        return frozenset()

    tokens = set(spec.classifications)
    tokens.update(token for token in split(r"[^a-z0-9]+", spec.id.lower()) if token)
    tokens.update(token for token in split(r"[^a-z0-9]+", spec.family.lower()) if token)
    feature_types = {
        str(feature.get("type", "")).strip().lower()
        for feature in spec.fixed_features
    }
    capabilities: set[str] = set()
    if tokens.intersection({"accessible", "ada", "disabled", "wheelchair"}):
        capabilities.add("accessible")
    if tokens.intersection({"ev", "electric", "charging", "charger"}) or feature_types.intersection(
        {"charging_post", "ev_charger", "charger"}
    ):
        capabilities.add("ev")
    return frozenset(capabilities)


def _obstacle_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    specs = site.obstacle_specs
    if not specs:
        default_clearance = float(site.constraints.get("setbacks", {}).get("obstacle", 0.0))
        specs = tuple(
            SiteAreaSpec(
                id=f"obstacle-{index}",
                kind="obstacle",
                geometry={"type": "polygon", "points": polygon},
                clearance=default_clearance,
            )
            for index, polygon in enumerate(site.obstacles, start=1)
        )
    return [
        _constraint_from_area(spec, "obstacle", _CONCRETE_PURPOSES)
        for spec in specs
    ]


def _reserved_area_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    for spec in site.reserved_areas:
        purposes = _purposes_from_affects(spec.affects)
        if not spec.affects:
            purposes = _purposes_from_access_flags(
                parking_allowed=spec.parking_allowed,
                vehicle_allowed=spec.vehicle_allowed,
            )
        geometries.append(_constraint_from_area(spec, "reserved_area", frozenset(purposes)))
    return geometries


def _site_feature_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    for index, feature in enumerate(site.site_features, start=1):
        if not isinstance(feature, dict):
            raise ValueError(f"site_features[{index}] must be an object")
        if not feature.get("enabled", True):
            continue
        affects = tuple(str(value) for value in feature.get("affects", []))
        purposes = _purposes_from_affects(affects)
        if feature.get("forbidden", False):
            purposes.update(_CONCRETE_PURPOSES)
        if not purposes:
            continue
        geometry = _declared_geometry(feature.get("geometry"), f"site_features[{index}].geometry")
        clearance = _nonnegative_float(feature.get("clearance", 0.0), f"site_features[{index}].clearance")
        geometries.append(
            _make_constraint(
                constraint_id=str(feature.get("id", f"site-feature-{index}")),
                source="site_feature",
                kind=str(feature.get("type", "feature")),
                geometry=geometry,
                clearance=clearance,
                purposes=frozenset(purposes),
                affects=affects,
                authority=str(feature.get("authority", "project_policy")),
                priority=str(feature.get("priority", "hard")),
                required=bool(feature.get("required", False)),
            )
        )
    return geometries


def _route_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    route_defaults = {
        "pedestrian_routes": (False, False),
        "accessible_routes": (False, False),
        "fire_lanes": (False, True),
        "access_routes": (False, True),
        "emergency_access_routes": (False, True),
    }
    for key, defaults in route_defaults.items():
        raw_items = site.pedestrian_and_emergency.get(key, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"pedestrian_and_emergency.{key} must be an array")
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"pedestrian_and_emergency.{key}[{index}] must be an object")
            if not item.get("enabled", True) or "geometry" not in item:
                continue
            affects = tuple(str(value) for value in item.get("affects", []))
            if affects:
                purposes = _purposes_from_affects(affects)
                if "parking_allowed" in item:
                    if item["parking_allowed"]:
                        purposes.discard("stall")
                    else:
                        purposes.add("stall")
                if "vehicle_allowed" in item:
                    if item["vehicle_allowed"]:
                        purposes.difference_update({"aisle", "swept_path"})
                    else:
                        purposes.update({"aisle", "swept_path"})
            else:
                parking_allowed = bool(item.get("parking_allowed", defaults[0]))
                vehicle_allowed = bool(item.get("vehicle_allowed", defaults[1]))
                purposes = _purposes_from_access_flags(parking_allowed, vehicle_allowed)
            label = f"pedestrian_and_emergency.{key}[{index}]"
            geometry = _declared_geometry(item["geometry"], f"{label}.geometry")
            clearance = _nonnegative_float(item.get("clearance", 0.0), f"{label}.clearance")
            geometries.append(
                _make_constraint(
                    constraint_id=str(item.get("id", f"{key}-{index}")),
                    source=key.removesuffix("s"),
                    kind=str(item.get("type", key.removesuffix("s"))),
                    geometry=geometry,
                    clearance=clearance,
                    purposes=frozenset(purposes),
                    affects=affects,
                    authority=str(item.get("authority", "project_policy")),
                    priority=str(item.get("priority", "hard")),
                    required=(
                        bool(item.get("required", False))
                        or (
                            bool(site.pedestrian_and_emergency.get("emergency_access_required", False))
                            and key in {"fire_lanes", "access_routes", "emergency_access_routes"}
                        )
                        or (site.parking_quotas.get("accessible_min", 0) > 0 and key == "accessible_routes")
                    ),
                )
            )
    return geometries


def _constraint_from_area(
    spec: SiteAreaSpec,
    source: str,
    purposes: frozenset[str],
) -> ConstraintGeometry:
    geometry = _declared_geometry(spec.geometry, f"{source} '{spec.id}'.geometry")
    return _make_constraint(
        constraint_id=spec.id,
        source=source,
        kind=spec.kind,
        geometry=geometry,
        clearance=spec.clearance,
        purposes=purposes,
        affects=spec.affects,
        authority=spec.authority,
        priority=spec.priority,
    )


def _make_constraint(
    *,
    constraint_id: str,
    source: str,
    kind: str,
    geometry: BaseGeometry,
    clearance: float,
    purposes: frozenset[str],
    affects: tuple[str, ...],
    authority: str,
    priority: str,
    required: bool = False,
) -> ConstraintGeometry:
    exclusion = geometry.buffer(clearance, join_style="mitre") if clearance else geometry
    return ConstraintGeometry(
        id=constraint_id,
        source=source,
        kind=kind,
        base_geometry=geometry,
        exclusion_geometry=exclusion,
        clearance=clearance,
        purposes=purposes,
        affects=affects,
        authority=authority,
        priority=priority,
        required=required,
    )


def _declared_geometry(raw: Any, label: str) -> BaseGeometry:
    if isinstance(raw, list):
        return _valid_polygon(raw, label)
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a geometry object")

    geometry_type = str(raw.get("type", "")).strip().lower()
    if geometry_type == "polygon":
        return _valid_polygon(raw.get("points"), label)
    if geometry_type == "circle":
        center = _point(raw.get("center"), f"{label}.center")
        radius = _positive_float(raw.get("radius"), f"{label}.radius")
        return ShapelyPoint(center).buffer(radius, quad_segs=16)
    if geometry_type == "rectangle":
        origin = _point(raw.get("origin"), f"{label}.origin")
        width = _positive_float(raw.get("width"), f"{label}.width")
        height = _positive_float(raw.get("height"), f"{label}.height")
        geometry = box(0.0, 0.0, width, height)
        geometry = affinity.rotate(
            geometry,
            float(raw.get("rotation_degrees", 0.0)),
            origin=(0.0, 0.0),
            use_radians=False,
        )
        return affinity.translate(geometry, xoff=origin[0], yoff=origin[1])
    if geometry_type == "polyline_buffer":
        points = [_point(value, f"{label}.points[]") for value in raw.get("points", [])]
        if len(points) < 2:
            raise ValueError(f"{label}.points must contain at least two points")
        width = _positive_float(raw.get("width"), f"{label}.width")
        return LineString(points).buffer(width / 2, cap_style="flat", join_style="mitre")
    raise ValueError(f"{label}.type={geometry_type!r} is not supported")


def _valid_polygon(raw: Any, label: str) -> ShapelyPolygon:
    if not isinstance(raw, list | tuple) or len(raw) < 3:
        raise ValueError(f"{label} must contain at least three points")
    points = [_point(value, f"{label}.points[]") for value in raw]
    geometry = ShapelyPolygon(points)
    if geometry.is_empty or geometry.area <= _AREA_TOLERANCE:
        raise ValueError(f"{label} must have positive area")
    if not geometry.is_valid:
        raise ValueError(f"{label} must be a valid polygon")
    return geometry


def _point(raw: Any, label: str) -> tuple[float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"{label} must be [x, y]")
    return (float(raw[0]), float(raw[1]))


def _positive_float(raw: Any, label: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _nonnegative_float(raw: Any, label: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def _purposes_from_access_flags(parking_allowed: bool, vehicle_allowed: bool) -> set[str]:
    purposes: set[str] = set()
    if not parking_allowed:
        purposes.add("stall")
    if not vehicle_allowed:
        purposes.update({"aisle", "swept_path"})
    return purposes


def _purposes_from_affects(affects: tuple[str, ...]) -> set[str]:
    normalized = {value.strip().lower() for value in affects}
    purposes: set[str] = set()
    if normalized.intersection({"forbidden", "clearance_only", "vehicle_clearance"}):
        purposes.update(_CONCRETE_PURPOSES)
    if normalized.intersection(
        {"door_clearance", "no_parking", "parking", "stall", "stalls", "stall_access"}
    ):
        purposes.add("stall")
    if normalized.intersection({"aisle", "aisles", "aisle_clearance", "entrance_throat", "queueing"}):
        purposes.add("aisle")
    if normalized.intersection(
        {"maneuver", "stall_access", "turning", "swept_path", "swept_paths", "vehicle_swept_path"}
    ):
        purposes.add("swept_path")
    return purposes


def _normalize_purpose(purpose: ConstraintPurpose) -> ConstraintPurpose:
    if purpose not in {"all", *_CONCRETE_PURPOSES}:
        raise ValueError(f"unsupported site-constraint purpose: {purpose!r}")
    return purpose


def _matches_purpose(item: ConstraintGeometry, purpose: ConstraintPurpose) -> bool:
    return bool(item.purposes) if purpose == "all" else purpose in item.purposes


def _layout_object_conflicts(layout: LayoutResult, purpose: Literal["stall", "aisle"]) -> list[dict[str, Any]]:
    objects = layout.stalls if purpose == "stall" else layout.aisles
    boundary = _valid_polygon(layout.site.boundary, "site.boundary")
    setback_boundary = boundary.buffer(-layout.site.margin, join_style="mitre")
    conflicts: list[dict[str, Any]] = []
    for item in objects:
        geometry = _valid_polygon(item.polygon, f"{purpose} '{item.id}'")
        reasons: list[dict[str, Any]] = []
        if not setback_boundary.buffer(_AREA_TOLERANCE).covers(geometry):
            reasons.append(
                {
                    "constraint_id": "site-boundary-setback",
                    "source": "site_boundary",
                    "kind": "setback",
                    "purpose": purpose,
                    "overlap_area": float(geometry.difference(setback_boundary).area),
                    "clearance": layout.site.margin,
                }
            )
        reasons.extend(constraint_conflicts(layout.site, geometry, purpose))
        for reason in reasons:
            conflicts.append(
                {
                    "object_id": item.id,
                    "object_type": purpose,
                    **reason,
                    "message": (
                        f"{purpose} '{item.id}' conflicts with {reason['source']} "
                        f"'{reason['constraint_id']}'"
                    ),
                }
            )
    return conflicts


def _stall_specs_by_id(site: SiteSpec) -> dict[str, StallSpec]:
    specs = [site.stall, *site.stall_candidates]
    if site.main_stall is not None:
        specs.append(site.main_stall)
    if site.branch_stall is not None:
        specs.append(site.branch_stall)
    return {spec.id: spec for spec in specs}


def _has_hard_route_geometry(data: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        raw_items = data.get(key, [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict) or "geometry" not in item:
                continue
            priority = str(item.get("priority", "hard")).strip().lower()
            authority = str(item.get("authority", "project_policy")).strip().lower()
            if (
                item.get("enabled", True)
                and authority != "advisory"
                and priority not in _ADVISORY_PRIORITIES
            ):
                return True
    return False


def _missing_required_route_geometry_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "pedestrian_routes",
        "accessible_routes",
        "fire_lanes",
        "access_routes",
        "emergency_access_routes",
    ):
        raw_items = data.get(key, [])
        if not isinstance(raw_items, list):
            continue
        for index, item in enumerate(raw_items, start=1):
            if isinstance(item, dict) and item.get("required", False) and "geometry" not in item:
                item_id = str(item.get("id", f"{key}-{index}"))
                errors.append(f"required {key.removesuffix('s')} '{item_id}' needs geometry")
    return errors


def _authority_summary(
    site: SiteSpec,
    declarations: list[ConstraintGeometry],
) -> dict[str, Any]:
    tiers: dict[str, Any] = {}
    for authority in _AUTHORITY_TIERS:
        items = [
            item
            for item in declarations
            if item.authority.strip().lower() == authority
        ]
        tiers[authority] = {
            "declared_count": len(items),
            "declared_ids": [item.id for item in items],
            "active_count": sum(1 for item in items if item.hard),
            "active_ids": [item.id for item in items if item.hard],
            "blocking": authority != "advisory",
        }
    metadata = _jurisdictional_metadata_status(site)
    metadata["required"] = bool(tiers["jurisdictional"]["declared_count"])
    if not metadata["required"]:
        metadata["valid"] = True
        metadata["missing"] = []
    tiers["jurisdictional"]["standards_metadata"] = metadata
    return tiers


def _jurisdictional_metadata_status(site: SiteSpec) -> dict[str, Any]:
    requirements = {
        "standards.standard_profile": site.standards.get("standard_profile"),
        "standards.source": site.standards.get("source"),
        "standards.effective_date": site.standards.get("effective_date"),
    }
    missing = [key for key, value in requirements.items() if not str(value or "").strip()]
    return {
        "valid": not missing,
        "missing": missing,
        "profile": requirements["standards.standard_profile"],
        "source": requirements["standards.source"],
        "effective_date": requirements["standards.effective_date"],
    }
