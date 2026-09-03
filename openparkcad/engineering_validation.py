from __future__ import annotations

from typing import Any

from openparkcad.models import LayoutResult
from openparkcad.site_constraints import declared_constraint_geometries


ENGINEERING_VALIDATION_VERSION = "openparkcad-engineering-0.3"
ENGINEERING_CONTRACT_VERSION = "openparkcad-v0.3"


def build_engineering_validation(
    layout: LayoutResult,
    *,
    result_scope: str = "official_layout",
) -> dict[str, Any]:
    """Combine vehicle and site-policy evidence into one fail-closed decision."""

    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    vehicle = maneuver.get("vehicle_validation", {})
    if not isinstance(vehicle, dict):
        vehicle = {}
    site_constraints = (
        layout.site_constraint_validation
        if isinstance(layout.site_constraint_validation, dict)
        else {}
    )

    active, advisory = _declared_rule_records(layout, vehicle, site_constraints)
    unsupported = _unsupported_rule_records(layout)
    failed = _failed_rule_records(vehicle, site_constraints)
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    _apply_road_traversal_rules(road, active, unsupported, failed)
    valid = bool(vehicle.get("valid", False)) and bool(site_constraints.get("valid", False)) and not failed
    if road.get("requested"):
        valid = valid and road.get("status") == "passed" and road.get("valid") is True

    return {
        "version": ENGINEERING_VALIDATION_VERSION,
        "contract_version": ENGINEERING_CONTRACT_VERSION,
        "result_scope": result_scope,
        "valid": valid,
        "decision": "pass" if valid else "fail",
        "layout": {
            "generation_mode": layout.generation_mode,
            "stall_count": layout.stall_count,
            "aisle_count": len(layout.aisles),
            "stall_ids": [stall.id for stall in layout.stalls],
            "aisle_ids": [aisle.id for aisle in layout.aisles],
        },
        "algorithm_versions": {
            "vehicle": vehicle.get("version"),
            "site_constraints": site_constraints.get("version"),
            "maneuver": maneuver.get("version"),
            "road_traversal": road.get("version"),
        },
        "rules": {
            "active": active,
            "advisory": advisory,
            "unsupported": unsupported,
            "failed": failed,
        },
        "authority": site_constraints.get("authority", {}),
        "vehicle_validation": vehicle,
        "site_constraint_validation": site_constraints,
        "quota_validation": site_constraints.get("quota", {}),
        "road_traversal_validation": road,
    }


