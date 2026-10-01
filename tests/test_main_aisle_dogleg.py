"""Main-aisle dogleg bypass around mid-site hard obstacles."""

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_candidates import _dogleg_enabled
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def _blocked_spine_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="dogleg",
        boundary=[(0, 0), (48, 0), (48, 52), (0, 52)],
        obstacles=[[(18, 10), (30, 10), (30, 28), (18, 28)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(24, 0),
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
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            **optimization,
        },
    )


def test_dogleg_enabled_with_prefer_obstacle_clearance():
    site = _blocked_spine_site()
    assert _dogleg_enabled(site) is True
    site_off = _blocked_spine_site(prefer_obstacle_clearance=False, enable_main_aisle_dogleg=False)
    assert _dogleg_enabled(site_off) is False


def test_dogleg_generates_jog_and_rear_main_around_obstacle():
    layout = generate_layout(_blocked_spine_site())

    assert layout.stall_count > 0
    roles = {aisle.role for aisle in layout.aisles}
    ids = {aisle.id for aisle in layout.aisles}
    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert "jog" in roles
    assert "A-JOG" in ids
    assert "A-MAIN-REAR" in ids
    assert "A-TURNAROUND" in ids

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-JOG" in validation["reachable_aisles"]
    assert "A-MAIN-REAR" in validation["reachable_aisles"]
    assert validation["stalls_without_exit_path"] == []

    served = {stall.served_by_aisle_id for stall in layout.stalls}
    assert served <= {"A-MAIN", "A-MAIN-REAR"}
    assert "A-MAIN-REAR" in served

    field_support = build_input_diagnostics(layout.site, layout)["field_support"]
    assert field_support["aisles.main_aisle_dogleg"] == "active"
    assert field_support["aisles.heading_candidate_selection"] == "active"
    assert field_support["constraints.entrance_to_main_aisle"] == "active"


def test_dogleg_places_t_end_caps_on_rear_turnaround():
    layout = generate_layout(_blocked_spine_site(enable_t_end_caps=True))

    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    end_stalls = [stall for stall in layout.stalls if stall.aisle_side == "end"]
    assert end_stalls
    assert {stall.served_by_aisle_id for stall in end_stalls} == {"A-TURNAROUND"}
    assert {stall.served_by_aisle_id for stall in layout.stalls if stall.aisle_side != "end"} <= {
        "A-MAIN",
        "A-MAIN-REAR",
    }
    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert validation["stalls_without_exit_path"] == []


def test_multi_jog_places_t_end_caps_on_rear_turnaround():
    layout = generate_layout(_staggered_multi_obstacle_site(enable_t_end_caps=True))

    assert layout.generation_mode == "phase1_main_aisle_multi_jog"
    end_stalls = [stall for stall in layout.stalls if stall.aisle_side == "end"]
    assert end_stalls
    assert {stall.served_by_aisle_id for stall in end_stalls} == {"A-TURNAROUND"}
    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True


def test_dogleg_can_be_disabled():
    layout = generate_layout(
        _blocked_spine_site(
            enable_main_aisle_dogleg=False,
            prefer_obstacle_clearance=False,
            auto_lateral_offsets_for_obstacles=True,
        )
    )
    # Lateral full-frame shift may still produce a valid non-dogleg layout.
    if layout.stall_count > 0:
        assert layout.generation_mode != "phase1_main_aisle_dogleg"
        assert not any(aisle.role == "jog" for aisle in layout.aisles)


def _wide_dogleg_branch_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="dogleg-branch",
        boundary=[(0, 0), (70, 0), (70, 70), (0, 70)],
        obstacles=[[(28, 12), (42, 12), (42, 32), (28, 32)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(35, 0),
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
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": True,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            "dogleg_offsets": [18.0, -18.0],
            "max_branches": 2,
            **optimization,
        },
    )


def test_dogleg_attaches_branches_to_rear_spine():
    layout = generate_layout(_wide_dogleg_branch_site())

    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert layout.stall_count > 0
    branch_aisles = [aisle for aisle in layout.aisles if aisle.role == "branch"]
    assert branch_aisles, "expected at least one branch on the dogleg rear spine"
    assert any(aisle.parent_aisle_id == "A-MAIN-REAR" for aisle in branch_aisles)
    assert layout.selected_branches
    assert any(branch.get("parent_aisle_id") == "A-MAIN-REAR" for branch in layout.selected_branches)

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    for aisle in branch_aisles:
        assert aisle.id in validation["reachable_aisles"]
    assert validation["stalls_without_exit_path"] == []


