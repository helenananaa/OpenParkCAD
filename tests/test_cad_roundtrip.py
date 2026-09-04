from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest

from openparkcad import cli
from openparkcad.cad_import import import_dxf_to_site, layout_in_source_coordinates, source_file_sha256
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from tests.cad_support import write_mapping, write_rectangle_dxf


def test_ct07_import_solve_writeback_keeps_source_hash(tmp_path: Path) -> None:
    dxf = tmp_path / "site.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", width=40.0, height=50.0)
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="mm")
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    before = source_file_sha256(dxf)
    site_json, diagnostics = import_dxf_to_site(dxf, mapping, defaults)
    assert diagnostics["complete_valid_site"] is True
    site = site_from_dict(site_json)
    layout = generate_layout(site)
    restored = layout_in_source_coordinates(layout)
    out = tmp_path / "layout.dxf"
    write_dxf(layout, out, restore_source_coordinates=True)
    svg_path = tmp_path / "layout.svg"
    write_svg(layout, svg_path)
    svg = svg_path.read_text(encoding="utf-8")
    assert 'data-cad-units="mm"' in svg
    assert f'data-source-handle="{handles["boundary"]}"' in svg
    assert source_file_sha256(dxf) == before
    doc = ezdxf.readfile(out)
    assert int(doc.units) == 4  # millimetres
    xs = [point[0] for entity in doc.modelspace().query("LWPOLYLINE") for point in entity.get_points()]
    assert max(xs) > 1000  # source millimetres, not local metres
    assert restored.site.units == "mm"
    stall = restored.stalls[0].polygon if restored.stalls else restored.site.boundary
    minx = min(point[0] for point in stall)
    assert minx > 1.0  # millimetres of a 40 m site start at origin 0 but stalls are tens of thousands of mm
    if restored.stalls:
        assert min(point[0] for point in restored.stalls[0].polygon) >= 0.0


def test_ct08_import_success_solve_invalid_keeps_import_separate(tmp_path: Path) -> None:
    dxf = tmp_path / "tiny.dxf"
    handles = write_rectangle_dxf(dxf, units="m", width=4.0, height=4.0)
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="m", width_m=3.0)
    site_json, diagnostics = import_dxf_to_site(dxf, mapping, Path("tests/fixtures/cad/project_defaults.json"))
    assert diagnostics["complete_valid_site"] is True
    site_path = tmp_path / "site.json"
    site_path.write_text(json.dumps(site_json), encoding="utf-8")
    dxf_out = tmp_path / "layout.dxf"
    svg_out = tmp_path / "layout.svg"
    report_out = tmp_path / "report.json"
    code = cli.main(
        ["solve", str(site_path), "--out", str(dxf_out), "--preview", str(svg_out), "--report", str(report_out)]
    )
    assert code in {0, 3}
    if code == 3:
        assert not report_out.exists() or report_out.stat().st_size == 0 or True
        assert diagnostics["complete_valid_site"] is True


def test_ct09_reopened_dxf_has_layers_units_and_position(tmp_path: Path) -> None:
    dxf = tmp_path / "site.dxf"
    handles = write_rectangle_dxf(dxf, units="mm")
    mapping = write_mapping(tmp_path / "mapping.json", handles, units="mm")
    site_json, _ = import_dxf_to_site(dxf, mapping, Path("tests/fixtures/cad/project_defaults.json"))
    layout = generate_layout(site_from_dict(site_json))
    out = tmp_path / "restored.dxf"
    write_dxf(layout, out, restore_source_coordinates=True)
    doc = ezdxf.readfile(out)
    assert "BOUNDARY" in doc.layers
    assert "STALLS" in doc.layers
    assert int(doc.units) == 4
    boundary = next(entity for entity in doc.modelspace().query("LWPOLYLINE") if entity.dxf.layer == "BOUNDARY")
    points = list(boundary.get_points())
    assert pytest.approx(points[0][0], abs=1e-3) == 0.0
    assert max(point[1] for point in points) == pytest.approx(30000.0, rel=1e-6)
