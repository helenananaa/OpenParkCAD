"""DXF to SiteSpec JSON conversion. Does not solve."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from dataclasses import replace

from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.coordinate_transform import (
    LOOP_TOLERANCE_M,
    CoordinateTransform,
    build_transform,
    resolve_source_units,
    transform_from_record,
    units_from_dxf_insunits,
)
from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, Point, Polygon, site_from_dict

MAPPING_VERSION = "cad-import-mapping-1"
IMPORT_VERSION = "cad-import-1"
_KNOWN_MAPPING_KEYS = frozenset({"version", "source_units", "layers", "entrance_entities"})
_KNOWN_LAYER_KEYS = frozenset({"boundary", "obstacles", "entrances"})


class CadImportError(ValueError):
    """Closed import failure with entity diagnostics."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def import_dxf_to_site(
    dxf_path: str | Path,
    mapping_path: str | Path,
    defaults_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dxf_path = Path(dxf_path)
    mapping = _load_json_object(mapping_path, "mapping")
    defaults = _load_json_object(defaults_path, "defaults")
    mapping_norm = _validate_mapping(mapping)
    source_bytes = dxf_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        import ezdxf

        document = ezdxf.readfile(dxf_path)
    except Exception as exc:
        raise CadImportError(f"could not read DXF: {exc}", _empty_diagnostics(source_sha256, str(dxf_path))) from exc

    msp = document.modelspace()
    dxf_units = units_from_dxf_insunits(int(getattr(document, "units", 0) or 0))
    entities: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    try:
        source_units, unit_source = resolve_source_units(
            mapping_units=mapping_norm.get("source_units"),
            dxf_units=dxf_units,
        )
    except ValueError as exc:
        issues.append({"code": "units", "message": str(exc)})
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError(str(exc), diagnostics) from exc

    layer_map = mapping_norm["layers"]
    boundary_layer = layer_map["boundary"]
    obstacle_layers = set(layer_map["obstacles"])
    entrance_layer = layer_map["entrances"]
    role_layers = {boundary_layer, *obstacle_layers, entrance_layer}

    boundary_candidates: list[tuple[str, Polygon, dict[str, Any]]] = []
    obstacles: list[tuple[str, Polygon, dict[str, Any]]] = []
    entrance_lines: dict[str, dict[str, Any]] = {}

    for entity in msp:
        record = _entity_record(entity)
        entities.append(record)
        layer = record["layer"]
        if layer not in role_layers:
            record["result"] = "ignored_non_role_layer"
            continue
        kind = record["type"]
        if kind in {"ARC", "SPLINE", "INSERT"} or record.get("has_bulge"):
            record["result"] = "unsupported"
            issues.append({"code": "unsupported_entity", "handle": record["handle"], "type": kind, "layer": layer})
            continue
        if record.get("elevation") not in {None, 0, 0.0}:
            record["result"] = "unsupported"
            issues.append({"code": "non_planar", "handle": record["handle"], "layer": layer})
            continue
        if layer == boundary_layer or layer in obstacle_layers:
            polygon, reason = _closed_straight_polygon(entity)
            if polygon is None:
                record["result"] = "unsupported"
                issues.append({"code": reason or "invalid_polygon", "handle": record["handle"], "layer": layer})
                continue
            if _self_intersects(polygon):
                record["result"] = "unsupported"
                issues.append({"code": "self_intersecting", "handle": record["handle"], "layer": layer})
                continue
            record["result"] = "accepted"
            if layer == boundary_layer:
                boundary_candidates.append((record["handle"], polygon, record))
            else:
                obstacles.append((record["handle"], polygon, record))
            continue
        if layer == entrance_layer:
            line, reason = _directed_line(entity)
            if line is None:
                record["result"] = "unsupported"
                issues.append({"code": reason or "invalid_entrance_line", "handle": record["handle"]})
                continue
            record["result"] = "accepted"
            entrance_lines[record["handle"].upper()] = {"record": record, "line": line}
            continue
        record["result"] = "ignored"

    if issues:
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError(issues[0]["code"], diagnostics)

    if len(boundary_candidates) != 1:
        issues.append({"code": "boundary_count", "message": f"expected 1 boundary, found {len(boundary_candidates)}"})
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError("boundary_count", diagnostics)

    boundary_handle, source_boundary, _boundary_record = boundary_candidates[0]
    if _has_holes(source_boundary):
        issues.append({"code": "holes_unsupported", "handle": boundary_handle})
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError("holes_unsupported", diagnostics)

    transform = build_transform(source_units=source_units, unit_source=unit_source, source_boundary=source_boundary)
    local_boundary = transform.polygon_to_local(source_boundary)
    loop_error = transform.max_roundtrip_error_m(source_boundary)
    if loop_error > LOOP_TOLERANCE_M:
        issues.append({"code": "roundtrip_error", "error_m": loop_error})
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError("roundtrip_error", diagnostics)

    local_obstacles = []
    obstacle_handles = []
    for handle, polygon, _record in obstacles:
        local_obstacles.append(
            {
                "id": f"obstacle-{handle}",
                "type": "obstacle",
                "geometry": {"type": "polygon", "points": [list(point) for point in transform.polygon_to_local(polygon)]},
            }
        )
        obstacle_handles.append(handle)

    site_polygon = ShapelyPolygon(local_boundary)
    entrances = _build_entrances(
        mapping_norm["entrance_entities"],
        entrance_lines,
        transform,
        site_polygon,
        issues,
    )
    if issues:
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError(issues[0]["code"], diagnostics)

    site_json = _assemble_site_json(defaults, local_boundary, local_obstacles, entrances)
    try:
        site_from_dict(site_json)
    except (KeyError, TypeError, ValueError) as exc:
        issues.append({"code": "site_parse", "message": str(exc)})
        diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=False)
        raise CadImportError(f"imported site is not a valid SiteSpec: {exc}", diagnostics) from exc

    metadata = {
        "cad_import": {
            "version": IMPORT_VERSION,
            "source_path": str(dxf_path),
            "source_sha256": source_sha256,
            "mapping_version": MAPPING_VERSION,
            "transform": transform.to_record(),
            "layers": layer_map,
            "source_handles": {
                "boundary": boundary_handle,
                "obstacles": obstacle_handles,
                "entrances": [item["handle"] for item in mapping_norm["entrance_entities"]],
            },
            "roundtrip_error_m": loop_error,
        }
    }
    existing_meta = site_json.get("metadata") if isinstance(site_json.get("metadata"), dict) else {}
    site_json["metadata"] = {**existing_meta, **metadata}

    diagnostics = _diagnostics(source_sha256, str(dxf_path), mapping_norm, entities, issues, complete=True)
    diagnostics["transform"] = transform.to_record()
    diagnostics["complete_valid_site"] = True
    return site_json, diagnostics