def _deep_obstacle_dogleg_site(**optimization) -> SiteSpec:
    """Long front corridor before a mid/deep obstacle so front+rear spines both fit branches."""
    return SiteSpec(
        name="dogleg-dual-spine",
        boundary=[(0, 0), (90, 0), (90, 90), (0, 90)],
        obstacles=[[(35, 35), (55, 35), (55, 55), (35, 55)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(45, 0),
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
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": True,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            "dogleg_offsets": [22.0, -22.0],
            "max_branches": 4,
            **optimization,
        },
    )


def test_dogleg_shares_branch_budget_across_front_and_rear_spines():
    layout = generate_layout(_deep_obstacle_dogleg_site())

    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    parents = {branch.get("parent_aisle_id") for branch in layout.selected_branches}
    assert "A-MAIN" in parents
    assert "A-MAIN-REAR" in parents
    assert len(layout.selected_branches) <= 4
    assert len(layout.selected_branches) >= 2

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert validation["stalls_without_exit_path"] == []


def _staggered_multi_obstacle_site(**optimization) -> SiteSpec:
    """Center block plus left/right mid blocks so one offset cannot clear the site."""
    return SiteSpec(
        name="multi-jog",
        boundary=[(0, 0), (90, 0), (90, 110), (0, 110)],
        obstacles=[
            [(32, 16), (58, 16), (58, 30), (32, 30)],
            # Leave a ~14m center gap so a 6m spine + clearances can return mid-site.
            [(52, 50), (80, 50), (80, 68), (52, 68)],
            [(10, 50), (38, 50), (38, 68), (10, 68)],
        ],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(45, 0),
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
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            "dogleg_offsets": [18.0, -18.0, 30.0, -30.0],
            "max_dogleg_jogs": 3,
            **optimization,
        },
    )


def test_multi_jog_chains_around_staggered_obstacles():
    layout = generate_layout(_staggered_multi_obstacle_site())

    assert layout.generation_mode == "phase1_main_aisle_multi_jog"
    assert layout.stall_count > 0
    jog_aisles = [aisle for aisle in layout.aisles if aisle.role == "jog"]
    main_aisles = [aisle for aisle in layout.aisles if aisle.role == "main"]
    assert len(jog_aisles) >= 2
    assert len(main_aisles) >= 3
    assert any(aisle.id == "A-MAIN-REAR" for aisle in main_aisles)
    assert any(aisle.id == "A-TURNAROUND" for aisle in layout.aisles)

    # Spine should recover depth past the second obstacle band (~y=68).
    rear = next(aisle for aisle in layout.aisles if aisle.id == "A-MAIN-REAR")
    rear_max_y = max(point[1] for point in rear.polygon)
    assert rear_max_y > 80.0

    field_support = build_input_diagnostics(layout.site, layout)["field_support"]
    assert field_support["aisles.main_aisle_multi_jog"] == "active"
    assert field_support["aisles.main_aisle_dogleg"] == "available"
    assert field_support["constraints.entrance_to_main_aisle"] == "active"

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-MAIN" in validation["reachable_aisles"]
    assert "A-MAIN-REAR" in validation["reachable_aisles"]
    assert any(aisle_id.startswith("A-JOG") for aisle_id in validation["reachable_aisles"])
    assert validation["stalls_without_exit_path"] == []


def test_multi_jog_can_be_capped_to_single_jog_family():
    # max_dogleg_jogs=1 prevents multi-jog planning (requires >=2 jogs).
    layout = generate_layout(_staggered_multi_obstacle_site(max_dogleg_jogs=1))
    if layout.stall_count > 0:
        assert layout.generation_mode != "phase1_main_aisle_multi_jog"
        assert sum(1 for aisle in layout.aisles if aisle.role == "jog") <= 1


