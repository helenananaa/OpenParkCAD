from dataclasses import replace

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad import diagnostics
from openparkcad.candidate_catalog import NETWORK_PREVIEW_VERSION, PROMOTION_VERSION, SELECTOR_VERSION, SNAPSHOT_VERSION
from openparkcad.candidate_layout_preview import _promotion_blockers, candidate_layout_preview_report
from openparkcad.candidate_network_preview import candidate_network_preview_report
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.candidate_snapshot import candidate_snapshot_report
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.models import (
    AisleClassSpec,
    AngleAttempt,
    CandidateObject,
    EntranceSpec,
    LayoutResult,
    SiteSpec,
    StallSpec,
    VehicleSpec,
)
from openparkcad.phase1_support import phase1_unsupported_inputs


def _phase1_site() -> SiteSpec:
    return SiteSpec(
        name="phase1",
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
        source_format="phase0",
    )


def test_phase1_layout_uses_entrance_connected_main_aisle():
    layout = generate_layout(_phase1_site())

    assert layout.generation_mode == "phase1_main_aisle"
    assert layout.main_entrance_id == "main"
    assert [aisle.id for aisle in layout.aisles] == ["A-MAIN", "A-TURNAROUND"]
    assert layout.aisles[0].role == "main"
    assert layout.aisles[0].connected_to_entrance_id == "main"
    assert layout.aisles[1].parent_aisle_id == "A-MAIN"
    assert layout.stall_count > 0
    assert all(stall.angle_degrees == 90.0 for stall in layout.stalls)
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-MAIN"}
    assert {stall.aisle_side for stall in layout.stalls} <= {"left", "right"}


def test_phase4a_candidate_snapshot_reports_selected_and_evaluated_objects():
    layout = generate_layout(_phase1_site())

    assert layout.candidate_objects
    kinds = {candidate.kind for candidate in layout.candidate_objects}
    assert {"aisle_skeleton", "aisle", "stall"} <= kinds
    assert any(candidate.role == "main" and candidate.status == "selected" for candidate in layout.candidate_objects)
    assert any(candidate.kind == "stall" and candidate.geometry for candidate in layout.candidate_objects)

    report = candidate_snapshot_report(layout)
    assert report["version"] == SNAPSHOT_VERSION
    assert report["object_count"] == len(layout.candidate_objects)
    assert report["status_counts"]["selected"] > 0
    assert report["catalog_counts"]["base"] > 0
    assert "conflict_count" in report
    assert "conflict_matrix" in report
    assert all(set(item) == {"left_id", "right_id", "type", "overlap_area"} for item in report["conflict_matrix"])
    assert report["selection"]["version"] == SELECTOR_VERSION
    assert report["selection"]["status"] == "shadow_only"
    assert report["selection"]["backend"] == "greedy"
    selected_ids = set(report["selection"]["selected_ids"])
    for conflict in report["conflict_matrix"]:
        assert not {conflict["left_id"], conflict["right_id"]} <= selected_ids

    input_diagnostics = build_input_diagnostics(layout.site, layout)
    assert input_diagnostics["field_support"]["optimization.candidate_objects"] == "active"
    assert input_diagnostics["field_support"]["optimization.candidate_conflict_matrix"] == "active"
    assert input_diagnostics["field_support"]["optimization.shadow_candidate_selector"] == "active"
    assert input_diagnostics["field_support"]["optimization.candidate_network_preview"] == "active"
    assert input_diagnostics["field_support"]["optimization.candidate_layout_preview"] == "active"
    assert input_diagnostics["field_support"]["optimization.candidate_layout_preview_scoring"] == "active"
    assert input_diagnostics["field_support"]["optimization.promote_candidate_layout_preview"] == "available"
    assert input_diagnostics["field_support"]["diagnostics.svg_candidate_network_preview"] == "active"
    assert input_diagnostics["candidate_snapshot"]["version"] == SNAPSHOT_VERSION
    assert input_diagnostics["candidate_snapshot"]["selection_version"] == SELECTOR_VERSION
    assert input_diagnostics["field_support"]["optimization.discrete_candidate_catalog"] == "active"
    assert input_diagnostics["field_support"]["optimization.selector_backend"] == "active"
    assert input_diagnostics["field_support"]["optimization.selector_backend_cpsat"] == "available"
    assert "connector_candidate_count" in input_diagnostics["candidate_snapshot"]
    assert input_diagnostics["candidate_network_preview"]["version"] == NETWORK_PREVIEW_VERSION
    assert input_diagnostics["candidate_network_preview"]["validation_valid"] is True


