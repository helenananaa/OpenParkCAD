from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, Point, SiteSpec
from openparkcad.phase1_support import aisle_directionality, one_way_allows_reverse_egress


_AISLE_CONNECTION_TOLERANCE = 1e-7


@dataclass(frozen=True)
class TrafficNode:
    id: str
    kind: str
    point: Point | None = None
    ref_id: str | None = None


@dataclass(frozen=True)
class TrafficEdge:
    id: str
    from_node_id: str
    to_node_id: str
    directionality: str
    role: str
    aisle_id: str | None = None
    entrance_id: str | None = None


@dataclass(frozen=True)
class StallAccess:
    stall_id: str
    aisle_id: str | None
    aisle_node_id: str | None
    access_side: str | None


@dataclass(frozen=True)
class TrafficGraph:
    nodes: list[TrafficNode] = field(default_factory=list)
    edges: list[TrafficEdge] = field(default_factory=list)
    stall_access: list[StallAccess] = field(default_factory=list)


def build_traffic_graph(layout: LayoutResult) -> TrafficGraph:
    """Build the first Phase 2 graph from declared, geometrically real connections."""
    nodes: list[TrafficNode] = []
    edges: list[TrafficEdge] = []
    aisles_by_id = {aisle.id: aisle for aisle in layout.aisles}
    entrances_by_id = {entrance.id: entrance for entrance in layout.site.entrances}

    for entrance in layout.site.entrances:
        nodes.append(
            TrafficNode(
                id=_entrance_node_id(entrance.id),
                kind="entrance",
                point=entrance.center,
                ref_id=entrance.id,
            )
        )

    circulation_aisles = [aisle for aisle in layout.aisles if not _is_non_circulation_aisle(aisle)]
    for aisle in circulation_aisles:
        nodes.append(
            TrafficNode(
                id=_aisle_node_id(aisle.id),
                kind=_node_kind_for_aisle(aisle),
                point=_centroid(aisle.polygon),
                ref_id=aisle.id,
            )
        )

    for aisle in circulation_aisles:
        aisle_node_id = _aisle_node_id(aisle.id)
        aisle_dir = _aisle_directionality(aisle, layout.site)
        if aisle.connected_to_entrance_id:
            entrance = entrances_by_id.get(aisle.connected_to_entrance_id)
            if entrance is None or _entrance_touches_aisle(layout.site, entrance, aisle):
                edges.extend(_entrance_edges(layout.site, aisle, aisle_node_id, aisle_dir))
        if aisle.parent_aisle_id:
            parent = aisles_by_id.get(aisle.parent_aisle_id)
            if parent is None or _aisles_touch(aisle, parent):
                edges.extend(
                    _directed_aisle_edges(
                        edge_id_prefix=f"E-PARENT-{aisle.parent_aisle_id}-{aisle.id}",
                        from_node_id=_aisle_node_id(aisle.parent_aisle_id),
                        to_node_id=aisle_node_id,
                        directionality=aisle_dir,
                        role="aisle_connection",
                        aisle_id=aisle.id,
                        allow_reverse=one_way_allows_reverse_egress(layout.site),
                    )
                )
        for connected_aisle_id in aisle.connected_aisle_ids:
            connected = aisles_by_id.get(connected_aisle_id)
            if connected is None or _aisles_touch(aisle, connected):
                edges.extend(
                    _directed_aisle_edges(
                        edge_id_prefix=f"E-CONNECTOR-{aisle.id}-{connected_aisle_id}",
                        from_node_id=aisle_node_id,
                        to_node_id=_aisle_node_id(connected_aisle_id),
                        directionality=aisle_dir,
                        role="aisle_connection",
                        aisle_id=aisle.id,
                        allow_reverse=one_way_allows_reverse_egress(layout.site),
                    )
                )

    aisle_ids = {aisle.id for aisle in layout.aisles}
    stall_access = [
        StallAccess(
            stall_id=stall.id,
            aisle_id=stall.served_by_aisle_id,
            aisle_node_id=_aisle_node_id(stall.served_by_aisle_id) if stall.served_by_aisle_id in aisle_ids else None,
            access_side=stall.aisle_side,
        )
        for stall in layout.stalls
    ]

    return TrafficGraph(nodes=nodes, edges=edges, stall_access=stall_access)