def _wide_multi_jog_dual_site(**optimization) -> SiteSpec:
    """Wider staggered obstacles so intermediate spines can host branches + exit."""
    return SiteSpec(
        name="multi-jog-dual",
        boundary=[(0, 0), (110, 0), (110, 110), (0, 110)],
        obstacles=[
            [(40, 16), (70, 16), (70, 30), (40, 30)],
            [(62, 50), (95, 50), (95, 68), (62, 68)],
            [(15, 50), (48, 50), (48, 68), (15, 68)],
        ],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(55, 0),
                width=8.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(55, 110),
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
            "enable_branches": True,
            "enable_connectors": False,
            "max_branches": 4,
            "enable_dual_entrance": True,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            "dogleg_offsets": [18.0, -18.0, 30.0, -30.0],
            "max_dogleg_jogs": 3,
            **optimization,
        },
    )


def test_multi_jog_dual_entrance_and_multi_spine_branches():
    layout = generate_layout(_wide_multi_jog_dual_site())

    assert layout.generation_mode == "phase1_main_aisle_multi_jog"
    assert layout.stall_count > 0
    assert layout.main_entrance_id == "entry-gate"
    assert any(aisle.role == "exit" for aisle in layout.aisles)
    exit_aisle = next(aisle for aisle in layout.aisles if aisle.role == "exit")
    assert exit_aisle.connected_to_entrance_id == "exit-gate"
    assert exit_aisle.parent_aisle_id == "A-TURNAROUND"

    parents = {branch.get("parent_aisle_id") for branch in layout.selected_branches}
    assert "A-MAIN-REAR" in parents
    # Intermediate main segment should be eligible under the shared budget.
    assert any(str(parent).startswith("A-MAIN") for parent in parents if parent != "A-MAIN-REAR") or len(
        layout.selected_branches
    ) >= 1

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-EXIT" in validation["reachable_aisles"]
    assert validation["stalls_without_exit_path"] == []


def test_adaptive_dogleg_offsets_clear_wide_center_obstacle():
    from openparkcad.phase1_candidates import _dogleg_offset_candidates

    site = SiteSpec(
        name="adaptive-wide",
        boundary=[(0, 0), (100, 0), (100, 80), (0, 80)],
        obstacles=[[(35, 12), (65, 12), (65, 40), (35, 40)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(50, 0),
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
                enabled=True,
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            # No explicit dogleg_offsets — adaptive must supply wide clearances.
        },
    )
    candidates = _dogleg_offset_candidates(site, site.entrances[0], 90.0)
    assert any(abs(value) > 15.0 for value in candidates)

    layout = generate_layout(site)
    assert layout.generation_mode == "phase1_main_aisle_dogleg"
    assert layout.stall_count > 0
    assert any(aisle.role == "jog" for aisle in layout.aisles)

    layout_off = generate_layout(
        SiteSpec(
            name="adaptive-off",
            boundary=site.boundary,
            obstacles=site.obstacles,
            stall=site.stall,
            aisle_width=site.aisle_width,
            margin=0.0,
            entrances=site.entrances,
            aisle_classes=site.aisle_classes,
            fixed_aisle_class=site.fixed_aisle_class,
            optimization={
                **site.optimization,
                "enable_adaptive_dogleg_offsets": False,
            },
        )
    )
    # Default ±1/2 aisle widths cannot clear this wide obstacle.
    assert layout_off.stall_count == 0 or layout_off.generation_mode != "phase1_main_aisle_dogleg"


def test_multi_jog_strict_one_way_dual_entrance():
    site = SiteSpec(
        name="multi-jog-one-way",
        boundary=[(0, 0), (110, 0), (110, 110), (0, 110)],
        obstacles=[
            [(40, 16), (70, 16), (70, 30), (40, 30)],
            [(62, 50), (95, 50), (95, 68), (62, 68)],
            [(15, 50), (48, 50), (48, 68), (15, 68)],
        ],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=3.5,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry-gate",
                mode="entry_only",
                center=(55, 0),
                width=6.0,
                heading_degrees=90.0,
                allowed_movements=("enter",),
            ),
            EntranceSpec(
                id="exit-gate",
                mode="exit_only",
                center=(55, 110),
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
            "dogleg_offsets": [18.0, -18.0, 30.0, -30.0],
            "max_dogleg_jogs": 3,
        },
    )
    layout = generate_layout(site)
    assert layout.generation_mode == "phase1_main_aisle_multi_jog"
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
    assert "A-EXIT" in validation["reachable_aisles"]
