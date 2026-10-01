"""Minimal dual-entrance (entry + far-end exit) template support."""

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def _through_lot_site(**overrides) -> SiteSpec:
    data = {
        "name": "dual-entrance",
        "boundary": [(0, 0), (24, 0), (24, 48), (0, 48)],
        "stall": StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        "aisle_width": 6.0,
        "margin": 0.0,
        "entrances": [
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(12, 0),
                width=8.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(12, 48),
                width=8.0,
                heading_degrees=270.0,
                allowed_movements=("exit",),
            ),
        ],
        "aisle_classes": [
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                enabled=True,
            )
        ],
        "fixed_aisle_class": "wide-two-way-no-cross",
        "optimization": {
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_dual_entrance": True,
            "enable_t_end_caps": False,
        },
    }
    data.update(overrides)
    return SiteSpec(**data)


def test_dual_entrance_attaches_exit_aisle_to_far_exit():
    layout = generate_layout(_through_lot_site())

    assert layout.stall_count > 0
    assert layout.main_entrance_id == "entry-gate"
    exit_aisles = [aisle for aisle in layout.aisles if aisle.role == "exit"]
    assert len(exit_aisles) == 1
    assert exit_aisles[0].id == "A-EXIT"
    assert exit_aisles[0].connected_to_entrance_id == "exit-gate"
    assert exit_aisles[0].parent_aisle_id == "A-TURNAROUND"

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-EXIT" in validation["reachable_aisles"]
    assert validation["stalls_without_exit_path"] == []

    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["entrances.dual_entry_exit"] == "active"


def test_dogleg_dual_entrance_attaches_exit_from_rear_turnaround():
    """Entry-only + far exit with mid-site obstacle uses dogleg and A-EXIT."""
    site = SiteSpec(
        name="dogleg-dual-entrance",
        boundary=[(0, 0), (90, 0), (90, 90), (0, 90)],
        obstacles=[[(35, 35), (55, 35), (55, 55), (35, 55)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(45, 0),
                width=8.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(45, 90),
                width=8.0,
                heading_degrees=270.0,
                allowed_movements=("exit",),
            ),
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_dual_entrance": True,
            "enable_main_aisle_dogleg": True,
            "prefer_obstacle_clearance": True,
            "auto_lateral_offsets_for_obstacles": False,
            "enable_t_end_caps": False,
            "dogleg_offsets": [22.0, -22.0],
        },
    )
    layout = generate_layout(site)

    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert layout.stall_count > 0
    assert layout.main_entrance_id == "entry-gate"
    exit_aisles = [aisle for aisle in layout.aisles if aisle.role == "exit"]
    assert len(exit_aisles) == 1
    assert exit_aisles[0].id == "A-EXIT"
    assert exit_aisles[0].connected_to_entrance_id == "exit-gate"
    assert exit_aisles[0].parent_aisle_id == "A-TURNAROUND"
    assert any(aisle.role == "jog" for aisle in layout.aisles)

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-EXIT" in validation["reachable_aisles"]
    assert "A-MAIN-REAR" in validation["reachable_aisles"]
    assert validation["stalls_without_exit_path"] == []


def test_dogleg_dual_entrance_prefers_offset_aligned_exit():
    site = SiteSpec(
        name="dogleg-offset-exit",
        boundary=[(0, 0), (90, 0), (90, 90), (0, 90)],
        obstacles=[[(35, 35), (55, 35), (55, 55), (35, 55)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(45, 0),
                width=8.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(67, 90),
                width=8.0,
                heading_degrees=270.0,
                allowed_movements=("exit",),
            ),
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_dual_entrance": True,
            "enable_main_aisle_dogleg": True,
            "prefer_obstacle_clearance": True,
            "auto_lateral_offsets_for_obstacles": False,
            "enable_t_end_caps": False,
            "dogleg_offsets": [22.0, -22.0],
        },
    )
    layout = generate_layout(site)
    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert any(aisle.role == "exit" for aisle in layout.aisles)
    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert validation["stalls_without_exit_path"] == []


def test_dual_entrance_enables_strict_one_way_without_reverse_egress():
    site = _through_lot_site(
        aisle_width=3.5,
        aisle_classes=[
            AisleClassSpec(
                id="narrow-one-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="one_way",
                centerline_crossing="not_applicable",
                enabled=True,
            )
        ],
        fixed_aisle_class="narrow-one-way",
        constraints={"circulation": {"one_way_allows_reverse_egress": False}},
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(12, 0),
                width=6.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(12, 48),
                width=6.0,
                heading_degrees=270.0,
                allowed_movements=("exit",),
            ),
        ],
    )
    # Wider lot so dual-side 90 stalls fit with 3.5m aisle.
    site = SiteSpec(
        name=site.name,
        boundary=[(0, 0), (18, 0), (18, 48), (0, 48)],
        stall=site.stall,
        aisle_width=3.5,
        margin=0.0,
        entrances=site.entrances,
        aisle_classes=site.aisle_classes,
        fixed_aisle_class=site.fixed_aisle_class,
        constraints=site.constraints,
        optimization=site.optimization,
    )

    layout = generate_layout(site)
    assert layout.stall_count > 0
    assert any(aisle.role == "exit" for aisle in layout.aisles)

    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    assert validation["aisle_directionality"] == "one_way"
    assert validation["one_way_allows_reverse_egress"] is False
    assert validation["reverse_egress_edge_count"] == 0
    assert validation["valid"] is True
    assert validation["stalls_without_exit_path"] == []
    assert all(edge.directionality == "one_way" for edge in graph.edges)


def test_dogleg_one_way_dual_entrance_strict_without_reverse_egress():
    site = SiteSpec(
        name="dogleg-one-way-dual",
        boundary=[(0, 0), (90, 0), (90, 90), (0, 90)],
        obstacles=[[(35, 35), (55, 35), (55, 55), (35, 55)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=3.5,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(45, 0),
                width=6.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(67, 90),
                width=6.0,
                heading_degrees=270.0,
                allowed_movements=("exit",),
            ),
        ],
        aisle_classes=[
            AisleClassSpec(
                id="narrow-one-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="one_way",
                centerline_crossing="not_applicable",
                enabled=True,
            )
        ],
        fixed_aisle_class="narrow-one-way",
        constraints={"circulation": {"one_way_allows_reverse_egress": False}},
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_dual_entrance": True,
            "enable_main_aisle_dogleg": True,
            "prefer_obstacle_clearance": True,
            "auto_lateral_offsets_for_obstacles": False,
            "enable_t_end_caps": False,
            "dogleg_offsets": [22.0, -22.0],
        },
    )
    layout = generate_layout(site)
    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert layout.stall_count > 0
    assert any(aisle.role == "exit" for aisle in layout.aisles)
    assert any(aisle.role == "jog" for aisle in layout.aisles)

    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    assert validation["valid"] is True
    assert validation["aisle_directionality"] == "one_way"
    assert validation["one_way_allows_reverse_egress"] is False
    assert validation["reverse_egress_edge_count"] == 0
    assert validation["stalls_without_exit_path"] == []


def test_dual_entrance_can_be_disabled():
    layout = generate_layout(_through_lot_site(optimization={
        "heading_deltas_degrees": [0],
        "entrance_offsets": [0],
        "enable_branches": False,
        "enable_connectors": False,
        "enable_dual_entrance": False,
        "enable_t_end_caps": False,
    }))
    assert not any(aisle.role == "exit" for aisle in layout.aisles)
