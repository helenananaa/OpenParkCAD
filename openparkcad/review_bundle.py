"""Frozen review-bundle-1 snapshots for offline comparison. Does not re-solve."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from openparkcad import __version__
from openparkcad.models import LayoutResult
from openparkcad.road_traversal import journey_trajectories

BUNDLE_VERSION = "review-bundle-1"


def build_review_bundle(
    layout: LayoutResult,
    *,
    scope: str = "official",
    stale: bool = False,
) -> dict[str, Any]:
    official_id = _official_id(layout)
    official_snapshot = _layout_snapshot(layout, candidate_id=official_id, official=True)
    candidates = [official_snapshot]
    preview = layout.candidate_layout_preview if isinstance(layout.candidate_layout_preview, dict) else {}
    if preview.get("aisles") or preview.get("stalls"):
        candidates.append(_preview_snapshot(layout, preview, official_id))
    search = layout.layout_search if isinstance(layout.layout_search, dict) else {}
    for item in search.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        if item.get("candidate_id") == official_id and item.get("geometry"):
            candidates[0] = _merge_search_snapshot(official_snapshot, item)
            continue
        candidates.append(_search_candidate_snapshot(item, official_id))
    if stale:
        for item in candidates:
            item["status"] = "stale"
    return {
        "version": BUNDLE_VERSION,
        "package_version": __version__,
        "scope": scope,
        "site": {
            "name": layout.site.name,
            "boundary": [list(point) for point in layout.site.boundary],
            "obstacles": [[list(point) for point in item] for item in layout.site.obstacles],
            "entrances": [
                {
                    "id": item.id,
                    "center": list(item.center),
                    "width": item.width,
                    "heading_degrees": item.heading_degrees,
                    "allowed_movements": list(item.allowed_movements),
                }
                for item in layout.site.entrances
            ],
        },
        "transform": (layout.site.metadata.get("cad_import") or {}).get("transform")
        if isinstance(layout.site.metadata, dict)
        else None,
        "official": {
            "candidate_id": official_id,
            "stall_count": layout.stall_count,
            "score": dict(layout.score or {}),
            "layout_identity": (layout.road_traversal_validation or {}).get("layout_identity"),
            "generation_mode": layout.generation_mode,
            "skeleton_id": (layout.site.metadata or {}).get("skeleton_id") if isinstance(layout.site.metadata, dict) else None,
            "family": (layout.site.metadata or {}).get("skeleton_family") if isinstance(layout.site.metadata, dict) else None,
        },
        "candidates": candidates,
        "road_traversal": layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {},
        "failures": _failure_objects(layout),
        "journeys": _journey_records(layout),
        "search": {
            "status": search.get("status"),
            "budget": search.get("budget"),
            "official_candidate_id": search.get("official_candidate_id"),
            "road_network_search": search.get("road_network_search"),
        },
    }


def snapshot_candidate_geometry(layout: LayoutResult) -> dict[str, Any]:
    return {
        "aisles": [
            {
                "id": aisle.id,
                "role": aisle.role,
                "polygon": [list(point) for point in aisle.polygon],
                "directionality": aisle.directionality,
            }
            for aisle in layout.aisles
        ],
        "stalls": [
            {
                "id": stall.id,
                "polygon": [list(point) for point in stall.polygon],
                "served_by_aisle_id": stall.served_by_aisle_id,
                "stall_type_id": stall.stall_type_id,
            }
            for stall in layout.stalls
        ],
    }


def _layout_snapshot(layout: LayoutResult, *, candidate_id: str, official: bool) -> dict[str, Any]:
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    status = "official" if official else "preview"
    if road.get("requested") and not (road.get("status") == "passed" and road.get("valid") is True):
        if road.get("status") == "unsupported":
            status = "unsupported"
        elif road.get("status") == "incomplete":
            status = "over_budget"
        elif not official:
            status = "failed"
    meta = layout.site.metadata if isinstance(layout.site.metadata, dict) else {}
    return {
        "candidate_id": candidate_id,
        "object_ids": [aisle.id for aisle in layout.aisles] + [stall.id for stall in layout.stalls],
        "official": official,
        "promoted": bool((layout.candidate_layout_promotion or {}).get("replaced")),
        "checks_passed": _checks_passed(layout),
        "status": status,
        "score": dict(layout.score or {}),
        "stall_count": layout.stall_count,
        "geometry": snapshot_candidate_geometry(layout),
        "family": meta.get("skeleton_family"),
        "skeleton_id": meta.get("skeleton_id"),
        "generation_mode": layout.generation_mode,
        "road_traversal": {
            "requested": road.get("requested"),
            "executed": road.get("executed"),
            "status": road.get("status"),
            "valid": road.get("valid"),
        },
        "duration_seconds": None,
        "scores_from_evaluation": True,
    }


def _preview_snapshot(layout: LayoutResult, preview: dict[str, Any], official_id: str) -> dict[str, Any]:
    aisles = preview.get("aisles") if isinstance(preview.get("aisles"), list) else []
    stalls = preview.get("stalls") if isinstance(preview.get("stalls"), list) else []
    validation = preview.get("validation") if isinstance(preview.get("validation"), dict) else {}
    return {
        "candidate_id": f"{official_id}-preview",
        "object_ids": [item.get("id") for item in aisles + stalls if isinstance(item, dict)],
        "official": False,
        "promoted": preview.get("status") == "promoted_to_official",
        "checks_passed": bool(validation.get("valid")),
        "status": "preview" if validation.get("valid") else "failed",
        "score": dict(preview.get("score") or {}),
        "stall_count": preview.get("stall_count"),
        "geometry": {
            "aisles": [
                {"id": item.get("id"), "role": item.get("role"), "polygon": item.get("geometry")}
                for item in aisles
                if isinstance(item, dict)
            ],
            "stalls": [
                {"id": item.get("id"), "polygon": item.get("geometry"), "source": item.get("source")}
                for item in stalls
                if isinstance(item, dict)
            ],
        },
        "road_traversal": (validation.get("road_traversal_validation") or {}),
        "duration_seconds": None,
        "scores_from_evaluation": True,
    }


def _search_candidate_snapshot(item: dict[str, Any], official_id: str) -> dict[str, Any]:
    status = "failed"
    if item.get("not_evaluated") or item.get("status") == "not_solved":
        status = "not_solved"
    elif (item.get("checks") or {}).get("road_traversal", {}).get("status") == "incomplete":
        status = "over_budget"
    elif (item.get("checks") or {}).get("road_traversal", {}).get("status") == "unsupported":
        status = "unsupported"
    elif item.get("valid"):
        status = "preview"
    if item.get("candidate_id") == official_id:
        status = "official"
    geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {"aisles": [], "stalls": []}
    return {
        "candidate_id": item.get("candidate_id"),
        "object_ids": [aisle.get("id") for aisle in geometry.get("aisles") or [] if isinstance(aisle, dict)]
        + [stall.get("id") for stall in geometry.get("stalls") or [] if isinstance(stall, dict)],
        "official": item.get("candidate_id") == official_id,
        "promoted": bool(item.get("promoted")),
        "checks_passed": bool(item.get("valid")),
        "status": status,
        "score": {"total": item.get("official_score_total")},
        "stall_count": item.get("stall_count"),
        "geometry": geometry,
        "road_traversal": (item.get("checks") or {}).get("road_traversal") or {},
        "duration_seconds": item.get("duration_seconds"),
        "scores_from_evaluation": True,
        "failure_class": item.get("failure_class"),
    }


def _merge_search_snapshot(official: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    merged = dict(official)
    if isinstance(item.get("geometry"), dict) and item["geometry"].get("aisles"):
        merged["geometry"] = item["geometry"]
    merged["duration_seconds"] = item.get("duration_seconds")
    return merged


def _failure_objects(layout: LayoutResult) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    for item in road.get("failures") or []:
        if isinstance(item, dict):
            failures.append(
                {
                    "kind": "road_traversal",
                    "object_id": item.get("stall_id"),
                    "reason": item.get("reason"),
                    "collision_object": item.get("collision_object"),
                    "status": item.get("status"),
                }
            )
    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    for stall_id in graph.get("unreachable_stalls") or []:
        failures.append({"kind": "traffic_graph", "object_id": stall_id, "reason": "unreachable_stall"})
    return failures


def _journey_records(layout: LayoutResult) -> list[dict[str, Any]]:
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    journeys = road.get("journeys") if isinstance(road.get("journeys"), list) else []
    traces = {stall_id: points for stall_id, points in journey_trajectories(layout)}
    records = []
    for item in journeys:
        if not isinstance(item, dict):
            continue
        stall_id = str(item.get("stall_id") or "")
        records.append(
            {
                "stall_id": stall_id,
                "status": item.get("status"),
                "entrance_id": item.get("entrance_id"),
                "exit_id": item.get("exit_id"),
                "trajectory": traces.get(stall_id) or (item.get("details") or {}).get("trajectory") or [],
            }
        )
    return records


def _official_id(layout: LayoutResult) -> str:
    search = layout.layout_search if isinstance(layout.layout_search, dict) else {}
    if search.get("official_candidate_id"):
        return str(search["official_candidate_id"])
    digest = hashlib.sha256(
        json.dumps(
            {"aisles": [aisle.id for aisle in layout.aisles], "stalls": [stall.id for stall in layout.stalls]},
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"official-{digest}"


def _checks_passed(layout: LayoutResult) -> bool:
    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    site = layout.site_constraint_validation if isinstance(layout.site_constraint_validation, dict) else {}
    operational = layout.operational_quality if isinstance(layout.operational_quality, dict) else {}
    return bool(graph.get("valid") and maneuver.get("valid") and site.get("valid") and operational.get("valid", True))
