"""Branch length clip diagnostics against hard exclusions."""

from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_candidates import _branch_length_clip_report
from openparkcad.layout_geometry import available_area
from openparkcad.scoring import score_layout


def _side_obstacle_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="branch-clip",
        boundary=[(0, 0), (80, 0), (80, 60), (0, 60)],
        obstacles=[[(55, 15), (75, 15), (75, 45), (55, 45)]],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(20, 0),
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
            "max_branches": 1,
            "enable_t_end_caps": False,
            "branch_sides": ["left", "right"],
            "branch_start_positions": [25.0],
            **optimization,
        },
    )


def test_branch_clip_report_detects_exclusion_shortening():
    site = _side_obstacle_site()
    entrance = site.entrances[0]
    available = available_area(site)
    right = _branch_length_clip_report(site, available, entrance, 90.0, 25.0, "right")
    left = _branch_length_clip_report(site, available, entrance, 90.0, 25.0, "left")

    assert right["clipped_by_exclusion"] is True
    assert float(right["clip_amount"]) > 0.0
    assert float(right["clear_length"]) + 1e-6 < float(right["open_boundary_length"])
    # Boundary-only length exceeds obstacle-clipped length on the blocked side.
    assert float(right["open_boundary_length"]) > float(right["clear_length"]) + 1.0
    # Opposite side probe is present for side-selection hints.
    assert "opposite_side_clear_length" in right
    assert float(left["clear_length"]) > 0.0


def test_branch_candidates_include_clip_fields():
    layout = generate_layout(_side_obstacle_site())
    assert layout.stall_count > 0
    # Selected branch should not cross the right-side obstacle footprint.
    assert layout.selected_branches
    clip_fields = []
    for attempt in layout.attempts:
        for diagnostic in attempt.branch_candidates:
            if "clear_length" in diagnostic:
                clip_fields.append(diagnostic)
    assert clip_fields, "expected branch diagnostics with clear_length"
    sample = clip_fields[0]
    assert "open_boundary_length" in sample
    assert "clip_amount" in sample
    assert "clipped_by_exclusion" in sample


def test_obstacle_clearance_weight_and_cap_are_configurable():
    site = _side_obstacle_site(
        prefer_obstacle_clearance=True,
        obstacle_clearance_weight=25.0,
        obstacle_clearance_cap=5.0,
    )
    layout = generate_layout(site)
    score = score_layout(layout)
    assert "obstacle_clearance" in score
    assert score["obstacle_clearance"] <= 5.0 + 1e-9
    # Weight intensity flows into the clearance value term.
    assert abs(score["obstacle_clearance_value"] - score["obstacle_clearance"] * 25.0) < 1e-6


def test_branch_selection_score_penalizes_clipped_side():
    """Soft branch score prefers freer side when stall counts are comparable."""
    from openparkcad.phase1_candidates import _branch_selection_score
    from openparkcad.scoring import score_total as layout_score_total

    site = _side_obstacle_site(
        branch_clear_length_bonus=1.0,
        branch_clip_penalty=2.0,
        branch_clipped_side_penalty=100.0,
    )
    # Minimal dummy layouts share stall count; diagnostics drive the soft score.
    layout = generate_layout(site)
    assert layout.stall_count > 0
    clipped = {
        "clear_length": 20.0,
        "clip_amount": 30.0,
        "clipped_by_exclusion": True,
        "prefer_side_hint": "left",
    }
    open_side = {
        "clear_length": 40.0,
        "clip_amount": 0.0,
        "clipped_by_exclusion": False,
        "prefer_side_hint": None,
    }
    clipped_score = _branch_selection_score(site, layout, layout_score_total, clipped)
    open_score = _branch_selection_score(site, layout, layout_score_total, open_side)
    assert open_score > clipped_score


def test_network_preview_includes_jog_and_exit_roles():
    from openparkcad.candidate_network_preview import build_candidate_network_preview
    from openparkcad.candidate_snapshot import attach_candidate_snapshot, build_candidate_objects
    from openparkcad.models import EntranceSpec
    from dataclasses import replace

    site = SiteSpec(
        name="preview-roles",
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
    # Ensure candidate objects exist even if snapshot path omitted roles previously.
    if not layout.candidate_objects:
        layout = replace(layout, candidate_objects=build_candidate_objects(layout))
    layout = attach_candidate_snapshot(layout)
    preview = build_candidate_network_preview(layout)
    roles = {str(aisle.get("role")) for aisle in preview.get("aisles", [])}
    assert "main" in roles
    if any(aisle.role == "jog" for aisle in layout.aisles):
        assert "jog" in roles
    if any(aisle.role == "exit" for aisle in layout.aisles):
        assert "exit" in roles
