import json
from pathlib import Path

from openparkcad.diagnostic_geometry import pedestrian_emergency_shapes, site_feature_shapes
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict


def test_phase0_example_parses_core_fields():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))

    site = site_from_dict(data)

    assert site.source_format == "phase0"
    assert site.name == "phase0 large branch test sample"
    assert site.entrances[0].id == "main-gate"
    assert site.vehicle.id == "passenger-car"
    assert site.stall.id == "standard-90"
    assert site.aisle_width == 6.0
    assert site.entrances[0].center == (43.0, 0.0)
    assert site.optimization["max_branches"] == 2
    assert site.optimization["enable_connectors"] is True
    assert site.optimization["connector_allow_outer_stall_row"] is True
    assert site.optimization["connector_inset_depths"] == [0.0, 2.65, 5.3, 7.95]
    assert site.optimization["connector_allow_l_shape_end_stalls"] is True
    assert site.optimization["maneuver_l_shape_fallback"] is True
    assert site.optimization["promote_candidate_layout_preview"] is True
    assert site.candidate_angles == (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)
    assert len(site.site_features) == 2


def test_phase0_parser_preserves_enabled_stall_type_candidates():
    data = {
        "version": "0.1",
        "name": "candidate stalls",
        "site": {
            "boundary": {
                "type": "polygon",
                "points": [[0, 0], [30, 0], [30, 30], [0, 30]],
            }
        },
        "entrances": [
            {
                "id": "main",
                "mode": "shared",
                "center": [15, 0],
                "width": 8.0,
                "heading_degrees": 90,
            }
        ],
        "parking": {
            "stall_types": [
                {
                    "id": "standard-90",
                    "family": "perpendicular",
                    "width": 2.5,
                    "length": 5.0,
                    "allowed_angles": [90],
                    "enabled": True,
                },
                {
                    "id": "angled-60",
                    "family": "angled",
                    "width": 2.5,
                    "length": 5.0,
                    "allowed_angles": [60],
                    "enabled": True,
                },
                {
                    "id": "disabled-parallel",
                    "family": "parallel",
                    "width": 2.5,
                    "length": 6.0,
                    "allowed_angles": [0],
                    "enabled": False,
                },
            ]
        },
    }

    site = site_from_dict(data)

    assert site.stall.id == "standard-90"
    assert [stall.id for stall in site.stall_candidates] == ["standard-90", "angled-60"]


def test_phase0_diagnostics_distinguish_pending_active_and_advisory_fields():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)

    diagnostics = build_input_diagnostics(site)

    assert diagnostics["source_format"] == "phase0"
    assert "site_features" not in diagnostics["parsed_future_fields"]
    assert diagnostics["field_support"]["site_features"] == "declared_pending_layout"
    assert diagnostics["field_support"]["entrances"] == "drawn_not_enforced"
    assert diagnostics["field_support"]["vehicles.design_vehicle"] == "requested_pending_layout"
    assert diagnostics["field_support"]["pedestrian_and_emergency.pedestrian_routes"] == "advisory_only"
    assert diagnostics["vehicle_validation"]["mode"] == "conservative_analytic"
    assert any("rear-axle turning-radius" in item for item in diagnostics["warnings"])
    assert any(item["constraint"] == "entrance to main aisle" and item["status"] == "future" for item in diagnostics["constraint_status"])


def test_phase0_diagnostic_shapes_are_available_for_export():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)

    feature_shapes = site_feature_shapes(site.site_features)
    pedestrian_shapes = pedestrian_emergency_shapes(site.pedestrian_and_emergency)

    assert {shape.id for shape in feature_shapes} == {"column-a1", "charger-demo"}
    assert {shape.layer for shape in feature_shapes} == {"SITE_FEATURES"}
    assert {shape.id for shape in pedestrian_shapes} == {"walkway-demo"}
    assert {shape.layer for shape in pedestrian_shapes} == {"PEDESTRIAN"}


