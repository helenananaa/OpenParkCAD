from __future__ import annotations

from dataclasses import replace

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.candidate_catalog import (
    PROMOTION_VERSION,
    SNAPSHOT_VERSION,
    SPINE_AISLE_ROLES,
    STALL_MODULE_KIND,
    STALL_MODULE_ROLE,
    catalog_class,
    stall_module_segment_stalls,
)
from openparkcad.candidate_layout_preview import build_candidate_layout_preview, candidate_layout_preview_layout
from openparkcad.candidate_network_preview import build_candidate_network_preview
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.layout_geometry import available_area, normalized_local_box_to_world, polygon_points
from openparkcad.phase1_candidates import (
    ConnectorGeometry,
    _offset_entrance,
    _world_to_local,
    place_branch_family_stalls,
    place_connector_family_stalls,
    place_main_family_stalls,
)
from openparkcad.phase1_support import supports_phase1_stall
from openparkcad.maneuver_validation import validate_maneuvers
from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle, ParkingStall, Polygon
from openparkcad.operational_quality import operational_quality_report
from openparkcad.scoring import score_layout
from openparkcad.site_constraints import validate_site_constraints
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def attach_candidate_snapshot(layout: LayoutResult) -> LayoutResult:
    objects = build_candidate_objects(layout)
    selection = select_candidate_objects(objects, layout.site)
    enriched = replace(layout, candidate_objects=objects, candidate_selection=selection)
    enriched = replace(enriched, candidate_network_preview=build_candidate_network_preview(enriched))
    enriched = replace(enriched, candidate_layout_preview=build_candidate_layout_preview(enriched))
    return _maybe_promote_candidate_layout(enriched)


def build_candidate_objects(layout: LayoutResult) -> list[CandidateObject]:
    objects: list[CandidateObject] = []
    objects.extend(_main_attempt_candidates(layout))
    attempt_candidates = _branch_and_connector_attempt_candidates(layout)
    objects.extend(attempt_candidates)
    objects.extend(_synthetic_connector_candidates(layout, attempt_candidates))
    objects.extend(_selected_aisle_candidates(layout))
    objects.extend(_stall_module_candidates(layout, objects))
    objects.extend(_selected_stall_candidates(layout))
    return _with_conflicts(_deduplicate(objects))


def candidate_snapshot_report(layout: LayoutResult) -> dict[str, object]:
    objects = layout.candidate_objects or build_candidate_objects(layout)
    conflict_matrix = _conflict_matrix(objects)
    selection = layout.candidate_selection if isinstance(layout.candidate_selection, dict) else {}
    if selection:
        reported_selection = dict(selection)
    else:
        reported_selection = {
            "status": "not_recorded",
            "reason": "selection_not_attached_to_layout",
            "selected_ids": [],
            "selected_count": 0,
            "selected_branch_count": 0,
        }
    return {
        "version": SNAPSHOT_VERSION,
        "object_count": len(objects),
        "status_counts": _status_counts(objects),
        "catalog_counts": _catalog_counts(objects),
        "conflict_count": len(conflict_matrix),
        "conflict_matrix": conflict_matrix,
        "selection": reported_selection,
        "objects": [_candidate_report(item) for item in objects],
    }


def attach_official_selection_evidence(source: LayoutResult, official: LayoutResult) -> LayoutResult:
    """Keep the selector decision that produced ``official``; do not re-solve."""
    selection = dict(source.candidate_selection) if isinstance(source.candidate_selection, dict) else {}
    return replace(
        official,
        candidate_objects=list(source.candidate_objects),
        candidate_selection=selection,
        selected_branches=_catalog_selected_branches(source, official),
        selected_connectors=_catalog_selected_connectors(source, official),
        candidate_network_preview=dict(source.candidate_network_preview or {}),
        candidate_layout_preview=dict(source.candidate_layout_preview or {}),
    )


def finalize_official_selection_evidence(layout: LayoutResult) -> LayoutResult:
    """Publish stored selector evidence after a rebuilt candidate wins search."""
    if layout.generation_mode != "candidate_layout_promoted":
        return layout
    promoted = replace(
        layout,
        candidate_network_preview=_promoted_network_preview(layout, layout),
        candidate_layout_preview=_promoted_layout_preview(layout, layout),
    )
    return replace(promoted, candidate_layout_promotion=_promotion_report(promoted, "promoted", selection_source=layout))


def _maybe_promote_candidate_layout(layout: LayoutResult) -> LayoutResult:
    if not layout.site.optimization.get("promote_candidate_layout_preview", False):
        return replace(layout, candidate_layout_promotion=_promotion_report(layout, "not_requested"))

    comparison = layout.candidate_layout_preview.get("comparison", {})
    if not isinstance(comparison, dict) or not comparison.get("promotion_eligible"):
        return replace(layout, candidate_layout_promotion=_promotion_report(layout, "rejected"))

    preview_layout = candidate_layout_preview_layout(layout)
    official_geom = rebuild_official_layout_from_selection(layout, preview_layout)
    promoted = replace(
        layout,
        aisles=official_geom.aisles,
        stalls=official_geom.stalls,
        generation_mode="candidate_layout_promoted",
    )
    promoted = _with_recomputed_validation(promoted)
    if not _promoted_official_valid(promoted):
        rejected = replace(layout, candidate_layout_promotion=_promotion_report(layout, "rejected", extra_blockers=["official_rebuild_revalidation_failed"]))
        return rejected

    # Candidate selection describes the pre-promotion decision, while selected
    # aisle/stall objects must describe the official post-promotion layout.
    official_objects = build_candidate_objects(promoted)
    official_selection = select_candidate_objects(official_objects, promoted.site)
    promoted = replace(
        promoted,
        candidate_objects=official_objects,
        candidate_selection=official_selection,
        selected_branches=_catalog_selected_branches(layout, promoted),
        selected_connectors=_catalog_selected_connectors(layout, promoted),
        candidate_network_preview=_promoted_network_preview(layout, promoted),
        candidate_layout_preview=_promoted_layout_preview(layout, promoted),
    )
    return replace(promoted, candidate_layout_promotion=_promotion_report(promoted, "promoted", selection_source=layout))


def rebuild_official_layout_from_selection(source: LayoutResult, preview: LayoutResult) -> LayoutResult:
    """Rewrite preview aisles onto catalog source ids and official stall numbers."""
    id_map = _preview_to_official_id_map(source)
    aisles = [
        replace(
            aisle,
            id=id_map.get(aisle.id, aisle.id),
            parent_aisle_id=_remap_id(aisle.parent_aisle_id, id_map),
            connected_aisle_ids=tuple(_remap_id(item, id_map) or item for item in aisle.connected_aisle_ids),
            connected_to_entrance_id=aisle.connected_to_entrance_id,
        )
        for aisle in preview.aisles
    ]
    stalls = [
        replace(
            stall,
            served_by_aisle_id=_remap_id(stall.served_by_aisle_id, id_map),
        )
        for stall in preview.stalls
    ]
    return replace(preview, aisles=aisles, stalls=_official_stalls(stalls), generation_mode="candidate_layout_promoted")


