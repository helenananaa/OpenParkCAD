from __future__ import annotations

from pathlib import Path
from math import cos, radians, sin

import ezdxf

from openparkcad.diagnostic_geometry import pedestrian_emergency_shapes, site_feature_shapes
from openparkcad.models import LayoutResult, Point, Polygon


def write_dxf(layout: LayoutResult, path: str | Path, *, restore_source_coordinates: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if restore_source_coordinates:
        from openparkcad.cad_import import layout_in_source_coordinates

        layout = layout_in_source_coordinates(layout)

    doc = ezdxf.new("R2010", setup=True)
    units = str(layout.site.units).strip().lower()
    doc.units = ezdxf.units.MM if units in {"mm", "millimetre", "millimeter"} else ezdxf.units.M
    if "OPENPARKCAD" not in doc.appids:
        doc.appids.add("OPENPARKCAD")
    _ensure_layers(doc)

    msp = doc.modelspace()
    _add_polyline(msp, layout.site.boundary, "BOUNDARY")
    for obstacle in layout.site.obstacles:
        _add_polyline(msp, obstacle, "OBSTACLES")
    for entrance in layout.site.entrances:
        _add_entrance(msp, entrance.center, entrance.width, entrance.heading_degrees, entrance.id)
    diagnostic_shapes = site_feature_shapes(layout.site.site_features)
    diagnostic_shapes.extend(pedestrian_emergency_shapes(layout.site.pedestrian_and_emergency))
    for shape in diagnostic_shapes:
        for poly in shape.polygons:
            _add_polyline(msp, poly, shape.layer)
        for line in shape.polylines:
            _add_open_polyline(msp, line, shape.layer)
        if shape.label_point:
            _add_text(msp, shape.label_point, shape.id, shape.layer, height=0.45)
    for aisle in layout.aisles:
        layer = _aisle_layer(aisle.role)
        entity = _add_polyline(msp, aisle.polygon, layer)
        entity.set_xdata(
            "OPENPARKCAD",
            [
                (1000, "parking_aisle"),
                (1000, aisle.id),
                (1000, aisle.role),
                (1000, aisle.parent_aisle_id or ""),
            ],
        )
        _add_text(msp, _centroid(aisle.polygon), aisle.id, "LABELS", height=0.4)
    for stall in layout.stalls:
        entity = _add_polyline(msp, stall.polygon, "STALLS")
        entity.set_xdata(
            "OPENPARKCAD",
            [
                (1000, "parking_stall"),
                (1000, stall.id),
                (1000, stall.stall_type_id or layout.site.stall.id),
            ],
        )
        _add_text(msp, _centroid(stall.polygon), stall.id, "LABELS", height=0.45)
    from openparkcad.road_traversal import journey_trajectories

    for stall_id, points in journey_trajectories(layout):
        if len(points) < 2:
            continue
        entity = _add_open_polyline(msp, points, "TRAJECTORIES")
        if entity is not None:
            entity.set_xdata(
                "OPENPARKCAD",
                [
                    (1000, "road_traversal"),
                    (1000, stall_id),
                    (1000, (layout.road_traversal_validation or {}).get("layout_identity") or ""),
                ],
            )

    doc.saveas(target)


def _aisle_layer(role: str) -> str:
    """Map aisle roles onto dedicated DXF layers (plus legacy AISLES fallback)."""
    mapping = {
        "main": "AISLES_MAIN",
        "turnaround": "AISLES_TURNAROUND",
        "branch": "AISLES_BRANCH",
        "connector": "AISLES_CONNECTOR",
        "jog": "AISLES_JOG",
        "exit": "AISLES_EXIT",
        "passing_bay": "AISLES_PASSING_BAY",
    }
    return mapping.get(role, "AISLES")


def _ensure_layers(doc) -> None:
    layers = {
        "BOUNDARY": 7,
        "OBSTACLES": 1,
        "ENTRANCES": 3,
        "SITE_FEATURES": 30,
        "PEDESTRIAN": 94,
        "FIRE_LANES": 10,
        "AISLES": 8,
        "AISLES_MAIN": 8,
        "AISLES_TURNAROUND": 94,
        "AISLES_BRANCH": 140,
        "AISLES_CONNECTOR": 40,
        "AISLES_JOG": 200,
        "AISLES_EXIT": 3,
        "AISLES_PASSING_BAY": 30,
        "STALLS": 5,
        "LABELS": 3,
        "TRAJECTORIES": 6,
    }
    for name, color in layers.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)


def _add_polyline(msp, poly: Polygon, layer: str):
    return msp.add_lwpolyline(poly, close=True, dxfattribs={"layer": layer})


def _add_open_polyline(msp, points: list[Point], layer: str):
    if len(points) >= 2:
        return msp.add_lwpolyline(points, close=False, dxfattribs={"layer": layer})
    return None


def _add_text(msp, point: Point, text: str, layer: str, height: float) -> None:
    entity = msp.add_text(text, dxfattribs={"layer": layer, "height": height})
    entity.dxf.insert = point


def _add_entrance(msp, center: Point, width: float, heading_degrees: float, label: str) -> None:
    start, end = _entrance_segment(center, width, heading_degrees)
    msp.add_line(start, end, dxfattribs={"layer": "ENTRANCES"})
    _add_text(msp, (center[0], center[1] + 0.8), label, "ENTRANCES", height=0.55)


def _centroid(poly: Polygon) -> Point:
    return (sum(point[0] for point in poly) / len(poly), sum(point[1] for point in poly) / len(poly))


def _entrance_segment(center: Point, width: float, heading_degrees: float) -> tuple[Point, Point]:
    normal = radians(heading_degrees + 90)
    dx = cos(normal) * width / 2
    dy = sin(normal) * width / 2
    return ((center[0] - dx, center[1] - dy), (center[0] + dx, center[1] + dy))
