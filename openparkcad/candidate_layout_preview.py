from __future__ import annotations

from dataclasses import replace
from typing import Any

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.candidate_catalog import STALL_MODULE_KIND
from openparkcad.candidate_network_preview import candidate_network_preview_report
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.layout_geometry import available_area
from openparkcad.maneuver_validation import apply_maneuver_filter, validate_maneuvers
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall
from openparkcad.operational_quality import operational_quality_report
from openparkcad.phase1_support import aisle_directionality
from openparkcad.scoring import score_metrics, stall_family_score_metrics
from openparkcad.contact_retarget import apply_contact_retarget
from openparkcad.site_constraints import apply_contact_filter
from openparkcad.site_constraints import validate_site_constraints
from openparkcad.traffic_graph import traffic_graph_summary


def build_candidate_layout_preview(layout: LayoutResult) -> dict[str, object]:
    network = candidate_network_preview_report(layout)
    aisles = _preview_aisles(network)
    stalls = _preview_stalls(layout, aisles)
    preview_layout = _preview_layout(layout, aisles, stalls)
    preview_layout = apply_maneuver_filter(preview_layout)
    before = [(stall.id, stall.stall_type_id, tuple(stall.polygon)) for stall in preview_layout.stalls]
    preview_layout = apply_contact_retarget(preview_layout)
    after = [(stall.id, stall.stall_type_id, tuple(stall.polygon)) for stall in preview_layout.stalls]
    if after != before:
        preview_layout = apply_maneuver_filter(preview_layout)
    preview_layout = apply_contact_filter(preview_layout)
    stalls = _renumber_preview_stalls(_preview_stalls_matching_layout(stalls, preview_layout))
    preview_layout = _preview_layout(layout, aisles, stalls)
    validation = _validate_layout_preview(layout, preview_layout, aisles, stalls)
    score = _preview_score(layout, aisles, stalls)
    comparison = _layout_comparison(layout, aisles, stalls, score, validation)
    return {
        "version": "phase4c-2a",
        "status": "preview_only",
        "source_network_preview_version": network.get("version"),
        "aisle_count": len(aisles),
        "stall_count": len(stalls),
        "score": score,
        "comparison": comparison,
        "aisles": aisles,
        "stalls": stalls,
        "validation": validation,
        "notes": [
            "Preview stalls are generated from current main-aisle stalls plus shadow candidate generated stalls.",
            "Preview score uses the same configured weights as the official layout for report-level comparison.",
            "Preview is report-only and does not replace the generated layout, DXF, or SVG yet.",
        ],
    }


def candidate_layout_preview_report(layout: LayoutResult) -> dict[str, object]:
    return layout.candidate_layout_preview or build_candidate_layout_preview(layout)


def candidate_layout_preview_layout(layout: LayoutResult) -> LayoutResult:
    preview = candidate_layout_preview_report(layout)
    aisles = _preview_aisles(preview)
    stalls = _preview_stalls_from_report(preview)
    return _preview_layout(layout, aisles, stalls)


def _preview_aisles(network: dict[str, object]) -> list[dict[str, object]]:
    raw = network.get("aisles", [])
    if not isinstance(raw, list):
        return []
    return [aisle for aisle in raw if isinstance(aisle, dict) and _valid_polygon(aisle.get("geometry"))]