def validate_traffic_graph(graph: TrafficGraph, layout: LayoutResult) -> dict[str, Any]:
    node_ids = {node.id for node in graph.nodes}
    aisle_ids = {aisle.id for aisle in layout.aisles}
    circulation_aisle_ids = {
        aisle.id for aisle in layout.aisles if not _is_non_circulation_aisle(aisle)
    }
    entrance_ids = {entrance.id for entrance in layout.site.entrances}
    entry_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_entry(entrance)}
    exit_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_exit(entrance)}

    missing_edge_nodes = [
        {"edge_id": edge.id, "missing_node_id": node_id}
        for edge in graph.edges
        for node_id in (edge.from_node_id, edge.to_node_id)
        if node_id not in node_ids
    ]
    missing_entrances = [
        edge.entrance_id
        for edge in graph.edges
        if edge.entrance_id and edge.entrance_id not in entrance_ids
    ]
    missing_aisles = [
        access.stall_id
        for access in graph.stall_access
        if not access.aisle_id or access.aisle_id not in aisle_ids
    ]
    disconnected_aisle_connections = _disconnected_aisle_connections(layout)
    disconnected_entrance_connections = _disconnected_entrance_connections(layout)

    reachable_from_entries = _reachable_nodes(graph, entry_nodes, reverse=False)
    can_reach_exits = _reachable_nodes(graph, exit_nodes, reverse=True)

    reachable_aisles = sorted(
        aisle.id
        for aisle in layout.aisles
        if not _is_non_circulation_aisle(aisle) and _aisle_node_id(aisle.id) in reachable_from_entries
    )
    unreachable_aisles = sorted(circulation_aisle_ids - set(reachable_aisles))
    unreachable_stalls = sorted(
        access.stall_id
        for access in graph.stall_access
        if access.aisle_node_id not in reachable_from_entries
    )
    no_exit_stalls = sorted(
        access.stall_id
        for access in graph.stall_access
        if access.aisle_node_id not in can_reach_exits
    )

    dead_ends = _dead_ends(graph, layout)
    errors = []
    if missing_edge_nodes:
        errors.append("edge_references_missing_node")
    if missing_entrances:
        errors.append("aisle_references_missing_entrance")
    if missing_aisles:
        errors.append("stall_references_missing_aisle")
    if disconnected_aisle_connections:
        errors.append("aisle_connections_do_not_touch")
    if disconnected_entrance_connections:
        errors.append("entrance_connections_do_not_touch")
    if unreachable_aisles:
        errors.append("unreachable_aisles")
    if unreachable_stalls:
        errors.append("unreachable_stalls")
    if layout.stalls and no_exit_stalls:
        errors.append("stalls_without_exit_path")
    if any(item["status"] == "dead_end_without_turnaround" for item in dead_ends):
        errors.append("dead_end_without_turnaround")

    layout_dir = _layout_aisle_directionality(layout.site)
    reverse_egress = one_way_allows_reverse_egress(layout.site) if layout_dir == "one_way" else None
    return {
        "valid": not errors,
        "errors": errors,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "stall_access_count": len(graph.stall_access),
        "entry_nodes": sorted(entry_nodes),
        "exit_nodes": sorted(exit_nodes),
        "reachable_aisles": reachable_aisles,
        "unreachable_aisles": unreachable_aisles,
        "unreachable_stalls": unreachable_stalls,
        "stalls_without_exit_path": no_exit_stalls,
        "missing_edge_nodes": missing_edge_nodes,
        "missing_entrances": sorted(set(missing_entrances)),
        "stalls_missing_aisles": sorted(missing_aisles),
        "disconnected_aisle_connections": disconnected_aisle_connections,
        "disconnected_entrance_connections": disconnected_entrance_connections,
        "dead_ends": dead_ends,
        "aisle_directionality": layout_dir,
        "one_way_allows_reverse_egress": reverse_egress,
        "one_way_edge_count": sum(1 for edge in graph.edges if edge.directionality == "one_way"),
        "reverse_egress_edge_count": sum(1 for edge in graph.edges if edge.role == "reverse_egress"),
    }


