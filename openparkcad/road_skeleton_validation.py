"""Fail-closed structure and geometry checks for RoadSkeleton."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any

from shapely.geometry import GeometryCollection, Point as ShapelyPoint
from shapely.ops import unary_union

from openparkcad.models import SiteSpec
from openparkcad.road_skeleton import (
    SUPPORTED_DIRECTIONALITY,
    SUPPORTED_MOVEMENT_KINDS,
    SUPPORTED_NODE_KINDS,
    SUPPORTED_PARKING_SIDES,
    SUPPORTED_SEGMENT_ROLES,
    RoadMovement,
    RoadSegment,
    RoadSkeleton,
    SkeletonIssue,
    node_by_id,
    segment_by_id,
)
from openparkcad.road_skeleton_geometry import (
    CONTACT_TOLERANCE,
    derive_skeleton_polygons,
    point_on_segment_pavement,
    segment_touches,
)
from openparkcad.site_constraints import site_usable_area

CODE_DUPLICATE_ID = "duplicate_id"
CODE_MISSING_NODE = "missing_node"
CODE_MISSING_SEGMENT = "missing_segment"
CODE_NON_POSITIVE_WIDTH = "non_positive_width"
CODE_NON_FINITE = "non_finite_coordinate"
CODE_EMPTY_CENTERLINE = "empty_centerline"
CODE_COINCIDENT_CENTERLINE = "coincident_centerline"
CODE_UNSUPPORTED_DIRECTIONALITY = "unsupported_directionality"
CODE_UNSUPPORTED_ROLE = "unsupported_role"
CODE_UNSUPPORTED_NODE_KIND = "unsupported_node_kind"
CODE_UNSUPPORTED_MOVEMENT = "unsupported_movement"
CODE_UNSUPPORTED_PARKING_SIDE = "unsupported_parking_side"
CODE_ENTRANCE_PORT_MISMATCH = "entrance_port_mismatch"
CODE_UNDECLARED_INTERSECTION = "undeclared_intersection"
CODE_DECLARED_CONNECTION_WITHOUT_CONTACT = "declared_connection_without_contact"
CODE_ROAD_OUTSIDE_DRIVEABLE = "road_outside_driveable"
CODE_SELF_INTERSECTION = "self_intersection"
CODE_ISOLATED_COMPONENT = "isolated_component"
CODE_ILLEGAL_MOVEMENT = "illegal_movement"

ENTRANCE_MATCH_TOLERANCE = 0.51
DRIVEABLE_AREA_TOLERANCE = 0.05


def _issue(code: str, message: str, object_id: str | None = None, **details: Any) -> SkeletonIssue:
    return SkeletonIssue(code=code, message=message, object_id=object_id, details=details)


def validate_skeleton(skeleton: RoadSkeleton, *, site: SiteSpec | None = None) -> tuple[SkeletonIssue, ...]:
    issues: list[SkeletonIssue] = []
    issues.extend(validate_structure(skeleton, site=site))
    issues.extend(validate_geometry(skeleton, site=site))
    return tuple(issues)


def validate_structure(skeleton: RoadSkeleton, *, site: SiteSpec | None = None) -> tuple[SkeletonIssue, ...]:
    issues: list[SkeletonIssue] = []
    node_ids = [node.id for node in skeleton.nodes]
    segment_ids = [segment.id for segment in skeleton.segments]
    movement_ids = [movement.id for movement in skeleton.movements]
    issues.extend(_duplicate_issues("node", node_ids))
    issues.extend(_duplicate_issues("segment", segment_ids))
    issues.extend(_duplicate_issues("movement", movement_ids))

    nodes = node_by_id(skeleton)
    segments = segment_by_id(skeleton)

    for node in skeleton.nodes:
        if node.kind not in SUPPORTED_NODE_KINDS:
            issues.append(_issue(CODE_UNSUPPORTED_NODE_KIND, f"unsupported node kind {node.kind!r}", node.id))
        if not _finite_point(node.point):
            issues.append(_issue(CODE_NON_FINITE, "node point is not finite", node.id))
        if node.heading_degrees is not None and not math.isfinite(node.heading_degrees):
            issues.append(_issue(CODE_NON_FINITE, "node heading is not finite", node.id))

    for segment in skeleton.segments:
        issues.extend(_segment_structure_issues(segment, nodes))

    for movement in skeleton.movements:
        issues.extend(_movement_structure_issues(movement, nodes, segments))

    if site is not None:
        issues.extend(_entrance_issues(skeleton, site, nodes))
    return tuple(issues)


def validate_geometry(skeleton: RoadSkeleton, *, site: SiteSpec | None = None) -> tuple[SkeletonIssue, ...]:
    issues: list[SkeletonIssue] = []
    polygons = derive_skeleton_polygons(skeleton)
    nodes = node_by_id(skeleton)
    segments = segment_by_id(skeleton)

    for segment in skeleton.segments:
        polygon = polygons[segment.id]
        line = segment.centerline
        if _centerline_self_intersects(line):
            issues.append(_issue(CODE_SELF_INTERSECTION, "centerline self-intersects", segment.id))
        if polygon.is_empty:
            issues.append(_issue(CODE_EMPTY_CENTERLINE, "derived pavement is empty", segment.id))

    issues.extend(_undeclared_intersection_issues(skeleton, polygons))
    issues.extend(_declared_contact_issues(skeleton, nodes, segments))
    issues.extend(_isolated_component_issues(skeleton))

    if site is not None:
        usable = site_usable_area(site, "aisle")
        half_width = max((segment.width for segment in skeleton.segments), default=0.0) / 2.0 + 0.05
        entrance_caps = [
            ShapelyPoint(node.point).buffer(half_width)
            for node in skeleton.nodes
            if node.kind == "entrance_port"
        ]
        gate_allowance = unary_union(entrance_caps) if entrance_caps else GeometryCollection()
        for segment in skeleton.segments:
            pavement = polygons[segment.id]
            leftover = pavement.difference(usable.buffer(DRIVEABLE_AREA_TOLERANCE))
            leftover = leftover.difference(gate_allowance)
            if not leftover.is_empty and leftover.area > 1e-4:
                issues.append(
                    _issue(
                        CODE_ROAD_OUTSIDE_DRIVEABLE,
                        "derived pavement leaves the driveable aisle area",
                        segment.id,
                        leftover_area=float(leftover.area),
                    )
                )
    return tuple(issues)


def _duplicate_issues(kind: str, ids: list[str]) -> list[SkeletonIssue]:
    seen: dict[str, int] = {}
    issues: list[SkeletonIssue] = []
    for item in ids:
        seen[item] = seen.get(item, 0) + 1
    for item, count in seen.items():
        if count > 1:
            issues.append(_issue(CODE_DUPLICATE_ID, f"duplicate {kind} id {item!r}", item, count=count))
    return issues


def _finite_point(point: tuple[float, float]) -> bool:
    return math.isfinite(point[0]) and math.isfinite(point[1])


def _distinct_centerline_points(centerline: tuple[tuple[float, float], ...]) -> list[tuple[float, float]]:
    distinct: list[tuple[float, float]] = []
    for point in centerline:
        if not distinct or math.hypot(point[0] - distinct[-1][0], point[1] - distinct[-1][1]) > 1e-9:
            distinct.append(point)
    return distinct


def _segment_structure_issues(segment: RoadSegment, nodes: Mapping[str, Any]) -> list[SkeletonIssue]:
    issues: list[SkeletonIssue] = []
    if segment.start_node_id not in nodes:
        issues.append(_issue(CODE_MISSING_NODE, f"start node {segment.start_node_id!r} is missing", segment.id))
    if segment.end_node_id not in nodes:
        issues.append(_issue(CODE_MISSING_NODE, f"end node {segment.end_node_id!r} is missing", segment.id))
    if segment.role not in SUPPORTED_SEGMENT_ROLES:
        issues.append(_issue(CODE_UNSUPPORTED_ROLE, f"unsupported role {segment.role!r}", segment.id))
    if segment.directionality not in SUPPORTED_DIRECTIONALITY:
        issues.append(
            _issue(
                CODE_UNSUPPORTED_DIRECTIONALITY,
                f"unsupported directionality {segment.directionality!r}",
                segment.id,
            )
        )
    if not math.isfinite(segment.width) or segment.width <= 0:
        code = CODE_NON_FINITE if not math.isfinite(segment.width) else CODE_NON_POSITIVE_WIDTH
        issues.append(_issue(code, f"width must be finite and positive, got {segment.width!r}", segment.id))
    for side in segment.parking_sides:
        if side not in SUPPORTED_PARKING_SIDES:
            issues.append(_issue(CODE_UNSUPPORTED_PARKING_SIDE, f"unsupported parking side {side!r}", segment.id))
    if any(not _finite_point(point) for point in segment.centerline):
        issues.append(_issue(CODE_NON_FINITE, "centerline contains a non-finite point", segment.id))
    distinct = _distinct_centerline_points(segment.centerline)
    if len(segment.centerline) < 2:
        issues.append(_issue(CODE_EMPTY_CENTERLINE, "centerline needs at least two points", segment.id))
    elif len(distinct) < 2:
        issues.append(_issue(CODE_COINCIDENT_CENTERLINE, "centerline points are coincident", segment.id))
    return issues


def _movement_structure_issues(
    movement: RoadMovement,
    nodes: Mapping[str, Any],
    segments: Mapping[str, RoadSegment],
) -> list[SkeletonIssue]:
    issues: list[SkeletonIssue] = []
    if movement.from_segment_id not in segments:
        issues.append(_issue(CODE_MISSING_SEGMENT, f"from segment {movement.from_segment_id!r} is missing", movement.id))
    if movement.to_segment_id not in segments:
        issues.append(_issue(CODE_MISSING_SEGMENT, f"to segment {movement.to_segment_id!r} is missing", movement.id))
    if movement.via_node_id not in nodes:
        issues.append(_issue(CODE_MISSING_NODE, f"via node {movement.via_node_id!r} is missing", movement.id))
    if movement.movement_kind not in SUPPORTED_MOVEMENT_KINDS:
        issues.append(
            _issue(CODE_UNSUPPORTED_MOVEMENT, f"unsupported movement kind {movement.movement_kind!r}", movement.id)
        )
    if type(movement.allowed) is not bool:
        issues.append(_issue(CODE_ILLEGAL_MOVEMENT, "allowed must be a boolean, not an integer", movement.id))
    return issues


def _entrance_issues(skeleton: RoadSkeleton, site: SiteSpec, nodes: Mapping[str, Any]) -> list[SkeletonIssue]:
    issues: list[SkeletonIssue] = []
    entrances = {entrance.id: entrance for entrance in site.entrances}
    for entrance_id in skeleton.entrance_ids:
        if entrance_id not in entrances:
            issues.append(
                _issue(CODE_ENTRANCE_PORT_MISMATCH, f"entrance_id {entrance_id!r} is not on the site", entrance_id)
            )
    for node in skeleton.nodes:
        if node.kind != "entrance_port":
            continue
        source_id = node.source_id or (node.id if node.id in entrances else None)
        if source_id not in entrances:
            issues.append(
                _issue(CODE_ENTRANCE_PORT_MISMATCH, "entrance_port is not a real SiteSpec entrance", node.id)
            )
            continue
        entrance = entrances[source_id]
        distance = math.hypot(node.point[0] - entrance.center[0], node.point[1] - entrance.center[1])
        if distance > ENTRANCE_MATCH_TOLERANCE:
            issues.append(
                _issue(
                    CODE_ENTRANCE_PORT_MISMATCH,
                    "entrance_port does not match the site entrance location",
                    node.id,
                    distance=distance,
                )
            )
    return issues


def _centerline_self_intersects(centerline: tuple[tuple[float, float], ...]) -> bool:
    if len(centerline) < 4:
        return False
    from openparkcad.geometry import segments_intersect

    edges = list(zip(centerline, centerline[1:]))
    for index, (a, b) in enumerate(edges):
        for other_a, other_b in edges[index + 2 :]:
            if a == other_b or b == other_a:
                continue
            if segments_intersect(a, b, other_a, other_b):
                return True
    return False


def _share_node(left: RoadSegment, right: RoadSegment) -> bool:
    return bool({left.start_node_id, left.end_node_id} & {right.start_node_id, right.end_node_id})


def _undeclared_intersection_issues(
    skeleton: RoadSkeleton,
    polygons: Mapping[str, Any],
) -> list[SkeletonIssue]:
    issues: list[SkeletonIssue] = []
    segments = list(skeleton.segments)
    for index, left in enumerate(segments):
        for right in segments[index + 1 :]:
            if _share_node(left, right):
                continue
            overlap = polygons[left.id].intersection(polygons[right.id])
            if overlap.is_empty or overlap.area <= 1e-4:
                continue
            issues.append(
                _issue(
                    CODE_UNDECLARED_INTERSECTION,
                    "roads overlap without a shared node or declared connection",
                    left.id,
                    other_id=right.id,
                    overlap_area=float(overlap.area),
                )
            )
    return issues


def _declared_contact_issues(
    skeleton: RoadSkeleton,
    nodes: Mapping[str, Any],
    segments: Mapping[str, RoadSegment],
) -> list[SkeletonIssue]:
    issues: list[SkeletonIssue] = []
    for movement in skeleton.movements:
        if not movement.allowed:
            continue
        if movement.from_segment_id not in segments or movement.to_segment_id not in segments:
            continue
        if movement.via_node_id not in nodes:
            continue
        left = segments[movement.from_segment_id]
        right = segments[movement.to_segment_id]
        via = nodes[movement.via_node_id].point
        contact = segment_touches(left, right) and point_on_segment_pavement(left, via) and point_on_segment_pavement(
            right, via
        )
        if not contact:
            issues.append(
                _issue(
                    CODE_DECLARED_CONNECTION_WITHOUT_CONTACT,
                    "declared movement has no geometric contact at via_node",
                    movement.id,
                    via_node_id=movement.via_node_id,
                    tolerance=CONTACT_TOLERANCE,
                )
            )
    return issues


def _isolated_component_issues(skeleton: RoadSkeleton) -> list[SkeletonIssue]:
    if not skeleton.entrance_ids or not skeleton.segments:
        return []
    adjacency: dict[str, set[str]] = defaultdict(set)
    for segment in skeleton.segments:
        adjacency[segment.start_node_id].add(segment.end_node_id)
        adjacency[segment.end_node_id].add(segment.start_node_id)
    entrance_nodes = {
        node.id
        for node in skeleton.nodes
        if node.kind == "entrance_port" or node.source_id in skeleton.entrance_ids or node.id in skeleton.entrance_ids
    }
    if not entrance_nodes:
        return [_issue(CODE_ISOLATED_COMPONENT, "no entrance_port is connected to declared entrance_ids")]
    seen: set[str] = set()
    queue = deque(entrance_nodes)
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(adjacency[current] - seen)
    issues: list[SkeletonIssue] = []
    for segment in skeleton.segments:
        if segment.start_node_id not in seen and segment.end_node_id not in seen:
            issues.append(_issue(CODE_ISOLATED_COMPONENT, "segment is not connected to an entrance", segment.id))
    return issues
