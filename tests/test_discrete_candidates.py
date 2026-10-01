"""v0.4 discrete candidate catalog: classes, parents, greedy provenance."""

import json
from pathlib import Path

from openparkcad.candidate_catalog import (
    SNAPSHOT_VERSION,
    catalog_class,
    requested_selector_backend,
    selection_class,
)
from openparkcad.candidate_snapshot import candidate_snapshot_report
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec, site_from_dict


def _phase1_site() -> SiteSpec:
    return SiteSpec(
        name="catalog-phase1",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_t_end_caps": False},
    )


def test_catalog_class_splits_official_aisles_from_skeletons():
    assert catalog_class(kind="aisle", role="jog") == "base"
    assert catalog_class(kind="aisle", role="branch") == "base"
    assert catalog_class(kind="aisle_skeleton", role="branch") == "variable"
    assert catalog_class(kind="aisle_skeleton", role="connector") == "variable"
    assert catalog_class(kind="aisle_skeleton", role="main") == "spine_attempt"
    assert catalog_class(kind="stall", role="stall") == "derived"


def test_requested_selector_backend_parses_cpsat_without_fallback_reason():
    assert requested_selector_backend(None) == ("greedy", None)
    assert requested_selector_backend("greedy") == ("greedy", None)
    assert requested_selector_backend("cpsat") == ("cpsat", None)
    assert requested_selector_backend("or-tools") == ("cpsat", None)
    assert requested_selector_backend("mystery") == ("greedy", "unknown_selector_backend")


def test_official_layout_catalogues_base_aisles_and_greedy_provenance():
    layout = generate_layout(_phase1_site())
    official_ids = {f"C-SELECTED-{aisle.id}" for aisle in layout.aisles}
    base_ids = set(layout.candidate_selection["base_selected_ids"])
    assert official_ids <= base_ids
    assert layout.candidate_selection["base_selected_count"] == len(base_ids)
    assert layout.candidate_selection["backend"] == "greedy"
    assert layout.candidate_selection["requested_backend"] == "greedy"
    assert layout.candidate_selection["backend_fallback_reason"] is None
    assert layout.candidate_selection["solver_provenance"]["status"] == "optimal_greedy"
    assert set(layout.candidate_selection["selected_ids"]).isdisjoint(official_ids)

    for aisle in layout.aisles:
        match = next(item for item in layout.candidate_objects if item.id == f"C-SELECTED-{aisle.id}")
        assert selection_class(match) == "base"
        assert match.parent_ids == tuple(item for item in (aisle.parent_aisle_id, aisle.connected_to_entrance_id) if item)

    report = candidate_snapshot_report(layout)
    assert report["version"] == SNAPSHOT_VERSION
    assert report["catalog_counts"]["base"] >= len(layout.aisles)
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["optimization.discrete_candidate_catalog"] == "active"
    assert diagnostics["candidate_snapshot"]["selection_backend"] == "greedy"


def test_dogleg_branch_skeletons_parent_the_rear_spine():
    site = SiteSpec(
        name="dogleg-branch-catalog",
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
        },
    )
    layout = generate_layout(site)
    roles = {aisle.role for aisle in layout.aisles}
    assert "jog" in roles
    jog = next(item for item in layout.candidate_objects if item.kind == "aisle" and item.role == "jog")
    assert selection_class(jog) == "base"
    assert jog.id in layout.candidate_selection["base_selected_ids"]
    assert any(aisle.parent_aisle_id == "A-MAIN-REAR" for aisle in layout.aisles if aisle.role == "branch")

    rear_branches = [
        item
        for item in layout.candidate_objects
        if item.kind == "aisle_skeleton"
        and item.role == "branch"
        and item.metadata.get("parent_aisle_id") == "A-MAIN-REAR"
    ]
    assert rear_branches
    assert all(item.parent_ids == ("A-MAIN-REAR",) for item in rear_branches)


def test_passing_bay_aisles_are_base_not_greedy_variables():
    data = json.loads(Path("examples/passing_bay_narrow_site.json").read_text(encoding="utf-8"))
    layout = generate_layout(site_from_dict(data))
    bay_ids = {f"C-SELECTED-{aisle.id}" for aisle in layout.aisles if aisle.role == "passing_bay"}
    assert bay_ids
    assert bay_ids <= set(layout.candidate_selection["base_selected_ids"])
    assert bay_ids.isdisjoint(set(layout.candidate_selection["selected_ids"]))
    for item in layout.candidate_objects:
        if item.kind == "aisle" and item.role == "passing_bay":
            assert selection_class(item) == "base"
            assert item.metadata.get("parent_aisle_id")