def test_phase4b_shadow_selector_can_select_compatible_branch_candidates():
    site = SiteSpec(
        name="shadow-selector",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assert layout.candidate_selection["version"] == SELECTOR_VERSION
    assert layout.candidate_selection["selected_count"] > 0
    assert layout.candidate_selection["selected_branch_count"] <= site.optimization["max_branches"]
    assert layout.candidate_selection["max_branches"] == site.optimization["max_branches"]
    assert any(
        item["reason"] in {"exceeds_max_branches", "duplicate_branch_source_selected"}
        for item in layout.candidate_selection["rejected"]
    )
    preview = candidate_network_preview_report(layout)
    assert preview["version"] == NETWORK_PREVIEW_VERSION
    assert preview["status"] == "preview_only"
    assert preview["base_aisle_count"] >= 1
    aisle_selected_ids = [
        item_id
        for item_id in layout.candidate_selection["selected_ids"]
        if not str(item_id).startswith("C-MODULE-")
    ]
    assert preview["shadow_aisle_count"] == len(aisle_selected_ids)
    assert preview["valid_no_internal_conflicts"] is True
    assert preview["validation"]["version"] == NETWORK_PREVIEW_VERSION
    assert preview["validation"]["valid"] is True
    assert preview["validation"]["geometry_containment"]["valid"] is True
    assert preview["validation"]["traffic_graph"]["valid"] is True
    assert set(aisle_selected_ids) <= set(preview["selected_candidate_ids"])
    layout_preview = candidate_layout_preview_report(layout)
    assert layout_preview["version"] == "phase4c-2a"
    assert layout_preview["status"] == "preview_only"
    assert layout_preview["aisle_count"] == preview["aisle_count"]
    assert layout_preview["stall_count"] > 0
    assert layout_preview["score"]["stall_count"] == float(layout_preview["stall_count"])
    assert layout_preview["comparison"]["version"] == "phase4c-2a"
    assert layout_preview["comparison"]["current_layout"]["stall_count"] == layout.stall_count
    assert layout_preview["comparison"]["candidate_preview"]["stall_count"] == layout_preview["stall_count"]
    assert layout_preview["comparison"]["score_delta"] == layout_preview["score"]["total"] - layout.score["total"]
    assert "promotion_blockers" in layout_preview["comparison"]
    assert layout_preview["validation"]["version"] == "phase4c-1"
    assert layout_preview["validation"]["geometry_containment"]["valid"] is True
    assert layout_preview["validation"]["stall_association"]["valid"] is True
    assert layout_preview["validation"]["maneuver_validation"]["valid"] is True
    assert layout_preview["validation"]["traffic_graph"]["valid"] is True
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["optimization.candidate_shadow_branch_turnarounds"] == "active"
    selected_ids = set(layout.candidate_selection["selected_ids"])
    for candidate in layout.candidate_objects:
        if candidate.id not in selected_ids:
            continue
        assert not selected_ids.intersection(candidate.conflict_ids)


def test_operational_quality_blocker_can_stop_preview_promotion():
    validation = {
        "valid": True,
        "traffic_graph": {"dead_ends": []},
        "operational_quality": {
            "promotion_blockers": ["operational_quality_risk_exceeds_limit"],
        },
    }

    assert _promotion_blockers(validation, score_delta=10.0) == ["operational_quality_risk_exceeds_limit"]


def test_phase4c_shadow_selector_respects_connector_dependencies():
    site = SiteSpec(
        name="selector-connector-dependencies",
        boundary=[(0, 0), (40, 0), (40, 40), (0, 40)],
        optimization={"max_branches": 1},
    )
    objects = [
        CandidateObject(
            id="C-BRANCH-1",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(0, 0), (6, 0), (6, 20), (0, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-BRANCH-2",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(10, 0), (16, 0), (16, 20), (10, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-002"},
        ),
        CandidateObject(
            id="C-CONNECTOR-1",
            kind="aisle_skeleton",
            role="connector",
            status="rejected",
            geometry=[(0, 18), (16, 18), (16, 24), (0, 24)],
            score_features={"added_stalls": 8.0, "removed_stalls": 0.0},
            metadata={"source_id": "A-CONNECTOR-001", "connects": ["A-BRANCH-001", "A-BRANCH-002"]},
        ),
    ]

    selection = select_candidate_objects(objects, site)

    assert selection["selected_branch_count"] == 1
    assert selection["selected_connector_count"] == 0
    assert any(
        item["reason"] == "connector_dependency_not_selected"
        and item["missing_branch_source_ids"] == ["A-BRANCH-002"]
        for item in selection["rejected"]
    )


def test_phase4c_shadow_selector_can_select_loop_bundle():
    site = SiteSpec(
        name="selector-loop-bundle",
        boundary=[(0, 0), (40, 0), (40, 40), (0, 40)],
        optimization={"max_branches": 2},
    )
    objects = [
        CandidateObject(
            id="C-BRANCH-1",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(0, 0), (6, 0), (6, 20), (0, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-BRANCH-2",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(10, 0), (16, 0), (16, 20), (10, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-002"},
        ),
        CandidateObject(
            id="C-CONNECTOR-1",
            kind="aisle_skeleton",
            role="connector",
            status="rejected",
            geometry=[(0, 18), (16, 18), (16, 24), (0, 24)],
            score_features={"added_stalls": 1.0, "removed_stalls": 0.0},
            metadata={"source_id": "A-CONNECTOR-001", "connects": ["A-BRANCH-001", "A-BRANCH-002"]},
        ),
    ]

    selection = select_candidate_objects(objects, site)

    assert selection["version"] == SELECTOR_VERSION
    assert selection["eligible_bundle_count"] == 1
    assert selection["selected_bundle_count"] == 1
    assert selection["selected_branch_count"] == 2
    assert selection["selected_connector_count"] == 1
    assert selection["selected_ids"] == ["C-BRANCH-1", "C-BRANCH-2", "C-CONNECTOR-1"]
    assert selection["selected_bundles"][0]["type"] == "loop_bundle"
    assert selection["selected_bundles"][0]["connector_ids"] == ["C-CONNECTOR-1"]


def test_phase4c_network_preview_reports_connector_turnaround_suppression():
    site = SiteSpec(
        name="connector-preview-summary",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            "max_branches": 2,
            "enable_connectors": True,
            "connector_throat_length": 6.0,
            "connector_inset_depths": [0, 2.5, 5.0],
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)
    preview = candidate_network_preview_report(layout)

    assert preview["loop_connector_count"] == 1
    assert preview["suppressed_turnaround_count"] == 2
    assert preview["shadow_turnaround_count"] == 0
    assert preview["suppressed_turnaround_source_ids"] == [
        "A-BRANCH-001-TURNAROUND",
        "A-BRANCH-002-TURNAROUND",
    ]
    assert preview["connected_branch_source_ids"] == ["A-BRANCH-001", "A-BRANCH-002"]
    connector_aisle = next(aisle for aisle in preview["aisles"] if aisle["role"] == "connector")
    assert connector_aisle["metadata"]["connector_inset_depth"] in {0.0, 2.5, 5.0}
    assert "connector_inset_depth" in connector_aisle["score_features"]
    layout_preview = candidate_layout_preview_report(layout)
    connector_preview_id = connector_aisle["id"]
    connector_stalls = [
        stall
        for stall in layout_preview["stalls"]
        if stall["served_by_aisle_id"] == connector_preview_id
    ]
    assert connector_stalls
    assert layout_preview["validation"]["valid"] is True
    connector_polygon = ShapelyPolygon(connector_aisle["geometry"])
    for stall in layout_preview["stalls"]:
        if stall["served_by_aisle_id"] == connector_preview_id:
            continue
        assert ShapelyPolygon(stall["geometry"]).intersection(connector_polygon).area <= 1e-6


def test_phase4c_candidate_snapshot_can_synthesize_connector_candidates():
    site = SiteSpec(
        name="synthetic-connector",
        boundary=[(0, 0), (50, 0), (50, 50), (0, 50)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(25, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        optimization={"enable_connectors": True, "connector_inset_depths": [0, 5.0, 100.0]},
    )
    layout = LayoutResult(
        site=site,
        stalls=[],
        aisles=[],
        main_entrance_id="main",
        selected_heading_degrees=90.0,
        attempts=[
            AngleAttempt(
                angle_degrees=90.0,
                stall_count=0,
                branch_candidates=[
                    {
                        "branch_id": "A-BRANCH-001",
                        "side": "left",
                        "start_u": 14.0,
                        "length": 24.0,
                        "geometry": [(19, 14), (25, 14), (25, 38), (19, 38)],
                        "base_stall_count": 10,
                        "stall_count": 20,
                        "reason": "branch_improves_stall_count",
                        "graph_valid": True,
                    },
                    {
                        "branch_id": "A-BRANCH-002",
                        "side": "left",
                        "start_u": 30.0,
                        "length": 24.0,
                        "geometry": [(19, 30), (25, 30), (25, 50), (19, 50)],
                        "base_stall_count": 20,
                        "stall_count": 30,
                        "reason": "branch_improves_stall_count",
                        "graph_valid": True,
                    },
                ],
            )
        ],
    )

    report = candidate_snapshot_report(layout)
    connectors = [
        item
        for item in report["objects"]
        if item["role"] == "connector" and item["metadata"].get("synthetic")
    ]

    assert [item["metadata"]["connector_inset_depth"] for item in connectors] == [0.0, 5.0, 12.0]
    assert all(item["metadata"]["reason"] == "shadow_connector_synthesized" for item in connectors)
    assert all(item["metadata"]["connects"] == ["A-BRANCH-001", "A-BRANCH-002"] for item in connectors)


def test_phase4c_candidate_layout_promotion_requires_explicit_flag():
    layout = generate_layout(_phase1_site())

    assert layout.generation_mode == "phase1_main_aisle"
    assert layout.candidate_layout_promotion["version"] == PROMOTION_VERSION
    assert layout.candidate_layout_promotion["requested"] is False
    assert layout.candidate_layout_promotion["status"] == "not_requested"
    assert layout.candidate_layout_promotion["official_output_replaced"] is False


def test_phase4c_candidate_layout_promotion_can_replace_official_output():
    site = SiteSpec(
        name="promote-main-preview",
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
            "enable_branches": False,
            "promote_candidate_layout_preview": True,
        },
    )

    layout = generate_layout(site)

    assert layout.generation_mode == "candidate_layout_promoted"
    assert layout.candidate_layout_promotion["status"] == "promoted"
    assert layout.candidate_layout_promotion["official_output_replaced"] is True
    assert layout.candidate_layout_preview["comparison"]["promotion_eligible"] is True
    assert {aisle.id for aisle in layout.aisles} == {"A-MAIN", "A-TURNAROUND"}
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-MAIN"}
    assert layout.candidate_layout_promotion["official_id_scheme"] == "catalog_source_id"
    assert layout.score["total"] == layout.candidate_layout_preview["score"]["total"]
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["optimization.promote_candidate_layout_preview"] == "active"


def test_phase4c_candidate_layout_promotion_adds_shadow_branch_turnarounds():
    site = SiteSpec(
        name="reject-dead-end-preview",
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
            "branch_start_positions": [18, 32, 46],
            "branch_sides": ["left"],
            "max_branches": 2,
            "enable_connectors": False,
            "promote_candidate_layout_preview": True,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assert layout.generation_mode == "candidate_layout_promoted"
    assert layout.candidate_network_preview["version"] == NETWORK_PREVIEW_VERSION
    assert layout.candidate_network_preview["shadow_turnaround_count"] > 0
    assert layout.candidate_layout_promotion["status"] == "promoted"
    assert layout.candidate_layout_promotion["official_output_replaced"] is True
    assert layout.candidate_layout_preview["comparison"]["promotion_eligible"] is True
    assert "preview_has_dead_end_without_turnaround" not in layout.candidate_layout_promotion["blockers"]
    assert not [
        item
        for item in layout.candidate_layout_preview["validation"]["traffic_graph"]["dead_ends"]
        if item["status"] == "dead_end_without_turnaround"
    ]
    assert {aisle.role for aisle in layout.aisles} >= {"branch", "turnaround"}


def test_phase4b_candidate_network_preview_is_drawn_in_svg(tmp_path):
    site = SiteSpec(
        name="shadow-selector-svg",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )
    layout = generate_layout(site)
    target = tmp_path / "preview.svg"

    write_svg(layout, target)

    content = target.read_text(encoding="utf-8")
    assert 'id="candidate-network-preview"' in content
    assert 'data-status="preview-only"' in content
    assert "stroke-dasharray" in content
    assert "PN-AISLE-" in content


def test_phase4c_svg_draws_candidate_layout_preview_stalls(tmp_path):
    site = SiteSpec(
        name="connector-preview-stalls-svg",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            "max_branches": 2,
            "enable_connectors": True,
            "connector_throat_length": 6.0,
            "connector_inset_depths": [0, 5.0],
            "connector_allow_l_shape_end_stalls": False,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )
    layout = generate_layout(site)
    target = tmp_path / "preview-stalls.svg"

    write_svg(layout, target)

    content = target.read_text(encoding="utf-8")
    assert 'id="candidate-layout-preview-stalls"' in content
    assert "PL-STALL-" in content


def test_phase4c_svg_uses_official_view_after_candidate_layout_promotion(tmp_path):
    site = SiteSpec(
        name="promoted-svg",
        boundary=[(0, 0), (86, 0), (86, 66), (0, 66)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
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
            "max_branches": 2,
            "enable_connectors": True,
            "connector_throat_length": 3.0,
            "connector_inset_depths": [0, 5.0],
            "promote_candidate_layout_preview": True,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": -12,
                "branch_count": 0,
            },
        },
    )
    layout = generate_layout(site)
    target = tmp_path / "promoted.svg"

    write_svg(layout, target)

    content = target.read_text(encoding="utf-8")
    assert layout.generation_mode == "candidate_layout_promoted"
    assert 'id="candidate-network-preview"' not in content
    assert 'id="candidate-layout-preview-stalls"' not in content
    assert "PL-STALL-" not in content
    assert "P-072" in content


def test_phase1_layout_keeps_generated_geometry_inside_usable_area():
    site = _phase1_site()
    layout = generate_layout(site)
    usable = ShapelyPolygon(site.boundary)

    for aisle in layout.aisles:
        assert usable.covers(ShapelyPolygon(aisle.polygon))
    for stall in layout.stalls:
        assert usable.covers(ShapelyPolygon(stall.polygon))


def test_phase1_layout_rejects_narrow_two_way_aisle():
    site = _phase1_site()
    unsupported = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=site.stall,
        aisle_width=3.5,
        margin=site.margin,
        entrances=site.entrances,
        aisle_classes=[
            AisleClassSpec(
                id="narrow-two-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="narrow-two-way",
        source_format="phase0",
    )

    layout = generate_layout(unsupported)

    assert layout.generation_mode == "phase1_main_aisle"
    assert layout.stall_count == 0
    assert layout.aisles == []
    assert layout.unsupported_phase1_inputs
    assert layout.unsupported_phase1_inputs[0]["field"] == "aisles.classes.narrow-two-way"


def test_phase2b_rejects_candidates_without_exit_path():
    site = SiteSpec(
        name="entry-only",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry",
                mode="entry_only",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    )

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.attempts
    assert layout.attempts[0].graph_valid is False
    assert layout.attempts[0].graph_errors == ["stalls_without_exit_path"]


def test_phase1_reports_unsupported_stall_types_clearly():
    site = SiteSpec(
        name="painted",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, family="painted", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
    )

    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.stall_count == 0
    assert {item["field"] for item in layout.unsupported_phase1_inputs} == {
        "parking.active_stall.family",
    }
    assert diagnostics["unsupported_phase1_inputs"] == layout.unsupported_phase1_inputs


def test_t_end_stalls_generate_at_main_dead_end():
    site = SiteSpec(
        name="t-end-main",
        boundary=[(0, 0), (24, 0), (24, 40), (0, 40)],
        stall=StallSpec(
            id="t-end",
            width=2.5,
            length=5.0,
            family="t_end",
            allowed_angles=(90.0,),
            access_sides=("front",),
        ),
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
            "enable_branches": False,
        },
    )

    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.stall_count > 0
    assert all(stall.aisle_side == "end" for stall in layout.stalls)
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-TURNAROUND"}
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["rule_support"]["t_end_proxy"] == "active"
    assert diagnostics["field_support"]["parking.t_end"] == "active"


def test_t_end_exact_vehicle_check_reserves_envelope_clearance_from_site_edge():
    site = SiteSpec(
        name="t-end-exact-edge",
        boundary=[(0, 0), (24, 0), (24, 40), (0, 40)],
        stall=StallSpec(
            id="t-end",
            width=2.5,
            length=5.0,
            family="t_end",
            allowed_angles=(90.0,),
            access_sides=("front",),
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
                centerline_crossing="allowed",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
        },
    )
    without_exact = generate_layout(replace(site, vehicle=None, constraints={}))
    layout = generate_layout(site)
    assert without_exact.stall_count > 0
    assert layout.stall_count > 0
    assert all(stall.aisle_side == "end" for stall in layout.stalls)
    far_without = max(point[1] for stall in without_exact.stalls for point in stall.polygon)
    far_with = max(point[1] for stall in layout.stalls for point in stall.polygon)
    assert far_with <= far_without - 0.3 + 1e-6
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["vehicle_validation"]["valid"] is True
    assert layout.maneuver_validation["vehicle_rule_counts"]["reverse_in_t_end_bicycle_v1"] == layout.stall_count


def test_t_end_caps_can_augment_perpendicular_layout():
    # Extra depth beyond the aisle body lets end-caps place without shortening sides.
    base = SiteSpec(
        name="perp-no-caps",
        boundary=[(0, 0), (24, 0), (24, 48), (0, 48)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(12, 0), width=7.0, heading_degrees=90.0)
        ],
        aisle_classes=[
            AisleClassSpec(id="wide-two-way-no-cross", width=6.0, capacity="two_vehicle", directionality="two_way")
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_t_end_caps": False,
        },
    )
    with_caps = SiteSpec(
        name="perp-with-caps",
        boundary=base.boundary,
        stall=base.stall,
        aisle_width=base.aisle_width,
        margin=base.margin,
        entrances=base.entrances,
        aisle_classes=base.aisle_classes,
        fixed_aisle_class=base.fixed_aisle_class,
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_t_end_caps": True,
        },
    )

    layout_base = generate_layout(base)
    layout_caps = generate_layout(with_caps)

    assert any(stall.aisle_side == "end" for stall in layout_caps.stalls)
    assert all(stall.aisle_side != "end" for stall in layout_base.stalls)
    assert layout_caps.maneuver_validation["valid"] is True
    assert layout_caps.graph_validation.get("valid") is True


