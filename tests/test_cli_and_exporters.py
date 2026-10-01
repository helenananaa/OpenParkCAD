from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import ezdxf

from openparkcad import cli
from openparkcad.exporter_svg import write_svg
from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec


def _valid_site_data() -> dict[str, object]:
    return {
        "version": "0.1",
        "name": "CLI integration site",
        "site": {
            "boundary": {
                "type": "polygon",
                "points": [[0, 0], [24, 0], [24, 34], [0, 34]],
            }
        },
        "entrances": [
            {
                "id": "main",
                "mode": "shared",
                "center": [12, 0],
                "width": 7.0,
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
                }
            ]
        },
        "aisles": {
            "selection_mode": "fixed",
            "fixed_class": "wide-two-way",
            "classes": [
                {
                    "id": "wide-two-way",
                    "width": 6.0,
                    "capacity": "two_vehicle",
                    "directionality": "two_way",
                }
            ],
        },
        "constraints": {"setbacks": {"site_boundary": 0.0}},
    }


def _write_site(path: Path, data: object | None = None) -> Path:
    path.write_text(json.dumps(_valid_site_data() if data is None else data), encoding="utf-8")
    return path


def _solve_args(site: Path, output_dir: Path) -> tuple[list[str], Path, Path, Path]:
    dxf_path = output_dir / "layout.dxf"
    svg_path = output_dir / "layout.svg"
    report_path = output_dir / "report.json"
    return (
        [
            "solve",
            str(site),
            "--out",
            str(dxf_path),
            "--preview",
            str(svg_path),
            "--report",
            str(report_path),
        ],
        dxf_path,
        svg_path,
        report_path,
    )


def test_svg_escapes_model_labels_and_remains_valid_xml(tmp_path: Path) -> None:
    unsafe = "label <&> \"quoted\" 'single'"
    site = SiteSpec(
        name=unsafe,
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        entrances=[
            EntranceSpec(
                id=f"entrance {unsafe}",
                mode="shared",
                center=(10, 0),
                width=6.0,
                heading_degrees=90.0,
            )
        ],
        site_features=[
            {
                "id": f"feature {unsafe}",
                "geometry": {"type": "circle", "center": [3, 3], "radius": 0.5},
            }
        ],
        pedestrian_and_emergency={
            "pedestrian_routes": [
                {
                    "id": f"route {unsafe}",
                    "geometry": {
                        "type": "polyline_buffer",
                        "points": [[1, 18], [8, 18]],
                        "width": 1.0,
                    },
                }
            ]
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id=f"stall {unsafe}",
                polygon=[(2, 8), (4, 8), (4, 13), (2, 13)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(5, 0), (15, 0), (15, 20), (5, 20)],
                angle_degrees=90.0,
                role="main",
                connected_to_entrance_id=site.entrances[0].id,
            )
        ],
        candidate_network_preview={
            "aisles": [
                {
                    "id": f"preview aisle {unsafe}",
                    "role": "branch",
                    "geometry": [[12, 8], [18, 8], [18, 11], [12, 11]],
                }
            ]
        },
        candidate_layout_preview={
            "stalls": [
                {
                    "id": f"preview stall {unsafe}",
                    "source": "shadow_candidate",
                    "geometry": [[15, 12], [17, 12], [17, 17], [15, 17]],
                }
            ]
        },
    )
    target = tmp_path / "unsafe.svg"

    write_svg(layout, target)

    root = ElementTree.parse(target).getroot()
    visible_text = " ".join(text.strip() for text in root.itertext() if text.strip())
    assert unsafe in visible_text
    raw = target.read_text(encoding="utf-8")
    assert "&lt;&amp;&gt;" in raw
    assert "&quot;quoted&quot;" in raw
    assert "&apos;single&apos;" in raw


def test_cli_solve_writes_readable_dxf_svg_and_report(tmp_path: Path, capsys) -> None:
    site_path = _write_site(tmp_path / "site.json")
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "stalls:" in captured.out

    document = ezdxf.readfile(dxf_path)
    audit = document.audit()
    assert not audit.errors
    assert len(document.modelspace()) > 0

    root = ElementTree.parse(svg_path).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["site"] == "CLI integration site"
    assert report["stall_count"] > 0
    assert report["aisle_count"] > 0
    assert report["traffic_graph"]["validation"]["valid"] is True
    assert report["maneuver_validation"]["valid"] is True
    assert report["operational_quality"]["valid"] is True
    assert not list((tmp_path / "output").glob(".*.tmp"))
    assert not list((tmp_path / "output").glob(".*.bak"))