def source_file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _validate_mapping(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - _KNOWN_MAPPING_KEYS)
    if unknown:
        raise ValueError(f"mapping has unknown keys: {', '.join(unknown)}")
    if raw.get("version") != MAPPING_VERSION:
        raise ValueError(f"mapping.version must be {MAPPING_VERSION!r}")
    layers = raw.get("layers")
    if not isinstance(layers, dict):
        raise ValueError("mapping.layers must be an object")
    extra_layers = sorted(set(layers) - _KNOWN_LAYER_KEYS)
    if extra_layers:
        raise ValueError(f"mapping.layers has unknown keys: {', '.join(extra_layers)}")
    boundary = layers.get("boundary")
    if not isinstance(boundary, str) or not boundary:
        raise ValueError("mapping.layers.boundary must be a layer name")
    obstacles = layers.get("obstacles", [])
    if isinstance(obstacles, str):
        obstacles = [obstacles]
    if not isinstance(obstacles, list) or not all(isinstance(item, str) for item in obstacles):
        raise ValueError("mapping.layers.obstacles must be a layer name or list of names")
    entrances = layers.get("entrances")
    if not isinstance(entrances, str) or not entrances:
        raise ValueError("mapping.layers.entrances must be a layer name")
    entities = raw.get("entrance_entities")
    if not isinstance(entities, list) or not entities:
        raise ValueError("mapping.entrance_entities must be a non-empty list")
    normalized = []
    for index, item in enumerate(entities):
        if not isinstance(item, dict):
            raise ValueError(f"mapping.entrance_entities[{index}] must be an object")
        handle = str(item.get("handle") or "").strip()
        ident = str(item.get("id") or "").strip()
        width = item.get("width_m")
        if not handle or not ident:
            raise ValueError(f"mapping.entrance_entities[{index}] needs handle and id")
        if isinstance(width, bool) or not isinstance(width, int | float) or not math.isfinite(float(width)) or float(width) <= 0:
            raise ValueError(f"mapping.entrance_entities[{index}].width_m must be a positive finite number")
        movements = item.get("allowed_movements") or ["enter", "exit"]
        if not isinstance(movements, list) or not movements:
            raise ValueError(f"mapping.entrance_entities[{index}].allowed_movements must be a list")
        normalized.append(
            {
                "handle": handle.upper(),
                "id": ident,
                "width_m": float(width),
                "allowed_movements": [str(value) for value in movements],
            }
        )
    return {
        "version": MAPPING_VERSION,
        "source_units": raw.get("source_units"),
        "layers": {"boundary": boundary, "obstacles": list(obstacles), "entrances": entrances},
        "entrance_entities": normalized,
    }


