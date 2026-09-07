"""Independent parallel-ladder skeleton generator (v0.5 N4). Not wired to official search."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString, Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry

from openparkcad.models import SiteSpec
from openparkcad.road_network_config import parse_road_network_mapping
from openparkcad.road_skeleton import (
    ROAD_SKELETON_VERSION,
    RoadSkeleton,
    SkeletonValidationError,
    make_movement,
    make_node,
    make_segment,
    make_skeleton,
)
from openparkcad.road_skeleton_geometry import derive_segment_polygon
from openparkcad.road_skeleton_validation import CODE_ENTRANCE_PORT_MISMATCH, CODE_ROAD_OUTSIDE_DRIVEABLE, validate_skeleton
from openparkcad.site_constraints import site_usable_area

GENERATOR_VERSION = "parallel-ladder-1"
FAILURE_NO_GEOMETRIC_CORRIDOR = "no_geometric_corridor"
MIN_PARKING_AISLES = 2


@dataclass(frozen=True)
class LadderSearchConfig:
    max_skeletons: int = 16
    dominant_axis_count: int = 2
    max_parallel_aisles: int = 6
    cross_aisle_policy: str = "entry_end"
    allow_one_way_loop: bool = False


@dataclass(frozen=True)
class LadderCandidate:
    skeleton: RoadSkeleton
    prefilter_score: float
    prefilter_components: Mapping[str, float]
    axis_degrees: float
    aisle_count: int
    policy: str


@dataclass
class LadderGenerationResult:
    candidates: list[LadderCandidate] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    failure_class: str | None = None
    notes: list[str] = field(default_factory=list)


def read_ladder_config(site: SiteSpec, override: Mapping[str, Any] | None = None) -> LadderSearchConfig:
    raw = dict((site.optimization or {}).get("road_network") or {})
    if override:
        raw.update(dict(override))
    parsed = parse_road_network_mapping(raw)
    return LadderSearchConfig(
        max_skeletons=parsed.max_skeletons,
        dominant_axis_count=parsed.dominant_axis_count,
        max_parallel_aisles=parsed.max_parallel_aisles,
        cross_aisle_policy=parsed.cross_aisle_policy,
        allow_one_way_loop=False,
    )


def generate_parallel_ladder_skeletons(
    site: SiteSpec,
    *,
    config: LadderSearchConfig | Mapping[str, Any] | None = None,
) -> LadderGenerationResult:
    settings = config if isinstance(config, LadderSearchConfig) else read_ladder_config(site, config)
    result = LadderGenerationResult(
        counts={
            "generated": 0,
            "deduplicated": 0,
            "prefilter_passed": 0,
            "retained": 0,
            "rejected_width": 0,
            "rejected_geometry": 0,
            "omitted_by_cap": 0,
        }
    )
    usable = site_usable_area(site, "aisle")
    if usable.is_empty:
        result.failure_class = FAILURE_NO_GEOMETRIC_CORRIDOR
        return result
    aisle_width = float(site.aisle_width)
    stall_depth = float(site.stall.length)
    pitch = aisle_width + 2.0 * stall_depth
    min_length = max(2.0 * float(site.stall.width), 8.0)
    axes = _candidate_axes(site, usable, settings.dominant_axis_count)
    policies = ["both_ends", "entry_end"] if settings.cross_aisle_policy == "both_ends" else ["entry_end"]
    raw: list[LadderCandidate] = []
    seen: set[str] = set()
    for _origin, heading in axes:
        for policy in policies:
            for count in range(MIN_PARKING_AISLES, settings.max_parallel_aisles + 1):
                result.counts["generated"] += 1
                built = _build_one(
                    site, usable, heading, count, policy, aisle_width, stall_depth, pitch, min_length
                )
                if built is None:
                    result.counts["rejected_geometry"] += 1
                    continue
                skeleton, components = built
                if any(abs(segment.width - aisle_width) > 1e-9 for segment in skeleton.segments):
                    result.counts["rejected_width"] += 1
                    continue
                issues = validate_skeleton(skeleton, site=site)
                if any(issue.code in {CODE_ROAD_OUTSIDE_DRIVEABLE, CODE_ENTRANCE_PORT_MISMATCH} for issue in issues):
                    result.counts["rejected_geometry"] += 1
                    continue
                result.counts["prefilter_passed"] += 1
                if skeleton.skeleton_id in seen:
                    result.counts["deduplicated"] += 1
                    result.notes.append(f"dedup {skeleton.skeleton_id}")
                    continue
                seen.add(skeleton.skeleton_id)
                raw.append(
                    LadderCandidate(
                        skeleton=skeleton,
                        prefilter_score=_prefilter_score(components),
                        prefilter_components=components,
                        axis_degrees=heading,
                        aisle_count=count,
                        policy=policy,
                    )
                )
    raw.sort(key=lambda item: (-item.prefilter_score, item.skeleton.skeleton_id))
    retained = raw[: settings.max_skeletons]
    result.candidates = retained
    result.truncated = len(raw) > settings.max_skeletons
    result.counts["retained"] = len(retained)
    result.counts["omitted_by_cap"] = max(len(raw) - len(retained), 0)
    if not retained:
        result.failure_class = FAILURE_NO_GEOMETRIC_CORRIDOR
    return result


def _candidate_axes(site: SiteSpec, usable: BaseGeometry, limit: int) -> list[tuple[tuple[float, float], float]]:
    entrance = site.entrances[0]
    axes: list[tuple[tuple[float, float], float]] = [(entrance.center, float(entrance.heading_degrees) % 180.0)]
    minx, miny, maxx, maxy = usable.bounds
    axes.append((entrance.center, 0.0 if (maxx - minx) >= (maxy - miny) else 90.0))
    rectangle = usable.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    if len(coords) >= 2:
        heading = (math.degrees(math.atan2(coords[1][1] - coords[0][1], coords[1][0] - coords[0][0])) + 360.0) % 180.0
        axes.append((entrance.center, heading))
    unique: list[tuple[tuple[float, float], float]] = []
    for origin, heading in axes:
        heading = heading % 180.0
        if any(min(abs(heading - other[1]) % 180.0, 180.0 - abs(heading - other[1]) % 180.0) < 8.0 for other in unique):
            continue
        unique.append((origin, heading))
        if len(unique) >= limit:
            break
    return unique[:limit] or [(entrance.center, float(entrance.heading_degrees) % 180.0)]


def _build_one(
    site: SiteSpec,
    usable: BaseGeometry,
    heading: float,
    count: int,
    policy: str,
    aisle_width: float,
    stall_depth: float,
    pitch: float,
    min_length: float,
) -> tuple[RoadSkeleton, dict[str, float]] | None:
    entrance = site.entrances[0]
    frame = entrance.center
    pack = (count - 1) * pitch + aisle_width
    minx, miny, maxx, maxy = usable.bounds
    local_pts = [_to_local(pt, frame, heading) for pt in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy))]
    v_min, v_max = min(p[1] for p in local_pts), max(p[1] for p in local_pts)
    u_min, u_max = min(p[0] for p in local_pts), max(p[0] for p in local_pts)
    if pack > (v_max - v_min) + 1e-6:
        return None
    v0 = (v_min + v_max - pack) / 2.0
    aisle_vs = [v0 + aisle_width / 2.0 + i * pitch for i in range(count)]
    ev = _to_local(entrance.center, frame, heading)[1]
    if not (aisle_vs[0] - aisle_width <= ev <= aisle_vs[-1] + aisle_width):
        shift = ev - (aisle_vs[0] + aisle_vs[-1]) / 2.0
        aisle_vs = [v + shift for v in aisle_vs]
        if aisle_vs[0] - aisle_width / 2.0 < v_min - 0.2 or aisle_vs[-1] + aisle_width / 2.0 > v_max + 0.2:
            return None
    u_entry = max(u_min + aisle_width / 2.0, aisle_width / 2.0)
    u_far = u_max - aisle_width / 2.0
    park_end = u_far if policy == "both_ends" else u_far
    if park_end - u_entry < min_length:
        return None

    nodes = [
        make_node("N-ENT", "entrance_port", entrance.center, heading_degrees=entrance.heading_degrees, source_id=entrance.id)
    ]
    segments = []
    movements = []

    def world(u: float, v: float) -> tuple[float, float]:
        return _to_world(u, v, frame, heading)

    def add_node(nid: str, kind: str, u: float, v: float):
        nodes.append(make_node(nid, kind, world(u, v), heading_degrees=heading))

    def add_seg(sid: str, role: str, a_id: str, b_id: str, ua, va, ub, vb, sides) -> bool:
        a, b = world(ua, va), world(ub, vb)
        band = LineString([a, b]).buffer(aisle_width / 2.0, cap_style="flat", join_style="mitre")
        leftover = band.difference(usable.buffer(0.08))
        if leftover.area > max(0.8, 0.08 * max(band.area, 1.0)):
            return False
        segments.append(
            make_segment(
                sid,
                role,
                a_id,
                b_id,
                (a, b),
                aisle_width,
                "two_way",
                parking_sides=sides,
                source={"generator": GENERATOR_VERSION, "axis": heading, "policy": policy},
            )
        )
        return True

    pack_v0, pack_v1 = (min(aisle_vs[0], aisle_vs[-1]), max(aisle_vs[0], aisle_vs[-1]))
    ext_v0, ext_v1 = _available_v_span(usable, frame, heading, u_entry, aisle_width, pack_v0, pack_v1)
    half = aisle_width / 2.0
    cross_v0 = min(pack_v0, ext_v0 + half)
    cross_v1 = max(pack_v1, ext_v1 - half)
    if cross_v1 - cross_v0 < pack_v1 - pack_v0:
        cross_v0, cross_v1 = pack_v0, pack_v1
    add_node("N-C0", "junction", u_entry, cross_v0)
    add_node("N-C1", "junction", u_entry, cross_v1)
    if not add_seg("S-CROSS-ENTRY", "cross_aisle", "N-C0", "N-C1", u_entry, cross_v0, u_entry, cross_v1, ("none",)):
        nodes[:] = [node for node in nodes if node.id not in {"N-C0", "N-C1"}]
        cross_v0, cross_v1 = pack_v0, pack_v1
        add_node("N-C0", "junction", u_entry, cross_v0)
        add_node("N-C1", "junction", u_entry, cross_v1)
        if not add_seg("S-CROSS-ENTRY", "cross_aisle", "N-C0", "N-C1", u_entry, cross_v0, u_entry, cross_v1, ("none",)):
            return None
    if ShapelyPoint(entrance.center).distance(derive_segment_polygon(segments[-1])) > 0.51:
        return None
    gate_v = _to_local(entrance.center, frame, heading)[1]
    add_node("N-GATE", "junction", u_entry, gate_v)
    if not add_seg("S-THROAT", "cross_aisle", "N-ENT", "N-GATE", 0.0, gate_v, u_entry, gate_v, ("none",)):
        # Keep the entrance node even if the stub is too short to add; merge identities below.
        pass
    else:
        movements.append(make_movement("M-THROAT", "S-THROAT", "S-CROSS-ENTRY", "N-GATE", "straight"))
    movements.append(make_movement("M-ENTER", "S-CROSS-ENTRY", "S-CROSS-ENTRY", "N-ENT", "enter"))

    for i, v in enumerate(aisle_vs):
        add_node(f"N-A{i}-NEAR", "junction", u_entry, v)
        far_kind = "junction" if policy == "both_ends" else "turnaround"
        add_node(f"N-A{i}-FAR", far_kind, park_end, v)
        sid = f"S-PARK-{i}"
        if not add_seg(sid, "parking_aisle", f"N-A{i}-NEAR", f"N-A{i}-FAR", u_entry, v, park_end, v, ("left", "right")):
            return None
        movements.append(make_movement(f"M-T{i}-IN", "S-CROSS-ENTRY", sid, f"N-A{i}-NEAR", "left"))
        movements.append(make_movement(f"M-T{i}-OUT", sid, "S-CROSS-ENTRY", f"N-A{i}-NEAR", "right"))

    if policy == "both_ends":
        add_node("N-F0", "junction", u_far, aisle_vs[0])
        add_node("N-F1", "junction", u_far, aisle_vs[-1])
        if not add_seg("S-CROSS-FAR", "cross_aisle", "N-F0", "N-F1", u_far, aisle_vs[0], u_far, aisle_vs[-1], ("none",)):
            return None
        for i, v in enumerate(aisle_vs):
            movements.append(make_movement(f"M-F{i}-IN", "S-CROSS-FAR", f"S-PARK-{i}", f"N-A{i}-FAR", "left"))
            movements.append(make_movement(f"M-F{i}-OUT", f"S-PARK-{i}", "S-CROSS-FAR", f"N-A{i}-FAR", "right"))
    else:
        for i, v in enumerate(aisle_vs):
            add_node(f"N-A{i}-TIP", "turnaround", park_end + aisle_width / 2.0, v)
            if add_seg(
                f"S-TURN-{i}",
                "turnaround",
                f"N-A{i}-FAR",
                f"N-A{i}-TIP",
                park_end,
                v,
                park_end + aisle_width / 2.0,
                v,
                ("none",),
            ):
                movements.append(make_movement(f"M-TURN-{i}", f"S-PARK-{i}", f"S-TURN-{i}", f"N-A{i}-FAR", "straight"))

    try:
        skeleton = make_skeleton(
            family="parallel_ladder",
            nodes=nodes,
            segments=segments,
            movements=movements,
            entrance_ids=(entrance.id,),
            source={
                "generator": GENERATOR_VERSION,
                "axis_degrees": heading,
                "policy": policy,
                "aisle_count": count,
                "pitch": pitch,
            },
            site=site,
            strict=False,
        )
    except (ValueError, SkeletonValidationError):
        return None
    parking_len = sum(_seg_len(seg) for seg in skeleton.segments if seg.role == "parking_aisle")
    road_area = sum(float(derive_segment_polygon(seg).area) for seg in skeleton.segments)
    components = {
        "parking_length": parking_len,
        "aisle_count": float(count),
        "road_area": road_area,
        "t_junctions": float(sum(1 for mov in skeleton.movements if mov.movement_kind in {"left", "right"})),
        "dead_end_length": 0.0 if policy == "both_ends" else float(count) * aisle_width,
        "entrance_connected": 1.0,
        "obstacle_clearance": 1.0,
        "unsupported_turns_est": 0.0,
    }
    return skeleton, components


def _available_v_span(
    usable: BaseGeometry,
    frame: tuple[float, float],
    heading: float,
    u: float,
    aisle_width: float,
    pack_v0: float,
    pack_v1: float,
) -> tuple[float, float]:
    minx, miny, maxx, maxy = usable.bounds
    samples = []
    for x in (minx, maxx, (minx + maxx) / 2.0):
        for y in (miny, maxy, (miny + maxy) / 2.0):
            samples.append(_to_local((x, y), frame, heading)[1])
    v_lo, v_hi = min(samples), max(samples)
    step = max(aisle_width / 2.0, 1.0)
    usable_vs = []
    v = v_lo
    while v <= v_hi + 1e-6:
        point = _to_world(u, v, frame, heading)
        if usable.buffer(0.05).covers(ShapelyPoint(point)):
            usable_vs.append(v)
        v += step
    if not usable_vs:
        return pack_v0, pack_v1
    lo = min(min(usable_vs), pack_v0) + 0.35
    hi = max(max(usable_vs), pack_v1) - 0.35
    if hi - lo < abs(pack_v1 - pack_v0):
        return pack_v0, pack_v1
    return lo, hi


def _prefilter_score(components: Mapping[str, float]) -> float:
    return (
        0.4 * components.get("parking_length", 0.0)
        + 8.0 * components.get("aisle_count", 0.0)
        - 0.01 * components.get("road_area", 0.0)
        - 0.2 * components.get("dead_end_length", 0.0)
        + 0.5 * components.get("t_junctions", 0.0)
        + 5.0 * components.get("entrance_connected", 0.0)
        - 4.0 * components.get("unsupported_turns_est", 0.0)
    )


def _seg_len(segment) -> float:
    start, end = segment.centerline[0], segment.centerline[-1]
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _to_local(point: tuple[float, float], origin: tuple[float, float], heading_degrees: float) -> tuple[float, float]:
    radians = math.radians(heading_degrees)
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    return (dx * math.cos(radians) + dy * math.sin(radians), -dx * math.sin(radians) + dy * math.cos(radians))


def _to_world(u: float, v: float, origin: tuple[float, float], heading_degrees: float) -> tuple[float, float]:
    radians = math.radians(heading_degrees)
    return (
        origin[0] + u * math.cos(radians) - v * math.sin(radians),
        origin[1] + u * math.sin(radians) + v * math.cos(radians),
    )


def skeleton_debug_json(candidate: LadderCandidate) -> dict[str, Any]:
    skeleton = candidate.skeleton
    return {
        "version": GENERATOR_VERSION,
        "skeleton_id": skeleton.skeleton_id,
        "family": skeleton.family,
        "road_skeleton_version": ROAD_SKELETON_VERSION,
        "prefilter_score": candidate.prefilter_score,
        "prefilter_components": dict(candidate.prefilter_components),
        "axis_degrees": candidate.axis_degrees,
        "aisle_count": candidate.aisle_count,
        "policy": candidate.policy,
        "nodes": [{"id": n.id, "kind": n.kind, "point": list(n.point)} for n in skeleton.nodes],
        "segments": [
            {
                "id": s.id,
                "role": s.role,
                "width": s.width,
                "centerline": [list(p) for p in s.centerline],
            }
            for s in skeleton.segments
        ],
        "movements": [
            {"id": m.id, "from": m.from_segment_id, "to": m.to_segment_id, "kind": m.movement_kind}
            for m in skeleton.movements
        ],
    }


def skeleton_debug_svg(candidate: LadderCandidate, site: SiteSpec) -> str:
    colors = {"parking_aisle": "#3d7ea6", "cross_aisle": "#c47b2c", "turnaround": "#6b7c3d"}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -8 140 100">',
        '<g transform="scale(1,-1) translate(0,-85)">',
        f'<polygon fill="#f4f1ea" stroke="#333" points="{" ".join(f"{x},{y}" for x, y in site.boundary)}"/>',
    ]
    for segment in candidate.skeleton.segments:
        poly = derive_segment_polygon(segment)
        if poly.is_empty:
            continue
        fill = colors.get(segment.role, "#888")
        pts = " ".join(f"{x},{y}" for x, y in poly.exterior.coords)
        parts.append(f'<polygon fill="{fill}" fill-opacity="0.45" stroke="{fill}" points="{pts}"/>')
    parts.append("</g></svg>")
    return "\n".join(parts)