def test_parallel_stalls_generate_on_main_aisle():
    """Narrow strip sites fit parallel modules where dual-side 90-degree stalls do not."""
    site = SiteSpec(
        name="parallel-strip",
        # Depth along the aisle is long; cross-section is only wide enough for aisle + parallel rows.
        boundary=[(0, 0), (14, 0), (14, 52), (0, 52)],
        stall=StallSpec(
            id="parallel",
            width=2.2,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(7, 0),
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
            "enable_branches": False,
        },
    )

    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.stall_count > 0
    assert layout.generation_mode == "phase1_main_aisle"
    assert all(stall.stall_type_id == "parallel" for stall in layout.stalls)
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-MAIN"}
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["rule_support"]["parallel_proxy"] == "active"
    assert diagnostics["field_support"]["parking.parallel_main_aisle_generation"] == "active"
    assert diagnostics["field_support"]["parking.parallel_maneuver_proxy"] == "active"
    assert not any(item["field"] == "parking.active_stall.family" for item in layout.unsupported_phase1_inputs)


def test_parallel_branch_stalls_can_be_selected():
    site = SiteSpec(
        name="parallel-branch",
        boundary=[(0, 0), (70, 0), (70, 48), (0, 48)],
        stall=StallSpec(
            id="parallel",
            width=2.2,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
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
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "max_branches": 2,
            "enable_connectors": False,
        },
    )

    layout = generate_layout(site)

    assert layout.stall_count > 0
    assert layout.selected_branches
    branch_ids = {branch["id"] for branch in layout.selected_branches}
    assert any(stall.served_by_aisle_id in branch_ids for stall in layout.stalls)
    assert layout.maneuver_validation["rule_counts"].get("parallel_proxy", 0) == layout.stall_count


