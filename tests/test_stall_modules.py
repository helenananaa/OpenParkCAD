"""Discrete stall-module candidates: one strip per (aisle, side)."""

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.candidate_catalog import (
    DEFAULT_STALL_MODULE_SEGMENT_STALLS,
    STALL_MODULE_KIND,
    catalog_class,
    stall_module_segment_stalls,
)
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.layout_geometry import available_area
from openparkcad.models import AisleClassSpec, CandidateObject, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_candidates import ConnectorGeometry, place_connector_family_stalls


def _phase1_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="stall-modules",
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_t_end_caps": False, **optimization},
    )


def test_catalog_class_treats_stall_modules_as_variables():
    assert catalog_class(kind=STALL_MODULE_KIND, role="stall_module") == "variable"


def test_layout_catalogues_main_aisle_side_modules():
    layout = generate_layout(_phase1_site())
    modules = [item for item in layout.candidate_objects if item.kind == STALL_MODULE_KIND]
    sides = {item.metadata.get("aisle_side") for item in modules if item.metadata.get("parent_is_base")}
    assert {"left", "right"} <= sides
    selected_modules = set(layout.candidate_selection.get("selected_module_ids", []))
    assert selected_modules
    assert selected_modules <= set(layout.candidate_selection["selected_ids"])
    assert layout.candidate_selection["selected_module_count"] == len(selected_modules)
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["optimization.stall_modules"] == "active"
    assert diagnostics["field_support"]["optimization.stall_module_segment_stalls"] == "active"


def test_stall_module_segment_stalls_defaults_to_four_and_zero_keeps_whole_strip():
    assert DEFAULT_STALL_MODULE_SEGMENT_STALLS == 4
    assert stall_module_segment_stalls(None) == 4
    assert stall_module_segment_stalls({}) == 4
    assert stall_module_segment_stalls({"stall_module_segment_stalls": 0}) == 0
    assert stall_module_segment_stalls({"stall_module_segment_stalls": 2}) == 2
    assert stall_module_segment_stalls({"stall_module_segment_stalls": "not-a-number"}) == 0


def test_stall_modules_split_a_side_when_segment_stalls_is_set():
    whole = generate_layout(_phase1_site(stall_module_segment_stalls=0))
    split = generate_layout(_phase1_site(stall_module_segment_stalls=2))
    defaulted = generate_layout(_phase1_site())
    whole_left = [
        item
        for item in whole.candidate_objects
        if item.kind == STALL_MODULE_KIND
        and item.metadata.get("parent_is_base")
        and item.metadata.get("aisle_side") == "left"
        and not item.metadata.get("shadow_family")
    ]
    split_left = [
        item
        for item in split.candidate_objects
        if item.kind == STALL_MODULE_KIND
        and item.metadata.get("parent_is_base")
        and item.metadata.get("aisle_side") == "left"
        and not item.metadata.get("shadow_family")
    ]
    default_left = [
        item
        for item in defaulted.candidate_objects
        if item.kind == STALL_MODULE_KIND
        and item.metadata.get("parent_is_base")
        and item.metadata.get("aisle_side") == "left"
        and not item.metadata.get("shadow_family")
    ]
    assert len(whole_left) == 1
    assert len(split_left) >= 2
    assert len(default_left) >= 2
    assert all("-seg" in item.id for item in split_left)
    assert all("-seg" in item.id for item in default_left)
    selected = [item for item in split_left if item.id in set(split.candidate_selection["selected_ids"])]
    assert len(selected) >= 2
    assert len({item.metadata.get("family_slot") for item in selected}) == len(selected)
    diagnostics = build_input_diagnostics(split.site, split)
    assert diagnostics["field_support"]["optimization.stall_module_segment_stalls"] == "active"
    whole_diagnostics = build_input_diagnostics(whole.site, whole)
    assert whole_diagnostics["field_support"]["optimization.stall_module_segment_stalls"] == "available"


def test_module_without_parent_branch_is_rejected():
    objects = [
        CandidateObject(
            id="C-BRANCH-1",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(0, 0), (6, 0), (6, 20), (0, 20)],
            score_features={"stall_count": 4.0, "base_stall_count": 0.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-MODULE-C-BRANCH-1-left",
            kind=STALL_MODULE_KIND,
            role="stall_module",
            status="rejected",
            geometry=[(6, 0), (11, 0), (11, 20), (6, 20)],
            parent_ids=("C-BRANCH-1",),
            score_features={"stall_count": 4.0, "area": 100.0},
            metadata={
                "parent_is_base": False,
                "parent_candidate_id": "C-BRANCH-MISSING",
                "aisle_side": "left",
                "served_by_aisle_id": "A-BRANCH-001",
                "stall_source_ids": ["P-001"],
            },
        ),
    ]
    selection = select_candidate_objects(objects, _phase1_site(max_branches=0))
    assert "C-MODULE-C-BRANCH-1-left" not in selection["selected_ids"]
    assert any(item["reason"] == "module_parent_not_selected" for item in selection["rejected"])