def _assemble_site_json(
    defaults: dict[str, Any],
    boundary: Polygon,
    obstacles: list[dict[str, Any]],
    entrances: list[dict[str, Any]],
) -> dict[str, Any]:
    data = copy.deepcopy(defaults)
    if "site" in data and isinstance(data["site"], dict):
        if data["site"].get("boundary") or data["site"].get("obstacles"):
            raise ValueError("defaults must not override DXF boundary or obstacle geometry")
    if "entrances" in data and data["entrances"]:
        raise ValueError("defaults must not override DXF entrance geometry")
    data.setdefault("version", "0.3")
    data.setdefault("units", "m")
    data["site"] = {
        "boundary": {"type": "polygon", "points": [list(point) for point in boundary]},
        "obstacles": obstacles,
        "reserved_areas": data.get("site", {}).get("reserved_areas", []) if isinstance(data.get("site"), dict) else [],
    }
    data["entrances"] = entrances
    return data


def _build_entrances(
    specs: list[dict[str, Any]],
    lines: dict[str, dict[str, Any]],
    transform: CoordinateTransform,
    site_polygon: ShapelyPolygon,
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for spec in specs:
        handle = spec["handle"]
        found = lines.get(handle)
        if found is None:
            issues.append({"code": "unknown_handle", "handle": handle})
            continue
        start, end = found["line"]
        local_start = transform.to_local(start)
        local_end = transform.to_local(end)
        dx = local_end[0] - local_start[0]
        dy = local_end[1] - local_start[1]
        if math.hypot(dx, dy) <= 1e-9:
            issues.append({"code": "entrance_zero_length", "handle": handle})
            continue
        heading = math.degrees(math.atan2(dy, dx))
        inward = ShapelyPoint(local_start[0] + dx * 0.05, local_start[1] + dy * 0.05)
        if not site_polygon.buffer(1e-6).contains(inward) and not site_polygon.covers(inward):
            issues.append({"code": "entrance_heading_not_inward", "handle": handle, "id": spec["id"]})
            continue
        result.append(
            {
                "id": spec["id"],
                "mode": "shared" if set(spec["allowed_movements"]) >= {"enter", "exit"} else (
                    "entry_only" if "enter" in spec["allowed_movements"] else "exit_only"
                ),
                "center": [local_start[0], local_start[1]],
                "width": spec["width_m"],
                "heading_degrees": heading,
                "allowed_movements": spec["allowed_movements"],
                "source_handle": handle,
            }
        )
    return result


def _closed_straight_polygon(entity) -> tuple[Polygon | None, str | None]:
    dxftype = entity.dxftype()
    if dxftype not in {"LWPOLYLINE", "POLYLINE"}:
        return None, "unsupported_boundary_type"
    if dxftype == "LWPOLYLINE":
        if bool(entity.has_arc):
            return None, "bulge"
        points = [(float(x), float(y)) for x, y, *_rest in entity.get_points()]
        closed = bool(entity.closed)
    else:
        if entity.is_3d_polyline:
            return None, "non_planar"
        points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
        if any(abs(float(getattr(vertex.dxf, "bulge", 0.0) or 0.0)) > 1e-12 for vertex in entity.vertices):
            return None, "bulge"
        closed = bool(entity.is_closed)
    if not closed:
        return None, "open_boundary"
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return None, "too_few_points"
    return points, None


def _directed_line(entity) -> tuple[tuple[Point, Point] | None, str | None]:
    if entity.dxftype() != "LINE":
        return None, "entrance_not_line"
    start = entity.dxf.start
    end = entity.dxf.end
    return ((float(start.x), float(start.y)), (float(end.x), float(end.y))), None


def _self_intersects(points: Polygon) -> bool:
    polygon = ShapelyPolygon(points)
    if not polygon.is_valid:
        return True
    ring = LineString(list(points) + [points[0]])
    return not ring.is_simple


def _has_holes(points: Polygon) -> bool:
    return False


def _entity_record(entity) -> dict[str, Any]:
    handle = str(entity.dxf.handle).upper()
    layer = str(entity.dxf.layer)
    kind = entity.dxftype()
    elevation = getattr(entity.dxf, "elevation", None)
    has_bulge = False
    if kind == "LWPOLYLINE":
        has_bulge = bool(getattr(entity, "has_arc", False))
    elif kind == "POLYLINE":
        has_bulge = any(abs(float(getattr(vertex.dxf, "bulge", 0.0) or 0.0)) > 1e-12 for vertex in entity.vertices)
    return {
        "handle": handle,
        "layer": layer,
        "type": kind,
        "elevation": None if elevation is None else float(elevation),
        "has_bulge": has_bulge,
        "result": "seen",
    }


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object")
    return raw


def _empty_diagnostics(source_sha256: str, path: str) -> dict[str, Any]:
    return {
        "version": IMPORT_VERSION,
        "source_path": path,
        "source_sha256": source_sha256,
        "complete_valid_site": False,
        "entities": [],
        "issues": [],
    }


def _diagnostics(
    source_sha256: str,
    path: str,
    mapping: dict[str, Any],
    entities: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    complete: bool,
) -> dict[str, Any]:
    return {
        "version": IMPORT_VERSION,
        "source_path": path,
        "source_sha256": source_sha256,
        "mapping": {
            "version": mapping.get("version"),
            "layers": mapping.get("layers"),
        },
        "complete_valid_site": complete and not issues,
        "entities": entities,
        "issues": issues,
    }


def layout_in_source_coordinates(layout: LayoutResult) -> LayoutResult:
    """Return a copy whose geometry is in the recorded source CAD coordinates."""

    meta = layout.site.metadata.get("cad_import") if isinstance(layout.site.metadata, dict) else None
    if not isinstance(meta, dict) or "transform" not in meta:
        raise ValueError("layout has no cad_import transform to restore")
    transform = transform_from_record(meta["transform"])
    site = replace(
        layout.site,
        boundary=transform.polygon_to_source(layout.site.boundary),
        obstacles=[transform.polygon_to_source(item) for item in layout.site.obstacles],
        units=transform.source_units,
        entrances=[
            EntranceSpec(
                id=item.id,
                mode=item.mode,
                center=transform.to_source(item.center),
                width=item.width,
                heading_degrees=item.heading_degrees,
                allowed_movements=item.allowed_movements,
            )
            for item in layout.site.entrances
        ],
    )
    aisles = [
        ParkingAisle(
            id=aisle.id,
            polygon=transform.polygon_to_source(aisle.polygon),
            angle_degrees=aisle.angle_degrees,
            role=aisle.role,
            connected_to_entrance_id=aisle.connected_to_entrance_id,
            parent_aisle_id=aisle.parent_aisle_id,
            connected_aisle_ids=aisle.connected_aisle_ids,
            directionality=aisle.directionality,
        )
        for aisle in layout.aisles
    ]
    stalls = [
        ParkingStall(
            id=stall.id,
            polygon=transform.polygon_to_source(stall.polygon),
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=stall.stall_type_id,
        )
        for stall in layout.stalls
    ]
    return replace(layout, site=site, aisles=aisles, stalls=stalls)