def _apply_road_traversal_rules(
    road: dict[str, Any],
    active: list[dict[str, Any]],
    unsupported: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> None:
    if not road.get("requested"):
        return
    record = {
        "id": "road.traversal",
        "kind": "road_traversal",
        "source": "constraints.road_traversal",
        "authority": "project_policy",
        "priority": "hard",
        "scope": road.get("scope"),
        "status": road.get("status"),
        "reason": road.get("reason"),
    }
    status = road.get("status")
    if status == "passed" and road.get("valid") is True:
        record["status"] = "active"
        active.append(record)
        return
    if status == "unsupported":
        unsupported.append(record)
        return
    if status == "incomplete":
        record["classification"] = "incomplete"
        failed.append(record)
        return
    if not road:
        failed.append(
            {
                "id": "road.traversal",
                "kind": "road_traversal",
                "status": "failed",
                "reason": "requested road_traversal evidence is missing",
            }
        )
        return
    failed.append(record)


def _declared_rule_records(
    layout: LayoutResult,
    vehicle: dict[str, Any],
    site_constraints: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    try:
        declarations = declared_constraint_geometries(layout.site, include_advisory=True)
    except ValueError:
        declarations = []

    for item in declarations:
        record = {
            "id": f"site.{item.id}",
            "kind": item.kind,
            "source": item.source,
            "authority": item.authority,
            "priority": item.priority,
            "purposes": sorted(item.purposes),
            "status": "active" if item.hard else "advisory",
        }
        (active if item.hard else advisory).append(record)

    requested = vehicle.get("requested", {})
    if isinstance(requested, dict):
        vehicle_valid = bool(vehicle.get("valid", False))
        for name in ("turning_radius", "swept_path", "reverse_distance"):
            if requested.get(name):
                active.append(
                    {
                        "id": f"vehicle.{name}",
                        "kind": "vehicle_maneuver",
                        "source": "constraints.maneuvering",
                        "authority": "project_policy",
                        "priority": "hard",
                        "status": "active" if vehicle_valid else "failed",
                    }
                )

    quota = site_constraints.get("quota", {})
    required = quota.get("required", {}) if isinstance(quota, dict) else {}
    if isinstance(required, dict):
        shortfall = quota.get("shortfall", {})
        shortfall = shortfall if isinstance(shortfall, dict) else {}
        for key, capability in (("accessible_min", "accessible"), ("ev_min", "ev")):
            if int(required.get(key, 0)) > 0:
                active.append(
                    {
                        "id": f"quota.{key}",
                        "kind": "parking_quota",
                        "source": "parking.quotas",
                        "authority": "project_policy",
                        "priority": "hard",
                        "status": "failed" if int(shortfall.get(capability, 0)) > 0 else "active",
                    }
                )

    usability = site_constraints.get("route_usability", {})
    if isinstance(usability, dict):
        for key, rule_id, source in (
            ("accessible_route", "route.accessible_usability", "pedestrian_and_emergency"),
            ("accessible_route_continuity", "route.accessible_continuity", "pedestrian_and_emergency"),
            ("accessible_route_dimensions", "route.accessible_dimensions", "pedestrian_and_emergency"),
            ("emergency_access", "route.emergency_access_connectivity", "pedestrian_and_emergency"),
            ("ev_charger", "equipment.ev_charger_usability", "site_features"),
        ):
            block = usability.get(key, {})
            if not isinstance(block, dict) or not block.get("requested"):
                continue
            active.append(
                {
                    "id": rule_id,
                    "kind": "route_usability",
                    "source": source,
                    "authority": "project_policy",
                    "priority": "hard",
                    "status": "failed" if block.get("status") == "active_failed" else "active",
                }
            )

    return _sorted_rule_records(active), _sorted_rule_records(advisory)


def _unsupported_rule_records(layout: LayoutResult) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, item in enumerate(layout.unsupported_phase1_inputs, start=1):
        if isinstance(item, dict):
            field = str(item.get("field", f"unsupported-{index}"))
            reason = str(item.get("reason", "unsupported by the active generator"))
        else:
            field = f"unsupported-{index}"
            reason = str(item)
        records.append(
            {
                "id": field,
                "kind": "unsupported_input",
                "status": "unsupported",
                "reason": reason,
            }
        )
    return _sorted_rule_records(records)


def _failed_rule_records(
    vehicle: dict[str, Any],
    site_constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    if not vehicle:
        failed.append(
            {
                "id": "vehicle.validation_missing",
                "kind": "validation_contract",
                "status": "failed",
                "reason": "vehicle validation block is missing",
            }
        )
    else:
        reason = vehicle.get("reason")
        if reason:
            failed.append(
                {
                    "id": "vehicle.configuration",
                    "kind": "vehicle_maneuver",
                    "status": "failed",
                    "reason": str(reason),
                }
            )
        checks = vehicle.get("checks", [])
        if isinstance(checks, list):
            for index, check in enumerate(checks, start=1):
                if not isinstance(check, dict) or check.get("valid", False):
                    continue
                failed.append(
                    {
                        "id": f"vehicle.{check.get('stall_id', index)}.{check.get('rule_id', 'check')}",
                        "kind": "vehicle_maneuver",
                        "status": "failed",
                        "object_id": check.get("stall_id"),
                        "reason": str(check.get("reason") or "vehicle maneuver check failed"),
                    }
                )
        if vehicle.get("valid") is False and not any(
            str(item.get("id", "")).startswith("vehicle.") for item in failed
        ):
            failed.append(
                {
                    "id": "vehicle.validation",
                    "kind": "vehicle_maneuver",
                    "status": "failed",
                    "reason": "one or more requested vehicle checks failed",
                }
            )

    if not site_constraints:
        failed.append(
            {
                "id": "site.validation_missing",
                "kind": "validation_contract",
                "status": "failed",
                "reason": "site constraint validation block is missing",
            }
        )
    else:
        errors = site_constraints.get("errors", [])
        if isinstance(errors, list):
            for index, error in enumerate(errors, start=1):
                failed.append(
                    {
                        "id": f"site.error.{index}",
                        "kind": "site_constraint",
                        "status": "failed",
                        "reason": str(error),
                    }
                )
        if site_constraints.get("valid") is False and not any(
            str(item.get("id", "")).startswith("site.") for item in failed
        ):
            failed.append(
                {
                    "id": "site.validation",
                    "kind": "site_constraint",
                    "status": "failed",
                    "reason": "one or more active site constraints failed",
                }
            )
    return _sorted_rule_records(failed)


def _sorted_rule_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: str(item.get("id", "")))