def _preview_stalls(layout: LayoutResult, aisles: list[dict[str, object]]) -> list[dict[str, object]]:
    stalls: list[dict[str, object]] = []
    current_layout_source_to_preview_id = _current_layout_source_to_preview_id(aisles)
    allowed = _selected_module_stall_allowance(layout)
    for stall in layout.stalls:
        if stall.served_by_aisle_id not in current_layout_source_to_preview_id:
            continue
        if allowed is not None and stall.id not in allowed["official"]:
            continue
        stalls.append(
            _preview_stall(
                source_id=stall.id,
                polygon=stall.polygon,
                angle_degrees=stall.angle_degrees,
                served_by_aisle_id=current_layout_source_to_preview_id[stall.served_by_aisle_id],
                aisle_side=stall.aisle_side,
                stall_type_id=stall.stall_type_id,
                source="current_layout",
                index=len(stalls) + 1,
            )
        )
    for aisle in aisles:
        metadata = aisle.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        generated = metadata.get("generated_stalls", [])
        if not isinstance(generated, list):
            continue
        for item in generated:
            if not isinstance(item, dict) or not _valid_polygon(item.get("geometry")):
                continue
            if allowed is not None:
                parent_candidate = str(aisle.get("candidate_id") or "")
                source_id = str(item.get("source_id") or "")
                if (parent_candidate, source_id) not in allowed["generated"]:
                    continue
            stalls.append(
                _preview_stall(
                    source_id=str(item.get("source_id", "")),
                    polygon=item["geometry"],
                    angle_degrees=float(item.get("angle_degrees", 0.0)),
                    served_by_aisle_id=str(aisle["id"]),
                    aisle_side=str(item.get("aisle_side")) if item.get("aisle_side") is not None else None,
                    stall_type_id=str(item.get("stall_type_id")) if item.get("stall_type_id") is not None else None,
                    source="shadow_candidate",
                    index=len(stalls) + 1,
                )
            )
        if allowed is not None:
            extra = allowed.get("family_generated_by_candidate", {})
            parent_candidate = str(aisle.get("candidate_id") or "")
            if isinstance(extra, dict) and parent_candidate in extra:
                for item in extra[parent_candidate]:
                    if not isinstance(item, dict) or not _valid_polygon(item.get("geometry")):
                        continue
                    stalls.append(
                        _preview_stall(
                            source_id=str(item.get("source_id", "")),
                            polygon=item["geometry"],
                            angle_degrees=float(item.get("angle_degrees", 0.0)),
                            served_by_aisle_id=str(aisle["id"]),
                            aisle_side=str(item.get("aisle_side")) if item.get("aisle_side") is not None else None,
                            stall_type_id=str(item.get("stall_type_id")) if item.get("stall_type_id") is not None else None,
                            source="shadow_family",
                            index=len(stalls) + 1,
                        )
                    )
    if allowed is not None:
        family_generated = allowed.get("family_generated", {})
        if isinstance(family_generated, dict):
            for served, items in family_generated.items():
                preview_id = current_layout_source_to_preview_id.get(str(served))
                if not preview_id or not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or not _valid_polygon(item.get("geometry")):
                        continue
                    stalls.append(
                        _preview_stall(
                            source_id=str(item.get("source_id", "")),
                            polygon=item["geometry"],
                            angle_degrees=float(item.get("angle_degrees", 0.0)),
                            served_by_aisle_id=str(preview_id),
                            aisle_side=str(item.get("aisle_side")) if item.get("aisle_side") is not None else None,
                            stall_type_id=str(item.get("stall_type_id")) if item.get("stall_type_id") is not None else None,
                            source="shadow_family",
                            index=len(stalls) + 1,
                        )
                    )
    return _without_layout_conflicts(stalls, aisles)


def _preview_stall(
    source_id: str,
    polygon,
    angle_degrees: float,
    served_by_aisle_id: str,
    aisle_side: str | None,
    stall_type_id: str | None,
    source: str,
    index: int,
) -> dict[str, object]:
    return {
        "id": f"PL-STALL-{index:03d}",
        "source_id": source_id,
        "source": source,
        "geometry": [(float(x), float(y)) for x, y in polygon],
        "angle_degrees": angle_degrees,
        "served_by_aisle_id": served_by_aisle_id,
        "aisle_side": aisle_side,
        "stall_type_id": stall_type_id,
        "area": float(ShapelyPolygon(polygon).area),
    }


def _without_layout_conflicts(stalls: list[dict[str, object]], aisles: list[dict[str, object]]) -> list[dict[str, object]]:
    aisle_polygons = {
        str(aisle["id"]): ShapelyPolygon(aisle["geometry"])
        for aisle in aisles
        if aisle.get("id") and _valid_polygon(aisle.get("geometry"))
    }
    aisle_roles = {
        str(aisle["id"]): str(aisle.get("role", ""))
        for aisle in aisles
        if aisle.get("id")
    }
    kept: list[dict[str, object]] = []
    kept_polygons: list[ShapelyPolygon] = []
    for stall in sorted(stalls, key=lambda item: _stall_conflict_priority(item, aisle_roles)):
        stall_polygon = ShapelyPolygon(stall["geometry"])
        served_by = str(stall.get("served_by_aisle_id"))
        if _overlaps_non_serving_aisle(stall_polygon, served_by, aisle_polygons):
            continue
        if any(_area_overlap(stall_polygon, kept_polygon) for kept_polygon in kept_polygons):
            continue
        kept.append({**stall, "id": f"PL-STALL-{len(kept) + 1:03d}"})
        kept_polygons.append(stall_polygon)
    return kept