def test_parallel_connector_stalls_are_official_when_connectors_score():
    site = SiteSpec(
        name="parallel-connector",
        boundary=[(0, 0), (80, 0), (80, 56), (0, 56)],
        stall=StallSpec(
            id="parallel",
            width=2.2,
            length=6.0,
            family="parallel",
            allowed_angles=(0.0,),
            access_sides=("left", "right"),
        ),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(40, 0),
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
            "branch_start_positions": [12, 24, 36],
            "branch_sides": ["left"],
            "max_branches": 2,
            "enable_connectors": True,
            "enable_t_end_caps": False,
        },
    )
    layout = generate_layout(site)
    connector_ids = {item["id"] for item in layout.selected_connectors}
    assert connector_ids
    connector_stalls = [stall for stall in layout.stalls if stall.served_by_aisle_id in connector_ids]
    assert connector_stalls
    assert {stall.stall_type_id for stall in connector_stalls} == {"parallel"}
    assert layout.maneuver_validation["rule_counts"].get("parallel_proxy", 0) == layout.stall_count
    diagnostics = build_input_diagnostics(site, layout)
    assert diagnostics["field_support"]["parking.parallel_connector_generation"] == "active"


def test_angled_connector_stalls_are_official_when_connectors_score():
    site = SiteSpec(
        name="angled-connector",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
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
    connector_ids = {item["id"] for item in layout.selected_connectors}
    assert connector_ids
    connector_stalls = [stall for stall in layout.stalls if stall.served_by_aisle_id in connector_ids]
    assert connector_stalls
    assert {stall.stall_type_id for stall in connector_stalls} == {"angled-60"}
    assert layout.maneuver_validation["rule_counts"].get("angled_proxy", 0) == layout.stall_count
    diagnostics = build_input_diagnostics(site, layout)
    assert diagnostics["field_support"]["parking.angled_connector_generation"] == "active"


def test_phase3d_generates_angled_stalls_on_main_aisle():
    site = SiteSpec(
        name="angled-main",
        boundary=[(0, 0), (34, 0), (34, 38), (0, 38)],
        stall=StallSpec(width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
        aisle_width=6.0,
        margin=0.0,
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
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0]},
    )

    layout = generate_layout(site)

    assert layout.stall_count > 0
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-MAIN"}
    assert {stall.aisle_side for stall in layout.stalls} <= {"left", "right"}
    assert layout.selected_branches == []
    assert layout.graph_validation["valid"] is True
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["rule_counts"] == {"angled_proxy": layout.stall_count}
    assert all(
        abs(abs(stall.angle_degrees - layout.selected_heading_degrees) - 60.0) <= 1e-6
        for stall in layout.stalls
    )


