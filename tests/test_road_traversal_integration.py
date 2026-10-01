from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from openparkcad import cli
from openparkcad.candidate_snapshot import _promoted_official_valid, _with_recomputed_validation
from openparkcad.generator import generate_layout
from openparkcad.layout_candidates import LayoutCandidateEvaluation
from openparkcad.layout_search import choose_official
from openparkcad.models import LayoutResult, site_from_dict
from openparkcad.road_traversal import apply_road_traversal, road_traversal_satisfied
from tests.road_traversal_support import through_layout
from tests.test_cli_and_exporters import _solve_args, _valid_site_data, _write_site


def test_rt01_unrequested_generate_layout_keeps_legacy_validity(tmp_path: Path) -> None:
    data = _valid_site_data()
    site = site_from_dict(data)
    layout = generate_layout(site)
    assert layout.stall_count > 0
    record = layout.road_traversal_validation
    assert record.get("requested") is False
    assert record.get("status") == "not_requested"
    assert record.get("valid") is None
    assert road_traversal_satisfied(layout) is True

    site_path = _write_site(tmp_path / "site.json", data)
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "out")
    assert cli.main(args) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["stall_count"] == layout.stall_count
    assert report["road_traversal_validation"]["status"] == "not_requested"
    assert dxf_path.is_file() and svg_path.is_file()


def test_rt09_unknown_and_bad_budget_are_input_errors(tmp_path: Path) -> None:
    data = _valid_site_data()
    data["constraints"] = {"road_traversal": {"enabled": True, "mystery": True}}
    path = _write_site(tmp_path / "bad.json", data)
    args, *_ = _solve_args(path, tmp_path / "out")
    assert cli.main(args) == 2

    data["constraints"] = {"road_traversal": {"enabled": True, "time_budget_seconds": False}}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert cli.main(args) == 2


def test_rt15_preview_pass_does_not_prove_changed_official_geometry() -> None:
    layout = apply_road_traversal(through_layout())
    assert layout.road_traversal_validation["status"] == "passed"
    moved = LayoutResult(
        site=layout.site,
        stalls=layout.stalls,
        aisles=[
            replace(layout.aisles[0], polygon=[(0.0, 10.0), (40.0, 10.0), (40.0, 16.0), (0.0, 16.0)])
        ],
        road_traversal_validation=layout.road_traversal_validation,
        graph_validation=layout.graph_validation,
        maneuver_validation=layout.maneuver_validation,
        site_constraint_validation=layout.site_constraint_validation,
        operational_quality=layout.operational_quality,
        engineering_validation=layout.engineering_validation,
    )
    assert road_traversal_satisfied(moved) is False
    recomputed = _with_recomputed_validation(moved)
    assert recomputed.road_traversal_validation.get("layout_identity") != layout.road_traversal_validation.get(
        "layout_identity"
    )
    assert recomputed.road_traversal_validation.get("status") != "passed" or recomputed.road_traversal_validation.get("valid") is not True


def test_rt16_candidate_isolation_cache_does_not_cross_vehicle_or_obstacle() -> None:
    first = apply_road_traversal(through_layout())
    blocked = apply_road_traversal(through_layout(extra_obstacle=[(8.0, 2.2), (14.0, 2.2), (14.0, 7.8), (8.0, 7.8)]))
    again = apply_road_traversal(through_layout())
    assert first.road_traversal_validation["layout_identity"] == again.road_traversal_validation["layout_identity"]
    assert first.road_traversal_validation["layout_identity"] != blocked.road_traversal_validation["layout_identity"]
    assert first.site.obstacles == []
    assert blocked.road_traversal_validation["status"] in {"failed", "incomplete", "unsupported"}


def test_rt17_promotion_recovers_only_when_enabled() -> None:
    baseline = apply_road_traversal(through_layout(extra_obstacle=[(8.0, 2.2), (14.0, 2.2), (14.0, 7.8), (8.0, 7.8)]))
    extra = apply_road_traversal(through_layout())
    evaluation = LayoutCandidateEvaluation(
        candidate_id="c-good",
        spine_id="s1",
        requested_backend="greedy",
        actual_backend="greedy",
        fallback_reason=None,
        preview={},
        rebuilt_layout=extra,
        checks={
            "graph": {"executed": True, "valid": True},
            "maneuver": {"executed": True, "valid": True},
            "site_quota": {"executed": True, "valid": True},
            "engineering": {"executed": True, "valid": True},
            "operational": {"executed": True, "valid": True},
            "road_traversal": {"executed": True, "valid": True, "status": "passed"},
        },
        score={"total": 900.0},
        duration_seconds=0.01,
        failure_class=None,
        used_template=False,
        provenance={},
        selection={},
        template_score_total=None,
    )
    official_off, publication_off, _ = choose_official(baseline, [evaluation], promotion_requested=False)
    assert publication_off["replaced"] is False
    assert official_off is baseline
    official_on, publication_on, _ = choose_official(baseline, [evaluation], promotion_requested=True)
    assert publication_on["replaced"] is True
    assert official_on is extra
    assert _promoted_official_valid(extra) is True or extra.road_traversal_validation.get("status") == "passed"


def test_rt18_all_candidates_failing_keeps_old_trio_and_writes_diagnostics(tmp_path: Path) -> None:
    data = _valid_site_data()
    data["constraints"]["road_traversal"] = {"enabled": True, "scope": "site_interior"}
    data["vehicles"] = {
        "design_vehicle": {
            "id": "passenger-car",
            "length": 4.8,
            "width": 1.9,
            "wheelbase": 2.8,
            "min_turning_radius": 5.5,
            "turning_radius_reference": "outer_front_wheel",
            "track_width": 1.6,
            "swept_path_margin": 0.3,
        }
    }
    site_path = _write_site(tmp_path / "site.json", data)
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "out")
    diagnostics = tmp_path / "reject.json"
    args.extend(["--diagnostics", str(diagnostics)])
    dxf_path.parent.mkdir(parents=True, exist_ok=True)
    dxf_path.write_text("old-dxf", encoding="utf-8")
    svg_path.write_text("old-svg", encoding="utf-8")
    report_path.write_text("old-report", encoding="utf-8")
    code = cli.main(args)
    assert code == 3
    assert dxf_path.read_text(encoding="utf-8") == "old-dxf"
    assert svg_path.read_text(encoding="utf-8") == "old-svg"
    assert report_path.read_text(encoding="utf-8") == "old-report"
    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["official_layout_published"] is False
    assert payload["road_traversal_validation"]["status"] in {"failed", "unsupported", "incomplete"}
    assert payload["road_traversal_validation"]["status"] != "passed"


def test_rt20_missing_evidence_is_not_passed() -> None:
    layout = through_layout()
    layout = LayoutResult(
        site=layout.site,
        stalls=layout.stalls,
        aisles=layout.aisles,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        site_constraint_validation={"valid": True},
        operational_quality={"valid": True},
        engineering_validation={"valid": True},
    )
    from openparkcad.cli import _final_layout_errors

    errors = _final_layout_errors(layout)
    assert any("missing" in item or "road traversal" in item for item in errors)
    assert road_traversal_satisfied(layout) is False
