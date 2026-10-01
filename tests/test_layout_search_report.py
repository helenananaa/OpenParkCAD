from __future__ import annotations

import json
from pathlib import Path

from openparkcad.cli import main
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.layout_search import layout_search_report
from openparkcad.models import site_from_dict


def _site_data(**optimization) -> dict[str, object]:
    return {
        "version": "0.3",
        "name": "layout-search-report",
        "site": {"boundary": {"type": "polygon", "points": [[0, 0], [36, 0], [36, 40], [0, 40]]}},
        "entrances": [{"id": "main", "mode": "shared", "center": [18, 0], "width": 8.0, "heading_degrees": 90}],
        "parking": {
            "stall_types": [
                {"id": "standard-90", "family": "perpendicular", "width": 2.5, "length": 5.0, "allowed_angles": [90]}
            ]
        },
        "aisles": {
            "selection_mode": "fixed",
            "fixed_class": "wide-two-way-no-cross",
            "classes": [
                {"id": "wide-two-way-no-cross", "width": 6.0, "capacity": "two_vehicle", "directionality": "two_way"}
            ],
        },
        "optimization": {
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            **optimization,
        },
    }


def test_illegal_layout_search_is_input_error_not_legacy_run() -> None:
    try:
        site_from_dict(_site_data(layout_search={"mode": "mystery"}))
    except ValueError as exc:
        assert "layout_search.mode" in str(exc)
    else:
        raise AssertionError("illegal mode must be an input error")
    try:
        site_from_dict(_site_data(layout_search={"mode": "multi_spine", "top_k": True}))
    except ValueError as exc:
        assert "top_k" in str(exc)
    else:
        raise AssertionError("boolean top_k must be an input error")


def test_cli_rejects_illegal_layout_search(tmp_path: Path, capsys) -> None:
    site_path = tmp_path / "bad.json"
    site_path.write_text(json.dumps(_site_data(layout_search={"mode": "all-the-spines"})), encoding="utf-8")
    code = main(
        [
            "solve",
            str(site_path),
            "--out",
            str(tmp_path / "layout.dxf"),
            "--preview",
            str(tmp_path / "layout.svg"),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )
    captured = capsys.readouterr()
    assert code != 0
    assert "invalid site input" in captured.err
    assert not (tmp_path / "layout.dxf").exists()


def test_legacy_report_includes_layout_search_1_not_requested() -> None:
    layout = generate_layout(site_from_dict(_site_data()))
    report = layout_search_report(layout)
    assert report["version"] == "layout-search-1"
    assert report["mode"] == "legacy"
    assert report["status"] == "not_requested"
    diagnostics = build_input_diagnostics(layout.site, layout)
    assert diagnostics["field_support"]["optimization.layout_search"] == "available"


def test_multi_spine_report_describes_official_layout_only(tmp_path: Path) -> None:
    site = site_from_dict(
        _site_data(
            main_aisle_lateral_offsets=[0.0, 6.0],
            layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 20.0},
            promote_candidate_layout_preview=False,
        )
    )
    dxf = tmp_path / "layout.dxf"
    svg = tmp_path / "layout.svg"
    report_path = tmp_path / "report.json"
    from openparkcad.cli import _write_output_set

    layout = generate_layout(site)
    _write_output_set(layout, dxf_path=dxf, svg_path=svg, report_path=report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["layout_search"]["version"] == "layout-search-1"
    assert payload["layout_search"]["mode"] == "multi_spine"
    assert payload["engineering_validation"]["result_scope"] == "official_layout"
    aisle_ids = {item["id"] for item in payload["aisles"]}
    assert {aisle.id for aisle in layout.aisles} == aisle_ids
    for stall in payload["stalls"]:
        served = stall.get("served_by_aisle_id")
        assert served is None or served in aisle_ids
    for aisle in payload["aisles"]:
        parent = aisle.get("parent_aisle_id")
        assert parent is None or parent in aisle_ids
    assert payload["stall_count"] == layout.stall_count
    assert dxf.stat().st_size > 0
    assert svg.stat().st_size > 0
    support = payload["input_diagnostics"]["field_support"]
    assert support["optimization.layout_search"] == "active"
    assert support["optimization.layout_search.mode"] == "active"


def test_cli_solve_twice_is_semantically_stable(tmp_path: Path) -> None:
    site_path = tmp_path / "site.json"
    site_path.write_text(
        json.dumps(
            _site_data(
                layout_search={"mode": "multi_spine", "top_k": 2, "refinement_budget_seconds": 15.0},
                promote_candidate_layout_preview=True,
            )
        ),
        encoding="utf-8",
    )
    scores = []
    stall_ids = []
    for index in (1, 2):
        out = tmp_path / f"run{index}"
        out.mkdir()
        code = main(
            [
                "solve",
                str(site_path),
                "--out",
                str(out / "layout.dxf"),
                "--preview",
                str(out / "layout.svg"),
                "--report",
                str(out / "report.json"),
            ]
        )
        assert code == 0
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        scores.append(report["score"]["total"])
        stall_ids.append(tuple(item["id"] for item in report["stalls"]))
        assert report["layout_search"]["mode"] == "multi_spine"
        assert report["engineering_validation"]["valid"] is True
        assert report["engineering_validation"]["result_scope"] == "official_layout"
    assert scores[0] == scores[1]
    assert stall_ids[0] == stall_ids[1]