def test_phase0_diagnostics_mark_main_aisle_connection_active_with_layout():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    layout = generate_layout(site)

    diagnostics = build_input_diagnostics(site, layout)

    assert layout.generation_mode == "candidate_layout_promoted"
    assert layout.stall_count == 83
    assert layout.candidate_layout_promotion["status"] == "promoted"
    assert layout.maneuver_validation["rule_counts"]["perpendicular_90_l_shape_proxy"] == 2
    assert layout.operational_quality["version"] == "phase5r-1"
    assert "operational_risk" in layout.score
    assert diagnostics["field_support"]["entrances"] == "active"
    assert diagnostics["field_support"]["constraints.entrance_to_main_aisle"] == "active"
    assert diagnostics["field_support"]["constraints.dead_end_turnaround"] == "active"
    assert diagnostics["field_support"]["aisles.heading_candidate_selection"] == "active"
    assert diagnostics["field_support"]["aisles.entrance_offset_selection"] == "active"
    assert diagnostics["field_support"]["aisles.single_branch_candidate"] == "active"
    assert diagnostics["heading_selection"]["selected_heading_degrees"] is not None
    assert diagnostics["heading_selection"]["selected_entrance_offset"] is not None
    assert diagnostics["branch_selection"]["enabled"] is True
    assert diagnostics["branch_selection"]["connector_inset_depths"] == [0.0, 2.65, 5.3, 7.95]
    assert diagnostics["field_support"]["optimization.score_breakdown"] == "active"
    assert diagnostics["field_support"]["optimization.promote_candidate_layout_preview"] == "active"
    assert diagnostics["field_support"]["optimization.connector_inset_depths"] == "active"
    assert diagnostics["field_support"]["optimization.connector_l_shape_end_stalls"] == "active"
    assert diagnostics["field_support"]["optimization.operational_risk_weight"] == "active"
    assert diagnostics["field_support"]["optimization.operational_quality_mode"] == "active"
    assert diagnostics["field_support"]["optimization.operational_max_risk_score"] == "active"
    assert diagnostics["field_support"]["optimization.operational_max_turnaround_dependency_ratio"] == "available"
    assert diagnostics["field_support"]["optimization.operational_max_average_route_length"] == "available"
    assert diagnostics["field_support"]["optimization.operational_max_long_route_ratio"] == "available"
    assert diagnostics["field_support"]["optimization.operational_directionality_issue_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_max_directionality_issue_ratio"] == "available"
    assert diagnostics["field_support"]["optimization.operational_narrow_two_way_issue_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_max_narrow_two_way_stall_ratio"] == "available"
    assert diagnostics["field_support"]["optimization.operational_min_passing_bays"] == "available"
    assert diagnostics["field_support"]["optimization.operational_passing_bay_shortage_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_passing_bay_touch_tolerance"] == "available"
    assert diagnostics["field_support"]["optimization.operational_min_passing_bay_area"] == "available"
    assert diagnostics["field_support"]["optimization.operational_passing_bay_geometry_issue_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_max_passing_bay_spacing"] == "available"
    assert diagnostics["field_support"]["optimization.operational_passing_bay_spacing_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_narrow_two_way_meeting_gap_risk"] == "available"
    assert diagnostics["field_support"]["optimization.operational_narrow_two_way_junction_merge_risk"] == "available"
    assert diagnostics["field_support"]["constraints.maneuver_l_shape_fallback"] == "active"
    assert diagnostics["field_support"]["constraints.turning_radius"] == "active_conservative"
    assert diagnostics["field_support"]["constraints.swept_path"] == "available"
    assert diagnostics["field_support"]["constraints.reverse_distance"] == "active_conservative"
    assert diagnostics["field_support"]["constraints.site_hard_exclusions"] == "active"
    assert diagnostics["field_support"]["site_features"] == "active"
    assert diagnostics["vehicle_validation"]["status"] == "active_conservative"
    assert diagnostics["field_support"]["diagnostics.engineering_validation"] == "active"
    assert diagnostics["engineering_validation"]["contract_version"] == "openparkcad-v0.3"
    assert diagnostics["field_support"]["constraints.operational_quality"] == "active"
    assert diagnostics["field_support"]["constraints.operational_directionality_risk"] == "active"
    assert diagnostics["field_support"]["constraints.operational_narrow_two_way_risk"] == "active"
    assert diagnostics["field_support"]["constraints.operational_route_summary"] == "active"
    assert diagnostics["score"]["total"] == layout.score["total"]
    assert any(item["constraint"] == "entrance to main aisle" and item["status"] == "active" for item in diagnostics["constraint_status"])


def test_deprecated_top_level_boundary_shape_is_rejected():
    data = {
        "name": "old shape",
        "boundary": [[0, 0], [10, 0], [10, 10], [0, 10]],
    }

    try:
        site_from_dict(data)
    except ValueError as exc:
        assert "top-level 'site' object" in str(exc)
    else:
        raise AssertionError("deprecated input shape should be rejected")