def _stall_conflict_priority(stall: dict[str, object], aisle_roles: dict[str, str]) -> tuple[int, int]:
    served_by = str(stall.get("served_by_aisle_id"))
    role = aisle_roles.get(served_by, "")
    source = str(stall.get("source", ""))
    if role == "connector":
        return (0, 0)
    if source == "current_layout":
        return (1, 0)
    if role == "main":
        return (2, 0)
    if role == "branch":
        return (3, 0)
    return (4, 0)


def _preview_stalls_matching_layout(stalls: list[dict[str, object]], layout: LayoutResult) -> list[dict[str, object]]:
    kept_geometries = {_polygon_key(stall.polygon) for stall in layout.stalls}
    return [stall for stall in stalls if _polygon_key(stall.get("geometry")) in kept_geometries]


def _renumber_preview_stalls(stalls: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**stall, "id": f"PL-STALL-{index:03d}"}
        for index, stall in enumerate(stalls, start=1)
    ]


def _overlaps_non_serving_aisle(stall: ShapelyPolygon, served_by: str, aisle_polygons: dict[str, ShapelyPolygon]) -> bool:
    for aisle_id, aisle_polygon in aisle_polygons.items():
        if aisle_id == served_by:
            continue
        if _area_overlap(stall, aisle_polygon):
            return True
    return False


def _area_overlap(left: ShapelyPolygon, right: ShapelyPolygon) -> bool:
    return left.intersection(right).area > 1e-6


def _polygon_key(raw: object) -> tuple[tuple[float, float], ...]:
    if not _valid_polygon(raw):
        return ()
    return tuple(sorted((round(float(x), 6), round(float(y), 6)) for x, y in raw))


def _validate_layout_preview(
    source_layout: LayoutResult,
    preview_layout: LayoutResult,
    aisles: list[dict[str, object]],
    stalls: list[dict[str, object]],
) -> dict[str, object]:
    containment = _containment_validation(source_layout, aisles, stalls)
    maneuver = validate_maneuvers(preview_layout)
    graph = traffic_graph_summary(preview_layout)
    operational = operational_quality_report(preview_layout)
    site_constraints = validate_site_constraints(preview_layout)
    validated_preview = replace(
        preview_layout,
        maneuver_validation=maneuver,
        site_constraint_validation=site_constraints,
        operational_quality=operational,
    )
    from openparkcad.road_traversal import apply_road_traversal, road_traversal_publication_error

    validated_preview = apply_road_traversal(validated_preview, result_layout_id="candidate_preview")
    engineering = build_engineering_validation(validated_preview, result_scope="candidate_preview")
    association = _stall_association_validation(aisles, stalls)
    errors: list[str] = []
    if not containment["valid"]:
        errors.append("preview_layout_geometry_outside_usable_area")
    if not maneuver["valid"]:
        errors.append("preview_layout_maneuver_invalid")
    if not graph["valid"]:
        errors.append("preview_layout_traffic_graph_invalid")
    if not operational["valid"]:
        errors.append("preview_layout_operational_quality_invalid")
    if not site_constraints["valid"]:
        errors.append("preview_layout_site_constraints_invalid")
    if not association["valid"]:
        errors.append("preview_layout_stall_association_invalid")
    if road_traversal_publication_error(validated_preview):
        errors.append("preview_layout_road_traversal_invalid")
    return {
        "version": "phase4c-1",
        "status": "preview_only",
        "valid": not errors,
        "errors": errors,
        "geometry_containment": containment,
        "stall_association": association,
        "maneuver_validation": maneuver,
        "site_constraint_validation": site_constraints,
        "engineering_validation": engineering,
        "operational_quality": operational,
        "traffic_graph": graph,
        "road_traversal_validation": validated_preview.road_traversal_validation,
    }


