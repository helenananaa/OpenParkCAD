"""Shared hand-built layouts for road-traversal tests."""

from __future__ import annotations

from openparkcad.models import (
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteSpec,
    StallSpec,
    VehicleSpec,
)


def design_vehicle(**overrides: object) -> VehicleSpec:
    values = dict(
        id="passenger-car",
        length=4.8,
        width=1.9,
        wheelbase=2.8,
        min_turning_radius=5.5,
        turning_radius_reference="outer_front_wheel",
        track_width=1.6,
        front_overhang=1.0,
        rear_overhang=1.0,
        swept_path_margin=0.3,
        max_reverse_distance=12.0,
        configuration="rigid",
    )
    values.update(overrides)
    return VehicleSpec(**values)  # type: ignore[arg-type]


def through_site(
    *,
    enabled: bool = True,
    one_way: bool = False,
    extra_obstacle: list[tuple[float, float]] | None = None,
    extra_stall: ParkingStall | None = None,
    second_entrance: bool = True,
    stall_family: str = "perpendicular",
    constraints_extra: dict | None = None,
    time_budget_seconds: float = 10.0,
) -> SiteSpec:
    constraints: dict = {"setbacks": {"site_boundary": 0.0}}
    if enabled:
        constraints["road_traversal"] = {
            "enabled": True,
            "scope": "site_interior",
            "time_budget_seconds": time_budget_seconds,
        }
    if constraints_extra:
        constraints.update(constraints_extra)
    obstacles = [extra_obstacle] if extra_obstacle else []
    entrances = [
        EntranceSpec(
            id="entry-gate",
            mode="entry_only" if second_entrance else "shared",
            center=(0.0, 5.0),
            width=8.0,
            heading_degrees=0.0,
            allowed_movements=("enter",) if second_entrance else ("enter", "exit"),
        )
    ]
    if second_entrance:
        entrances.append(
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(40.0, 5.0),
                width=8.0,
                heading_degrees=180.0,
                allowed_movements=("exit",),
            )
        )
    family = stall_family
    stall = StallSpec(id=f"standard-{family}", family=family, width=2.6, length=5.4, allowed_angles=(90.0 if family != "parallel" else 0.0,))
    return SiteSpec(
        name="road-traversal-through-site",
        boundary=[(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0)],
        obstacles=obstacles,
        stall=stall,
        stall_candidates=(stall,),
        aisle_width=6.0,
        entrances=entrances,
        vehicle=design_vehicle(),
        constraints=constraints,
    )


def through_layout(**kwargs: object) -> LayoutResult:
    extra_stall = kwargs.pop("extra_stall", None)
    one_way = bool(kwargs.get("one_way", False))
    site = through_site(**kwargs)  # type: ignore[arg-type]
    aisle = ParkingAisle(
        id="A-MAIN",
        polygon=[(0.0, 2.0), (40.0, 2.0), (40.0, 8.0), (0.0, 8.0)],
        angle_degrees=0.0,
        role="main",
        connected_to_entrance_id="entry-gate",
        connected_aisle_ids=("A-EXIT",) if site.entrances[-1].id == "exit-gate" else (),
        directionality="one_way" if one_way else "two_way",
    )
    exit_aisle = ParkingAisle(
        id="A-EXIT",
        polygon=[(34.0, 2.0), (40.0, 2.0), (40.0, 8.0), (34.0, 8.0)],
        angle_degrees=0.0,
        role="exit",
        connected_to_entrance_id="exit-gate",
        parent_aisle_id="A-MAIN",
        directionality="one_way" if one_way else "two_way",
    )
    stall = ParkingStall(
        id="P-001",
        polygon=[(16.0, 8.0), (18.6, 8.0), (18.6, 13.4), (16.0, 13.4)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
        stall_type_id=site.stall.id,
    )
    stalls = [stall]
    if extra_stall is not None:
        stalls.append(extra_stall)  # type: ignore[arg-type]
    aisles = [aisle]
    if any(item.id == "exit-gate" for item in site.entrances):
        aisles.append(exit_aisle)
        aisle = ParkingAisle(
            id=aisle.id,
            polygon=aisle.polygon,
            angle_degrees=aisle.angle_degrees,
            role=aisle.role,
            connected_to_entrance_id=aisle.connected_to_entrance_id,
            connected_aisle_ids=("A-EXIT",),
            directionality=aisle.directionality,
        )
        aisles[0] = aisle
    graph = {
        "valid": True,
        "errors": [],
        "unreachable_stalls": [],
        "stalls_without_exit_path": [],
    }
    return LayoutResult(
        site=site,
        stalls=stalls,
        aisles=aisles,
        graph_validation=graph,
        maneuver_validation={"valid": True, "invalid_stalls": []},
        site_constraint_validation={"valid": True, "errors": []},
        operational_quality={"valid": True, "risk_score": 0.0},
        engineering_validation={"valid": True, "rules": {"failed": []}},
    )


def branch_layout() -> LayoutResult:
    layout = through_layout()
    branch = ParkingAisle(
        id="A-BRANCH",
        polygon=[(18.0, 8.0), (24.0, 8.0), (24.0, 18.0), (18.0, 18.0)],
        angle_degrees=90.0,
        role="branch",
        parent_aisle_id="A-MAIN",
        directionality="two_way",
    )
    main = layout.aisles[0]
    main = ParkingAisle(
        id=main.id,
        polygon=main.polygon,
        angle_degrees=main.angle_degrees,
        role=main.role,
        connected_to_entrance_id=main.connected_to_entrance_id,
        connected_aisle_ids=tuple(list(main.connected_aisle_ids) + ["A-BRANCH"]),
        directionality=main.directionality,
    )
    aisles = [main, branch, *layout.aisles[1:]]
    stall = ParkingStall(
        id="P-BRANCH",
        polygon=[(24.0, 12.0), (29.4, 12.0), (29.4, 14.6), (24.0, 14.6)],
        angle_degrees=0.0,
        served_by_aisle_id="A-BRANCH",
        stall_type_id=layout.site.stall.id,
    )
    return LayoutResult(
        site=layout.site,
        stalls=[stall],
        aisles=aisles,
        graph_validation=layout.graph_validation,
        maneuver_validation=layout.maneuver_validation,
        site_constraint_validation=layout.site_constraint_validation,
        operational_quality=layout.operational_quality,
        engineering_validation=layout.engineering_validation,
    )
