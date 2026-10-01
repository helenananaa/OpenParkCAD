import json
from pathlib import Path

from openparkcad.cli import main
from openparkcad.generator import generate_layout
from openparkcad.maneuver_validation import _vehicle_check_policy, validate_maneuvers
from openparkcad.models import LayoutResult, SiteSpec, VehicleSpec, site_from_dict


def test_vehicle_rejection_survives_final_empty_layout() -> None:
    fixture = Path("tests/fixtures/v0_3/tight_rear_court_reject.json")
    site = site_from_dict(json.loads(fixture.read_text(encoding="utf-8")))

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.maneuver_validation["valid"] is False
    assert layout.maneuver_validation["result_scope"] == "best_vehicle_rejected_candidate"

    vehicle_validation = layout.maneuver_validation["vehicle_validation"]
    assert vehicle_validation["valid"] is False
    assert vehicle_validation["checked_stalls"] > 0
    assert vehicle_validation["invalid_stall_count"] == vehicle_validation["checked_stalls"]
    assert vehicle_validation["result_scope"] == "all_generated_stalls_rejected"
    assert layout.maneuver_validation["pre_filter_vehicle_validation"]["valid"] is False


def test_representative_pass_fixture_binds_exact_vehicle_and_site_invariants() -> None:
    fixture = Path("tests/fixtures/v0_3/irregular_courtyard_pass.json")
    site = site_from_dict(json.loads(fixture.read_text(encoding="utf-8")))

    layout = generate_layout(site)

    assert layout.stall_count > 0
    assert layout.graph_validation["valid"] is True
    assert layout.maneuver_validation["valid"] is True
    assert layout.site_constraint_validation["valid"] is True
    assert layout.operational_quality["valid"] is True
    assert layout.engineering_validation["valid"] is True

    vehicle = layout.maneuver_validation["vehicle_validation"]
    assert vehicle["requested"] == {
        "turning_radius": True,
        "swept_path": True,
        "reverse_distance": True,
    }
    assert vehicle["checked_stalls"] == layout.stall_count
    assert vehicle["invalid_stall_count"] == 0
    assert layout.maneuver_validation["vehicle_rule_counts"] == {
        "reverse_in_90_bicycle_v1": layout.stall_count
    }
    assert all(check["status"] == "active_exact" for check in vehicle["checks"])


def test_quota_fixture_fails_closed_with_explicit_shortfalls() -> None:
    fixture = Path("tests/fixtures/v0_3/offset_gate_quota_reject.json")
    site = site_from_dict(json.loads(fixture.read_text(encoding="utf-8")))

    layout = generate_layout(site)

    quota = layout.site_constraint_validation["quota"]
    assert layout.stall_count == 0
    assert layout.site_constraint_validation["valid"] is False
    assert quota["shortfall"]["accessible"] > 0
    assert quota["shortfall"]["ev"] > 0
    assert layout.engineering_validation["valid"] is False
    assert layout.engineering_validation["rules"]["failed"]


def test_vehicle_rejection_does_not_replace_transactional_outputs(tmp_path: Path, capsys) -> None:
    fixture = Path("tests/fixtures/v0_3/tight_rear_court_reject.json")
    dxf_path = tmp_path / "layout.dxf"
    svg_path = tmp_path / "layout.svg"
    report_path = tmp_path / "report.json"

    exit_code = main(
        [
            "solve",
            str(fixture),
            "--out",
            str(dxf_path),
            "--preview",
            str(svg_path),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 3
    assert "engineering validation failed" in capsys.readouterr().err
    assert not dxf_path.exists()
    assert not svg_path.exists()
    assert not report_path.exists()


def test_reverse_distance_policy_exposes_turning_radius_as_an_active_prerequisite() -> None:
    site = SiteSpec(
        name="reverse-only",
        boundary=[(0, 0), (10, 0), (10, 10), (0, 10)],
        vehicle=VehicleSpec(max_reverse_distance=12.0),
    )

    policy = _vehicle_check_policy(site)

    assert policy.require_reverse_distance is True
    assert policy.require_turning_radius is True
    assert policy.declared_turning_radius is False


def test_requested_vehicle_check_cannot_pass_with_zero_executed_checks() -> None:
    site = SiteSpec(
        name="empty-requested-check",
        boundary=[(0, 0), (10, 0), (10, 10), (0, 10)],
        vehicle=VehicleSpec(
            wheelbase=2.7,
            min_turning_radius=5.5,
            track_width=1.6,
        ),
        constraints={"maneuvering": {"require_turning_radius_check": True}},
    )

    report = validate_maneuvers(LayoutResult(site=site, stalls=[]))

    assert report["valid"] is False
    assert report["vehicle_validation"]["valid"] is False
    assert report["vehicle_validation"]["reason"] == "no_vehicle_checks_executed"
