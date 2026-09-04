"""Reversible source-WCS to local-metre transforms for CAD import."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from openparkcad.models import Point, Polygon

TRANSFORM_VERSION = "cad-transform-1"
LOOP_TOLERANCE_M = 1e-6
_SUPPORTED_UNITS = {
    "m": 1.0,
    "metre": 1.0,
    "meter": 1.0,
    "metres": 1.0,
    "meters": 1.0,
    "mm": 0.001,
    "millimetre": 0.001,
    "millimeter": 0.001,
    "millimetres": 0.001,
    "millimeters": 0.001,
}
_DXF_INSUNITS = {
    0: None,
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
}


@dataclass(frozen=True)
class CoordinateTransform:
    source_units: str
    unit_source: str
    scale: float
    origin_m: Point
    version: str = TRANSFORM_VERSION

    def to_local(self, point: Point) -> Point:
        return (self.scale * point[0] - self.origin_m[0], self.scale * point[1] - self.origin_m[1])

    def to_source(self, point: Point) -> Point:
        return ((point[0] + self.origin_m[0]) / self.scale, (point[1] + self.origin_m[1]) / self.scale)

    def polygon_to_local(self, points: Polygon) -> Polygon:
        return [self.to_local(item) for item in points]

    def polygon_to_source(self, points: Polygon) -> Polygon:
        return [self.to_source(item) for item in points]

    def max_roundtrip_error_m(self, points: Polygon) -> float:
        errors = []
        for point in points:
            local = self.to_local(point)
            restored = self.to_source(local)
            errors.append(math.hypot(restored[0] - point[0], restored[1] - point[1]) * self.scale)
        return max(errors) if errors else 0.0

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_units": self.source_units,
            "unit_source": self.unit_source,
            "scale": self.scale,
            "origin_m": [self.origin_m[0], self.origin_m[1]],
            "loop_tolerance_m": LOOP_TOLERANCE_M,
            "formula": "local = scale * source - origin_m",
            "inverse": "source = (local + origin_m) / scale",
        }


def normalize_unit_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if key in _SUPPORTED_UNITS:
        return "m" if _SUPPORTED_UNITS[key] == 1.0 else "mm"
    return key or None


def scale_for_unit(unit: str) -> float:
    key = str(unit).strip().lower()
    if key not in _SUPPORTED_UNITS:
        raise ValueError(f"unsupported source unit {unit!r}")
    return _SUPPORTED_UNITS[key]


def units_from_dxf_insunits(insunits: int | None) -> str | None:
    if insunits is None:
        return None
    declared = _DXF_INSUNITS.get(int(insunits))
    if declared in {None}:
        return None
    if declared in {"m", "mm"}:
        return declared
    return declared


def resolve_source_units(*, mapping_units: str | None, dxf_units: str | None) -> tuple[str, str]:
    mapping_norm = normalize_unit_name(mapping_units)
    dxf_norm = normalize_unit_name(dxf_units)
    if mapping_norm in {None} and dxf_norm in {None}:
        raise ValueError("source units are missing from both the mapping and the DXF INSUNITS")
    if mapping_norm is not None and dxf_norm is not None and mapping_norm != dxf_norm:
        raise ValueError(f"source units conflict: mapping={mapping_norm!r} dxf={dxf_norm!r}")
    if mapping_norm is not None and dxf_norm is not None:
        return mapping_norm, "mapping_and_dxf"
    if mapping_norm is not None:
        return mapping_norm, "mapping"
    assert dxf_norm is not None
    if dxf_norm not in {"m", "mm"}:
        raise ValueError(f"unsupported DXF unit {dxf_norm!r}")
    return dxf_norm, "dxf"


def build_transform(*, source_units: str, unit_source: str, source_boundary: Polygon) -> CoordinateTransform:
    scale = scale_for_unit(source_units)
    xs = [scale * point[0] for point in source_boundary]
    ys = [scale * point[1] for point in source_boundary]
    origin = (min(xs), min(ys))
    return CoordinateTransform(source_units=source_units, unit_source=unit_source, scale=scale, origin_m=origin)


def transform_from_record(raw: dict[str, Any]) -> CoordinateTransform:
    origin = raw.get("origin_m") or [0.0, 0.0]
    return CoordinateTransform(
        source_units=str(raw.get("source_units", "m")),
        unit_source=str(raw.get("unit_source", "mapping")),
        scale=float(raw.get("scale", 1.0)),
        origin_m=(float(origin[0]), float(origin[1])),
        version=str(raw.get("version", TRANSFORM_VERSION)),
    )
