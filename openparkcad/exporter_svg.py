from __future__ import annotations

from math import cos, radians, sin
from pathlib import Path
from xml.sax.saxutils import escape

from openparkcad.diagnostic_geometry import all_shape_points, pedestrian_emergency_shapes, site_feature_shapes
from openparkcad.geometry import bounds
from openparkcad.models import LayoutResult, Point, Polygon


def write_svg(layout: LayoutResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    diagnostic_shapes = site_feature_shapes(layout.site.site_features)
    diagnostic_shapes.extend(pedestrian_emergency_shapes(layout.site.pedestrian_and_emergency))

    all_points = list(layout.site.boundary)
    for obstacle in layout.site.obstacles:
        all_points.extend(obstacle)
    for entrance in layout.site.entrances:
        all_points.extend(_entrance_segment(entrance.center, entrance.width, entrance.heading_degrees))
    all_points.extend(all_shape_points(diagnostic_shapes))
    for aisle in layout.aisles:
        all_points.extend(aisle.polygon)
    for stall in layout.stalls:
        all_points.extend(stall.polygon)
    for aisle in _candidate_preview_aisles(layout):
        all_points.extend(aisle["geometry"])
    for stall in _candidate_layout_preview_stalls(layout):
        all_points.extend(stall["geometry"])
    from openparkcad.road_traversal import journey_trajectories

    trajectory_traces = journey_trajectories(layout)
    for _stall_id, points in trajectory_traces:
        all_points.extend(points)

    min_x, min_y, max_x, max_y = bounds(all_points)
    padding = 3.0
    width = max_x - min_x + padding * 2
    height = max_y - min_y + padding * 2

    def tx(point: Point) -> Point:
        x, y = point
        return (x - min_x + padding, max_y - y + padding)

    cad_meta = layout.site.metadata.get("cad_import") if isinstance(layout.site.metadata, dict) else {}
    cad_meta = cad_meta if isinstance(cad_meta, dict) else {}
    transform = cad_meta.get("transform") if isinstance(cad_meta.get("transform"), dict) else {}
    handles = cad_meta.get("source_handles") if isinstance(cad_meta.get("source_handles"), dict) else {}
    svg_attrs = f'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.3f} {height:.3f}"'
    if transform:
        origin = transform.get("origin_m") or [0, 0]
        svg_attrs += (
            f' data-cad-units="{_xml_attr(transform.get("source_units", ""))}"'
            f' data-cad-origin-m="{_xml_attr(origin[0])},{_xml_attr(origin[1])}"'
            f' data-cad-scale="{_xml_attr(transform.get("scale", 1))}"'
        )
    boundary_attrs = {}
    if handles.get("boundary"):
        boundary_attrs["data-source-handle"] = str(handles["boundary"])
        if transform.get("origin_m"):
            boundary_attrs["data-source-origin-m"] = f"{transform['origin_m'][0]},{transform['origin_m'][1]}"
    parts = [
        f"<svg {svg_attrs}>",
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        _polygon_svg(
            [tx(point) for point in layout.site.boundary],
            "#ffffff",
            "#0f172a",
            0.18,
            attributes=boundary_attrs or None,
        ),
    ]
    for obstacle in layout.site.obstacles:
        parts.append(_polygon_svg([tx(point) for point in obstacle], "#fecaca", "#991b1b", 0.12))
    for entrance in layout.site.entrances:
        start, end = [tx(point) for point in _entrance_segment(entrance.center, entrance.width, entrance.heading_degrees)]
        cx, cy = tx(entrance.center)
        parts.append(
            f'<line x1="{start[0]:.3f}" y1="{start[1]:.3f}" x2="{end[0]:.3f}" y2="{end[1]:.3f}" '
            'stroke="#16a34a" stroke-width="0.4" stroke-linecap="round"/>'
        )
        parts.append(
            f'<text x="{cx:.3f}" y="{cy - 1.0:.3f}" font-size="1.2" '
            f'text-anchor="middle" fill="#166534">{_xml_text(entrance.id)}</text>'
        )
    for shape in diagnostic_shapes:
        style = _diagnostic_style(shape.layer)
        for poly in shape.polygons:
            parts.append(_polygon_svg([tx(point) for point in poly], style["fill"], style["stroke"], style["stroke_width"], opacity=style["opacity"]))
        for line in shape.polylines:
            parts.append(_polyline_svg([tx(point) for point in line], style["stroke"], style["stroke_width"]))
        if shape.label_point:
            cx, cy = tx(shape.label_point)
            parts.append(
                f'<text x="{cx:.3f}" y="{cy - 0.6:.3f}" font-size="1.0" text-anchor="middle" '
                f'fill="{_xml_attr(style["stroke"])}">{_xml_text(shape.id)}</text>'
            )
    for aisle in layout.aisles:
        style = _aisle_style(aisle.role)
        poly = [tx(point) for point in aisle.polygon]
        parts.append(
            _polygon_svg(
                poly,
                str(style["fill"]),
                str(style["stroke"]),
                float(style["stroke_width"]),
                opacity=float(style["opacity"]),
            )
        )
        cx, cy = _centroid(poly)
        parts.append(
            f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="0.75" text-anchor="middle" '
            f'fill="{_xml_attr(str(style["stroke"]))}">{_xml_text(aisle.id)}</text>'
        )
    preview_aisles = _candidate_preview_aisles(layout)
    if preview_aisles:
        parts.append('<g id="candidate-network-preview" data-status="preview-only">')
        for aisle in preview_aisles:
            style = _candidate_preview_style(str(aisle["role"]))
            poly = [tx(point) for point in aisle["geometry"]]
            parts.append(
                _polygon_svg(
                    poly,
                    str(style["fill"]),
                    str(style["stroke"]),
                    float(style["stroke_width"]),
                    opacity=float(style["opacity"]),
                    dasharray=str(style["dasharray"]),
                )
            )
            cx, cy = _centroid(poly)
            parts.append(
                f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="0.85" text-anchor="middle" '
                f'fill="{_xml_attr(style["stroke"])}">{_xml_text(aisle["id"])}</text>'
            )
        parts.append("</g>")
    preview_stalls = _candidate_layout_preview_stalls(layout)
    if preview_stalls:
        parts.append('<g id="candidate-layout-preview-stalls" data-status="preview-only">')
        for stall in preview_stalls:
            poly = [tx(point) for point in stall["geometry"]]
            parts.append(
                _polygon_svg(
                    poly,
                    "#ffedd5",
                    "#ea580c",
                    0.08,
                    opacity=0.72,
                    dasharray="0.45 0.25",
                )
            )
            cx, cy = _centroid(poly)
            parts.append(
                f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="0.75" text-anchor="middle" '
                f'fill="#9a3412">{_xml_text(stall["id"])}</text>'
            )
        parts.append("</g>")
    for stall in layout.stalls:
        parts.append(
            _polygon_svg(
                [tx(point) for point in stall.polygon],
                "#dbeafe",
                "#1d4ed8",
                0.06,
                attributes={
                    "data-stall-id": _display_stall_label(stall.id),
                    "data-stall-type-id": stall.stall_type_id or layout.site.stall.id,
                },
            )
        )
        cx, cy = _centroid([tx(point) for point in stall.polygon])
        parts.append(
            f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="0.82" text-anchor="middle" '
            f'fill="#1e3a8a">{_xml_text(_display_stall_label(stall.id))}</text>'
        )
    if trajectory_traces:
        identity = ""
        if isinstance(layout.road_traversal_validation, dict):
            identity = str(layout.road_traversal_validation.get("layout_identity") or "")
        parts.append(f'<g id="road-traversal" data-layout-identity="{_xml_attr(identity)}">')
        for stall_id, points in trajectory_traces:
            parts.append(
                _polyline_svg(
                    [tx(point) for point in points],
                    "#7c3aed",
                    0.12,
                ).replace("<polyline", f'<polyline data-stall-id="{_xml_attr(stall_id)}"', 1)
            )
        parts.append("</g>")
    parts.append("</svg>")

    target.write_text("\n".join(parts), encoding="utf-8")


def _polygon_svg(
    poly: Polygon,
    fill: str,
    stroke: str,
    stroke_width: float,
    opacity: float = 1.0,
    dasharray: str | None = None,
    attributes: dict[str, object] | None = None,
) -> str:
    points = " ".join(f"{x:.3f},{y:.3f}" for x, y in poly)
    dash = f' stroke-dasharray="{_xml_attr(dasharray)}"' if dasharray else ""
    extra = "".join(
        f' {name}="{_xml_attr(value)}"'
        for name, value in sorted((attributes or {}).items())
    )
    return (
        f'<polygon points="{_xml_attr(points)}" fill="{_xml_attr(fill)}" '
        f'stroke="{_xml_attr(stroke)}" stroke-width="{stroke_width}" opacity="{opacity:.2f}"{dash}{extra}/>'
    )


def _polyline_svg(points: list[Point], stroke: str, stroke_width: float) -> str:
    point_text = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return (
        f'<polyline points="{_xml_attr(point_text)}" fill="none" stroke="{_xml_attr(stroke)}" '
        f'stroke-width="{stroke_width}" stroke-dasharray="0.6 0.4"/>'
    )


def _xml_text(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _xml_attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _centroid(poly: Polygon) -> Point:
    return (sum(point[0] for point in poly) / len(poly), sum(point[1] for point in poly) / len(poly))


def _entrance_segment(center: Point, width: float, heading_degrees: float) -> tuple[Point, Point]:
    normal = radians(heading_degrees + 90)
    dx = cos(normal) * width / 2
    dy = sin(normal) * width / 2
    return ((center[0] - dx, center[1] - dy), (center[0] + dx, center[1] + dy))


def _diagnostic_style(layer: str) -> dict[str, float | str]:
    if layer == "SITE_FEATURES":
        return {"fill": "#fde68a", "stroke": "#92400e", "stroke_width": 0.08, "opacity": 0.78}
    if layer == "FIRE_LANES":
        return {"fill": "#fee2e2", "stroke": "#dc2626", "stroke_width": 0.12, "opacity": 0.55}
    if layer == "PEDESTRIAN":
        return {"fill": "#dcfce7", "stroke": "#15803d", "stroke_width": 0.10, "opacity": 0.65}
    return {"fill": "#fef3c7", "stroke": "#92400e", "stroke_width": 0.08, "opacity": 0.70}


def _candidate_preview_aisles(layout: LayoutResult) -> list[dict[str, object]]:
    if _layout_promoted(layout):
        return []
    preview = layout.candidate_network_preview
    if not preview:
        return []
    aisles = preview.get("aisles", [])
    if not isinstance(aisles, list):
        return []
    return [aisle for aisle in aisles if _valid_preview_aisle(aisle)]


def _candidate_layout_preview_stalls(layout: LayoutResult) -> list[dict[str, object]]:
    if _layout_promoted(layout):
        return []
    preview = layout.candidate_layout_preview
    if not preview:
        return []
    stalls = preview.get("stalls", [])
    if not isinstance(stalls, list):
        return []
    current_stall_geometries = {_polygon_key(stall.polygon) for stall in layout.stalls}
    return [
        stall
        for stall in stalls
        if _valid_preview_stall(stall) and stall.get("source") == "shadow_candidate"
        and _polygon_key(stall["geometry"]) not in current_stall_geometries
    ]


def _valid_preview_aisle(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    geometry = raw.get("geometry")
    return isinstance(raw.get("id"), str) and isinstance(raw.get("role"), str) and _valid_polygon(geometry)


def _valid_preview_stall(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    geometry = raw.get("geometry")
    return isinstance(raw.get("id"), str) and _valid_polygon(geometry)


def _valid_polygon(raw: object) -> bool:
    if not isinstance(raw, list) or len(raw) < 3:
        return False
    return all(isinstance(item, list | tuple) and len(item) == 2 for item in raw)


def _layout_promoted(layout: LayoutResult) -> bool:
    return layout.generation_mode == "candidate_layout_promoted" or layout.candidate_layout_promotion.get("status") == "promoted"


def _display_stall_label(stall_id: str) -> str:
    if stall_id.startswith("PL-STALL-"):
        return f"P-{stall_id.removeprefix('PL-STALL-')}"
    return stall_id


def _polygon_key(raw: object) -> tuple[tuple[float, float], ...]:
    if not _valid_polygon(raw):
        return ()
    return tuple(sorted((round(float(x), 6), round(float(y), 6)) for x, y in raw))


def _aisle_style(role: str) -> dict[str, float | str]:
    """Official layout aisle styling by role (solid fills for CAD-style preview)."""
    if role == "main":
        return {"fill": "#e5e7eb", "stroke": "#4b5563", "stroke_width": 0.05, "opacity": 0.92}
    if role == "turnaround":
        return {"fill": "#d1fae5", "stroke": "#047857", "stroke_width": 0.05, "opacity": 0.90}
    if role == "branch":
        return {"fill": "#dbeafe", "stroke": "#1d4ed8", "stroke_width": 0.05, "opacity": 0.90}
    if role == "connector":
        return {"fill": "#fef3c7", "stroke": "#b45309", "stroke_width": 0.06, "opacity": 0.90}
    if role == "jog":
        return {"fill": "#ede9fe", "stroke": "#6d28d9", "stroke_width": 0.06, "opacity": 0.92}
    if role == "exit":
        return {"fill": "#cffafe", "stroke": "#0e7490", "stroke_width": 0.06, "opacity": 0.90}
    if role == "passing_bay":
        return {"fill": "#ffedd5", "stroke": "#c2410c", "stroke_width": 0.06, "opacity": 0.88}
    return {"fill": "#e5e7eb", "stroke": "#6b7280", "stroke_width": 0.04, "opacity": 0.90}


def _candidate_preview_style(role: str) -> dict[str, float | str]:
    if role == "main":
        return {"fill": "#ccfbf1", "stroke": "#0f766e", "stroke_width": 0.10, "opacity": 0.28, "dasharray": "0.9 0.45"}
    if role == "turnaround":
        return {"fill": "#d9f99d", "stroke": "#4d7c0f", "stroke_width": 0.10, "opacity": 0.26, "dasharray": "0.9 0.45"}
    if role == "connector":
        return {"fill": "#fde68a", "stroke": "#b45309", "stroke_width": 0.12, "opacity": 0.34, "dasharray": "0.7 0.35"}
    if role == "jog":
        return {"fill": "#ddd6fe", "stroke": "#6d28d9", "stroke_width": 0.12, "opacity": 0.32, "dasharray": "0.7 0.35"}
    if role == "exit":
        return {"fill": "#a5f3fc", "stroke": "#0e7490", "stroke_width": 0.12, "opacity": 0.32, "dasharray": "0.7 0.35"}
    if role == "passing_bay":
        return {"fill": "#fed7aa", "stroke": "#c2410c", "stroke_width": 0.12, "opacity": 0.34, "dasharray": "0.6 0.3"}
    if role == "branch":
        return {"fill": "#bfdbfe", "stroke": "#1d4ed8", "stroke_width": 0.12, "opacity": 0.32, "dasharray": "0.7 0.35"}
    return {"fill": "#f0abfc", "stroke": "#a21caf", "stroke_width": 0.12, "opacity": 0.32, "dasharray": "0.7 0.35"}
