from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.candidate_catalog import NETWORK_PREVIEW_VERSION, SPINE_AISLE_ROLES
from openparkcad.layout_geometry import available_area, polygon_points
from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle
from openparkcad.phase1_support import aisle_directionality
from openparkcad.traffic_graph import build_traffic_graph, traffic_graph_summary, validate_traffic_graph


def build_candidate_network_preview(layout: LayoutResult) -> dict[str, object]:
    objects = layout.candidate_objects
    object_by_id = {item.id: item for item in objects}
    selected_shadow_ids = [str(item) for item in layout.candidate_selection.get("selected_ids", [])]
    base_aisles = [
        item
        for item in objects
        if item.kind == "aisle" and item.status == "selected" and _base_preview_aisle(item)
    ]
    shadow_aisles = [
        object_by_id[item]
        for item in selected_shadow_ids
        if item in object_by_id
        and object_by_id[item].kind == "aisle_skeleton"
        and object_by_id[item].geometry is not None
    ]
    preview_objects = [*base_aisles, *shadow_aisles]
    preview_aisles = _preview_aisles(preview_objects)
    connector_summary = _connector_summary(preview_objects, preview_aisles)
    return {
        "version": NETWORK_PREVIEW_VERSION,
        "status": "preview_only",
        "source_selection_version": layout.candidate_selection.get("version"),
        "base_aisle_count": len(base_aisles),
        "shadow_aisle_count": len(shadow_aisles),
        "shadow_turnaround_count": _shadow_turnaround_count(preview_aisles),
        "connector_count": connector_summary["connector_count"],
        "loop_connector_count": connector_summary["loop_connector_count"],
        "connected_branch_source_ids": connector_summary["connected_branch_source_ids"],
        "suppressed_turnaround_source_ids": connector_summary["suppressed_turnaround_source_ids"],
        "suppressed_turnaround_count": connector_summary["suppressed_turnaround_count"],
        "aisle_count": len(preview_aisles),
        "selected_candidate_ids": [item.id for item in preview_objects],
        "selected_shadow_candidate_ids": selected_shadow_ids,
        "valid_no_internal_conflicts": _valid_no_internal_conflicts(preview_objects),
        "validation": _validate_preview(layout, preview_aisles, preview_objects),
        "aisles": preview_aisles,
        "notes": [
            "Preview includes official spine aisles (main/jog/exit/passing_bay and spine turnarounds) plus shadow-selected branch/connector candidates.",
            "Unconnected shadow branch candidates are expanded into branch aisle plus end-turnaround preview aisles.",
            "Connector-connected shadow branches suppress their generated end turnarounds in the preview network.",
            "Preview is report-only and does not replace the generated layout, stalls, DXF, or SVG yet.",
        ],
    }


def candidate_network_preview_report(layout: LayoutResult) -> dict[str, object]:
    return layout.candidate_network_preview or build_candidate_network_preview(layout)


def _preview_aisles(candidates: list[CandidateObject]) -> list[dict[str, object]]:
    connected_branch_ids = _connected_branch_source_ids(candidates)
    trim_context = _branch_trim_context(candidates)
    aisles: list[dict[str, object]] = []
    for candidate in candidates:
        aisles.append(_preview_aisle(candidate, len(aisles) + 1, trim_context))
        if not _needs_shadow_turnaround(candidate, connected_branch_ids):
            continue
        aisles.append(_preview_turnaround_aisle(candidate, len(aisles) + 1))
    return aisles


def _preview_aisle(candidate: CandidateObject, index: int, trim_context: dict[str, object]) -> dict[str, object]:
    geometry = _preview_geometry(candidate, trim_context)
    return {
        "id": f"PN-AISLE-{index:03d}",
        "candidate_id": candidate.id,
        "source_id": str(candidate.metadata.get("source_id", candidate.id)),
        "role": candidate.role,
        "kind": candidate.kind,
        "geometry": geometry,
        "parent_ids": list(candidate.parent_ids),
        "area": _geometry_area(geometry),
        "score_features": candidate.score_features,
        "metadata": _preview_metadata(candidate),
    }


