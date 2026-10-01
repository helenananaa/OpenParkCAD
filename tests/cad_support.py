"""Build CAD import fixtures with recorded DXF handles."""

from __future__ import annotations

import json
from pathlib import Path

import ezdxf

from openparkcad.models import Point, Polygon

DEFAULTS = {
    "version": "0.3",
    "name": "cad-imported site",
    "units": "m",
    "parking": {
        "stall_types": [
            {
                "id": "standard-90",
                "family": "perpendicular",
                "width": 2.5,
                "length": 5.0,
                "allowed_angles": [90],
            }
        ]
    },
    "aisles": {
        "selection_mode": "fixed",
        "fixed_class": "wide-two-way",
        "classes": [
            {
                "id": "wide-two-way",
                "width": 6.0,
                "capacity": "two_vehicle",
                "directionality": "two_way",
            }
        ],
    },
    "vehicles": {
        "design_vehicle": {
            "id": "passenger-car",
            "length": 4.8,
            "width": 1.9,
            "wheelbase": 2.8,
            "min_turning_radius": 5.5,
            "turning_radius_reference": "outer_front_wheel",
            "track_width": 1.6,
            "front_overhang": 1.0,
            "rear_overhang": 1.0,
            "swept_path_margin": 0.3,
            "max_reverse_distance": 12.0,
        }
    },
    "constraints": {"setbacks": {"site_boundary": 0.0}},
}


def write_defaults(path: Path) -> Path:
    path.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    return path


def rectangle_boundary(width: float = 20.0, height: float = 30.0, origin: Point = (0.0, 0.0)) -> Polygon:
    x, y = origin
    return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]


def write_rectangle_dxf(
    path: Path,
    *,
    units: str = "mm",
    origin: Point = (0.0, 0.0),
    width: float = 20.0,
    height: float = 30.0,
    obstacle: Polygon | None = None,
    extra_layer_text: bool = False,
    open_boundary: bool = False,
    bulge: bool = False,
    insert: bool = False,
    arc: bool = False,
) -> dict[str, str]:
    scale = 1000.0 if units == "mm" else 1.0
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM if units == "mm" else ezdxf.units.M
    for name in ("OPC_BOUNDARY", "OPC_OBSTACLES", "OPC_ENTRANCES", "OPC_NOTES"):
        if name not in doc.layers:
            doc.layers.add(name)
    msp = doc.modelspace()
    boundary = [(x * scale, y * scale) for x, y in rectangle_boundary(width, height, origin)]
    if open_boundary:
        entity = msp.add_lwpolyline(boundary, close=False, dxfattribs={"layer": "OPC_BOUNDARY"})
    elif bulge:
        entity = msp.add_lwpolyline(boundary, close=True, dxfattribs={"layer": "OPC_BOUNDARY"})
        with entity.points() as points:
            points[0] = (points[0][0], points[0][1], 0, 0, 0.5)
    else:
        entity = msp.add_lwpolyline(boundary, close=True, dxfattribs={"layer": "OPC_BOUNDARY"})
    handles = {"boundary": str(entity.dxf.handle).upper()}
    if obstacle is None:
        obstacle = [
            (origin[0] + 4.0, origin[1] + 4.0),
            (origin[0] + 6.0, origin[1] + 4.0),
            (origin[0] + 6.0, origin[1] + 6.0),
            (origin[0] + 4.0, origin[1] + 6.0),
        ]
    obs = msp.add_lwpolyline([(x * scale, y * scale) for x, y in obstacle], close=True, dxfattribs={"layer": "OPC_OBSTACLES"})
    handles["obstacle"] = str(obs.dxf.handle).upper()
    entrance_start = ((origin[0] + width / 2.0) * scale, origin[1] * scale)
    entrance_end = ((origin[0] + width / 2.0) * scale, (origin[1] + 2.0) * scale)
    line = msp.add_line(entrance_start, entrance_end, dxfattribs={"layer": "OPC_ENTRANCES"})
    handles["entrance"] = str(line.dxf.handle).upper()
    if extra_layer_text:
        msp.add_text("ignore me", dxfattribs={"layer": "OPC_NOTES", "height": 1.0 * scale}).dxf.insert = (
            (origin[0] + 1) * scale,
            (origin[1] + 1) * scale,
        )
        block = doc.blocks.new("NOTEBLK")
        block.add_circle((0, 0), 0.2 * scale)
        if insert:
            msp.add_blockref("NOTEBLK", ((origin[0] + 2) * scale, (origin[1] + 2) * scale), dxfattribs={"layer": "OPC_NOTES"})
    if insert and not extra_layer_text:
        block = doc.blocks.new("BAD")
        block.add_line((0, 0), (1, 0))
        msp.add_blockref("BAD", (origin[0] * scale, origin[1] * scale), dxfattribs={"layer": "OPC_BOUNDARY"})
    if arc:
        msp.add_arc(
            center=((origin[0] + 10) * scale, (origin[1] + 10) * scale),
            radius=2.0 * scale,
            start_angle=0,
            end_angle=90,
            dxfattribs={"layer": "OPC_BOUNDARY"},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return handles


def write_mapping(path: Path, handles: dict[str, str], *, units: str = "mm", width_m: float = 6.0) -> Path:
    payload = {
        "version": "cad-import-mapping-1",
        "source_units": units,
        "layers": {
            "boundary": "OPC_BOUNDARY",
            "obstacles": ["OPC_OBSTACLES"],
            "entrances": "OPC_ENTRANCES",
        },
        "entrance_entities": [
            {
                "handle": handles["entrance"],
                "id": "ENTRY-1",
                "width_m": width_m,
                "allowed_movements": ["enter", "exit"],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
