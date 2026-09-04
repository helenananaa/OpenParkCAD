from __future__ import annotations

import json
from pathlib import Path

from openparkcad import cli
from openparkcad.cad_import import source_file_sha256
from tests.cad_support import write_mapping, write_rectangle_dxf


def test_import_dxf_cli_writes_site_and_does_not_modify_source(tmp_path: Path) -> None:
    dxf = tmp_path / "site.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", extra_layer_text=True)
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="mm")
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    out = tmp_path / "site.json"
    diagnostics = tmp_path / "import.json"
    before = source_file_sha256(dxf)
    code = cli.main(
        [
            "import-dxf",
            str(dxf),
            "--mapping",
            str(mapping),
            "--defaults",
            str(defaults),
            "--out",
            str(out),
            "--diagnostics",
            str(diagnostics),
        ]
    )
    assert code == 0
    assert source_file_sha256(dxf) == before
    site = json.loads(out.read_text(encoding="utf-8"))
    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["complete_valid_site"] is True
    assert payload["source_sha256"] == before
    assert site["metadata"]["cad_import"]["source_sha256"] == before
    assert site["entrances"][0]["id"] == "ENTRY-1"


def test_failed_import_writes_diagnostics_only(tmp_path: Path) -> None:
    dxf = tmp_path / "bad.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", open_boundary=True)
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="mm")
    out = tmp_path / "site.json"
    diagnostics = tmp_path / "import.json"
    out.write_text("keep-me", encoding="utf-8")
    before = source_file_sha256(dxf)
    code = cli.main(
        [
            "import-dxf",
            str(dxf),
            "--mapping",
            str(mapping),
            "--defaults",
            "tests/fixtures/cad/project_defaults.json",
            "--out",
            str(out),
            "--diagnostics",
            str(diagnostics),
        ]
    )
    assert code == 2
    assert out.read_text(encoding="utf-8") == "keep-me"
    assert source_file_sha256(dxf) == before
    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["complete_valid_site"] is False
    assert payload["issues"]


def test_import_then_solve_two_shot_cli(tmp_path: Path) -> None:
    dxf = tmp_path / "site.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", width=40.0, height=50.0)
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="mm")
    site_json = tmp_path / "site.json"
    import_diag = tmp_path / "import.json"
    before = source_file_sha256(dxf)
    assert (
        cli.main(
            [
                "import-dxf",
                str(dxf),
                "--mapping",
                str(mapping),
                "--defaults",
                "tests/fixtures/cad/project_defaults.json",
                "--out",
                str(site_json),
                "--diagnostics",
                str(import_diag),
            ]
        )
        == 0
    )
    assert source_file_sha256(dxf) == before
    out_dir = tmp_path / "solve"
    code = cli.main(
        [
            "solve",
            str(site_json),
            "--out",
            str(out_dir / "layout.dxf"),
            "--preview",
            str(out_dir / "layout.svg"),
            "--report",
            str(out_dir / "report.json"),
            "--source-coordinates",
        ]
    )
    assert code in {0, 3}
    assert source_file_sha256(dxf) == before
    site = json.loads(site_json.read_text(encoding="utf-8"))
    assert site["metadata"]["cad_import"]["source_sha256"] == before
    if code == 0:
        svg = (out_dir / "layout.svg").read_text(encoding="utf-8")
        assert "data-cad-units=" in svg
        assert "data-source-handle=" in svg
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        assert report["stall_count"] >= 0