def _preview_turnaround_aisle(candidate: CandidateObject, index: int) -> dict[str, object]:
    source_id = str(candidate.metadata.get("source_id", candidate.id))
    geometry = candidate.metadata.get("branch_turnaround_geometry")
    return {
        "id": f"PN-AISLE-{index:03d}",
        "candidate_id": f"{candidate.id}-TURNAROUND",
        "source_id": str(candidate.metadata.get("branch_turnaround_id", f"{source_id}-TURNAROUND")),
        "role": "turnaround",
        "kind": "aisle",
        "geometry": geometry,
        "parent_ids": [candidate.id],
        "area": _geometry_area(geometry),
        "score_features": {"area": _geometry_area(geometry)} if _valid_polygon(geometry) else {},
        "metadata": {
            "source_id": str(candidate.metadata.get("branch_turnaround_id", f"{source_id}-TURNAROUND")),
            "parent_candidate_id": candidate.id,
            "parent_aisle_id": source_id,
            "angle_degrees": candidate.metadata.get("angle_degrees"),
            "preview_generated": True,
            "preview_reason": "shadow_branch_dead_end_turnaround",
        },
    }


def _base_preview_aisle(candidate: CandidateObject) -> bool:
    """Selected official aisles shown as the network-preview base layer.

    Branch/connector remain shadow-selectable. Generator branch turnarounds
    stay out of the base so dead-end caps come only from selected skeletons.
    """
    if candidate.role in SPINE_AISLE_ROLES:
        return True
    if candidate.role != "turnaround":
        return False
    parent = str(candidate.metadata.get("parent_aisle_id") or "")
    source = str(candidate.metadata.get("source_id") or "")
    return not _branch_owned_id(parent) and not _branch_owned_id(source)


def _branch_owned_id(raw: str) -> bool:
    return raw.startswith("A-BRANCH") or "-BRANCH-" in raw


def _preview_metadata(candidate: CandidateObject) -> dict[str, object]:
    keys = (
        "source_id",
        "reason",
        "side",
        "start_u",
        "length",
        "connects",
        "angle_degrees",
        "connected_aisle_ids",
        "connected_to_entrance_id",
        "parent_aisle_id",
        "connector_inset_depth",
        "generated_stalls",
        "branch_turnaround_id",
    )
    return {key: candidate.metadata[key] for key in keys if key in candidate.metadata}


def _preview_geometry(candidate: CandidateObject, trim_context: dict[str, object]):
    if candidate.role == "branch" and _valid_polygon(candidate.metadata.get("branch_aisle_geometry")):
        return _trim_connected_branch_geometry(candidate, candidate.metadata["branch_aisle_geometry"], trim_context)
    return candidate.geometry


def _branch_trim_context(candidates: list[CandidateObject]) -> dict[str, object]:
    main_geometries = [
        ShapelyPolygon(candidate.geometry)
        for candidate in candidates
        if candidate.role == "main" and candidate.geometry is not None
    ]
    connector_by_branch: dict[str, list[ShapelyPolygon]] = {}
    for candidate in candidates:
        if candidate.role != "connector" or candidate.geometry is None:
            continue
        connector = ShapelyPolygon(candidate.geometry)
        for branch_id in _connects(candidate):
            connector_by_branch.setdefault(branch_id, []).append(connector)
    return {
        "main": unary_union(main_geometries) if main_geometries else None,
        "connector_by_branch": connector_by_branch,
    }