def _preview_to_official_id_map(layout: LayoutResult) -> dict[str, str]:
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for item in _preview_aisles(layout):
        preview_id = str(item.get("id") or "")
        source_id = str(item.get("source_id") or "")
        if not preview_id:
            continue
        official_id = _unique_official_id(source_id or preview_id, used)
        used.add(official_id)
        mapping[preview_id] = official_id
    return mapping


def _unique_official_id(preferred: str, used: set[str]) -> str:
    if preferred not in used:
        return preferred
    index = 2
    while f"{preferred}-{index}" in used:
        index += 1
    return f"{preferred}-{index}"


def _remap_id(raw: str | None, id_map: dict[str, str]) -> str | None:
    if raw is None:
        return None
    return id_map.get(raw, raw)


def _promoted_official_valid(layout: LayoutResult) -> bool:
    from openparkcad.road_traversal import road_traversal_satisfied

    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    site = layout.site_constraint_validation if isinstance(layout.site_constraint_validation, dict) else {}
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    operational = layout.operational_quality if isinstance(layout.operational_quality, dict) else {}
    return bool(
        layout.aisles
        and layout.stalls
        and graph.get("valid")
        and maneuver.get("valid")
        and site.get("valid")
        and engineering.get("valid")
        and operational.get("valid", True)
        and road_traversal_satisfied(layout)
    )


def _with_recomputed_validation(layout: LayoutResult) -> LayoutResult:
    from openparkcad.road_traversal import apply_road_traversal

    maneuver = validate_maneuvers(layout)
    preview_validation = layout.candidate_layout_preview.get("validation", {})
    if isinstance(preview_validation, dict):
        preview_maneuver = preview_validation.get("maneuver_validation", {})
        if isinstance(preview_maneuver, dict):
            for key in ("filtered_stall_ids", "filtered_stall_count", "pre_filter_invalid_stalls"):
                if key in preview_maneuver:
                    maneuver[key] = preview_maneuver[key]
    graph = validate_traffic_graph(build_traffic_graph(layout), layout)
    site_constraints = validate_site_constraints(layout)
    operational = operational_quality_report(layout)
    validated = replace(
        layout,
        maneuver_validation=maneuver,
        graph_validation=graph,
        site_constraint_validation=site_constraints,
        operational_quality=operational,
    )
    validated = apply_road_traversal(validated, result_layout_id=_layout_identity_label(validated))
    validated = replace(validated, engineering_validation=build_engineering_validation(validated))
    return replace(validated, score=score_layout(validated))


def _layout_identity_label(layout: LayoutResult) -> str:
    return ",".join([aisle.id for aisle in layout.aisles] + [stall.id for stall in layout.stalls])


def _official_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [replace(stall, id=f"P-{index:03d}") for index, stall in enumerate(stalls, start=1)]


def _promoted_network_preview(source: LayoutResult, official: LayoutResult) -> dict[str, object]:
    preview = dict(source.candidate_network_preview)
    preview["status"] = "promoted_to_official"
    preview["official_output_replaced"] = True
    preview["official_aisle_ids"] = [aisle.id for aisle in official.aisles]
    preview["notes"] = [
        "This candidate network was validated and promoted to the official layout.",
        "candidate_id and source_id retain the pre-promotion decision provenance.",
    ]
    return preview


def _promoted_layout_preview(source: LayoutResult, official: LayoutResult) -> dict[str, object]:
    preview = dict(source.candidate_layout_preview)
    preview["status"] = "promoted_to_official"
    preview["official_output_replaced"] = True
    preview["official_aisle_ids"] = [aisle.id for aisle in official.aisles]
    preview["official_stall_ids"] = [stall.id for stall in official.stalls]
    preview["score"] = dict(official.score)
    comparison = dict(preview.get("comparison", {})) if isinstance(preview.get("comparison"), dict) else {}
    comparison["status"] = "promoted_to_official"
    comparison["candidate_preview"] = {
        "stall_count": official.stall_count,
        "aisle_count": len(official.aisles),
        "score_total": float(official.score.get("total", 0.0)),
        "validation_valid": True,
    }
    preview["comparison"] = comparison
    validation = dict(preview.get("validation", {})) if isinstance(preview.get("validation"), dict) else {}
    validation.update(
        {
            "status": "official",
            "valid": bool(
                official.graph_validation.get("valid")
                and official.maneuver_validation.get("valid")
                and official.site_constraint_validation.get("valid")
                and official.engineering_validation.get("valid")
                and official.operational_quality.get("valid", True)
            ),
            "traffic_graph": official.graph_validation,
            "maneuver_validation": official.maneuver_validation,
            "site_constraint_validation": official.site_constraint_validation,
            "engineering_validation": official.engineering_validation,
            "operational_quality": official.operational_quality,
        }
    )
    preview["validation"] = validation
    preview["notes"] = [
        "This candidate layout was validated and promoted to the official DXF, SVG, and report output.",
        "source_id values retain the pre-promotion candidate provenance.",
    ]
    return preview


