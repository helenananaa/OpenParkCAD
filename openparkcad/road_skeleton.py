"""Immutable road-skeleton identity and canonical payload (v0.5 N2).

IDs are SHA-256 of a canonical JSON payload. They do not use Python ``hash()``.
Generation parameters belong in ``source`` provenance and do not replace geometry.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

ROAD_SKELETON_VERSION = "road-skeleton-1"
SUPPORTED_NODE_KINDS = frozenset({"entrance_port", "junction", "terminal", "turnaround"})
SUPPORTED_SEGMENT_ROLES = frozenset(
    {
        "spine",
        "parking_aisle",
        "cross_aisle",
        "exit",
        "turnaround",
        "jog",
        "branch",
        "connector",
        "passing_bay",
        "main",
    }
)
SUPPORTED_DIRECTIONALITY = frozenset({"two_way", "one_way"})
SUPPORTED_MOVEMENT_KINDS = frozenset(
    {"straight", "left", "right", "u_turn", "enter", "exit", "merge", "diverge"}
)
SUPPORTED_PARKING_SIDES = frozenset({"left", "right", "both", "none"})
FLOAT_FORMAT = ".9f"


@dataclass(frozen=True)
class RoadNode:
    id: str
    kind: str
    point: tuple[float, float]
    heading_degrees: float | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class RoadSegment:
    id: str
    role: str
    start_node_id: str
    end_node_id: str
    centerline: tuple[tuple[float, float], ...]
    width: float
    directionality: str
    parking_sides: tuple[str, ...] = ()
    source: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class RoadMovement:
    id: str
    from_segment_id: str
    to_segment_id: str
    via_node_id: str
    movement_kind: str
    allowed: bool = True


@dataclass(frozen=True)
class SkeletonIssue:
    code: str
    message: str
    object_id: str | None = None
    details: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class RoadSkeleton:
    version: str
    skeleton_id: str
    family: str
    nodes: tuple[RoadNode, ...]
    segments: tuple[RoadSegment, ...]
    movements: tuple[RoadMovement, ...]
    entrance_ids: tuple[str, ...]
    source: Mapping[str, Any] = MappingProxyType({})
    payload_digest: str = ""


class SkeletonValidationError(ValueError):
    def __init__(self, issues: tuple[SkeletonIssue, ...]):
        self.issues = issues
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in issues) or "invalid skeleton"
        super().__init__(summary)


def _to_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_plain(item) for item in value]
    return value


def freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    return MappingProxyType({str(key): _freeze_value(item) for key, item in _to_plain(value).items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in _to_plain(value).items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def canon_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite float: {value!r}")
    return float(format(number, FLOAT_FORMAT))


def canon_point(value: Any) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError(f"point must be [x, y], got {value!r}")
    return (canon_float(value[0]), canon_float(value[1]))


def canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return canon_float(value)
    if isinstance(value, Mapping):
        return {str(key): canonicalize(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list | tuple):
        return [canonicalize(item) for item in value]
    return str(value)


def canonical_dumps(value: Any) -> str:
    return json.dumps(canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


def identity_payload(
    *,
    family: str,
    nodes: tuple[RoadNode, ...],
    segments: tuple[RoadSegment, ...],
    movements: tuple[RoadMovement, ...],
    entrance_ids: tuple[str, ...],
    version: str = ROAD_SKELETON_VERSION,
) -> dict[str, Any]:
    return {
        "version": version,
        "family": family,
        "entrance_ids": list(entrance_ids),
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "point": list(node.point),
                "heading_degrees": node.heading_degrees,
                "source_id": node.source_id,
            }
            for node in sorted(nodes, key=lambda item: item.id)
        ],
        "segments": [
            {
                "id": segment.id,
                "role": segment.role,
                "start_node_id": segment.start_node_id,
                "end_node_id": segment.end_node_id,
                "centerline": [list(point) for point in segment.centerline],
                "width": segment.width,
                "directionality": segment.directionality,
                "parking_sides": list(segment.parking_sides),
            }
            for segment in sorted(segments, key=lambda item: item.id)
        ],
        "movements": [
            {
                "id": movement.id,
                "from_segment_id": movement.from_segment_id,
                "to_segment_id": movement.to_segment_id,
                "via_node_id": movement.via_node_id,
                "movement_kind": movement.movement_kind,
                "allowed": movement.allowed,
            }
            for movement in sorted(movements, key=lambda item: item.id)
        ],
    }


def skeleton_id_for_payload(payload: Mapping[str, Any], *, version: str = ROAD_SKELETON_VERSION) -> str:
    digest = stable_digest(payload)
    return f"{version}:{digest}"


def make_node(
    id: str,
    kind: str,
    point: tuple[float, float] | list[float],
    *,
    heading_degrees: float | None = None,
    source_id: str | None = None,
) -> RoadNode:
    heading = None if heading_degrees is None else canon_float(heading_degrees)
    return RoadNode(
        id=str(id),
        kind=str(kind),
        point=canon_point(point),
        heading_degrees=heading,
        source_id=None if source_id is None else str(source_id),
    )


def make_segment(
    id: str,
    role: str,
    start_node_id: str,
    end_node_id: str,
    centerline: tuple[tuple[float, float], ...] | list[tuple[float, float]],
    width: float,
    directionality: str,
    *,
    parking_sides: tuple[str, ...] | list[str] = (),
    source: Mapping[str, Any] | None = None,
) -> RoadSegment:
    points = tuple(canon_point(point) for point in centerline)
    sides = tuple(str(side) for side in parking_sides)
    return RoadSegment(
        id=str(id),
        role=str(role),
        start_node_id=str(start_node_id),
        end_node_id=str(end_node_id),
        centerline=points,
        width=canon_float(width),
        directionality=str(directionality),
        parking_sides=sides,
        source=freeze_mapping(source),
    )


def make_movement(
    id: str,
    from_segment_id: str,
    to_segment_id: str,
    via_node_id: str,
    movement_kind: str,
    *,
    allowed: bool = True,
) -> RoadMovement:
    if type(allowed) is not bool:
        raise ValueError("movement.allowed must be a boolean")
    return RoadMovement(
        id=str(id),
        from_segment_id=str(from_segment_id),
        to_segment_id=str(to_segment_id),
        via_node_id=str(via_node_id),
        movement_kind=str(movement_kind),
        allowed=allowed,
    )


def make_skeleton(
    *,
    family: str,
    nodes: tuple[RoadNode, ...] | list[RoadNode],
    segments: tuple[RoadSegment, ...] | list[RoadSegment],
    movements: tuple[RoadMovement, ...] | list[RoadMovement] = (),
    entrance_ids: tuple[str, ...] | list[str] = (),
    source: Mapping[str, Any] | None = None,
    site: Any = None,
    version: str = ROAD_SKELETON_VERSION,
    strict: bool = True,
) -> RoadSkeleton:
    node_tuple = tuple(nodes)
    segment_tuple = tuple(segments)
    movement_tuple = tuple(movements)
    entrance_tuple = tuple(str(item) for item in entrance_ids)
    payload = identity_payload(
        family=str(family),
        nodes=node_tuple,
        segments=segment_tuple,
        movements=movement_tuple,
        entrance_ids=entrance_tuple,
        version=version,
    )
    digest = stable_digest(payload)
    skeleton = RoadSkeleton(
        version=version,
        skeleton_id=skeleton_id_for_payload(payload, version=version),
        family=str(family),
        nodes=node_tuple,
        segments=segment_tuple,
        movements=movement_tuple,
        entrance_ids=entrance_tuple,
        source=freeze_mapping(source),
        payload_digest=digest,
    )
    if strict:
        from openparkcad.road_skeleton_validation import validate_skeleton

        issues = validate_skeleton(skeleton, site=site)
        if issues:
            raise SkeletonValidationError(issues)
    return skeleton


def copy_skeleton(skeleton: RoadSkeleton) -> RoadSkeleton:
    return RoadSkeleton(
        version=skeleton.version,
        skeleton_id=skeleton.skeleton_id,
        family=skeleton.family,
        nodes=tuple(
            RoadNode(
                id=node.id,
                kind=node.kind,
                point=tuple(node.point),
                heading_degrees=node.heading_degrees,
                source_id=node.source_id,
            )
            for node in skeleton.nodes
        ),
        segments=tuple(
            RoadSegment(
                id=segment.id,
                role=segment.role,
                start_node_id=segment.start_node_id,
                end_node_id=segment.end_node_id,
                centerline=tuple(tuple(point) for point in segment.centerline),
                width=segment.width,
                directionality=segment.directionality,
                parking_sides=tuple(segment.parking_sides),
                source=freeze_mapping(segment.source),
            )
            for segment in skeleton.segments
        ),
        movements=tuple(skeleton.movements),
        entrance_ids=tuple(skeleton.entrance_ids),
        source=freeze_mapping(skeleton.source),
        payload_digest=skeleton.payload_digest,
    )


def node_by_id(skeleton: RoadSkeleton) -> dict[str, RoadNode]:
    return {node.id: node for node in skeleton.nodes}


def segment_by_id(skeleton: RoadSkeleton) -> dict[str, RoadSegment]:
    return {segment.id: segment for segment in skeleton.segments}


def allowed_movements(skeleton: RoadSkeleton, from_segment_id: str, to_segment_id: str) -> tuple[RoadMovement, ...]:
    return tuple(
        movement
        for movement in skeleton.movements
        if movement.allowed
        and movement.from_segment_id == from_segment_id
        and movement.to_segment_id == to_segment_id
    )


def turn_allowed(skeleton: RoadSkeleton, from_segment_id: str, to_segment_id: str) -> bool:
    return bool(allowed_movements(skeleton, from_segment_id, to_segment_id))
