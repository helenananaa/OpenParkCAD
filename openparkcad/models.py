from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]
Polygon = list[Point]


@dataclass(frozen=True)
class StallSpec:
    width: float = 2.5
    length: float = 5.3
    id: str = "standard"
    family: str = "perpendicular"
    allowed_angles: tuple[float, ...] = (0.0,)
    drive_over: bool = False
    access_sides: tuple[str, ...] = ("front",)
    blocked_sides: tuple[str, ...] = ()
    classifications: tuple[str, ...] = ()
    fixed_features: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class SiteAreaSpec:
    id: str
    kind: str
    geometry: dict[str, Any]
    clearance: float = 0.0
    affects: tuple[str, ...] = ()
    parking_allowed: bool = False
    vehicle_allowed: bool = False
    authority: str = "project_policy"
    priority: str = "hard"


@dataclass(frozen=True)
class EntranceSpec:
    id: str
    mode: str
    center: Point
    width: float
    heading_degrees: float
    allowed_movements: tuple[str, ...] = ("enter", "exit")


@dataclass(frozen=True)
class TrailerSpec:
    length: float
    width: float
    wheelbase: float | None = None
    track_width: float | None = None
    front_overhang: float | None = None
    rear_overhang: float | None = None


@dataclass(frozen=True)
class VehicleSpec:
    id: str = "passenger-car"
    length: float = 4.8
    width: float = 1.9
    wheelbase: float | None = None
    min_turning_radius: float | None = None
    turning_radius_reference: str = "outer_front_wheel"
    track_width: float | None = None
    front_overhang: float | None = None
    rear_overhang: float | None = None
    swept_path_margin: float = 0.0
    max_reverse_distance: float | None = None
    configuration: str = "rigid"
    hitch_offset: float | None = None
    trailer: TrailerSpec | None = None


def is_articulated_vehicle(vehicle: VehicleSpec | None) -> bool:
    if vehicle is None:
        return False
    if vehicle.trailer is not None:
        return True
    return str(vehicle.configuration).strip().lower() == "articulated"


@dataclass(frozen=True)
class AisleClassSpec:
    id: str
    width: float
    capacity: str = "two_vehicle"
    directionality: str = "two_way"
    centerline_crossing: str = "forbidden"
    enabled: bool = True


@dataclass(frozen=True)
class SiteSpec:
    name: str
    boundary: Polygon
    obstacles: list[Polygon] = field(default_factory=list)
    obstacle_specs: tuple[SiteAreaSpec, ...] = ()
    reserved_areas: tuple[SiteAreaSpec, ...] = ()
    stall: StallSpec = field(default_factory=StallSpec)
    stall_candidates: tuple[StallSpec, ...] = field(default_factory=tuple)
    main_stall: StallSpec | None = None
    branch_stall: StallSpec | None = None
    aisle_width: float = 6.0
    angle_degrees: float = 0.0
    candidate_angles: tuple[float, ...] = (0.0,)
    margin: float = 0.2
    version: str = "0.1"
    units: str = "m"
    standards: dict[str, Any] = field(default_factory=dict)
    entrances: list[EntranceSpec] = field(default_factory=list)
    vehicle: VehicleSpec | None = None
    aisle_classes: list[AisleClassSpec] = field(default_factory=list)
    aisle_selection_mode: str = "fixed"
    fixed_aisle_class: str | None = None
    site_features: list[dict[str, Any]] = field(default_factory=list)
    pedestrian_and_emergency: dict[str, Any] = field(default_factory=dict)
    parking_quotas: dict[str, int] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    optimization: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_format: str = "phase0"


@dataclass(frozen=True)
class ParkingStall:
    id: str
    polygon: Polygon
    angle_degrees: float
    served_by_aisle_id: str | None = None
    aisle_side: str | None = None
    stall_type_id: str | None = None


@dataclass(frozen=True)
class ParkingAisle:
    id: str
    polygon: Polygon
    angle_degrees: float
    role: str = "aisle"
    connected_to_entrance_id: str | None = None
    parent_aisle_id: str | None = None
    connected_aisle_ids: tuple[str, ...] = ()
    directionality: str = "two_way"