def _preview_aisles(layout: LayoutResult) -> list[dict[str, object]]:
    raw = layout.candidate_network_preview.get("aisles", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _catalog_selected_branches(pre_layout: LayoutResult, promoted: LayoutResult) -> list[dict[str, object]]:
    official_ids = {aisle.id for aisle in promoted.aisles}
    selected: list[dict[str, object]] = []
    for candidate in _selected_variable_candidates(pre_layout, role="branch"):
        source_id = str(candidate.metadata.get("source_id") or "")
        official_id = source_id if source_id in official_ids else None
        if official_id is None:
            continue
        selected.append(
            {
                "id": official_id,
                "source_id": source_id,
                "side": candidate.metadata.get("side"),
                "start_u": candidate.metadata.get("start_u"),
                "length": candidate.metadata.get("length"),
                "parent_aisle_id": candidate.metadata.get("parent_aisle_id"),
            }
        )
    return selected


def _catalog_selected_connectors(pre_layout: LayoutResult, promoted: LayoutResult) -> list[dict[str, object]]:
    official_ids = {aisle.id for aisle in promoted.aisles}
    selected: list[dict[str, object]] = []
    for candidate in _selected_variable_candidates(pre_layout, role="connector"):
        source_id = str(candidate.metadata.get("source_id") or "")
        if source_id not in official_ids:
            continue
        source_connects = [str(item) for item in candidate.metadata.get("connects", [])] if isinstance(candidate.metadata.get("connects"), list | tuple) else []
        official_connects = [item for item in source_connects if item in official_ids]
        selected.append(
            {
                "id": source_id,
                "source_id": source_id,
                "source_connects": source_connects,
                "connects": official_connects,
            }
        )
    return selected


def _selected_variable_candidates(layout: LayoutResult, *, role: str) -> list[CandidateObject]:
    selected_ids = {str(item) for item in layout.candidate_selection.get("selected_ids", [])}
    return [
        item
        for item in layout.candidate_objects
        if item.id in selected_ids and item.role == role
    ]


def _promotion_report(
    layout: LayoutResult,
    status: str,
    extra_blockers: list[str] | None = None,
    *,
    selection_source: LayoutResult | None = None,
) -> dict[str, object]:
    comparison = layout.candidate_layout_preview.get("comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}
    blockers = comparison.get("promotion_blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    blocker_list = [str(item) for item in blockers]
    if extra_blockers:
        blocker_list.extend(extra_blockers)
    decision = selection_source if selection_source is not None else layout
    selection = decision.candidate_selection if isinstance(decision.candidate_selection, dict) else {}
    report: dict[str, object] = {
        "version": PROMOTION_VERSION,
        "requested": bool(layout.site.optimization.get("promote_candidate_layout_preview", False)),
        "status": status,
        "source_preview_version": layout.candidate_layout_preview.get("version"),
        "promotion_eligible": bool(comparison.get("promotion_eligible", False)) and not extra_blockers,
        "reason": extra_blockers[0] if extra_blockers else _promotion_reason(status, comparison),
        "blockers": blocker_list,
        "official_output_replaced": status == "promoted",
        "official_id_scheme": "catalog_source_id" if status == "promoted" else None,
        "pre_promotion_backend": selection.get("backend"),
        "pre_promotion_requested_backend": selection.get("requested_backend"),
        "pre_promotion_selected_ids": list(selection.get("selected_ids", [])),
    }
    if status == "promoted":
        id_map = _preview_to_official_id_map(layout)
        preview_stalls = candidate_layout_preview_layout(layout).stalls
        report.update(
            {
                "official_aisle_ids": [aisle.id for aisle in layout.aisles],
                "official_stall_ids": [stall.id for stall in layout.stalls],
                "candidate_to_official_aisle_ids": {
                    str(item.get("candidate_id")): id_map.get(str(item.get("id")), str(item.get("source_id") or item.get("id")))
                    for item in _preview_aisles(layout)
                    if item.get("candidate_id") is not None
                },
                "preview_to_official_aisle_ids": id_map,
                "preview_to_official_stall_ids": {
                    preview.id: official.id
                    for preview, official in zip(preview_stalls, layout.stalls, strict=True)
                },
                "pre_promotion_solver_provenance": dict(selection.get("solver_provenance") or {}),
            }
        )
    return report


def _promotion_reason(status: str, comparison: dict[str, object]) -> str:
    if status == "not_requested":
        return "promote_candidate_layout_preview_not_enabled"
    if status == "promoted":
        return "candidate_layout_preview_promoted_to_official_output"
    reason = comparison.get("reason")
    return str(reason) if reason else "candidate_layout_preview_not_promotion_eligible"


def _main_attempt_candidates(layout: LayoutResult) -> list[CandidateObject]:
    candidates: list[CandidateObject] = []
    selected_key = (
        layout.main_entrance_id,
        layout.selected_heading_degrees,
        layout.selected_entrance_offset,
    )
    for index, attempt in enumerate(layout.attempts, start=1):
        key = (
            attempt.entrance_id,
            attempt.angle_degrees,
            attempt.entrance_offset,
        )
        selected = _same_main_attempt(key, selected_key)
        status = "selected" if selected else ("rejected" if attempt.graph_valid else "invalid")
        candidates.append(
            CandidateObject(
                id=f"C-MAIN-{index:03d}",
                kind="aisle_skeleton",
                role="main",
                status=status,
                score_features={
                    "stall_count": float(attempt.stall_count),
                    "heading_delta_degrees": float(attempt.heading_delta_degrees),
                    "entrance_offset": float(attempt.entrance_offset),
                },
                metadata={
                    "entrance_id": attempt.entrance_id,
                    "heading_degrees": attempt.angle_degrees,
                    "heading_delta_degrees": attempt.heading_delta_degrees,
                    "entrance_offset": attempt.entrance_offset,
                    "branch_side": attempt.branch_side,
                    "branch_start_u": attempt.branch_start_u,
                    "branch_length": attempt.branch_length,
                    "graph_valid": attempt.graph_valid,
                    "graph_errors": attempt.graph_errors,
                    "selection_class": catalog_class(kind="aisle_skeleton", role="main"),
                },
            )
        )
    return candidates


def _branch_and_connector_attempt_candidates(layout: LayoutResult) -> list[CandidateObject]:
    candidates: list[CandidateObject] = []
    sequence = 1
    for attempt in layout.attempts:
        for diagnostic in _flatten_attempt_diagnostics(attempt.branch_candidates):
            source_id = str(diagnostic.get("branch_id") or diagnostic.get("connector_id") or "")
            if not source_id:
                continue
            is_connector = "connector_id" in diagnostic
            candidate_id = f"{source_id}-TRY-{sequence:03d}"
            selected = (
                _connector_attempt_selected(diagnostic, layout.selected_connectors)
                if is_connector
                else _branch_attempt_selected(diagnostic, layout.selected_branches)
            )
            candidates.append(_attempt_candidate(candidate_id, source_id, diagnostic, selected, is_connector))
            sequence += 1
    return candidates


def _flatten_attempt_diagnostics(diagnostics: list[dict[str, object]]) -> list[dict[str, object]]:
    """Dogleg/multi-jog nest branch trials under branch_diagnostics."""
    flat: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        nested = diagnostic.get("branch_diagnostics")
        if isinstance(nested, list):
            flat.extend(item for item in nested if isinstance(item, dict))
        if diagnostic.get("branch_id") or diagnostic.get("connector_id"):
            flat.append(diagnostic)
    return flat


def _attempt_candidate(
    candidate_id: str,
    source_id: str,
    diagnostic: dict[str, object],
    selected: bool,
    is_connector: bool,
) -> CandidateObject:
    reason = str(diagnostic.get("reason", "candidate_evaluated"))
    graph_valid = bool(diagnostic.get("graph_valid", True))
    status = "selected" if selected else ("rejected" if graph_valid else "invalid")
    role = "connector" if is_connector else "branch"
    if is_connector:
        parent_ids = tuple(str(item) for item in diagnostic.get("connects", []))
    else:
        parent = diagnostic.get("parent_aisle_id") or diagnostic.get("spine_parent") or "A-MAIN"
        parent_ids = (str(parent),)
    score_features: dict[str, float] = {}
    for key in ("stall_count", "base_stall_count", "removed_stalls", "added_stalls", "length", "start_u", "connector_inset_depth"):
        value = diagnostic.get(key)
        if isinstance(value, int | float):
            score_features[key] = float(value)
    return CandidateObject(
        id=f"C-{candidate_id}",
        kind="aisle_skeleton",
        role=role,
        status=status,
        geometry=_diagnostic_geometry(diagnostic),
        parent_ids=parent_ids,
        score_features=score_features,
        metadata={
            **diagnostic,
            "source_id": source_id,
            "accepted_reason": reason if selected else None,
            "parent_aisle_id": parent_ids[0] if parent_ids and not is_connector else diagnostic.get("parent_aisle_id"),
            "selection_class": catalog_class(kind="aisle_skeleton", role=role),
        },
    )


def _synthetic_connector_candidates(layout: LayoutResult, candidates: list[CandidateObject]) -> list[CandidateObject]:
    if not layout.site.optimization.get("enable_connectors", True):
        return []
    entrance = next((item for item in layout.site.entrances if item.id == layout.main_entrance_id), None)
    if entrance is None:
        return []
    heading = layout.selected_heading_degrees
    if heading is None:
        return []
    branches = _best_shadow_branches_by_source(candidates)
    existing_keys = _existing_connector_keys(candidates)
    synthetic: list[CandidateObject] = []
    for left, right in _synthetic_connector_pairs(branches):
        connects = tuple(sorted((_source_id(left), _source_id(right))))
        if connects in existing_keys:
            continue
        for inset_depth in _connector_inset_depths(layout.site, left, right):
            connector_result = _synthetic_connector_geometry(layout, entrance, heading, left, right, inset_depth)
            if connector_result is None:
                continue
            connector, actual_inset_depth = connector_result
            status = "rejected"
            if not available_area(layout.site).covers(ShapelyPolygon(connector)):
                status = "invalid"
            sequence = len(synthetic) + 1
            synthetic.append(
                CandidateObject(
                    id=f"C-SHADOW-CONNECTOR-{sequence:03d}",
                    kind="aisle_skeleton",
                    role="connector",
                    status=status,
                    geometry=connector,
                    parent_ids=(connects[0], connects[1]),
                    score_features={
                        "added_stalls": 0.0,
                        "removed_stalls": 0.0,
                        "length": abs(_start_u(left) - _start_u(right)),
                        "connector_inset_depth": actual_inset_depth,
                    },
                    metadata={
                        "source_id": f"A-SHADOW-CONNECTOR-{sequence:03d}",
                        "reason": "shadow_connector_synthesized" if status != "invalid" else "shadow_connector_outside_usable_area",
                        "selection_class": catalog_class(kind="aisle_skeleton", role="connector"),
                        "connects": list(connects),
                        "side": _side(left),
                        "start_u_min": min(_start_u(left), _start_u(right)),
                        "start_u_max": max(_start_u(left), _start_u(right)),
                        "length": abs(_start_u(left) - _start_u(right)),
                        "connector_inset_depth": actual_inset_depth,
                        "removed_turnarounds": [f"{connects[0]}-TURNAROUND", f"{connects[1]}-TURNAROUND"],
                        "generated_stalls": [],
                        "synthetic": True,
                    },
                )
            )
    return synthetic


def _best_shadow_branches_by_source(candidates: list[CandidateObject]) -> list[CandidateObject]:
    best: dict[str, CandidateObject] = {}
    for candidate in candidates:
        if candidate.role != "branch" or candidate.geometry is None or candidate.status == "invalid":
            continue
        if not _branch_metadata_complete(candidate):
            continue
        source = _source_id(candidate)
        if source not in best or _branch_candidate_score(candidate) > _branch_candidate_score(best[source]):
            best[source] = candidate
    return sorted(best.values(), key=lambda item: (_side(item), _start_u(item), _source_id(item)))


def _synthetic_connector_pairs(branches: list[CandidateObject]) -> list[tuple[CandidateObject, CandidateObject]]:
    pairs: list[tuple[CandidateObject, CandidateObject]] = []
    by_side: dict[str, list[CandidateObject]] = {}
    for branch in branches:
        by_side.setdefault(_side(branch), []).append(branch)
    for side_branches in by_side.values():
        ordered = sorted(side_branches, key=_start_u)
        pairs.extend(zip(ordered, ordered[1:]))
    return pairs


def _synthetic_connector_geometry(
    layout: LayoutResult,
    entrance,
    heading_degrees: float,
    left: CandidateObject,
    right: CandidateObject,
    inset_depth: float,
) -> tuple[Polygon, float] | None:
    u1 = _start_u(left)
    u2 = _start_u(right)
    if abs(u2 - u1) <= layout.site.aisle_width:
        return None
    if _side(left) != _side(right):
        return None
    direction = 1 if _side(left) == "left" else -1
    length = min(_branch_length(left), _branch_length(right))
    if length <= layout.site.aisle_width:
        return None
    actual_inset_depth = min(max(float(inset_depth), 0.0), _max_connector_inset_depth(layout.site, length))
    center_v = direction * (length - layout.site.aisle_width / 2 - actual_inset_depth)
    u_min = min(u1, u2) - layout.site.aisle_width / 2
    u_max = max(u1, u2) + layout.site.aisle_width / 2
    local = (
        u_min,
        center_v - layout.site.aisle_width / 2,
        u_max,
        center_v + layout.site.aisle_width / 2,
    )
    return polygon_points(normalized_local_box_to_world(local, entrance, heading_degrees)), actual_inset_depth


def _connector_inset_depths(site, left: CandidateObject, right: CandidateObject) -> tuple[float, ...]:
    branch_length = min(_branch_length(left), _branch_length(right))
    max_depth = _max_connector_inset_depth(site, branch_length)
    raw = site.optimization.get("connector_inset_depths")
    if isinstance(raw, list):
        depths = []
        for item in raw:
            try:
                depths.append(float(item))
            except (TypeError, ValueError):
                continue
        return _normalized_inset_depths(depths, max_depth)
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return (0.0,)
    stall = site.branch_stall or site.main_stall or site.stall
    if stall.family != "perpendicular" or not _angle_allowed(90.0, stall.allowed_angles):
        return (0.0,)
    return _normalized_inset_depths(
        [0.0, stall.length / 2, stall.length, stall.length * 1.5],
        max_depth,
    )


def _normalized_inset_depths(depths: list[float], max_depth: float) -> tuple[float, ...]:
    values = {round(min(max(depth, 0.0), max_depth), 6) for depth in depths}
    values.add(0.0)
    return tuple(sorted(values))


def _max_connector_inset_depth(site, branch_length: float) -> float:
    return max(branch_length - site.aisle_width * 2, 0.0)


def _angle_allowed(angle: float, allowed_angles) -> bool:
    return any(abs(float(item) - angle) <= 1e-6 for item in allowed_angles)


def _connector_outer_stall_depth(site, branch_length: float) -> float:
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return 0.0
    stall = site.branch_stall or site.main_stall or site.stall
    if stall.family != "perpendicular" or not _angle_allowed(90.0, stall.allowed_angles):
        return 0.0
    return min(stall.length, _max_connector_inset_depth(site, branch_length))


def _branch_metadata_complete(candidate: CandidateObject) -> bool:
    return (
        isinstance(candidate.metadata.get("source_id"), str)
        and isinstance(candidate.metadata.get("side"), str)
        and isinstance(candidate.metadata.get("start_u"), int | float)
        and isinstance(candidate.metadata.get("length"), int | float)
    )


def _branch_candidate_score(candidate: CandidateObject) -> float:
    stall_count = candidate.score_features.get("stall_count")
    base_stall_count = candidate.score_features.get("base_stall_count")
    if isinstance(stall_count, int | float) and isinstance(base_stall_count, int | float):
        return float(stall_count) - float(base_stall_count)
    return float(candidate.score_features.get("length", 0.0))


def _existing_connector_keys(candidates: list[CandidateObject]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.role != "connector":
            continue
        connects = candidate.metadata.get("connects", [])
        if not isinstance(connects, list | tuple) or len(connects) != 2:
            continue
        keys.add(tuple(sorted(str(item) for item in connects)))
    return keys


def _selected_aisle_candidates(layout: LayoutResult) -> list[CandidateObject]:
    return [_aisle_candidate(aisle) for aisle in layout.aisles]


def _stall_module_candidates(layout: LayoutResult, objects: list[CandidateObject]) -> list[CandidateObject]:
    modules: list[CandidateObject] = []
    spine_ids = {aisle.id for aisle in layout.aisles if aisle.role in SPINE_AISLE_ROLES | {"main"}}
    grouped: dict[tuple[str, str], list[ParkingStall]] = {}
    for stall in layout.stalls:
        served = stall.served_by_aisle_id
        side = stall.aisle_side
        if not served or not side or served not in spine_ids:
            continue
        grouped.setdefault((served, side), []).append(stall)
    for (served, side), stalls in grouped.items():
        stall_type_id = str(stalls[0].stall_type_id or layout.site.stall.id)
        _append_stall_modules(
            modules,
            layout.site,
            [_official_stall_dict(stall) for stall in stalls],
            module_id_prefix=f"C-MODULE-{served}-{side}",
            served_by_aisle_id=served,
            aisle_side=side,
            parent_ids=(f"C-SELECTED-{served}",),
            parent_is_base=True,
            parent_candidate_id=f"C-SELECTED-{served}",
            stall_type_id=stall_type_id,
            stall_family=_stall_family_for_type(layout.site, stall_type_id),
            shadow_family=False,
        )
    modules.extend(_alternative_spine_family_modules(layout, grouped))

    for candidate in objects:
        if candidate.kind != "aisle_skeleton" or candidate.role not in {"branch", "connector"}:
            continue
        generated = candidate.metadata.get("generated_stalls", [])
        if not isinstance(generated, list):
            continue
        by_side: dict[str, list[dict[str, object]]] = {}
        for item in generated:
            if not isinstance(item, dict) or not isinstance(item.get("aisle_side"), str):
                continue
            by_side.setdefault(str(item["aisle_side"]), []).append(item)
        served = str(candidate.metadata.get("source_id") or candidate.id)
        for side, items in by_side.items():
            stall_type_id = str(items[0].get("stall_type_id") or layout.site.stall.id)
            _append_stall_modules(
                modules,
                layout.site,
                items,
                module_id_prefix=f"C-MODULE-{candidate.id}-{side}",
                served_by_aisle_id=served,
                aisle_side=side,
                parent_ids=(candidate.id,),
                parent_is_base=False,
                parent_candidate_id=candidate.id,
                stall_type_id=stall_type_id,
                stall_family=_stall_family_for_type(layout.site, stall_type_id),
                shadow_family=False,
            )
    modules.extend(_alternative_branch_family_modules(layout, objects))
    modules.extend(_alternative_connector_family_modules(layout, objects))
    return modules


def _stall_module(
    *,
    module_id: str,
    stalls_geometry: list,
    stall_source_ids: list[str],
    generated_stalls: list[dict[str, object]],
    served_by_aisle_id: str,
    aisle_side: str,
    parent_ids: tuple[str, ...],
    parent_is_base: bool,
    parent_candidate_id: str,
    stall_type_id: str = "",
    stall_family: str = "",
    shadow_family: bool = False,
    segment_index: int | None = None,
) -> CandidateObject | None:
    geometry = _union_polygons(stalls_geometry)
    if geometry is None or not stall_source_ids:
        return None
    slot = f"{parent_candidate_id}|{aisle_side}"
    source = f"{served_by_aisle_id}-{aisle_side}-{stall_type_id}" if stall_type_id else f"{served_by_aisle_id}-{aisle_side}"
    if segment_index is not None:
        slot = f"{slot}|seg{segment_index}"
        source = f"{source}-seg{segment_index}"
    return CandidateObject(
        id=module_id,
        kind=STALL_MODULE_KIND,
        role=STALL_MODULE_ROLE,
        status="rejected",
        geometry=geometry,
        parent_ids=parent_ids,
        score_features={"stall_count": float(len(stall_source_ids)), "area": _area(geometry)},
        metadata={
            "source_id": source,
            "served_by_aisle_id": served_by_aisle_id,
            "aisle_side": aisle_side,
            "parent_is_base": parent_is_base,
            "parent_candidate_id": parent_candidate_id,
            "family_slot": slot,
            "segment_index": segment_index,
            "stall_type_id": stall_type_id,
            "stall_family": stall_family,
            "shadow_family": shadow_family,
            "stall_source_ids": stall_source_ids,
            "generated_stalls": generated_stalls,
            "selection_class": catalog_class(kind=STALL_MODULE_KIND, role=STALL_MODULE_ROLE),
        },
    )


def _generated_stall_sort_key(item: dict[str, object]) -> tuple[float, float]:
    geom = item.get("geometry")
    if not isinstance(geom, list) or not geom:
        return (0.0, 0.0)
    xs = [float(point[0]) for point in geom if isinstance(point, list | tuple) and len(point) == 2]
    ys = [float(point[1]) for point in geom if isinstance(point, list | tuple) and len(point) == 2]
    if not xs or not ys:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _iter_stall_segments(site, items: list[dict[str, object]]) -> list[tuple[int | None, list[dict[str, object]]]]:
    ordered = sorted(items, key=_generated_stall_sort_key)
    size = stall_module_segment_stalls(site.optimization)
    if not ordered:
        return []
    if size <= 0 or len(ordered) <= size:
        return [(None, ordered)]
    return [(index, ordered[index : index + size]) for index in range(0, len(ordered), size)]


def _official_stall_dict(stall: ParkingStall) -> dict[str, object]:
    return {
        "source_id": stall.id,
        "geometry": stall.polygon,
        "angle_degrees": stall.angle_degrees,
        "served_by_aisle_id": stall.served_by_aisle_id,
        "aisle_side": stall.aisle_side,
        "stall_type_id": stall.stall_type_id,
    }


def _append_stall_modules(
    modules: list[CandidateObject],
    site,
    generated: list[dict[str, object]],
    *,
    module_id_prefix: str,
    served_by_aisle_id: str,
    aisle_side: str,
    parent_ids: tuple[str, ...],
    parent_is_base: bool,
    parent_candidate_id: str,
    stall_type_id: str,
    stall_family: str,
    shadow_family: bool,
) -> None:
    for segment_index, chunk in _iter_stall_segments(site, generated):
        suffix = "" if segment_index is None else f"-seg{segment_index}"
        module = _stall_module(
            module_id=f"{module_id_prefix}{suffix}",
            stalls_geometry=[item["geometry"] for item in chunk if "geometry" in item],
            stall_source_ids=[str(item.get("source_id") or "") for item in chunk if item.get("source_id")],
            generated_stalls=chunk,
            served_by_aisle_id=served_by_aisle_id,
            aisle_side=aisle_side,
            parent_ids=parent_ids,
            parent_is_base=parent_is_base,
            parent_candidate_id=parent_candidate_id,
            stall_type_id=stall_type_id,
            stall_family=stall_family,
            shadow_family=shadow_family,
            segment_index=segment_index,
        )
        if module is not None:
            modules.append(module)


def _stall_family_for_type(site, stall_type_id: str) -> str:
    for spec in (site.stall_candidates or (site.stall,)):
        if spec.id == stall_type_id:
            return spec.family
    return site.stall.family


def _alternative_spine_family_modules(
    layout: LayoutResult,
    grouped: dict[tuple[str, str], list[ParkingStall]],
) -> list[CandidateObject]:
    specs = [spec for spec in (layout.site.stall_candidates or ()) if spec.id != layout.site.stall.id]
    if not specs or layout.selected_heading_degrees is None or not layout.main_entrance_id:
        return []
    entrance = next((item for item in layout.site.entrances if item.id == layout.main_entrance_id), None)
    if entrance is None:
        return []
    heading = float(layout.selected_heading_degrees)
    usable = available_area(layout.site)
    official_types = {
        (served, side): str(stalls[0].stall_type_id or layout.site.stall.id)
        for (served, side), stalls in grouped.items()
    }
    modules: list[CandidateObject] = []
    for aisle in layout.aisles:
        if aisle.role not in SPINE_AISLE_ROLES and aisle.role != "main":
            continue
        span = _aisle_local_span(aisle.polygon, entrance, heading)
        if span is None:
            continue
        start_u, end_u, v_center = span
        for spec in specs:
            probe_site = replace(layout.site, stall=spec, main_stall=spec)
            if not supports_phase1_stall(probe_site):
                continue
            placed = place_main_family_stalls(
                layout.site,
                spec,
                usable,
                entrance,
                heading,
                start_u,
                end_u,
                served_by_aisle_id=aisle.id,
                v_center=v_center,
            )
            by_side: dict[str, list] = {}
            for stall in placed:
                if stall.aisle_side:
                    by_side.setdefault(stall.aisle_side, []).append(stall)
            for side, stalls in by_side.items():
                if official_types.get((aisle.id, side)) == spec.id:
                    continue
                generated = [
                    {
                        "source_id": f"{spec.id}-{side}-{index}",
                        "geometry": stall.polygon,
                        "angle_degrees": stall.angle_degrees,
                        "served_by_aisle_id": aisle.id,
                        "aisle_side": side,
                        "stall_type_id": spec.id,
                    }
                    for index, stall in enumerate(stalls, start=1)
                ]
                _append_stall_modules(
                    modules,
                    layout.site,
                    generated,
                    module_id_prefix=f"C-MODULE-{aisle.id}-{side}-{spec.id}",
                    served_by_aisle_id=aisle.id,
                    aisle_side=side,
                    parent_ids=(f"C-SELECTED-{aisle.id}",),
                    parent_is_base=True,
                    parent_candidate_id=f"C-SELECTED-{aisle.id}",
                    stall_type_id=spec.id,
                    stall_family=spec.family,
                    shadow_family=True,
                )
    return modules


def _alternative_branch_family_modules(layout: LayoutResult, objects: list[CandidateObject]) -> list[CandidateObject]:
    specs = list(layout.site.stall_candidates or ())
    if len(specs) <= 1 or layout.selected_heading_degrees is None or not layout.main_entrance_id:
        return []
    entrance = next((item for item in layout.site.entrances if item.id == layout.main_entrance_id), None)
    if entrance is None:
        return []
    if abs(float(layout.selected_entrance_offset or 0.0)) > 1e-9:
        entrance = _offset_entrance(entrance, float(layout.selected_entrance_offset))
    heading = float(layout.selected_heading_degrees)
    usable = available_area(layout.site)
    aisle_parts = [ShapelyPolygon(aisle.polygon) for aisle in layout.aisles if aisle.polygon]
    occupied = unary_union(aisle_parts) if aisle_parts else ShapelyPolygon()
    modules: list[CandidateObject] = []
    for candidate in objects:
        if candidate.kind != "aisle_skeleton" or candidate.role != "branch":
            continue
        metadata = candidate.metadata
        if not isinstance(metadata.get("start_u"), int | float) or not isinstance(metadata.get("length"), int | float):
            continue
        if not isinstance(metadata.get("side"), str):
            continue
        official_types = {
            str(item.get("aisle_side")): str(item.get("stall_type_id") or layout.site.stall.id)
            for item in metadata.get("generated_stalls", [])
            if isinstance(item, dict) and item.get("aisle_side")
        }
        branch_id = str(metadata.get("source_id") or candidate.id)
        branch_u = float(metadata["start_u"])
        length = float(metadata["length"])
        start_t = layout.site.aisle_width
        end_t = length - layout.site.aisle_width
        if end_t <= start_t + 1e-9:
            continue
        for spec in specs:
            if spec.family not in {"perpendicular", "angled", "parallel"}:
                continue
            probe_site = replace(layout.site, stall=spec, branch_stall=spec)
            if not supports_phase1_stall(probe_site):
                continue
            placed = place_branch_family_stalls(
                layout.site,
                spec,
                usable,
                entrance,
                heading,
                branch_u,
                str(metadata["side"]),
                branch_id,
                start_t,
                end_t,
                occupied,
            )
            by_side: dict[str, list] = {}
            for stall in placed:
                if stall.aisle_side:
                    by_side.setdefault(stall.aisle_side, []).append(stall)
            for side, stalls in by_side.items():
                if official_types.get(side) == spec.id:
                    continue
                generated = [
                    {
                        "source_id": f"{spec.id}-{side}-{index}",
                        "geometry": stall.polygon,
                        "angle_degrees": stall.angle_degrees,
                        "served_by_aisle_id": branch_id,
                        "aisle_side": side,
                        "stall_type_id": spec.id,
                    }
                    for index, stall in enumerate(stalls, start=1)
                ]
                _append_stall_modules(
                    modules,
                    layout.site,
                    generated,
                    module_id_prefix=f"C-MODULE-{candidate.id}-{side}-{spec.id}",
                    served_by_aisle_id=branch_id,
                    aisle_side=side,
                    parent_ids=(candidate.id,),
                    parent_is_base=False,
                    parent_candidate_id=candidate.id,
                    stall_type_id=spec.id,
                    stall_family=spec.family,
                    shadow_family=True,
                )
    return modules


def _alternative_connector_family_modules(layout: LayoutResult, objects: list[CandidateObject]) -> list[CandidateObject]:
    specs = list(layout.site.stall_candidates or ())
    if len(specs) <= 1 or layout.selected_heading_degrees is None or not layout.main_entrance_id:
        return []
    entrance = next((item for item in layout.site.entrances if item.id == layout.main_entrance_id), None)
    if entrance is None:
        return []
    if abs(float(layout.selected_entrance_offset or 0.0)) > 1e-9:
        entrance = _offset_entrance(entrance, float(layout.selected_entrance_offset))
    heading = float(layout.selected_heading_degrees)
    usable = available_area(layout.site)
    aisle_parts = [ShapelyPolygon(aisle.polygon) for aisle in layout.aisles if aisle.polygon]
    occupied = unary_union(aisle_parts) if aisle_parts else ShapelyPolygon()
    modules: list[CandidateObject] = []
    for candidate in objects:
        if candidate.kind != "aisle_skeleton" or candidate.role != "connector":
            continue
        geometry = _connector_geometry_from_metadata(candidate.metadata)
        if geometry is None:
            continue
        official_types = {
            str(item.get("aisle_side")): str(item.get("stall_type_id") or layout.site.stall.id)
            for item in candidate.metadata.get("generated_stalls", [])
            if isinstance(item, dict) and item.get("aisle_side")
        }
        connector_id = str(candidate.metadata.get("source_id") or candidate.id)
        for spec in specs:
            if spec.family not in {"perpendicular", "angled", "parallel"}:
                continue
            probe_site = replace(layout.site, stall=spec, branch_stall=spec)
            if not supports_phase1_stall(probe_site):
                continue
            placed = place_connector_family_stalls(
                layout.site,
                spec,
                usable,
                entrance,
                heading,
                geometry,
                connector_id,
                occupied,
            )
            by_side: dict[str, list] = {}
            for stall in placed:
                if stall.aisle_side:
                    by_side.setdefault(stall.aisle_side, []).append(stall)
            for side, stalls in by_side.items():
                if official_types.get(side) == spec.id:
                    continue
                generated = [
                    {
                        "source_id": f"{spec.id}-{side}-{index}",
                        "geometry": stall.polygon,
                        "angle_degrees": stall.angle_degrees,
                        "served_by_aisle_id": connector_id,
                        "aisle_side": side,
                        "stall_type_id": spec.id,
                    }
                    for index, stall in enumerate(stalls, start=1)
                ]
                _append_stall_modules(
                    modules,
                    layout.site,
                    generated,
                    module_id_prefix=f"C-MODULE-{candidate.id}-{side}-{spec.id}",
                    served_by_aisle_id=connector_id,
                    aisle_side=side,
                    parent_ids=(candidate.id,),
                    parent_is_base=False,
                    parent_candidate_id=candidate.id,
                    stall_type_id=spec.id,
                    stall_family=spec.family,
                    shadow_family=True,
                )
    return modules


def _connector_geometry_from_metadata(metadata: dict[str, object]) -> ConnectorGeometry | None:
    try:
        u_min = float(metadata["u_min"])
        u_max = float(metadata["u_max"])
        center_v = float(metadata["center_v"])
        inset_depth = float(metadata.get("connector_inset_depth") or metadata.get("inset_depth") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    side = metadata.get("side")
    if not isinstance(side, str):
        return None
    pattern = str(metadata.get("connector_pattern") or metadata.get("pattern") or "same_side_u")
    v_min = float(metadata.get("v_min") or 0.0)
    v_max = float(metadata.get("v_max") or 0.0)
    polygon = metadata.get("geometry")
    return ConnectorGeometry(
        polygon=polygon,
        u_min=u_min,
        u_max=u_max,
        center_v=center_v,
        side=side,
        inset_depth=inset_depth,
        pattern=pattern,
        v_min=v_min,
        v_max=v_max,
    )


def _aisle_local_span(polygon: Polygon, entrance, heading_degrees: float) -> tuple[float, float, float] | None:
    us: list[float] = []
    vs: list[float] = []
    for point in polygon:
        u, v = _world_to_local((float(point[0]), float(point[1])), entrance, heading_degrees)
        us.append(u)
        vs.append(v)
    if not us:
        return None
    return min(us), max(us), (min(vs) + max(vs)) / 2


def _union_polygons(polygons: list) -> Polygon | None:
    parts = []
    for item in polygons:
        if not isinstance(item, list) or len(item) < 3:
            continue
        try:
            poly = ShapelyPolygon(item)
        except Exception:
            continue
        if not poly.is_empty:
            parts.append(poly)
    if not parts:
        return None
    merged = unary_union(parts)
    if merged.is_empty:
        return None
    if merged.geom_type == "Polygon":
        return polygon_points(merged)
    geoms = [item for item in getattr(merged, "geoms", []) if getattr(item, "area", 0.0) > 1e-6]
    if not geoms:
        return None
    return polygon_points(max(geoms, key=lambda item: item.area))


def _selected_stall_candidates(layout: LayoutResult) -> list[CandidateObject]:
    return [_stall_candidate(stall) for stall in layout.stalls]


def _aisle_candidate(aisle: ParkingAisle) -> CandidateObject:
    parent_ids = tuple(item for item in (aisle.parent_aisle_id, aisle.connected_to_entrance_id) if item)
    return CandidateObject(
        id=f"C-SELECTED-{aisle.id}",
        kind="aisle",
        role=aisle.role,
        status="selected",
        geometry=aisle.polygon,
        parent_ids=parent_ids,
        score_features={"area": _area(aisle.polygon)},
        metadata={
            "source_id": aisle.id,
            "angle_degrees": aisle.angle_degrees,
            "connected_to_entrance_id": aisle.connected_to_entrance_id,
            "parent_aisle_id": aisle.parent_aisle_id,
            "connected_aisle_ids": list(aisle.connected_aisle_ids),
            "directionality": aisle.directionality,
            "selection_class": catalog_class(kind="aisle", role=aisle.role),
        },
    )


def _stall_candidate(stall: ParkingStall) -> CandidateObject:
    return CandidateObject(
        id=f"C-SELECTED-{stall.id}",
        kind="stall",
        role="stall",
        status="selected",
        geometry=stall.polygon,
        parent_ids=(stall.served_by_aisle_id,) if stall.served_by_aisle_id else (),
        score_features={"area": _area(stall.polygon)},
        metadata={
            "source_id": stall.id,
            "angle_degrees": stall.angle_degrees,
            "aisle_side": stall.aisle_side,
            "stall_type_id": stall.stall_type_id,
            "selection_class": catalog_class(kind="stall", role="stall"),
        },
    )


def _candidate_report(candidate: CandidateObject) -> dict[str, object]:
    return {
        "id": candidate.id,
        "kind": candidate.kind,
        "role": candidate.role,
        "status": candidate.status,
        "geometry": candidate.geometry,
        "parent_ids": list(candidate.parent_ids),
        "conflict_ids": list(candidate.conflict_ids),
        "score_features": candidate.score_features,
        "metadata": candidate.metadata,
    }


def _status_counts(objects: list[CandidateObject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in objects:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _catalog_counts(objects: list[CandidateObject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in objects:
        klass = catalog_class(kind=item.kind, role=item.role)
        counts[klass] = counts.get(klass, 0) + 1
    return counts


def _with_conflicts(objects: list[CandidateObject]) -> list[CandidateObject]:
    conflict_map: dict[str, set[str]] = {item.id: set() for item in objects}
    for index, left in enumerate(objects):
        for right in objects[index + 1 :]:
            conflict = _geometry_conflict(left, right)
            if conflict is None:
                continue
            conflict_map[left.id].add(right.id)
            conflict_map[right.id].add(left.id)
    return [
        replace(item, conflict_ids=tuple(sorted(conflict_map[item.id])))
        for item in objects
    ]


def _conflict_matrix(objects: list[CandidateObject]) -> list[dict[str, object]]:
    object_by_id = {item.id: item for item in objects}
    seen: set[tuple[str, str]] = set()
    conflicts: list[dict[str, object]] = []
    for left in objects:
        for right_id in left.conflict_ids:
            first_id, second_id = sorted((left.id, right_id))
            pair = (first_id, second_id)
            if pair in seen:
                continue
            seen.add(pair)
            right = object_by_id.get(right_id)
            if right is None:
                continue
            conflict = _geometry_conflict(left, right)
            if conflict is None:
                continue
            conflicts.append(conflict)
    return sorted(conflicts, key=lambda item: (str(item["left_id"]), str(item["right_id"])))


def _geometry_conflict(left: CandidateObject, right: CandidateObject) -> dict[str, object] | None:
    if left.geometry is None or right.geometry is None:
        return None
    # Stall modules are coarse side-strips; stall-level overlap is filtered in
    # the layout preview instead of excluding the whole strip.
    if left.kind == STALL_MODULE_KIND or right.kind == STALL_MODULE_KIND:
        return None
    if _allowed_overlap(left, right):
        return None
    left_geometry = ShapelyPolygon(left.geometry)
    right_geometry = ShapelyPolygon(right.geometry)
    intersection = left_geometry.intersection(right_geometry)
    if intersection.area <= 1e-6:
        return None
    return {
        "left_id": left.id,
        "right_id": right.id,
        "type": "geometry_overlap",
        "overlap_area": float(intersection.area),
    }


def _allowed_overlap(left: CandidateObject, right: CandidateObject) -> bool:
    if _module_parent_overlap(left, right) or _module_parent_overlap(right, left):
        return True
    if not _is_aisle_like(left) or not _is_aisle_like(right):
        return False
    left_source = _source_id(left)
    right_source = _source_id(right)
    if left_source in right.parent_ids or right_source in left.parent_ids:
        return True
    if left_source in _connected_source_ids(right) or right_source in _connected_source_ids(left):
        return True
    return False


def _is_aisle_like(candidate: CandidateObject) -> bool:
    return candidate.kind in {"aisle", "aisle_skeleton"}


def _module_parent_overlap(module: CandidateObject, other: CandidateObject) -> bool:
    if module.kind != STALL_MODULE_KIND:
        return False
    if other.id in module.parent_ids:
        return True
    served = module.metadata.get("served_by_aisle_id")
    other_source = other.metadata.get("source_id")
    return bool(served and other_source and served == other_source)


def _same_main_attempt(
    left: tuple[str | None, float, float],
    right: tuple[str | None, float | None, float],
) -> bool:
    return (
        left[0] == right[0]
        and right[1] is not None
        and abs(left[1] - right[1]) <= 1e-6
        and abs(left[2] - right[2]) <= 1e-6
    )


def _branch_attempt_selected(diagnostic: dict[str, object], selected_branches: list[dict[str, object]]) -> bool:
    for branch in selected_branches:
        if str(diagnostic.get("branch_id")) != str(branch.get("id")):
            continue
        if str(diagnostic.get("side")) != str(branch.get("side")):
            continue
        if not _close(diagnostic.get("start_u"), branch.get("start_u")):
            continue
        if not _close(diagnostic.get("length"), branch.get("length")):
            continue
        return True
    return False


def _connector_attempt_selected(diagnostic: dict[str, object], selected_connectors: list[dict[str, object]]) -> bool:
    diagnostic_connects = [str(item) for item in diagnostic.get("connects", [])]
    for connector in selected_connectors:
        if str(diagnostic.get("connector_id")) != str(connector.get("id")):
            continue
        if diagnostic_connects == [str(item) for item in connector.get("connects", [])]:
            diagnostic_inset = diagnostic.get("connector_inset_depth")
            selected_inset = connector.get("connector_inset_depth")
            if diagnostic_inset is None and selected_inset is None:
                return True
            if _close(diagnostic_inset, selected_inset):
                return True
    return False


def _close(left: object, right: object) -> bool:
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return abs(float(left) - float(right)) <= 1e-6


def _diagnostic_geometry(diagnostic: dict[str, object]) -> Polygon | None:
    raw = diagnostic.get("geometry")
    if not isinstance(raw, list):
        return None
    points: Polygon = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            return None
        points.append((float(item[0]), float(item[1])))
    return points if len(points) >= 3 else None


def _source_id(candidate: CandidateObject) -> str:
    source_id = candidate.metadata.get("source_id")
    return str(source_id) if source_id is not None else candidate.id


def _side(candidate: CandidateObject) -> str:
    return str(candidate.metadata.get("side", ""))


def _start_u(candidate: CandidateObject) -> float:
    value = candidate.metadata.get("start_u")
    return float(value) if isinstance(value, int | float) else 0.0


def _branch_length(candidate: CandidateObject) -> float:
    value = candidate.metadata.get("length")
    return float(value) if isinstance(value, int | float) else 0.0


def _connected_source_ids(candidate: CandidateObject) -> tuple[str, ...]:
    raw = candidate.metadata.get("connected_aisle_ids", [])
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(str(item) for item in raw)


def _area(polygon: Polygon) -> float:
    return float(ShapelyPolygon(polygon).area)


def _deduplicate(objects: list[CandidateObject]) -> list[CandidateObject]:
    unique: list[CandidateObject] = []
    seen: set[str] = set()
    for item in objects:
        if item.id in seen:
            continue
        unique.append(item)
        seen.add(item.id)
    return unique
