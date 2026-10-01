"""Coverage for opposite-side connectors and main-aisle lateral offsets."""

from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, LayoutResult, SiteSpec, StallSpec
from openparkcad.phase1_candidates import (
    _connector_geometry,
    _connector_pairs,
    _opposite_connector_pairs,
    _aisle_lateral_offsets,
)


def _wide_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="network-expansion",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "max_branches": 4,
            "enable_connectors": True,
            "enable_opposite_connectors": True,
            "enable_t_end_caps": False,
            **optimization,
        },
    )


def test_opposite_connector_pairs_match_aligned_left_right_branches():
    layout = LayoutResult(
        site=_wide_site(),
        stalls=[],
        selected_branches=[
            {"id": "A-BRANCH-001", "side": "left", "start_u": 20.0, "length": 18.0},
            {"id": "A-BRANCH-002", "side": "right", "start_u": 21.0, "length": 17.0},
            {"id": "A-BRANCH-003", "side": "left", "start_u": 40.0, "length": 16.0},
        ],
    )

    pairs = _opposite_connector_pairs(layout)

    assert len(pairs) >= 1
    assert any(
        pair[0]["side"] == "left"
        and pair[1]["side"] == "right"
        and abs(float(pair[0]["start_u"]) - float(pair[1]["start_u"])) <= 6.0
        for pair in pairs
    )
    # Aligned pairs plus optional best end-loop pair.
    assert len(pairs) <= 2


def test_opposite_connector_geometry_builds_end_loop_when_enabled():
    site = _wide_site(enable_opposite_end_loops=True, enable_opposite_connectors=True)
    entrance = site.entrances[0]
    geometry = _connector_geometry(
        site,
        entrance,
        90.0,
        {"id": "A-BRANCH-001", "side": "left", "start_u": 24.0, "length": 22.0},
        {"id": "A-BRANCH-002", "side": "right", "start_u": 36.0, "length": 20.0},
        inset_depth=0.0,
        main_aisle_length=50.0,
    )

    assert geometry is not None
    assert geometry.pattern == "opposite_end_loop"
    assert geometry.side == "opposite"
    assert geometry.v_max > 0
    assert geometry.v_min < 0
    # End cross sits beyond the farther branch station.
    assert geometry.u_max > max(24.0, 36.0)


def test_opposite_connector_geometry_falls_back_to_short_cross():
    site = _wide_site(enable_opposite_end_loops=False, enable_opposite_connectors=True)
    entrance = site.entrances[0]
    geometry = _connector_geometry(
        site,
        entrance,
        90.0,
        {"id": "A-BRANCH-001", "side": "left", "start_u": 24.0, "length": 18.0},
        {"id": "A-BRANCH-002", "side": "right", "start_u": 24.0, "length": 18.0},
        inset_depth=0.0,
    )

    assert geometry is not None
    assert geometry.pattern == "opposite_cross"
    assert geometry.v_max <= site.aisle_width / 2 + site.aisle_width + 1e-6


def test_same_side_connector_pairs_still_found():
    layout = LayoutResult(
        site=_wide_site(),
        stalls=[],
        selected_branches=[
            {"id": "A-BRANCH-001", "side": "left", "start_u": 18.0, "length": 18.0},
            {"id": "A-BRANCH-002", "side": "left", "start_u": 36.0, "length": 18.0},
            {"id": "A-BRANCH-003", "side": "right", "start_u": 20.0, "length": 16.0},
        ],
    )

    pairs = _connector_pairs(layout)
    same_side = [pair for pair in pairs if pair[0]["side"] == pair[1]["side"]]
    opposite = [pair for pair in pairs if pair[0]["side"] != pair[1]["side"]]

    assert same_side
    assert opposite


def test_generate_layout_can_select_opposite_or_same_side_connectors():
    layout = generate_layout(_wide_site())

    assert layout.stall_count > 0
    assert layout.selected_branches
    # Large site should produce either multi-branch loop connectors or at least dual-side branches.
    sides = {branch["side"] for branch in layout.selected_branches}
    assert sides <= {"left", "right"}
    if layout.selected_connectors:
        assert all("pattern" in item for item in layout.selected_connectors)
        assert {item.get("pattern") for item in layout.selected_connectors} <= {
            "same_side_u",
            "opposite_cross",
            "opposite_end_loop",
        }


def test_aisle_lateral_offsets_default_and_enabled():
    site = _wide_site()
    assert _aisle_lateral_offsets(site) == (0.0,)

    enabled = _wide_site(enable_main_aisle_lateral_offsets=True)
    offsets = _aisle_lateral_offsets(enabled)
    assert 0.0 in offsets
    assert any(abs(item) > 0 for item in offsets)

    explicit = _wide_site(main_aisle_lateral_offsets=[-2.0, 0.0, 2.0])
    assert _aisle_lateral_offsets(explicit) == (-2.0, 0.0, 2.0)


def test_lateral_offset_candidates_still_produce_valid_layout():
    layout = generate_layout(
        _wide_site(
            enable_main_aisle_lateral_offsets=True,
            enable_branches=True,
            max_branches=2,
        )
    )

    assert layout.stall_count > 0
    assert layout.maneuver_validation.get("valid") is True
    assert layout.graph_validation.get("valid") is True
    assert any(aisle.role == "main" for aisle in layout.aisles)


def test_obstacle_clearance_score_prefers_farther_layout_when_enabled():
    from openparkcad.scoring import score_layout

    near = generate_layout(
        SiteSpec(
            name="near-obstacle",
            boundary=[(0, 0), (50, 0), (50, 40), (0, 40)],
            obstacles=[[(20, 10), (28, 10), (28, 18), (20, 18)]],
            stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
            aisle_width=6.0,
            margin=0.0,
            entrances=[EntranceSpec(id="main", mode="shared", center=(10, 0), width=8.0, heading_degrees=90.0)],
            aisle_classes=[
                AisleClassSpec(id="w", width=6.0, capacity="two_vehicle", directionality="two_way")
            ],
            fixed_aisle_class="w",
            optimization={
                "heading_deltas_degrees": [0],
                "entrance_offsets": [0],
                "enable_branches": False,
                "prefer_obstacle_clearance": True,
            },
        )
    )
    far = generate_layout(
        SiteSpec(
            name="far-obstacle",
            boundary=[(0, 0), (50, 0), (50, 40), (0, 40)],
            obstacles=[[(40, 30), (48, 30), (48, 38), (40, 38)]],
            stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
            aisle_width=6.0,
            margin=0.0,
            entrances=[EntranceSpec(id="main", mode="shared", center=(10, 0), width=8.0, heading_degrees=90.0)],
            aisle_classes=[
                AisleClassSpec(id="w", width=6.0, capacity="two_vehicle", directionality="two_way")
            ],
            fixed_aisle_class="w",
            optimization={
                "heading_deltas_degrees": [0],
                "entrance_offsets": [0],
                "enable_branches": False,
                "prefer_obstacle_clearance": True,
            },
        )
    )

    assert near.stall_count > 0 and far.stall_count > 0
    near_score = score_layout(near)
    far_score = score_layout(far)
    assert far_score["obstacle_clearance"] >= near_score["obstacle_clearance"]
    # With the preference enabled, farther geometry should not lose on the clearance term.
    assert far_score["obstacle_clearance_value"] >= near_score["obstacle_clearance_value"]