def test_phase3d_can_add_angled_branch_stalls():
    site = SiteSpec(
        name="angled-branch",
        boundary=[(0, 0), (80, 0), (80, 64), (0, 64)],
        stall=StallSpec(width=2.5, length=5.0, family="angled", allowed_angles=(60.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(40, 0),
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
            "branch_start_positions": [24],
            "branch_sides": ["left"],
            "max_branches": 1,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assert [branch["id"] for branch in layout.selected_branches] == ["A-BRANCH-001"]
    assert "A-BRANCH-001" in {stall.served_by_aisle_id for stall in layout.stalls}
    assert layout.selected_connectors == []
    assert layout.graph_validation["valid"] is True
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["rule_counts"] == {"angled_proxy": layout.stall_count}
    assert not any(
        item["reason"] == "connectors_not_supported_for_stall_family" for item in layout.attempts[0].branch_candidates
    )


def test_phase3f_compares_stall_type_assignments_by_aisle_role():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    site = SiteSpec(
        name="stall-type-compare",
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
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assignments = {
        (item["main_stall_type_id"], item["branch_stall_type_id"])
        for item in layout.stall_assignment_attempts
    }
    assert assignments == {
        ("standard-90", "standard-90"),
        ("standard-90", "angled-60"),
        ("angled-60", "standard-90"),
        ("angled-60", "angled-60"),
    }
    assert layout.selected_stall_assignment["main"] in {"standard-90", "angled-60"}
    assert layout.selected_stall_assignment["branch"] in {"standard-90", "angled-60"}
    assert layout.site.stall.id == layout.selected_stall_assignment["main"]
    assert (layout.site.branch_stall or layout.site.stall).id == layout.selected_stall_assignment["branch"]
    assert {stall.stall_type_id for stall in layout.stalls} <= set(layout.selected_stall_assignment.values())
    assert sum(1 for item in layout.stall_assignment_attempts if item["selected"]) == 1
    selected_attempt = next(item for item in layout.stall_assignment_attempts if item["selected"])
    assert selected_attempt["score_total"] == max(item["score_total"] for item in layout.stall_assignment_attempts)
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["stall_type_selection"]["selected_stall_assignment"] == layout.selected_stall_assignment
    assert diagnostics["field_support"]["parking.stall_type_candidate_selection"] == "active"
    assert diagnostics["field_support"]["parking.stall_type_segment_assignment"] == "active"


def test_phase1_layout_evaluates_heading_candidates():
    site = SiteSpec(
        name="skewed",
        boundary=[(0, 0), (30, 0), (44, 44), (14, 44)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0, 20], "entrance_offsets": [0]},
    )

    layout = generate_layout(site)

    assert {attempt.heading_delta_degrees for attempt in layout.attempts} == {0.0, 20.0}
    assert layout.selected_heading_delta_degrees in {0.0, 20.0}
    assert layout.stall_count == max(attempt.stall_count for attempt in layout.attempts)


def test_phase1_layout_evaluates_entrance_offset_candidates():
    site = SiteSpec(
        name="offsets",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [-1, 0, 1]},
    )

    layout = generate_layout(site)

    assert {attempt.entrance_offset for attempt in layout.attempts} == {-1.0, 0.0, 1.0}
    assert layout.selected_entrance_offset in {-1.0, 0.0, 1.0}
    assert layout.stall_count == max(attempt.stall_count for attempt in layout.attempts)


def test_phase1_layout_can_add_single_branch_candidate():
    common = dict(
        name="branch",
        boundary=[(0, 0), (60, 0), (60, 50), (0, 50)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(30, 0),
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
    )
    base = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False})
    branched = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_positions": [24]})

    base_layout = generate_layout(base)
    branch_layout = generate_layout(branched)

    assert branch_layout.stall_count > base_layout.stall_count
    assert branch_layout.selected_branch_side in {"left", "right"}
    assert any(aisle.id == "A-BRANCH-001" for aisle in branch_layout.aisles)


def test_phase2c_layout_can_select_multiple_branch_candidates():
    site = SiteSpec(
        name="multi-branch",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
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
            "max_branches": 2,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assert [branch["id"] for branch in layout.selected_branches] == ["A-BRANCH-001", "A-BRANCH-002"]
    assert layout.score["branch_count"] == 2.0
    assert layout.graph_validation["valid"] is True
    assert {"A-BRANCH-001", "A-BRANCH-002"} <= {stall.served_by_aisle_id for stall in layout.stalls}
    assert any(item["reason"] == "branch_overlaps_existing_layout" for item in layout.attempts[0].branch_candidates)


def test_phase2d_layout_can_connect_same_side_branches():
    site = SiteSpec(
        name="connector",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
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
            "connector_throat_length": 3.0,
            "connector_inset_depths": [0, 5.0],
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": -20,
                "branch_count": 0,
            },
        },
    )

    layout = generate_layout(site)

    assert len(layout.selected_connectors) == 1
    selected_connector = layout.selected_connectors[0]
    assert selected_connector["id"] == "A-CONNECTOR-001"
    assert selected_connector["connects"] == ["A-BRANCH-001", "A-BRANCH-002"]
    assert selected_connector["connector_inset_depth"] == 5.0
    assert selected_connector["removed_stalls"] > 0
    assert selected_connector["added_stalls"] > selected_connector["removed_stalls"]
    assert selected_connector["removed_turnarounds"] == [
        "A-BRANCH-001-TURNAROUND",
        "A-BRANCH-002-TURNAROUND",
    ]
    assert "A-BRANCH-001-TURNAROUND" not in {aisle.id for aisle in layout.aisles}
    assert "A-BRANCH-002-TURNAROUND" not in {aisle.id for aisle in layout.aisles}
    connector = next(aisle for aisle in layout.aisles if aisle.id == "A-CONNECTOR-001")
    assert connector.role == "connector"
    assert connector.parent_aisle_id == "A-BRANCH-001"
    assert connector.connected_aisle_ids == ("A-BRANCH-002",)
    assert layout.graph_validation["valid"] is True
    assert "A-CONNECTOR-001" in layout.graph_validation["reachable_aisles"]
    assert any(stall.served_by_aisle_id == "A-CONNECTOR-001" for stall in layout.stalls)
    assert {item["aisle_id"] for item in layout.graph_validation["dead_ends"]} == {"A-TURNAROUND"}
    connector_attempts = [item for item in layout.attempts[0].branch_candidates if item.get("connector_id") == "A-CONNECTOR-001"]
    assert {item["connector_inset_depth"] for item in connector_attempts} == {0.0, 5.0}
    assert any(item["reason"] == "connector_improves_score" for item in layout.attempts[0].branch_candidates)


