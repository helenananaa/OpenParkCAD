import math

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.generator import generate_layout
from openparkcad.maneuver_validation import apply_maneuver_filter, validate_maneuvers
from openparkcad.models import (
    AisleClassSpec,
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteSpec,
    StallSpec,
    VehicleSpec,
)


def test_phase3a_maneuver_validation_rejects_blocked_access_envelope():
    site = SiteSpec(
        name="blocked-access",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        obstacles=[[(4.5, 4), (6.0, 4), (6.0, 5), (4.5, 5)]],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(4, 6), (6.5, 6), (6.5, 11), (4, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    assert validation["invalid_stalls"][0]["stall_id"] == "P-001"
    assert validation["invalid_stalls"][0]["reason"] == "access_envelope_hits_boundary_or_obstacle"


def test_phase3a_generator_filters_stalls_without_required_access_depth():
    site = SiteSpec(
        name="strict-access-depth",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=7.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "maneuver_access_depth": 7.0,
        },
    )

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["filtered_stall_count"] > 0
    assert all(ShapelyPolygon(stall.polygon).is_valid for stall in layout.stalls)


def test_phase3a_filter_keeps_valid_access_envelopes():
    site = SiteSpec(
        name="valid-access",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(4, 6), (6.5, 6), (6.5, 11), (4, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    filtered = apply_maneuver_filter(layout)

    assert filtered.stall_count == 1
    assert filtered.maneuver_validation["valid"] is True
    assert filtered.maneuver_validation["rule_counts"] == {"perpendicular_90_proxy": 1}
    assert filtered.maneuver_validation["envelopes"][0]["rule_id"] == "perpendicular_90_proxy"


def test_phase3b_turning_proxy_rejects_blocked_side_sweep():
    site = SiteSpec(
        name="blocked-turn-sweep",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        obstacles=[[(8.5, 2), (9.2, 2), (9.2, 4), (8.5, 4)]],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "maneuver_turn_buffer_length": 2.0,
            "maneuver_turn_coverage_ratio": 0.98,
            "maneuver_l_shape_fallback": False,
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(5, 6), (7.5, 6), (7.5, 11), (5, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    assert validation["turn_buffer_length"] == 2.0
    assert validation["invalid_stalls"][0]["reason"] == "turning_sweep_hits_boundary_or_obstacle"


def test_phase3b_l_shape_fallback_accepts_one_sided_corner_sweep():
    site = SiteSpec(
        name="l-shape-corner-turn",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "maneuver_turn_buffer_length": 2.0,
            "maneuver_turn_coverage_ratio": 0.98,
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(5, 6), (7.5, 6), (7.5, 11), (5, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (7.5, 0), (7.5, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["rule_counts"] == {"perpendicular_90_l_shape_proxy": 1}
    assert validation["rule_support"]["perpendicular_90_l_shape_proxy"] == "active"
    assert validation["envelopes"][0]["rule_id"] == "perpendicular_90_l_shape_proxy"
    assert validation["envelopes"][0]["base_rule_id"] == "perpendicular_90_proxy"
    assert validation["envelopes"][0]["maneuver_variant"] == "l_shape_start"
    assert validation["envelopes"][0]["fallback_from_reason"] == "turning_sweep_not_in_drivable_aisle"


def test_phase3b_turning_proxy_can_filter_generated_stalls_near_aisle_ends():
    site = SiteSpec(
        name="strict-turn-buffer",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=7.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "maneuver_turn_buffer_length": 8.0,
            "maneuver_turn_coverage_ratio": 0.98,
            "maneuver_l_shape_fallback": False,
        },
    )

    layout = generate_layout(site)

    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["filtered_stall_count"] > 0
    assert any(
        item["reason"] == "turning_sweep_not_in_drivable_aisle"
        for item in layout.maneuver_validation["pre_filter_invalid_stalls"]
    )


def test_parallel_proxy_validates_traffic_side_access():
    site = SiteSpec(
        name="parallel-rule",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(
            width=2.5,
            length=5.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(6, 6), (11, 6), (11, 8.5), (6, 8.5)],
                angle_degrees=0.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="standard",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (20, 0), (20, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["rule_counts"] == {"parallel_proxy": 1}
    assert validation["rule_support"]["parallel_proxy"] == "active"
    assert validation["envelopes"][0]["rule_id"] == "parallel_proxy"
    assert validation["envelopes"][0]["drivable_coverage_ratio"] >= 0.95


def test_parallel_proxy_rejects_blocked_traffic_side_access():
    site = SiteSpec(
        name="blocked-parallel",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        obstacles=[[(4.5, 3.0), (8.5, 3.0), (8.5, 5.5), (4.5, 5.5)]],
        stall=StallSpec(
            width=2.5,
            length=5.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        optimization={"maneuver_parallel_access_depth": 3.0},
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(4, 6), (9, 6), (9, 8.5), (4, 8.5)],
                angle_degrees=0.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    assert validation["invalid_stalls"][0]["rule_id"] == "parallel_proxy"
    assert validation["invalid_stalls"][0]["reason"] in {
        "access_envelope_hits_boundary_or_obstacle",
        "access_envelope_not_in_drivable_aisle",
        "turning_sweep_hits_boundary_or_obstacle",
        "turning_sweep_not_in_drivable_aisle",
    }


def test_parallel_vehicle_analytic_check_allows_declared_reverse_limit():
    site = SiteSpec(
        name="parallel-vehicle",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(
            width=2.5,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        vehicle=VehicleSpec(
            length=4.8,
            width=1.9,
            wheelbase=2.8,
            min_turning_radius=5.5,
            turning_radius_reference="outer_front_wheel",
            track_width=1.6,
            front_overhang=1.0,
            rear_overhang=1.0,
            max_reverse_distance=12.0,
        ),
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(6, 6), (12, 6), (12, 8.5), (6, 8.5)],
                angle_degrees=0.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (20, 0), (20, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["parallel_vehicle_analytic_v1"] == 1
    vehicle_check = validation["envelopes"][0]["vehicle_validation"]
    assert vehicle_check["status"] == "active_conservative"
    assert vehicle_check["rule_id"] == "parallel_vehicle_analytic_v1"


def test_parallel_vehicle_swept_path_uses_exact_s_curve():
    site = SiteSpec(
        name="parallel-swept",
        boundary=[(-2, -2), (26, -2), (26, 16), (-2, 16)],
        stall=StallSpec(
            width=2.5,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        vehicle=VehicleSpec(
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
        ),
        constraints={"maneuvering": {"require_swept_path_check": True}},
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(6, 6), (12, 6), (12, 8.5), (6, 8.5)],
                angle_degrees=0.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (24, 0), (24, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["parallel_reverse_s_curve_bicycle_v1"] == 1
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["status"] == "active_exact"
    assert check["rule_id"] == "parallel_reverse_s_curve_bicycle_v1"


def test_generated_parallel_layout_accepts_exact_vehicle_check():
    site = SiteSpec(
        name="parallel-exact-generate",
        boundary=[(0, 0), (20, 0), (20, 52), (0, 52)],
        stall=StallSpec(
            id="parallel",
            width=2.5,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        vehicle=_angled_vehicle(),
        constraints={"maneuvering": {"require_swept_path_check": True}},
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(10, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                centerline_crossing="allowed",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_t_end_caps": False,
        },
    )
    layout = generate_layout(site)
    assert layout.stall_count > 0
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["vehicle_validation"]["valid"] is True
    assert layout.maneuver_validation["vehicle_rule_counts"]["parallel_reverse_s_curve_bicycle_v1"] == layout.stall_count


def test_t_end_vehicle_swept_path_uses_exact_template():
    site = SiteSpec(
        name="t-end-swept",
        boundary=[(-2, -2), (22, -2), (22, 18), (-2, 18)],
        stall=StallSpec(
            width=2.5,
            length=5.4,
            family="t_end",
            allowed_angles=(90.0,),
        ),
        aisle_width=6.0,
        margin=0.0,
        vehicle=_angled_vehicle(),
        constraints={"maneuvering": {"require_swept_path_check": True}},
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(8.0, 6.0), (10.6, 6.0), (10.6, 11.4), (8.0, 11.4)],
                angle_degrees=90.0,
                served_by_aisle_id="A-TURNAROUND",
                aisle_side="end",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-TURNAROUND",
                polygon=[(0.0, 0.0), (20.0, 0.0), (20.0, 6.0), (0.0, 6.0)],
                angle_degrees=90.0,
                role="turnaround",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["reverse_in_t_end_bicycle_v1"] == 1
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["status"] == "active_exact"
    assert check["rule_id"] == "reverse_in_t_end_bicycle_v1"
    assert check["stall_family"] == "t_end"


def test_generated_t_end_layout_accepts_exact_vehicle_check():
    site = SiteSpec(
        name="t-end-exact-generate",
        boundary=[(0, 0), (36, 0), (36, 60), (0, 60)],
        stall=StallSpec(
            id="t-end",
            width=2.5,
            length=5.4,
            family="t_end",
            allowed_angles=(90.0,),
            access_sides=("front",),
        ),
        aisle_width=8.0,
        margin=0.0,
        vehicle=_angled_vehicle(),
        constraints={"maneuvering": {"require_swept_path_check": True}},
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(18, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=8.0,
                capacity="two_vehicle",
                directionality="two_way",
                centerline_crossing="allowed",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_t_end_caps": False,
        },
    )
    layout = generate_layout(site)
    assert layout.stall_count > 0
    assert all(stall.aisle_side == "end" for stall in layout.stalls)
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["vehicle_validation"]["valid"] is True
    assert layout.maneuver_validation["vehicle_rule_counts"]["reverse_in_t_end_bicycle_v1"] == layout.stall_count


def _angled_vehicle() -> VehicleSpec:
    return VehicleSpec(
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
    )


def _angled_60_layout(*, require_swept_path: bool = False, require_turning_radius: bool = False) -> LayoutResult:
    angle = 60.0
    theta = math.radians(angle)
    width, length = 2.5, 5.0
    front_pitch = width / math.sin(theta)
    forward_shift = length * math.cos(theta)
    lateral_depth = length * math.sin(theta)
    start_u = 8.0
    front_v = 6.0
    site = SiteSpec(
        name="angled-vehicle",
        boundary=[(-2, -2), (34, -2), (34, 16), (-2, 16)],
        stall=StallSpec(width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
        aisle_width=6.0,
        margin=0.0,
        vehicle=_angled_vehicle(),
        constraints={
            "maneuvering": {
                "require_swept_path_check": require_swept_path,
                "require_turning_radius_check": require_turning_radius,
            }
        },
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                centerline_crossing="allowed",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
    )
    return LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[
                    (start_u, front_v),
                    (start_u + front_pitch, front_v),
                    (start_u + front_pitch + forward_shift, front_v + lateral_depth),
                    (start_u + forward_shift, front_v + lateral_depth),
                ],
                angle_degrees=60.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (30, 0), (30, 6), (0, 6)],
                angle_degrees=0.0,
                role="main",
            )
        ],
    )


def test_angled_vehicle_analytic_check_uses_scaled_arc_bound():
    layout = _angled_60_layout(require_turning_radius=True)
    validation = validate_maneuvers(layout)
    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["angled_vehicle_analytic_v1"] == 1
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["status"] == "active_conservative"
    assert check["stall_angle_degrees"] == 60.0
    rear_radius = check["turning_radius_resolution"]["rear_axle_radius"]
    assert check["reverse_distance_upper_bound"] == math.radians(60.0) * rear_radius + 5.0


def test_angled_vehicle_swept_path_uses_exact_template():
    layout = _angled_60_layout(require_swept_path=True)
    validation = validate_maneuvers(layout)
    assert validation["valid"] is True
    assert validation["vehicle_validation"]["valid"] is True
    assert validation["vehicle_rule_counts"]["reverse_in_angled_bicycle_v1"] == 1
    check = validation["envelopes"][0]["vehicle_validation"]
    assert check["status"] == "active_exact"
    assert check["rule_id"] == "reverse_in_angled_bicycle_v1"


def test_phase3d_generated_angled_layout_accepts_exact_vehicle_check():
    site = SiteSpec(
        name="angled-exact-generate",
        boundary=[(0, 0), (34, 0), (34, 38), (0, 38)],
        stall=StallSpec(width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
        aisle_width=6.0,
        margin=0.0,
        vehicle=_angled_vehicle(),
        constraints={"maneuvering": {"require_swept_path_check": True}},
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(17, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                centerline_crossing="allowed",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_t_end_caps": False},
    )
    layout = generate_layout(site)
    assert layout.stall_count > 0
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["vehicle_validation"]["valid"] is True
    assert layout.maneuver_validation["vehicle_rule_counts"]["reverse_in_angled_bicycle_v1"] == layout.stall_count


def test_phase3c_angled_proxy_validates_angled_stall_access():
    site = SiteSpec(
        name="angled-rule",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(4, 6), (6.5, 6), (9, 11), (6.5, 11)],
                angle_degrees=60.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["rule_counts"] == {"angled_proxy": 1}
    assert validation["rule_support"]["angled_proxy"] == "active"
    assert validation["envelopes"][0]["rule_id"] == "angled_proxy"
    assert validation["envelopes"][0]["depth"] < site.aisle_width


def test_phase3c_filters_future_maneuver_rule_stalls():
    site = SiteSpec(
        name="filter-future-rule",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(width=2.5, length=5.0, family="painted", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(4, 6), (6.5, 6), (6.5, 11), (4, 11)],
                angle_degrees=60.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    filtered = apply_maneuver_filter(layout)

    assert filtered.stall_count == 0
    assert filtered.maneuver_validation["valid"] is True
    assert filtered.maneuver_validation["filtered_stall_count"] == 1
    assert filtered.maneuver_validation["pre_filter_invalid_stalls"][0]["reason"] == (
        "stall_family_maneuver_rule_not_implemented"
    )


def test_t_end_proxy_validates_end_face_access():
    site = SiteSpec(
        name="t-end-rule",
        boundary=[(0, 0), (20, 0), (20, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, family="t_end", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(3.75, 10), (6.25, 10), (6.25, 15), (3.75, 15)],
                angle_degrees=90.0,
                served_by_aisle_id="A-TURNAROUND",
                aisle_side="end",
                stall_type_id="t-end",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(2, 0), (8, 0), (8, 10), (2, 10)],
                angle_degrees=90.0,
                role="main",
            ),
            ParkingAisle(
                id="A-TURNAROUND",
                polygon=[(0, 4), (10, 4), (10, 10), (0, 10)],
                angle_degrees=90.0,
                role="turnaround",
                parent_aisle_id="A-MAIN",
            ),
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is True
    assert validation["rule_counts"] == {"t_end_proxy": 1}
    assert validation["rule_support"]["t_end_proxy"] == "active"
    assert validation["envelopes"][0]["rule_id"] == "t_end_proxy"
