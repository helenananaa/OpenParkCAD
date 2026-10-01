import json
from dataclasses import replace
from pathlib import Path

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import LayoutResult, site_from_dict


def test_generated_layout_reports_scoring_weights_as_active():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    diagnostics = build_input_diagnostics(site, generate_layout(site))

    assert diagnostics["field_support"]["optimization.weights"] == "active"


def test_exact_vehicle_diagnostics_state_scope_and_fail_closed_boundary():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    maneuvering = dict(site.constraints["maneuvering"])
    maneuvering["require_swept_path_check"] = True
    exact_site = replace(site, constraints={**site.constraints, "maneuvering": maneuvering})
    vehicle_report = {
        "version": "v0.3-vehicle-maneuver-1",
        "valid": True,
        "status": "active",
        "checked_stalls": 4,
        "invalid_stall_count": 0,
        "checks": [{"status": "active_exact", "rule_id": "reverse_in_90_bicycle_v1"}],
    }
    layout = LayoutResult(
        site=exact_site,
        stalls=[],
        maneuver_validation={"valid": True, "vehicle_validation": vehicle_report},
        site_constraint_validation={"valid": True, "quota": {"valid": True}},
    )

    diagnostics = build_input_diagnostics(exact_site, layout)

    assert diagnostics["vehicle_validation"] == {
        "mode": "exact_swept_path",
        "status": "active_exact",
        "requested": {"turning_radius": True, "swept_path": True, "reverse_distance": True},
        "declared_requests": {"turning_radius": True, "swept_path": True, "reverse_distance": True},
        "fail_closed_when_requested": True,
        "checked_stalls": 4,
        "invalid_stall_count": 0,
        "report_version": "v0.3-vehicle-maneuver-1",
        "scope": (
            "perpendicular-90 reverse-in, acute-angled reverse-in, parallel reverse S-curve, and T-end "
            "reverse-in templates with exact constant-curvature pose integration and a conservative sampled "
            "body envelope"
        ),
    }
    assert diagnostics["field_support"]["constraints.turning_radius"] == "active_exact"
    assert diagnostics["field_support"]["constraints.swept_path"] == "active_exact"


def test_failed_requested_vehicle_and_quota_checks_are_reported_active_failed():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    failed_site = replace(site, parking_quotas={"ev_min": 1})
    layout = LayoutResult(
        site=failed_site,
        stalls=[],
        maneuver_validation={
            "valid": False,
            "vehicle_validation": {
                "version": "v0.3-vehicle-maneuver-1",
                "valid": False,
                "checked_stalls": 1,
                "invalid_stall_count": 1,
            },
        },
        site_constraint_validation={
            "valid": False,
            "quota": {"valid": False, "required": {"ev_min": 1}, "actual": {"ev": 0}},
        },
    )

    diagnostics = build_input_diagnostics(failed_site, layout)

    assert diagnostics["vehicle_validation"]["status"] == "active_failed"
    assert diagnostics["field_support"]["constraints.turning_radius"] == "active_failed"
    assert diagnostics["field_support"]["parking.quotas.ev_min"] == "active_failed"
    assert diagnostics["field_support"]["constraints.site_hard_exclusions"] == "active_failed"
    assert any("fail-closed" in warning for warning in diagnostics["warnings"])


def test_reverse_only_diagnostics_expose_turning_radius_as_active_prerequisite():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    reverse_only = replace(
        site,
        constraints={
            **site.constraints,
            "maneuvering": {
                "require_turning_radius_check": False,
                "require_swept_path_check": False,
                "max_reverse_distance": 12.0,
            },
        },
    )

    diagnostics = build_input_diagnostics(reverse_only)

    assert diagnostics["vehicle_validation"]["declared_requests"] == {
        "turning_radius": False,
        "swept_path": False,
        "reverse_distance": True,
    }
    assert diagnostics["vehicle_validation"]["requested"] == {
        "turning_radius": True,
        "swept_path": False,
        "reverse_distance": True,
    }