def _preview_score(
    layout: LayoutResult,
    aisles: list[dict[str, object]],
    stalls: list[dict[str, object]],
) -> dict[str, float]:
    preview_layout = _preview_layout(layout, aisles, stalls)
    operational = operational_quality_report(preview_layout)
    metrics = {
        "stall_count": float(len(stalls)),
        "aisle_area": sum(float(aisle.get("area", 0.0)) for aisle in aisles),
        "heading_delta": abs(layout.selected_heading_delta_degrees),
        "entrance_offset": abs(layout.selected_entrance_offset),
        "branch_count": float(len([aisle for aisle in aisles if aisle.get("role") == "branch"])),
        "dead_end_length": _preview_dead_end_length(aisles),
        "operational_risk": float(operational["risk_score"]),
    }
    metrics.update(stall_family_score_metrics(layout.site, stalls))
    return score_metrics(layout.site, metrics)


def _layout_comparison(
    layout: LayoutResult,
    aisles: list[dict[str, object]],
    stalls: list[dict[str, object]],
    preview_score: dict[str, float],
    validation: dict[str, object],
) -> dict[str, object]:
    current_score_total = float(layout.score.get("total", 0.0))
    preview_score_total = float(preview_score.get("total", 0.0))
    score_delta = preview_score_total - current_score_total
    stall_delta = len(stalls) - layout.stall_count
    validation_valid = bool(validation.get("valid"))
    promotion_blockers = _promotion_blockers(
        validation,
        score_delta,
        aisle_count=len(aisles),
        stall_count=len(stalls),
    )
    return {
        "version": "phase4c-2a",
        "status": "preview_only",
        "current_layout": {
            "stall_count": layout.stall_count,
            "aisle_count": len(layout.aisles),
            "score_total": current_score_total,
        },
        "candidate_preview": {
            "stall_count": len(stalls),
            "aisle_count": len(aisles),
            "score_total": preview_score_total,
            "validation_valid": validation_valid,
        },
        "stall_delta": stall_delta,
        "score_delta": score_delta,
        "promotion_eligible": not promotion_blockers,
        "promotion_blockers": promotion_blockers,
        "reason": _comparison_reason(promotion_blockers),
    }


def _promotion_blockers(
    validation: dict[str, object],
    score_delta: float,
    aisle_count: int | None = None,
    stall_count: int | None = None,
) -> list[str]:
    blockers: list[str] = []
    if aisle_count is not None and aisle_count <= 0:
        blockers.append("preview_layout_has_no_aisles")
    if stall_count is not None and stall_count <= 0:
        blockers.append("preview_layout_has_no_stalls")
    if not bool(validation.get("valid")):
        blockers.append("preview_validation_failed")
    blockers.extend(_operational_quality_blockers(validation))
    if _dead_ends_without_turnaround(validation):
        blockers.append("preview_has_dead_end_without_turnaround")
    if score_delta < 0:
        blockers.append("preview_score_lower_than_current_layout")
    return blockers


def _comparison_reason(promotion_blockers: list[str]) -> str:
    if promotion_blockers:
        return promotion_blockers[0]
    return "preview_valid_and_score_not_lower_than_current_layout"


def _dead_ends_without_turnaround(validation: dict[str, object]) -> list[dict[str, object]]:
    graph = validation.get("traffic_graph", {})
    if not isinstance(graph, dict):
        return []
    dead_ends = graph.get("dead_ends", [])
    if not isinstance(dead_ends, list):
        return []
    return [
        item
        for item in dead_ends
        if isinstance(item, dict) and item.get("status") == "dead_end_without_turnaround"
    ]


def _operational_quality_blockers(validation: dict[str, object]) -> list[str]:
    operational = validation.get("operational_quality", {})
    if not isinstance(operational, dict):
        return []
    blockers = operational.get("promotion_blockers", [])
    if not isinstance(blockers, list):
        return []
    return [str(item) for item in blockers]


def _preview_dead_end_length(aisles: list[dict[str, object]]) -> float:
    connected_branch_source_ids = {
        str(branch_id)
        for aisle in aisles
        if aisle.get("role") == "connector"
        for branch_id in _metadata_list(aisle, "connects")
    }
    total = 0.0
    for aisle in aisles:
        role = aisle.get("role")
        if role == "main":
            total += _long_side_length(aisle.get("geometry"))
        elif role == "branch" and str(aisle.get("source_id")) not in connected_branch_source_ids:
            total += _branch_length(aisle)
    return total


def _branch_length(aisle: dict[str, object]) -> float:
    metadata = aisle.get("metadata", {})
    if isinstance(metadata, dict):
        length = metadata.get("length")
        if isinstance(length, int | float):
            return float(length)
    return _long_side_length(aisle.get("geometry"))