def traffic_graph_report(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    return {
        "nodes": [asdict(node) for node in graph.nodes],
        "edges": [asdict(edge) for edge in graph.edges],
        "stall_access": [asdict(access) for access in graph.stall_access],
        "validation": validation,
    }


def traffic_graph_summary(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    return {
        "node_count": validation["node_count"],
        "edge_count": validation["edge_count"],
        "stall_access_count": validation["stall_access_count"],
        "valid": validation["valid"],
        "errors": validation["errors"],
        "reachable_aisles": validation["reachable_aisles"],
        "unreachable_aisles": validation["unreachable_aisles"],
        "unreachable_stalls": validation["unreachable_stalls"],
        "stalls_without_exit_path": validation["stalls_without_exit_path"],
        "disconnected_aisle_connections": validation["disconnected_aisle_connections"],
        "disconnected_entrance_connections": validation["disconnected_entrance_connections"],
        "dead_ends": validation["dead_ends"],
    }


def _entrance_edges(
    site: SiteSpec,
    aisle: ParkingAisle,
    aisle_node_id: str,
    aisle_dir: str,
) -> list[TrafficEdge]:
    entrance = next((item for item in site.entrances if item.id == aisle.connected_to_entrance_id), None)
    entrance_node = _entrance_node_id(aisle.connected_to_entrance_id or "")
    base_id = f"E-ENTRANCE-{aisle.connected_to_entrance_id}-{aisle.id}"

    if entrance and _allows_entry(entrance) and not _allows_exit(entrance):
        return [
            TrafficEdge(
                id=base_id,
                from_node_id=entrance_node,
                to_node_id=aisle_node_id,
                directionality="one_way",
                role="entrance_connection",
                aisle_id=aisle.id,
                entrance_id=aisle.connected_to_entrance_id,
            )
        ]
    if entrance and _allows_exit(entrance) and not _allows_entry(entrance):
        return [
            TrafficEdge(
                id=base_id,
                from_node_id=aisle_node_id,
                to_node_id=entrance_node,
                directionality="one_way",
                role="entrance_connection",
                aisle_id=aisle.id,
                entrance_id=aisle.connected_to_entrance_id,
            )
        ]

    # Shared entrance: two-way, or one-way enter with optional reverse egress.
    if aisle_dir == "one_way":
        edges = [
            TrafficEdge(
                id=f"{base_id}-ENTER",
                from_node_id=entrance_node,
                to_node_id=aisle_node_id,
                directionality="one_way",
                role="entrance_connection",
                aisle_id=aisle.id,
                entrance_id=aisle.connected_to_entrance_id,
            )
        ]
        if one_way_allows_reverse_egress(site):
            edges.append(
                TrafficEdge(
                    id=f"{base_id}-EGRESS",
                    from_node_id=aisle_node_id,
                    to_node_id=entrance_node,
                    directionality="one_way",
                    role="reverse_egress",
                    aisle_id=aisle.id,
                    entrance_id=aisle.connected_to_entrance_id,
                )
            )
        return edges

    return [
        TrafficEdge(
            id=base_id,
            from_node_id=entrance_node,
            to_node_id=aisle_node_id,
            directionality="two_way",
            role="entrance_connection",
            aisle_id=aisle.id,
            entrance_id=aisle.connected_to_entrance_id,
        )
    ]


def _directed_aisle_edges(
    *,
    edge_id_prefix: str,
    from_node_id: str,
    to_node_id: str,
    directionality: str,
    role: str,
    aisle_id: str,
    allow_reverse: bool,
) -> list[TrafficEdge]:
    """Emit forward aisle edges, plus explicit reverse-egress arcs for one-way modules."""
    if directionality != "one_way":
        return [
            TrafficEdge(
                id=edge_id_prefix,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                directionality="two_way",
                role=role,
                aisle_id=aisle_id,
            )
        ]
    edges = [
        TrafficEdge(
            id=edge_id_prefix,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            directionality="one_way",
            role=role,
            aisle_id=aisle_id,
        )
    ]
    if allow_reverse:
        edges.append(
            TrafficEdge(
                id=f"{edge_id_prefix}-EGRESS",
                from_node_id=to_node_id,
                to_node_id=from_node_id,
                directionality="one_way",
                role="reverse_egress",
                aisle_id=aisle_id,
            )
        )
    return edges


def _reachable_nodes(graph: TrafficGraph, starts: set[str], reverse: bool) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in graph.edges:
        _add_arc(adjacency, edge.from_node_id, edge.to_node_id)
        if edge.directionality == "two_way":
            _add_arc(adjacency, edge.to_node_id, edge.from_node_id)

    if reverse:
        reversed_adjacency: dict[str, set[str]] = {}
        for source, targets in adjacency.items():
            for target in targets:
                _add_arc(reversed_adjacency, target, source)
        adjacency = reversed_adjacency

    seen: set[str] = set()
    stack = list(starts)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(sorted(adjacency.get(node, set()) - seen))
    return seen


def _dead_ends(graph: TrafficGraph, layout: LayoutResult) -> list[dict[str, Any]]:
    undirected_degree: dict[str, int] = {node.id: 0 for node in graph.nodes}
    for edge in graph.edges:
        undirected_degree[edge.from_node_id] = undirected_degree.get(edge.from_node_id, 0) + 1
        undirected_degree[edge.to_node_id] = undirected_degree.get(edge.to_node_id, 0) + 1

    items = []
    for aisle in layout.aisles:
        if _is_non_circulation_aisle(aisle):
            continue
        node_id = _aisle_node_id(aisle.id)
        if aisle.role == "turnaround":
            items.append(
                {
                    "aisle_id": aisle.id,
                    "node_id": node_id,
                    "parent_aisle_id": aisle.parent_aisle_id,
                    "turnaround_present": True,
                    "status": "allowed_with_turnaround",
                }
            )
        elif undirected_degree.get(node_id, 0) <= 1:
            dead_end_allowed = _allows_dead_end_aisles(layout.site)
            items.append(
                {
                    "aisle_id": aisle.id,
                    "node_id": node_id,
                    "parent_aisle_id": aisle.parent_aisle_id,
                    "turnaround_present": False,
                    "status": "allowed_dead_end" if dead_end_allowed else "dead_end_without_turnaround",
                }
            )
    return items


def _disconnected_aisle_connections(layout: LayoutResult) -> list[dict[str, Any]]:
    aisles_by_id = {aisle.id: aisle for aisle in layout.aisles}
    disconnected: list[dict[str, Any]] = []
    for aisle in layout.aisles:
        if _is_non_circulation_aisle(aisle):
            continue
        declarations = []
        if aisle.parent_aisle_id:
            declarations.append(("parent", aisle.parent_aisle_id))
        declarations.extend(("connected", aisle_id) for aisle_id in aisle.connected_aisle_ids)
        for relationship, related_aisle_id in declarations:
            related = aisles_by_id.get(related_aisle_id)
            if related is None or _aisles_touch(aisle, related):
                continue
            disconnected.append(
                {
                    "aisle_id": aisle.id,
                    "related_aisle_id": related_aisle_id,
                    "relationship": relationship,
                    "distance": _aisle_distance(aisle, related),
                }
            )
    return disconnected


def _disconnected_entrance_connections(layout: LayoutResult) -> list[dict[str, Any]]:
    entrances_by_id = {entrance.id: entrance for entrance in layout.site.entrances}
    disconnected: list[dict[str, Any]] = []
    for aisle in layout.aisles:
        if not aisle.connected_to_entrance_id:
            continue
        entrance = entrances_by_id.get(aisle.connected_to_entrance_id)
        if entrance is None or _entrance_touches_aisle(layout.site, entrance, aisle):
            continue
        disconnected.append(
            {
                "aisle_id": aisle.id,
                "entrance_id": entrance.id,
                "distance": _entrance_aisle_distance(entrance, aisle),
                "allowed_distance": _entrance_connection_tolerance(layout.site),
            }
        )
    return disconnected


def _aisles_touch(left: ParkingAisle, right: ParkingAisle) -> bool:
    distance = _aisle_distance(left, right)
    return distance is not None and distance <= _AISLE_CONNECTION_TOLERANCE


def _aisle_distance(left: ParkingAisle, right: ParkingAisle) -> float | None:
    try:
        left_geometry = ShapelyPolygon(left.polygon)
        right_geometry = ShapelyPolygon(right.polygon)
    except (TypeError, ValueError):
        return None
    if left_geometry.is_empty or right_geometry.is_empty:
        return None
    return float(left_geometry.distance(right_geometry))


def _entrance_touches_aisle(site: SiteSpec, entrance: EntranceSpec, aisle: ParkingAisle) -> bool:
    distance = _entrance_aisle_distance(entrance, aisle)
    return distance is not None and distance <= _entrance_connection_tolerance(site)


def _entrance_aisle_distance(entrance: EntranceSpec, aisle: ParkingAisle) -> float | None:
    try:
        aisle_geometry = ShapelyPolygon(aisle.polygon)
    except (TypeError, ValueError):
        return None
    if aisle_geometry.is_empty:
        return None
    normal = math.radians(float(entrance.heading_degrees) + 90.0)
    half_width = max(float(entrance.width), 0.0) / 2.0
    dx = math.cos(normal) * half_width
    dy = math.sin(normal) * half_width
    if half_width <= _AISLE_CONNECTION_TOLERANCE:
        entrance_geometry = ShapelyPoint(float(entrance.center[0]), float(entrance.center[1]))
    else:
        entrance_geometry = LineString(
            [
                (float(entrance.center[0]) - dx, float(entrance.center[1]) - dy),
                (float(entrance.center[0]) + dx, float(entrance.center[1]) + dy),
            ]
        )
    return float(entrance_geometry.distance(aisle_geometry))


def _entrance_connection_tolerance(site: SiteSpec) -> float:
    return max(float(site.margin), 0.0) + _AISLE_CONNECTION_TOLERANCE


def _allows_dead_end_aisles(site: SiteSpec) -> bool:
    circulation = site.constraints.get("circulation", {})
    if not isinstance(circulation, dict):
        return False
    return circulation.get("allow_dead_end_aisles") is True


def _layout_aisle_directionality(site: SiteSpec) -> str:
    return aisle_directionality(site)


def _aisle_directionality(aisle: ParkingAisle, site: SiteSpec) -> str:
    if aisle.directionality in {"one_way", "two_way"}:
        return aisle.directionality
    return _layout_aisle_directionality(site)


def _is_non_circulation_aisle(aisle: ParkingAisle) -> bool:
    """Shoulders / widenings that are drawn but not independent graph nodes."""
    return aisle.role in {"passing_bay"}


def _node_kind_for_aisle(aisle: ParkingAisle) -> str:
    if aisle.role == "turnaround":
        return "turnaround"
    if aisle.role == "branch":
        return "branch_aisle"
    if aisle.role == "connector":
        return "connector_aisle"
    if aisle.role == "exit":
        return "exit_aisle"
    if aisle.role == "jog":
        return "jog_aisle"
    if aisle.role == "main":
        return "main_aisle"
    if aisle.role == "passing_bay":
        return "passing_bay"
    return "aisle"


def _centroid(poly) -> Point:
    return (
        sum(point[0] for point in poly) / len(poly),
        sum(point[1] for point in poly) / len(poly),
    )


def _add_arc(adjacency: dict[str, set[str]], source: str, target: str) -> None:
    adjacency.setdefault(source, set()).add(target)


def _entrance_node_id(entrance_id: str) -> str:
    return f"N-ENTRANCE-{entrance_id}"


def _aisle_node_id(aisle_id: str) -> str:
    return f"N-AISLE-{aisle_id}"


def _allows_entry(entrance) -> bool:
    if entrance.mode == "shared":
        return True
    if entrance.mode == "entry_only":
        return True
    if entrance.mode == "exit_only":
        return False
    return "enter" in entrance.allowed_movements


def _allows_exit(entrance) -> bool:
    if entrance.mode == "shared":
        return True
    if entrance.mode == "entry_only":
        return False
    if entrance.mode == "exit_only":
        return True
    return "exit" in entrance.allowed_movements