def _trim_connected_branch_geometry(candidate: CandidateObject, geometry: object, trim_context: dict[str, object]):
    connector_by_branch = trim_context.get("connector_by_branch", {})
    if not isinstance(connector_by_branch, dict):
        return geometry
    source_id = str(candidate.metadata.get("source_id", candidate.id))
    connectors = connector_by_branch.get(source_id, [])
    main = trim_context.get("main")
    if not connectors or main is None or not _valid_polygon(geometry):
        return geometry
    branch = ShapelyPolygon(geometry)
    axis = _branch_axis(branch, main)
    if axis is None:
        return geometry
    kept = branch
    for connector in connectors:
        cut = _branch_connector_half_plane(branch, main, connector, axis)
        if cut is None:
            continue
        kept = kept.intersection(cut)
    components = [item for item in getattr(kept, "geoms", [kept]) if getattr(item, "area", 0.0) > 1e-6]
    if not components:
        return geometry
    return polygon_points(max(components, key=lambda item: item.area))


def _branch_connector_half_plane(branch, main, connector, axis: tuple[float, float]):
    connector_projections = [_project(point, axis) for point in connector.exterior.coords]
    if not connector_projections:
        return None
    main_projection = _project((main.centroid.x, main.centroid.y), axis)
    connector_projection = _project((connector.centroid.x, connector.centroid.y), axis)
    keep_greater = main_projection >= connector_projection
    threshold = max(connector_projections) if keep_greater else min(connector_projections)
    min_x, min_y, max_x, max_y = unary_union([branch, main, connector]).bounds
    span = max(max_x - min_x, max_y - min_y, 1.0) * 4
    return _half_plane(axis, threshold, keep_greater, span)


def _half_plane(axis: tuple[float, float], threshold: float, keep_greater: bool, span: float) -> ShapelyPolygon:
    ax, ay = axis
    px, py = -ay, ax
    origin = (ax * threshold, ay * threshold)
    direction = 1 if keep_greater else -1
    return ShapelyPolygon(
        [
            (origin[0] + px * span, origin[1] + py * span),
            (origin[0] + ax * span * direction + px * span, origin[1] + ay * span * direction + py * span),
            (origin[0] + ax * span * direction - px * span, origin[1] + ay * span * direction - py * span),
            (origin[0] - px * span, origin[1] - py * span),
        ]
    )


def _unit_vector(dx: float, dy: float) -> tuple[float, float] | None:
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-9:
        return None
    return (dx / length, dy / length)