def _metadata_list(aisle: dict[str, object], key: str) -> list[object]:
    metadata = aisle.get("metadata", {})
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get(key, [])
    if not isinstance(raw, list | tuple):
        return []
    return list(raw)


def _long_side_length(raw_polygon: object) -> float:
    if not _valid_polygon(raw_polygon):
        return 0.0
    points = [(float(x), float(y)) for x, y in raw_polygon]
    lengths = []
    for start, end in zip(points, points[1:] + points[:1]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        lengths.append((dx * dx + dy * dy) ** 0.5)
    return max(lengths or [0.0])


def _containment_validation(
    layout: LayoutResult,
    aisles: list[dict[str, object]],
    stalls: list[dict[str, object]],
) -> dict[str, object]:
    usable = available_area(layout.site)
    outside_aisles = _outside_items(usable, aisles, "aisle")
    outside_stalls = _outside_items(usable, stalls, "stall")
    return {
        "valid": not outside_aisles and not outside_stalls,
        "checked_aisle_count": len(aisles),
        "checked_stall_count": len(stalls),
        "outside_aisles": outside_aisles,
        "outside_stalls": outside_stalls,
    }


def _outside_items(usable, items: list[dict[str, object]], item_type: str) -> list[dict[str, object]]:
    outside: list[dict[str, object]] = []
    for item in items:
        geometry = item.get("geometry")
        if not _valid_polygon(geometry):
            outside.append({"id": item.get("id"), "type": item_type, "reason": "missing_or_invalid_geometry"})
            continue
        polygon = ShapelyPolygon(geometry)
        if not usable.covers(polygon):
            outside.append(
                {
                    "id": item.get("id"),
                    "type": item_type,
                    "outside_area": float(polygon.difference(usable).area),
                }
            )
    return outside


def _stall_association_validation(aisles: list[dict[str, object]], stalls: list[dict[str, object]]) -> dict[str, object]:
    aisle_ids = {str(aisle["id"]) for aisle in aisles}
    missing = [
        str(stall["id"])
        for stall in stalls
        if str(stall.get("served_by_aisle_id")) not in aisle_ids
    ]
    return {
        "valid": not missing,
        "checked_stall_count": len(stalls),
        "stalls_missing_aisles": missing,
    }


def _preview_stalls_from_report(preview: dict[str, object]) -> list[dict[str, object]]:
    raw = preview.get("stalls", [])
    if not isinstance(raw, list):
        return []
    return [stall for stall in raw if isinstance(stall, dict) and _valid_polygon(stall.get("geometry"))]


def _preview_layout(
    layout: LayoutResult,
    aisles: list[dict[str, object]],
    stalls: list[dict[str, object]],
) -> LayoutResult:
    source_to_preview_id = _source_to_preview_id(aisles)
    return LayoutResult(
        site=layout.site,
        aisles=[_parking_aisle(item, layout, source_to_preview_id) for item in aisles],
        stalls=[_parking_stall(item) for item in stalls],
        generation_mode="candidate_layout_preview",
        main_entrance_id=layout.main_entrance_id,
        selected_heading_degrees=layout.selected_heading_degrees,
        selected_heading_delta_degrees=layout.selected_heading_delta_degrees,
        selected_entrance_offset=layout.selected_entrance_offset,
    )


def _parking_aisle(raw: dict[str, object], layout: LayoutResult, source_to_preview_id: dict[str, str]) -> ParkingAisle:
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    parent_aisle_id = _mapped_parent_id(raw, metadata, source_to_preview_id)
    connected_aisle_ids = _mapped_connected_ids(metadata, source_to_preview_id)
    directionality = metadata.get("directionality")
    if not isinstance(directionality, str):
        directionality = aisle_directionality(layout.site)
    return ParkingAisle(
        id=str(raw["id"]),
        polygon=[(float(x), float(y)) for x, y in raw["geometry"]],
        angle_degrees=_float(metadata.get("angle_degrees"), layout.selected_angle_degrees),
        role=str(raw["role"]),
        connected_to_entrance_id=_optional_str(metadata.get("connected_to_entrance_id")),
        parent_aisle_id=parent_aisle_id,
        connected_aisle_ids=connected_aisle_ids,
        directionality=directionality if directionality in {"one_way", "two_way"} else "two_way",
    )


def _parking_stall(raw: dict[str, object]) -> ParkingStall:
    return ParkingStall(
        id=str(raw["id"]),
        polygon=[(float(x), float(y)) for x, y in raw["geometry"]],
        angle_degrees=_float(raw.get("angle_degrees"), 0.0),
        served_by_aisle_id=_optional_str(raw.get("served_by_aisle_id")),
        aisle_side=_optional_str(raw.get("aisle_side")),
        stall_type_id=_optional_str(raw.get("stall_type_id")),
    )


def _source_to_preview_id(aisles: list[dict[str, object]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for aisle in aisles:
        aisle_id = aisle.get("id")
        if not aisle_id:
            continue
        for key in ("candidate_id", "source_id"):
            value = aisle.get(key)
            if value:
                mapping.setdefault(str(value), str(aisle_id))
    return mapping


def _selected_module_stall_allowance(layout: LayoutResult) -> dict[str, object] | None:
    """If stall modules exist, preview stalls must belong to a selected module."""
    modules = [item for item in layout.candidate_objects if item.kind == STALL_MODULE_KIND]
    if not modules:
        return None
    selected_ids = {str(item) for item in layout.candidate_selection.get("selected_ids", [])}
    official: set[str] = set()
    generated: set[tuple[str, str]] = set()
    family_generated: dict[str, list[dict[str, object]]] = {}
    family_generated_by_candidate: dict[str, list[dict[str, object]]] = {}
    for module in modules:
        if module.id not in selected_ids:
            continue
        stall_ids = module.metadata.get("stall_source_ids", [])
        if not isinstance(stall_ids, list | tuple):
            continue
        if module.metadata.get("shadow_family"):
            raw = module.metadata.get("generated_stalls", [])
            stalls = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            if module.metadata.get("parent_is_base"):
                served = str(module.metadata.get("served_by_aisle_id") or "")
                if served and stalls:
                    family_generated.setdefault(served, []).extend(stalls)
            else:
                parent = str(module.metadata.get("parent_candidate_id") or "")
                if parent and stalls:
                    family_generated_by_candidate.setdefault(parent, []).extend(stalls)
            continue
        if module.metadata.get("parent_is_base"):
            official.update(str(item) for item in stall_ids)
            continue
        parent = str(module.metadata.get("parent_candidate_id") or "")
        generated.update((parent, str(item)) for item in stall_ids if item)
    return {
        "official": official,
        "generated": generated,
        "family_generated": family_generated,
        "family_generated_by_candidate": family_generated_by_candidate,
    }


def _current_layout_source_to_preview_id(aisles: list[dict[str, object]]) -> dict[str, str]:
    return {
        str(aisle["source_id"]): str(aisle["id"])
        for aisle in aisles
        if aisle.get("source_id")
        and aisle.get("id")
        and aisle.get("kind") == "aisle"
        and aisle.get("role") in {"main"}
    }


def _mapped_parent_id(raw: dict[str, object], metadata: dict[str, object], source_to_preview_id: dict[str, str]) -> str | None:
    parent_candidate = metadata.get("parent_candidate_id")
    if isinstance(parent_candidate, str):
        mapped = source_to_preview_id.get(parent_candidate)
        if mapped:
            return mapped
    parent = metadata.get("parent_aisle_id")
    if isinstance(parent, str) and parent in source_to_preview_id:
        return source_to_preview_id[parent]
    parent_ids = raw.get("parent_ids", [])
    if not isinstance(parent_ids, list | tuple):
        return None
    for parent_id in parent_ids:
        mapped = source_to_preview_id.get(str(parent_id))
        if mapped:
            return mapped
    return None


def _mapped_connected_ids(metadata: dict[str, object], source_to_preview_id: dict[str, str]) -> tuple[str, ...]:
    raw = metadata.get("connects") or metadata.get("connected_aisle_ids") or []
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(source_to_preview_id[item] for item in (str(value) for value in raw) if item in source_to_preview_id)


def _valid_polygon(raw: object) -> bool:
    if not isinstance(raw, list) or len(raw) < 3:
        return False
    return all(isinstance(item, list | tuple) and len(item) == 2 for item in raw)


def _optional_str(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    return str(raw)


def _float(raw: Any, default: float) -> float:
    if isinstance(raw, int | float):
        return float(raw)
    return default
