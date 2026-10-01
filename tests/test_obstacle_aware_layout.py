"""Obstacle-aware lateral offsets and branch clearance preference."""

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_candidates import _aisle_lateral_offsets
from openparkcad.scoring import score_layout


def _site_with_center_obstacle(**optimization) -> SiteSpec:
    # Obstacle sits on the entry centerline; lateral offsets must swing the aisle aside.
    return SiteSpec(
        name="obstacle-aware",
        boundary=[(0, 0), (48, 0), (48, 48), (0, 48)],
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
            "enable_branches": True,
            "max_branches": 2,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            **optimization,
        },
    )


def test_auto_lateral_offsets_when_prefer_obstacle_clearance():
    site = _site_with_center_obstacle(prefer_obstacle_clearance=True)
    offsets = _aisle_lateral_offsets(site)
    assert 0.0 in offsets
    assert any(abs(item) > 0 for item in offsets)


def test_auto_lateral_offsets_explicit_enable_without_prefer_flag():
    site = _site_with_center_obstacle(auto_lateral_offsets_for_obstacles=True)
    offsets = _aisle_lateral_offsets(site)
    assert any(abs(item) > 0 for item in offsets)


def test_auto_lateral_offsets_off_by_default_without_prefer_flag():
    site = _site_with_center_obstacle()
    assert _aisle_lateral_offsets(site) == (0.0,)


def test_obstacle_site_generates_valid_layout_with_auto_offsets():
    layout = generate_layout(
        _site_with_center_obstacle(
            prefer_obstacle_clearance=True,
            auto_lateral_offsets_for_obstacles=True,
            # Isolate lateral-offset path from the dogleg candidate family.
            enable_main_aisle_dogleg=False,
        )
    )
    assert layout.stall_count > 0
    assert layout.graph_validation.get("valid") is True
    score = score_layout(layout)
    assert "obstacle_clearance" in score
    assert score["obstacle_clearance"] >= 0.0
    assert score["obstacle_clearance_value"] > 0.0

    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["aisles.main_aisle_lateral_offsets"] in {"active", "available"}
