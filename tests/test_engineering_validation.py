from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteAreaSpec, SiteSpec


def test_engineering_validation_combines_active_advisory_and_failed_rules() -> None:
    site = SiteSpec(
        name="contract-site",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        obstacle_specs=(
            SiteAreaSpec(
                id="hard-core",
                kind="column",
                geometry={"type": "polygon", "points": [[8, 8], [9, 8], [9, 9], [8, 9]]},
            ),
        ),
        reserved_areas=(
            SiteAreaSpec(
                id="soft-landscape",
                kind="landscape",
                geometry={"type": "polygon", "points": [[1, 1], [2, 1], [2, 2], [1, 2]]},
                priority="advisory",
            ),
        ),
    )
    layout = LayoutResult(
        site=site,
        stalls=[ParkingStall("P-001", [(2, 2), (4, 2), (4, 7), (2, 7)], 0.0)],
        aisles=[ParkingAisle("A-001", [(0, 7), (10, 7), (10, 13), (0, 13)], 0.0)],
        maneuver_validation={
            "version": "maneuver-test",
            "vehicle_validation": {
                "version": "vehicle-test",
                "valid": False,
                "requested": {"turning_radius": True, "swept_path": False, "reverse_distance": False},
                "reason": "design_vehicle_missing",
                "checks": [],
            },
        },
        site_constraint_validation={
            "version": "site-test",
            "valid": True,
            "errors": [],
            "authority": {"project_policy": 1, "advisory": 1},
            "quota": {"required": {"accessible_min": 0, "ev_min": 0}},
        },
        unsupported_phase1_inputs=[{"field": "future.field", "reason": "not implemented"}],
    )

    report = build_engineering_validation(layout)

    assert report["version"] == "openparkcad-engineering-0.3"
    assert report["contract_version"] == "openparkcad-v0.3"
    assert report["result_scope"] == "official_layout"
    assert report["valid"] is False
    assert {item["id"] for item in report["rules"]["active"]} == {
        "site.hard-core",
        "vehicle.turning_radius",
    }
    assert [item["id"] for item in report["rules"]["advisory"]] == ["site.soft-landscape"]
    assert [item["id"] for item in report["rules"]["unsupported"]] == ["future.field"]
    assert [item["id"] for item in report["rules"]["failed"]] == ["vehicle.configuration"]


def test_engineering_validation_passes_when_nested_hard_checks_pass() -> None:
    site = SiteSpec(name="pass", boundary=[(0, 0), (10, 0), (10, 10), (0, 10)])
    layout = LayoutResult(
        site=site,
        stalls=[],
        maneuver_validation={
            "vehicle_validation": {
                "version": "vehicle-test",
                "valid": True,
                "requested": {"turning_radius": False, "swept_path": False, "reverse_distance": False},
                "checks": [],
            }
        },
        site_constraint_validation={
            "version": "site-test",
            "valid": True,
            "errors": [],
            "quota": {"required": {"accessible_min": 0, "ev_min": 0}},
        },
    )

    report = build_engineering_validation(layout)

    assert report["valid"] is True
    assert report["decision"] == "pass"
    assert report["rules"]["failed"] == []