def test_family_slots_are_mutually_exclusive():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    site = _phase1_site()
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=perpendicular,
        stall_candidates=(perpendicular, angled),
        aisle_width=site.aisle_width,
        margin=site.margin,
        entrances=site.entrances,
        aisle_classes=site.aisle_classes,
        fixed_aisle_class=site.fixed_aisle_class,
        optimization=site.optimization,
    )
    layout = generate_layout(site)
    modules = [item for item in layout.candidate_objects if item.kind == STALL_MODULE_KIND and item.metadata.get("parent_is_base")]
    families = {item.metadata.get("stall_family") for item in modules}
    assert {"perpendicular", "angled"} <= families
    selected = [
        item
        for item in modules
        if item.id in set(layout.candidate_selection["selected_ids"])
    ]
    slots = [item.metadata.get("family_slot") for item in selected]
    assert slots
    assert len(slots) == len(set(slots))
    assert any(item["reason"] == "module_family_slot_taken" for item in layout.candidate_selection["rejected"])


def test_branch_family_slots_are_mutually_exclusive():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    site = SiteSpec(
        name="branch-family-modules",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=perpendicular,
        stall_candidates=(perpendicular, angled),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(43, 0),
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
            "branch_start_positions": [24, 42, 60],
            "branch_sides": ["left"],
            "max_branches": 1,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )
    layout = generate_layout(site)
    branch_modules = [
        item
        for item in layout.candidate_objects
        if item.kind == STALL_MODULE_KIND and item.metadata.get("parent_is_base") is False
    ]
    families = {item.metadata.get("stall_family") for item in branch_modules}
    assert {"perpendicular", "angled"} <= families
    selected = [item for item in branch_modules if item.id in set(layout.candidate_selection["selected_ids"])]
    slots = [item.metadata.get("family_slot") for item in selected]
    assert len(slots) == len(set(slots))
    assert any(
        item["reason"] == "module_family_slot_taken" and str(item["id"]).startswith("C-MODULE-")
        for item in layout.candidate_selection["rejected"]
    )


def test_connector_family_slots_are_mutually_exclusive():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    parallel = StallSpec(id="parallel", width=2.5, length=6.0, family="parallel", allowed_angles=(0.0,))
    site = SiteSpec(
        name="connector-family-modules",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=perpendicular,
        stall_candidates=(perpendicular, parallel),
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
            "branch_start_positions": [18, 32, 46],
            "branch_sides": ["left"],
            "max_branches": 2,
            "enable_connectors": True,
            "connector_throat_length": 3.0,
            "connector_inset_depths": [0, 5.0],
            "enable_t_end_caps": False,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": -20,
                "branch_count": 0,
            },
        },
    )
    layout = generate_layout(site)
    assert layout.selected_connectors
    connector_modules = [
        item
        for item in layout.candidate_objects
        if item.kind == STALL_MODULE_KIND
        and item.metadata.get("parent_is_base") is False
        and str(item.metadata.get("served_by_aisle_id") or "").startswith("A-CONNECTOR")
    ]
    families = {item.metadata.get("stall_family") for item in connector_modules}
    assert "perpendicular" in families
    assert "parallel" in families
    selected = [item for item in connector_modules if item.id in set(layout.candidate_selection["selected_ids"])]
    slots = [item.metadata.get("family_slot") for item in selected]
    assert len(slots) == len(set(slots))
    assert any(
        item["reason"] == "module_family_slot_taken" and "CONNECTOR" in str(item["id"]).upper()
        for item in layout.candidate_selection["rejected"]
    )


def _connector_place_site() -> SiteSpec:
    return SiteSpec(
        name="connector-place",
        boundary=[(0, 0), (80, 0), (80, 80), (0, 80)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(40, 0), width=8.0, heading_degrees=90.0),
        ],
    )


def test_place_connector_family_stalls_on_opposite_cross_and_end_loop():
    site = _connector_place_site()
    entrance = site.entrances[0]
    available = available_area(site)
    occupied = ShapelyPolygon()
    perp = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    parallel = StallSpec(id="parallel", width=2.5, length=6.0, family="parallel", allowed_angles=(0.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    cross = ConnectorGeometry(
        polygon=[(20, 20), (26, 20), (26, 60), (20, 60)],
        u_min=20.0,
        u_max=26.0,
        center_v=0.0,
        side="left",
        inset_depth=0.0,
        pattern="opposite_cross",
        v_min=-22.0,
        v_max=22.0,
    )
    loop = ConnectorGeometry(
        polygon=[(50, 10), (70, 10), (70, 70), (50, 70)],
        u_min=50.0,
        u_max=70.0,
        center_v=0.0,
        side="left",
        inset_depth=0.0,
        pattern="opposite_end_loop",
        v_min=-24.0,
        v_max=24.0,
    )
    for geometry in (cross, loop):
        placed = {
            spec.family: place_connector_family_stalls(
                site, spec, available, entrance, 90.0, geometry, "A-CONNECTOR-001", occupied
            )
            for spec in (perp, parallel, angled)
        }
        assert placed["perpendicular"]
        assert placed["parallel"]
        assert placed["angled"]
        assert {stall.stall_type_id for stall in placed["parallel"]} == {"parallel"}
        assert {stall.stall_type_id for stall in placed["angled"]} == {"angled-60"}


def test_preview_stalls_come_from_selected_modules():
    layout = generate_layout(_phase1_site())
    selected_modules = [
        item
        for item in layout.candidate_objects
        if item.kind == STALL_MODULE_KIND and item.id in set(layout.candidate_selection["selected_ids"])
    ]
    allowed = {stall_id for module in selected_modules for stall_id in module.metadata.get("stall_source_ids", [])}
    preview = layout.candidate_layout_preview
    official_preview = [stall for stall in preview["stalls"] if stall.get("source") == "current_layout"]
    assert official_preview
    assert {stall["source_id"] for stall in official_preview} <= allowed
