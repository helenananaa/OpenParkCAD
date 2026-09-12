"""Serializable project revisions, stable object ids, and accepted layouts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from openparkcad.layout_locks import LayoutLock, parse_lock
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, site_from_dict

PROJECT_VERSION = "openparkcad-project-1"


@dataclass
class ProjectRevision:
    revision: int
    site: dict[str, Any]
    locks: list[dict[str, Any]]
    input_digest: str
    accepted_layout_ref: str | None = None


@dataclass
class ProjectState:
    version: str = PROJECT_VERSION
    name: str = "project"
    revisions: list[ProjectRevision] = field(default_factory=list)
    current_revision: int = 0
    accepted_site: dict[str, Any] | None = None
    accepted_layout: dict[str, Any] | None = None
    object_ids: dict[str, str] = field(default_factory=dict)
    object_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    redo_stack: list[dict[str, Any]] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "current_revision": self.current_revision,
            "revisions": [
                {
                    "revision": item.revision,
                    "site": item.site,
                    "locks": item.locks,
                    "input_digest": item.input_digest,
                    "accepted_layout_ref": item.accepted_layout_ref,
                }
                for item in self.revisions
            ],
            "accepted_site": self.accepted_site,
            "accepted_layout": self.accepted_layout,
            "object_ids": dict(self.object_ids),
            "object_sources": deepcopy(self.object_sources),
            "history": list(self.history),
        }


def input_digest(site: dict[str, Any], locks: list[dict[str, Any]]) -> str:
    payload = json.dumps({"site": site, "locks": locks}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def site_dict_from_layout(layout: LayoutResult) -> dict[str, Any]:
    """Serialize enforced site inputs, including scoped areas and all stall types."""
    site = layout.site
    obstacles = [_area_record(item) for item in site.obstacle_specs] if site.obstacle_specs else [
        {"id": f"obstacle-{index}", "geometry": {"type": "polygon", "points": polygon}}
        for index, polygon in enumerate(site.obstacles, start=1)
    ]
    payload = {
        "version": site.version, "name": site.name, "units": site.units,
        "standards": site.standards,
        "site": {
            "boundary": {"type": "polygon", "points": site.boundary},
            "obstacles": obstacles,
            "reserved_areas": [_area_record(item) for item in site.reserved_areas],
        },
        "entrances": [asdict(item) for item in site.entrances],
        "parking": {
            "stall_types": [asdict(item) for item in site.stall_candidates or (site.stall,)],
            "quotas": site.parking_quotas,
        },
        "aisles": {
            "selection_mode": site.aisle_selection_mode,
            "fixed_class": site.fixed_aisle_class,
            "classes": [asdict(item) for item in site.aisle_classes],
        },
        "vehicles": {"design_vehicle": _without_none(asdict(site.vehicle))} if site.vehicle else {},
        "site_features": site.site_features,
        "pedestrian_and_emergency": site.pedestrian_and_emergency,
        "constraints": site.constraints,
        "optimization": site.optimization,
        "metadata": site.metadata,
        "diagnostics": site.diagnostics,
    }
    if payload["aisles"]["fixed_class"] is None:
        payload["aisles"].pop("fixed_class")
    # Normalize tuples as JSON arrays and detach revisions from mutable inputs.
    return json.loads(json.dumps(payload))


def _without_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _without_none(item) if isinstance(item, dict) else item
            for key, item in value.items() if item is not None}


def _area_record(area) -> dict[str, Any]:
    record = asdict(area)
    record["type"] = record.pop("kind")
    return record


def parse_project(raw: dict[str, Any]) -> ProjectState:
    version = str(raw.get("version") or "")
    if version and version != PROJECT_VERSION:
        raise ValueError(f"unsupported project version {version!r}")
    state = ProjectState(name=str(raw.get("name") or "project"), current_revision=int(raw.get("current_revision") or 0))
    for item in raw.get("revisions") or []:
        state.revisions.append(
            ProjectRevision(
                revision=int(item["revision"]),
                site=dict(item["site"]),
                locks=list(item.get("locks") or []),
                input_digest=str(item["input_digest"]),
                accepted_layout_ref=item.get("accepted_layout_ref"),
            )
        )
    state.accepted_site = raw.get("accepted_site")
    state.accepted_layout = raw.get("accepted_layout")
    state.object_ids = dict(raw.get("object_ids") or {})
    state.object_sources = deepcopy(raw.get("object_sources") or {})
    state.history = list(raw.get("history") or [])
    return state


def save_project(state: ProjectState, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_record(), indent=2), encoding="utf-8")
    tmp.replace(target)


def load_project(path: str | Path) -> ProjectState:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("project file must be an object")
    return parse_project(raw)


def accepted_layout_snapshot(layout: LayoutResult) -> dict[str, Any]:
    from openparkcad.skeleton_identity import official_skeleton_mapping

    return {
        "skeleton_mapping": official_skeleton_mapping(layout),
        "layout_search": deepcopy(layout.layout_search),
        "site_selection": {
            "stall_id": layout.site.stall.id,
            "main_stall_id": layout.site.main_stall.id if layout.site.main_stall else None,
            "branch_stall_id": layout.site.branch_stall.id if layout.site.branch_stall else None,
            "aisle_width": layout.site.aisle_width,
            "angle_degrees": layout.site.angle_degrees,
            "margin": layout.site.margin,
        },
        "aisles": [
            {
                "id": aisle.id,
                "role": aisle.role,
                "polygon": [list(point) for point in aisle.polygon],
                "directionality": aisle.directionality,
                "angle_degrees": aisle.angle_degrees,
                "connected_to_entrance_id": aisle.connected_to_entrance_id,
                "parent_aisle_id": aisle.parent_aisle_id,
                "connected_aisle_ids": list(aisle.connected_aisle_ids),
            }
            for aisle in layout.aisles
        ],
        "stalls": [
            {
                "id": stall.id,
                "polygon": [list(point) for point in stall.polygon],
                "served_by_aisle_id": stall.served_by_aisle_id,
                "stall_type_id": stall.stall_type_id,
                "angle_degrees": stall.angle_degrees,
                "aisle_side": stall.aisle_side,
            }
            for stall in layout.stalls
        ],
        "generation_mode": layout.generation_mode,
        "main_entrance_id": layout.main_entrance_id,
        "selected_heading_degrees": layout.selected_heading_degrees,
        "selected_stall_type_id": layout.selected_stall_type_id,
    }


def layout_from_project_state(state: ProjectState) -> LayoutResult | None:
    geometry = state.accepted_layout
    site_payload = state.accepted_site
    if not isinstance(geometry, dict) or not (geometry.get("aisles") or geometry.get("stalls")):
        return None
    if not isinstance(site_payload, dict):
        site_payload = state.revisions[-1].site if state.revisions else None
    if not isinstance(site_payload, dict) or "site" not in site_payload:
        return None
    site = site_from_dict(site_payload)
    selection = geometry.get("site_selection") or {}
    specs = {item.id: item for item in site.stall_candidates or (site.stall,)}
    mapping = geometry.get("skeleton_mapping") or {}
    metadata = deepcopy(site.metadata)
    if mapping.get("skeleton_id"):
        metadata.update(skeleton_id=mapping["skeleton_id"], skeleton_family=mapping["family"])
    site = replace(
        site,
        metadata=metadata,
        stall=specs.get(selection.get("stall_id"), site.stall),
        main_stall=specs.get(selection.get("main_stall_id")),
        branch_stall=specs.get(selection.get("branch_stall_id")),
        aisle_width=selection.get("aisle_width", site.aisle_width),
        angle_degrees=selection.get("angle_degrees", site.angle_degrees),
        margin=selection.get("margin", site.margin),
    )
    aisles: list[ParkingAisle] = []
    for index, item in enumerate(geometry.get("aisles") or [], start=1):
        if not isinstance(item, dict):
            continue
        points = _snapshot_polygon(item.get("polygon"))
        if not points:
            continue
        aisles.append(
            ParkingAisle(
                id=str(item.get("id") or f"A-{index}"),
                polygon=points,
                angle_degrees=float(item.get("angle_degrees") or 0.0),
                role=str(item.get("role") or "aisle"),
                connected_to_entrance_id=item.get("connected_to_entrance_id"),
                parent_aisle_id=item.get("parent_aisle_id"),
                connected_aisle_ids=tuple(item.get("connected_aisle_ids") or ()),
                directionality=str(item.get("directionality") or "two_way"),
            )
        )
    stalls: list[ParkingStall] = []
    for index, item in enumerate(geometry.get("stalls") or [], start=1):
        if not isinstance(item, dict):
            continue
        points = _snapshot_polygon(item.get("polygon"))
        if not points:
            continue
        stalls.append(
            ParkingStall(
                id=str(item.get("id") or f"P-{index:03d}"),
                polygon=points,
                angle_degrees=float(item.get("angle_degrees") or 0.0),
                served_by_aisle_id=item.get("served_by_aisle_id"),
                aisle_side=item.get("aisle_side"),
                stall_type_id=item.get("stall_type_id"),
            )
        )
    if not aisles and not stalls:
        return None
    return LayoutResult(
        site=site,
        stalls=stalls,
        aisles=aisles,
        generation_mode=str(geometry.get("generation_mode") or "phase1_main_aisle"),
        main_entrance_id=geometry.get("main_entrance_id"),
        selected_heading_degrees=geometry.get("selected_heading_degrees"),
        selected_stall_type_id=geometry.get("selected_stall_type_id"),
        layout_search=deepcopy(geometry.get("layout_search") or {}),
    )


def _snapshot_polygon(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or len(raw) < 3:
        return []
    points: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            return []
        points.append((float(item[0]), float(item[1])))
    return points


def current_locks(state: ProjectState) -> list[LayoutLock]:
    if not state.revisions:
        return []
    latest = max(state.revisions, key=lambda item: item.revision)
    return [parse_lock(item) for item in latest.locks]