@dataclass(frozen=True)
class AngleAttempt:
    angle_degrees: float
    stall_count: int
    entrance_id: str | None = None
    heading_delta_degrees: float = 0.0
    entrance_offset: float = 0.0
    branch_side: str | None = None
    branch_start_u: float | None = None
    branch_length: float | None = None
    branch_candidates: list[dict[str, Any]] = field(default_factory=list)
    graph_valid: bool = True
    graph_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateObject:
    id: str
    kind: str
    role: str
    status: str
    geometry: Polygon | None = None
    parent_ids: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    score_features: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LayoutResult:
    site: SiteSpec
    stalls: list[ParkingStall]
    aisles: list[ParkingAisle] = field(default_factory=list)
    selected_angle_degrees: float = 0.0
    attempts: list[AngleAttempt] = field(default_factory=list)
    generation_mode: str = "phase1_main_aisle"
    main_entrance_id: str | None = None
    selected_heading_degrees: float | None = None
    selected_heading_delta_degrees: float = 0.0
    selected_entrance_offset: float = 0.0
    selected_branch_side: str | None = None
    selected_branch_start_u: float | None = None
    selected_branch_length: float | None = None
    selected_branches: list[dict[str, Any]] = field(default_factory=list)
    selected_connectors: list[dict[str, Any]] = field(default_factory=list)
    selected_stall_type_id: str | None = None
    stall_type_attempts: list[dict[str, Any]] = field(default_factory=list)
    selected_stall_assignment: dict[str, str] = field(default_factory=dict)
    stall_assignment_attempts: list[dict[str, Any]] = field(default_factory=list)
    score: dict[str, float] = field(default_factory=dict)
    graph_validation: dict[str, Any] = field(default_factory=dict)
    maneuver_validation: dict[str, Any] = field(default_factory=dict)
    site_constraint_validation: dict[str, Any] = field(default_factory=dict)
    engineering_validation: dict[str, Any] = field(default_factory=dict)
    road_traversal_validation: dict[str, Any] = field(default_factory=dict)
    operational_quality: dict[str, Any] = field(default_factory=dict)
    candidate_objects: list[CandidateObject] = field(default_factory=list)
    candidate_selection: dict[str, Any] = field(default_factory=dict)
    candidate_network_preview: dict[str, Any] = field(default_factory=dict)
    candidate_layout_preview: dict[str, Any] = field(default_factory=dict)
    candidate_layout_promotion: dict[str, Any] = field(default_factory=dict)
    unsupported_phase1_inputs: list[dict[str, str]] = field(default_factory=list)
    layout_search: dict[str, Any] = field(default_factory=dict)

    @property
    def stall_count(self) -> int:
        return len(self.stalls)


def _point(raw: Any) -> Point:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"Point must be [x, y], got {raw!r}")
    return (float(raw[0]), float(raw[1]))


def _polygon(raw: Any) -> Polygon:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError("Polygon must contain at least three points")
    return [_point(item) for item in raw]


def site_from_dict(data: dict[str, Any]) -> SiteSpec:
    if "site" not in data:
        raise ValueError("OpenParkCAD now requires the Phase 0+ JSON shape with a top-level 'site' object")
    return _phase0_site_from_dict(data)


def _phase0_site_from_dict(data: dict[str, Any]) -> SiteSpec:
    site_data = _dict(data.get("site"), "site")
    parking_data = _dict(data.get("parking", {}), "parking")
    aisles_data = _dict(data.get("aisles", {}), "aisles")
    constraints = _dict(data.get("constraints", {}), "constraints")
    from openparkcad.road_traversal_models import parse_road_traversal_mapping

    parse_road_traversal_mapping(constraints)

    boundary = _geometry_polygon(site_data["boundary"], "site.boundary")
    obstacle_setback = float(_dict(constraints.get("setbacks", {}), "constraints.setbacks").get("obstacle", 0.0))
    obstacle_specs = tuple(
        _site_area_spec(item, index, "site.obstacles", default_clearance=obstacle_setback)
        for index, item in enumerate(site_data.get("obstacles", []), start=1)
    )
    obstacles = [
        _geometry_polygon(item.geometry, f"site.obstacles[{index}].geometry")
        for index, item in enumerate(obstacle_specs, start=1)
    ]
    reserved_areas = tuple(
        _site_area_spec(item, index, "site.reserved_areas")
        for index, item in enumerate(site_data.get("reserved_areas", []), start=1)
    )

    stall_candidates = _enabled_stall_specs(parking_data)
    stall = stall_candidates[0]
    aisle_classes = [_aisle_class(item) for item in aisles_data.get("classes", [])]
    fixed_aisle_class = aisles_data.get("fixed_class")
    aisle_width = _selected_aisle_width(aisle_classes, fixed_aisle_class, default=6.0)
    margin = float(_dict(constraints.get("setbacks", {}), "constraints.setbacks").get("site_boundary", 0.2))

    entrances = [_entrance(item, index) for index, item in enumerate(data.get("entrances", []), start=1)]
    vehicle_data = _dict(data.get("vehicles", {}), "vehicles").get("design_vehicle")
    vehicle = _vehicle(vehicle_data) if vehicle_data else None

    return SiteSpec(
        version=str(data.get("version", "0.1")),
        name=str(data.get("name", "parking site")),
        units=str(data.get("units", "m")),
        standards=_dict(data.get("standards", {}), "standards"),
        boundary=boundary,
        obstacles=obstacles,
        obstacle_specs=obstacle_specs,
        reserved_areas=reserved_areas,
        stall=stall,
        stall_candidates=stall_candidates,
        aisle_width=aisle_width,
        angle_degrees=stall.allowed_angles[0],
        candidate_angles=_stall_candidate_angles(parking_data),
        margin=margin,
        entrances=entrances,
        vehicle=vehicle,
        aisle_classes=aisle_classes,
        aisle_selection_mode=str(aisles_data.get("selection_mode", "fixed")),
        fixed_aisle_class=str(fixed_aisle_class) if fixed_aisle_class else None,
        site_features=list(data.get("site_features", [])),
        pedestrian_and_emergency=_dict(data.get("pedestrian_and_emergency", {}), "pedestrian_and_emergency"),
        parking_quotas=_parking_quotas(parking_data),
        constraints=constraints,
        optimization=_optimization(_dict(data.get("optimization", {}), "optimization")),
        diagnostics=_dict(data.get("diagnostics", {}), "diagnostics"),
        metadata=_dict(data.get("metadata", {}), "metadata"),
        source_format="phase0",
    )