def _branch_axis(branch, main) -> tuple[float, float] | None:
    rectangle = branch.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    edges: list[tuple[float, float, float]] = []
    for start, end in zip(coords, coords[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        edges.append((dx * dx + dy * dy, dx, dy))
    if not edges:
        return None
    _, dx, dy = max(edges, key=lambda item: item[0])
    axis = _unit_vector(dx, dy)
    if axis is None:
        return None
    to_main = (main.centroid.x - branch.centroid.x, main.centroid.y - branch.centroid.y)
    if axis[0] * to_main[0] + axis[1] * to_main[1] < 0:
        return (-axis[0], -axis[1])
    return axis


def _project(point, axis: tuple[float, float]) -> float:
    return float(point[0]) * axis[0] + float(point[1]) * axis[1]


def _connected_branch_source_ids(candidates: list[CandidateObject]) -> set[str]:
    connected: set[str] = set()
    for candidate in candidates:
        if candidate.role != "connector":
            continue
        connects = candidate.metadata.get("connects", [])
        if not isinstance(connects, list | tuple):
            continue
        connected.update(str(item) for item in connects)
    return connected


def _needs_shadow_turnaround(candidate: CandidateObject, connected_branch_ids: set[str]) -> bool:
    if candidate.role != "branch":
        return False
    source_id = str(candidate.metadata.get("source_id", candidate.id))
    return source_id not in connected_branch_ids and _valid_polygon(candidate.metadata.get("branch_turnaround_geometry"))


def _shadow_turnaround_count(preview_aisles: list[dict[str, object]]) -> int:
    return len(
        [
            aisle
            for aisle in preview_aisles
            if aisle.get("role") == "turnaround"
            and isinstance(aisle.get("metadata"), dict)
            and aisle["metadata"].get("preview_generated")
        ]
    )


def _connector_summary(preview_objects: list[CandidateObject], preview_aisles: list[dict[str, object]]) -> dict[str, object]:
    connectors = [candidate for candidate in preview_objects if candidate.role == "connector"]
    connected_branch_ids = sorted(_connected_branch_source_ids(preview_objects))
    visible_turnaround_sources = {
        str(aisle.get("source_id"))
        for aisle in preview_aisles
        if aisle.get("role") == "turnaround"
        and isinstance(aisle.get("metadata"), dict)
        and aisle["metadata"].get("preview_generated")
    }
    suppressed = [
        f"{branch_id}-TURNAROUND"
        for branch_id in connected_branch_ids
        if f"{branch_id}-TURNAROUND" not in visible_turnaround_sources
    ]
    return {
        "connector_count": len(connectors),
        "loop_connector_count": len([connector for connector in connectors if len(_connects(connector)) >= 2]),
        "connected_branch_source_ids": connected_branch_ids,
        "suppressed_turnaround_source_ids": sorted(suppressed),
        "suppressed_turnaround_count": len(suppressed),
    }


def _connects(candidate: CandidateObject) -> list[str]:
    raw = candidate.metadata.get("connects", [])
    if not isinstance(raw, list | tuple):
        return []
    return [str(item) for item in raw]


def _valid_no_internal_conflicts(objects: list[CandidateObject]) -> bool:
    selected = {item.id for item in objects}
    for item in objects:
        if selected.intersection(item.conflict_ids):
            return False
    return True


def _geometry_area(geometry: object) -> float:
    if not _valid_polygon(geometry):
        return 0.0
    return float(ShapelyPolygon(geometry).area)


def _valid_polygon(raw: object) -> bool:
    if not isinstance(raw, list) or len(raw) < 3:
        return False
    return all(isinstance(item, list | tuple) and len(item) == 2 for item in raw)


def _validate_preview(
    layout: LayoutResult,
    preview_aisles: list[dict[str, object]],
    preview_objects: list[CandidateObject],
) -> dict[str, object]:
    containment = _containment_validation(layout, preview_aisles)
    traffic_layout = _preview_layout(layout, preview_aisles)
    traffic_graph = build_traffic_graph(traffic_layout)
    traffic_validation = validate_traffic_graph(traffic_graph, traffic_layout)
    internal_conflicts_valid = _valid_no_internal_conflicts(preview_objects)
    errors: list[str] = []
    if not internal_conflicts_valid:
        errors.append("preview_internal_conflicts")
    if not containment["valid"]:
        errors.append("preview_geometry_outside_usable_area")
    if not traffic_validation["valid"]:
        errors.append("preview_traffic_graph_invalid")
    return {
        "version": NETWORK_PREVIEW_VERSION,
        "status": "preview_only",
        "valid": not errors,
        "errors": errors,
        "internal_conflicts": {
            "valid": internal_conflicts_valid,
            "conflicting_candidate_ids": _internal_conflict_ids(preview_objects),
        },
        "geometry_containment": containment,
        "traffic_graph": traffic_graph_summary(traffic_layout),
    }


def _containment_validation(layout: LayoutResult, preview_aisles: list[dict[str, object]]) -> dict[str, object]:
    usable = available_area(layout.site)
    outside: list[dict[str, object]] = []
    for aisle in preview_aisles:
        geometry = aisle.get("geometry")
        if not _valid_polygon(geometry):
            outside.append({"aisle_id": aisle.get("id"), "reason": "missing_or_invalid_geometry"})
            continue
        polygon = ShapelyPolygon(geometry)
        if not usable.covers(polygon):
            outside.append(
                {
                    "aisle_id": aisle.get("id"),
                    "candidate_id": aisle.get("candidate_id"),
                    "outside_area": float(polygon.difference(usable).area),
                }
            )
    return {
        "valid": not outside,
        "checked_aisle_count": len(preview_aisles),
        "outside_aisles": outside,
    }


def _preview_layout(layout: LayoutResult, preview_aisles: list[dict[str, object]]) -> LayoutResult:
    source_to_preview_id = _source_to_preview_id(preview_aisles)
    aisles = [
        _parking_aisle_from_preview(layout, aisle, source_to_preview_id)
        for aisle in preview_aisles
        if _valid_polygon(aisle.get("geometry"))
    ]
    return LayoutResult(
        site=layout.site,
        stalls=[],
        aisles=aisles,
        generation_mode="candidate_network_preview",
        main_entrance_id=layout.main_entrance_id,
        selected_heading_degrees=layout.selected_heading_degrees,
        selected_heading_delta_degrees=layout.selected_heading_delta_degrees,
        selected_entrance_offset=layout.selected_entrance_offset,
    )


def _parking_aisle_from_preview(
    layout: LayoutResult,
    preview: dict[str, object],
    source_to_preview_id: dict[str, str],
) -> ParkingAisle:
    role = str(preview["role"])
    metadata = preview.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    parent_aisle_id = _mapped_parent_id(preview, source_to_preview_id)
    connected_aisle_ids = _mapped_connected_ids(metadata, source_to_preview_id)
    connected_to_entrance_id = None
    if role == "main":
        connected_to_entrance_id = str(metadata.get("connected_to_entrance_id") or layout.main_entrance_id or "")
    angle = metadata.get("angle_degrees", layout.selected_heading_degrees or layout.selected_angle_degrees)
    directionality = metadata.get("directionality")
    if not isinstance(directionality, str):
        directionality = aisle_directionality(layout.site)
    return ParkingAisle(
        id=str(preview["id"]),
        polygon=[(float(x), float(y)) for x, y in preview["geometry"]],
        angle_degrees=float(angle) if isinstance(angle, int | float) else layout.selected_angle_degrees,
        role=role,
        connected_to_entrance_id=connected_to_entrance_id or None,
        parent_aisle_id=parent_aisle_id,
        connected_aisle_ids=connected_aisle_ids,
        directionality=directionality if directionality in {"one_way", "two_way"} else "two_way",
    )


def _mapped_parent_id(preview: dict[str, object], source_to_preview_id: dict[str, str]) -> str | None:
    metadata = preview.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    parent_candidate = metadata.get("parent_candidate_id")
    if isinstance(parent_candidate, str):
        mapped = source_to_preview_id.get(parent_candidate)
        if mapped:
            return mapped
    raw_parent = metadata.get("parent_aisle_id")
    if isinstance(raw_parent, str):
        mapped = source_to_preview_id.get(raw_parent)
        if mapped:
            return mapped
    parent_ids = preview.get("parent_ids", [])
    if not isinstance(parent_ids, list | tuple):
        return None
    for parent_id in parent_ids:
        mapped = source_to_preview_id.get(str(parent_id))
        if mapped:
            return mapped
    return None


def _source_to_preview_id(preview_aisles: list[dict[str, object]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for aisle in preview_aisles:
        aisle_id = aisle.get("id")
        if not aisle_id:
            continue
        for key in ("candidate_id", "source_id"):
            value = aisle.get(key)
            if value:
                mapping.setdefault(str(value), str(aisle_id))
    return mapping


def _mapped_connected_ids(metadata: dict[str, object], source_to_preview_id: dict[str, str]) -> tuple[str, ...]:
    raw = metadata.get("connects") or metadata.get("connected_aisle_ids") or []
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(source_to_preview_id[item] for item in (str(value) for value in raw) if item in source_to_preview_id)


def _internal_conflict_ids(preview_objects: list[CandidateObject]) -> list[str]:
    selected = {item.id for item in preview_objects}
    conflicting: set[str] = set()
    for item in preview_objects:
        if selected.intersection(item.conflict_ids):
            conflicting.add(item.id)
            conflicting.update(selected.intersection(item.conflict_ids))
    return sorted(conflicting)