def test_phase1_branch_attempts_report_candidate_reasons():
    site = SiteSpec(
        name="branch-report",
        boundary=[(0, 0), (64, 0), (64, 34), (50, 45), (10, 39), (0, 28)],
        stall=StallSpec(width=2.5, length=5.3, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.2,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(8, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_step": 2.5},
    )

    layout = generate_layout(site)

    assert layout.attempts[0].branch_candidates
    assert {item["reason"] for item in layout.attempts[0].branch_candidates}


def test_diagnostics_reads_phase1_support_without_generator_dependency():
    assert diagnostics.phase1_unsupported_inputs is phase1_unsupported_inputs


def test_generate_layout_preserves_branch_toggle_pipeline_metadata():
    common = dict(
        name="branch-toggle",
        boundary=[(0, 0), (60, 0), (60, 50), (0, 50)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(30, 0),
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
    )
    disabled = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False})
    enabled = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_positions": [24]})

    disabled_layout = generate_layout(disabled)
    enabled_layout = generate_layout(enabled)

    assert disabled_layout.selected_branch_side is None
    assert disabled_layout.graph_validation["valid"] is True
    assert disabled_layout.attempts[0].branch_candidates == []
    assert enabled_layout.selected_branch_side in {"left", "right"}
    assert enabled_layout.graph_validation["valid"] is True
    assert enabled_layout.attempts[0].graph_valid is True
    assert enabled_layout.attempts[0].branch_candidates
    assert enabled_layout.stall_count > disabled_layout.stall_count