def test_cli_reports_missing_file_without_traceback(tmp_path: Path, capsys) -> None:
    site_path = tmp_path / "missing.json"
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "error: site file not found:" in captured.err
    assert "Traceback" not in captured.err
    assert not any(path.exists() for path in (dxf_path, svg_path, report_path))


def test_cli_reports_bad_json_without_traceback(tmp_path: Path, capsys) -> None:
    site_path = tmp_path / "bad.json"
    site_path.write_text('{"site": ', encoding="utf-8")
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "error: invalid JSON" in captured.err
    assert "line 1, column" in captured.err
    assert "Traceback" not in captured.err
    assert not any(path.exists() for path in (dxf_path, svg_path, report_path))


def test_cli_reports_invalid_site_input_without_traceback(tmp_path: Path, capsys) -> None:
    site_path = _write_site(tmp_path / "invalid.json", {"name": "missing site"})
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "error: invalid site input:" in captured.err
    assert "top-level 'site' object" in captured.err
    assert "Traceback" not in captured.err
    assert not any(path.exists() for path in (dxf_path, svg_path, report_path))


def test_cli_rejects_invalid_final_layout_before_export(tmp_path: Path, capsys, monkeypatch) -> None:
    site_path = _write_site(tmp_path / "site.json")
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")
    invalid_layout = SimpleNamespace(
        stall_count=1,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        operational_quality={"valid": False, "risk_score": 7.0},
    )
    monkeypatch.setattr(cli, "generate_layout", lambda site: invalid_layout)

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "error: no valid final layout:" in captured.err
    assert "operational quality hard rejection (risk score 7)" in captured.err
    assert not any(path.exists() for path in (dxf_path, svg_path, report_path))


def test_cli_keeps_existing_output_set_when_rendering_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    site_path = _write_site(tmp_path / "site.json")
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")
    dxf_path.parent.mkdir(parents=True)
    sentinels = {
        dxf_path: "old dxf",
        svg_path: "old svg",
        report_path: "old report",
    }
    for path, content in sentinels.items():
        path.write_text(content, encoding="utf-8")

    valid_layout = SimpleNamespace(
        stall_count=1,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        operational_quality={"valid": True},
    )
    monkeypatch.setattr(cli, "generate_layout", lambda site: valid_layout)
    monkeypatch.setattr(
        cli,
        "write_dxf",
        lambda layout, path, **_kwargs: Path(path).write_text("new dxf", encoding="utf-8"),
    )

    def fail_svg(layout, path) -> None:
        Path(path).write_text("partial svg", encoding="utf-8")
        raise OSError("simulated SVG failure")

    monkeypatch.setattr(cli, "write_svg", fail_svg)

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code == 4
    assert "error: could not write outputs: simulated SVG failure" in captured.err
    for path, content in sentinels.items():
        assert path.read_text(encoding="utf-8") == content
    assert not list(dxf_path.parent.glob(".*.tmp"))
    assert not list(dxf_path.parent.glob(".*.bak"))


def test_cli_rolls_back_existing_output_set_when_commit_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    site_path = _write_site(tmp_path / "site.json")
    args, dxf_path, svg_path, report_path = _solve_args(site_path, tmp_path / "output")
    dxf_path.parent.mkdir(parents=True)
    sentinels = {
        dxf_path: "old dxf",
        svg_path: "old svg",
        report_path: "old report",
    }
    for path, content in sentinels.items():
        path.write_text(content, encoding="utf-8")

    valid_layout = SimpleNamespace(
        stall_count=1,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        operational_quality={"valid": True},
    )
    monkeypatch.setattr(cli, "generate_layout", lambda site: valid_layout)

    def render(layout, path, **_kwargs) -> None:
        Path(path).write_text("new output", encoding="utf-8")

    monkeypatch.setattr(cli, "write_dxf", render)
    monkeypatch.setattr(cli, "write_svg", render)
    monkeypatch.setattr(cli, "_write_report", render)

    real_replace = cli.os.replace

    def fail_report_commit(source, destination) -> None:
        if Path(destination) == report_path and Path(source).suffix == ".tmp":
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_report_commit)

    exit_code = cli.main(args)

    captured = capsys.readouterr()
    assert exit_code == 4
    assert "error: could not write outputs: simulated commit failure" in captured.err
    for path, content in sentinels.items():
        assert path.read_text(encoding="utf-8") == content
    assert not list(dxf_path.parent.glob(".*.tmp"))
    assert not list(dxf_path.parent.glob(".*.bak"))