def _dict(raw: Any, label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    return raw


def _optimization(raw: dict[str, Any]) -> dict[str, Any]:
    from openparkcad.candidate_catalog import parse_selector_num_workers
    from openparkcad.layout_search import parse_layout_search_mapping

    parse_selector_num_workers(raw)
    parse_layout_search_mapping(raw)
    return raw


def _geometry_polygon(raw: Any, label: str) -> Polygon:
    if isinstance(raw, list):
        return _polygon(raw)
    geometry = _dict(raw, label)
    geometry_type = geometry.get("type")
    if geometry_type == "polygon":
        return _polygon(geometry["points"])
    raise ValueError(f"{label}.type={geometry_type!r} is documented but not implemented yet")


def _site_area_spec(
    raw: Any,
    index: int,
    label: str,
    *,
    default_clearance: float = 0.0,
) -> SiteAreaSpec:
    if isinstance(raw, list):
        geometry = {"type": "polygon", "points": raw}
        return SiteAreaSpec(
            id=f"{label.rsplit('.', 1)[-1]}-{index}",
            kind="obstacle",
            geometry=geometry,
            clearance=_nonnegative_float(default_clearance, f"{label}[{index}].clearance"),
        )

    item = _dict(raw, f"{label}[{index}]")
    geometry = _dict(item.get("geometry"), f"{label}[{index}].geometry")
    if not geometry:
        raise ValueError(f"{label}[{index}].geometry must be provided")
    declared_clearance = _nonnegative_float(
        item.get("clearance", 0.0),
        f"{label}[{index}].clearance",
    )
    minimum_clearance = _nonnegative_float(
        default_clearance,
        f"{label}[{index}].default_clearance",
    )
    return SiteAreaSpec(
        id=str(item.get("id", f"{label.rsplit('.', 1)[-1]}-{index}")),
        kind=str(item.get("type", "reserved" if label.endswith("reserved_areas") else "obstacle")),
        geometry=geometry,
        clearance=max(declared_clearance, minimum_clearance),
        affects=tuple(str(value) for value in item.get("affects", [])),
        parking_allowed=bool(item.get("parking_allowed", False)),
        vehicle_allowed=bool(item.get("vehicle_allowed", False)),
        authority=str(item.get("authority", "project_policy")),
        priority=str(item.get("priority", "hard")),
    )


def _enabled_stall_specs(parking_data: dict[str, Any]) -> tuple[StallSpec, ...]:
    stall_types = parking_data.get("stall_types", [])
    enabled = [item for item in stall_types if item.get("enabled", True)]
    raw_candidates = enabled or (stall_types[:1] if stall_types else [{}])
    return tuple(_stall_spec(data) for data in raw_candidates)


def _stall_spec(data: dict[str, Any]) -> StallSpec:
    return StallSpec(
        id=str(data.get("id", "standard")),
        family=str(data.get("family", "perpendicular")),
        width=float(data.get("width", 2.5)),
        length=float(data.get("length", 5.3)),
        allowed_angles=tuple(float(item) for item in data.get("allowed_angles", [0])),
        drive_over=bool(data.get("drive_over", False)),
        access_sides=tuple(str(item) for item in data.get("access_sides", ["front"])),
        blocked_sides=tuple(str(item) for item in data.get("blocked_sides", [])),
        classifications=_stall_classifications(data),
        fixed_features=tuple(
            _dict(item, "parking.stall_types[].fixed_features[]")
            for item in data.get("fixed_features", [])
        ),
    )


def _stall_classifications(data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("classifications", data.get("qualifications", data.get("tags", [])))
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list | tuple):
        values = [str(item) for item in raw]
    else:
        raise ValueError("parking.stall_types[].classifications must be a string or array")
    if data.get("accessible", False):
        values.append("accessible")
    if data.get("ev", False):
        values.append("ev")
    return tuple(dict.fromkeys(value.strip().lower() for value in values if value.strip()))


def _parking_quotas(parking_data: dict[str, Any]) -> dict[str, int]:
    raw = _dict(parking_data.get("quotas", {}), "parking.quotas")
    quotas: dict[str, int] = {}
    for key, value in raw.items():
        number = _nonnegative_float(value, f"parking.quotas.{key}")
        if not number.is_integer():
            raise ValueError(f"parking.quotas.{key} must be a whole number")
        quotas[str(key)] = int(number)
    return quotas


def _nonnegative_float(raw: Any, label: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def _stall_candidate_angles(parking_data: dict[str, Any]) -> tuple[float, ...]:
    angles: list[float] = []
    for stall_type in parking_data.get("stall_types", []):
        if not stall_type.get("enabled", True):
            continue
        for angle in stall_type.get("allowed_angles", [0]):
            value = float(angle)
            if value not in angles:
                angles.append(value)
    return tuple(angles or [0.0])


def _aisle_class(raw: Any) -> AisleClassSpec:
    data = _dict(raw, "aisles.classes[]")
    return AisleClassSpec(
        id=str(data["id"]),
        width=float(data.get("width", 6.0)),
        capacity=str(data.get("capacity", "two_vehicle")),
        directionality=str(data.get("directionality", "two_way")),
        centerline_crossing=str(data.get("centerline_crossing", "forbidden")),
        enabled=bool(data.get("enabled", True)),
    )


def _selected_aisle_width(classes: list[AisleClassSpec], fixed_class: Any, default: float) -> float:
    if fixed_class:
        for aisle_class in classes:
            if aisle_class.id == fixed_class:
                return aisle_class.width
    for aisle_class in classes:
        if aisle_class.enabled:
            return aisle_class.width
    return default


def _entrance(raw: Any, index: int) -> EntranceSpec:
    data = _dict(raw, f"entrances[{index}]")
    return EntranceSpec(
        id=str(data.get("id", f"entrance-{index}")),
        mode=str(data.get("mode", "shared")),
        center=_point(data["center"]),
        width=float(data.get("width", 6.0)),
        heading_degrees=float(data.get("heading_degrees", 0.0)),
        allowed_movements=tuple(str(item) for item in data.get("allowed_movements", ["enter", "exit"])),
    )


def _vehicle(raw: Any) -> VehicleSpec:
    data = _dict(raw, "vehicles.design_vehicle")
    trailer_raw = data.get("trailer")
    trailer = _trailer(trailer_raw) if trailer_raw else None
    default_configuration = "articulated" if trailer is not None else "rigid"
    return VehicleSpec(
        id=str(data.get("id", "passenger-car")),
        length=float(data.get("length", 4.8)),
        width=float(data.get("width", 1.9)),
        wheelbase=float(data["wheelbase"]) if "wheelbase" in data else None,
        min_turning_radius=float(data["min_turning_radius"]) if "min_turning_radius" in data else None,
        turning_radius_reference=str(data.get("turning_radius_reference", "outer_front_wheel")),
        track_width=float(data["track_width"]) if "track_width" in data else None,
        front_overhang=float(data["front_overhang"]) if "front_overhang" in data else None,
        rear_overhang=float(data["rear_overhang"]) if "rear_overhang" in data else None,
        swept_path_margin=float(data.get("swept_path_margin", 0.0)),
        max_reverse_distance=float(data["max_reverse_distance"]) if "max_reverse_distance" in data else None,
        configuration=str(data.get("configuration", default_configuration)),
        hitch_offset=_optional_float(data, "hitch_offset"),
        trailer=trailer,
    )


def _trailer(raw: Any) -> TrailerSpec:
    data = _dict(raw, "vehicles.design_vehicle.trailer")
    if "length" not in data or "width" not in data:
        raise ValueError("vehicles.design_vehicle.trailer requires length and width")
    return TrailerSpec(
        length=float(data["length"]),
        width=float(data["width"]),
        wheelbase=_optional_float(data, "wheelbase"),
        track_width=_optional_float(data, "track_width"),
        front_overhang=_optional_float(data, "front_overhang"),
        rear_overhang=_optional_float(data, "rear_overhang"),
    )


def _optional_float(data: dict[str, Any], key: str) -> float | None:
    if key not in data or data[key] is None:
        return None
    return float(data[key])
