import json
from pathlib import Path

import ezdxf
import pytest

from openparkcad import __version__
from openparkcad.cli import _write_report, main
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict


def _example_layout():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    return generate_layout(site_from_dict(data))


def test_official_report_has_one_versioned_engineering_decision(tmp_path: Path) -> None:
    layout = _example_layout()
    report_path = tmp_path / "report.json"

    _write_report(layout, report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    engineering = report["engineering_validation"]
    assert report["report_contract_version"] == "openparkcad-report-0.3"
    assert report["package_version"] == __version__ == "0.3.0"
    assert engineering["result_scope"] == "official_layout"
    assert engineering["valid"] is True
    assert engineering["layout"]["stall_ids"] == [stall.id for stall in layout.stalls]
    assert engineering["vehicle_validation"] == report["maneuver_validation"]["vehicle_validation"]
    assert engineering["site_constraint_validation"] == report["site_constraint_validation"]
    assert {item["id"] for item in report["stall_types"]} == {
        item.id for item in (layout.site.stall_candidates or (layout.site.stall,))
    }
    assert all("classifications" in item for item in report["stall_types"])


def test_dxf_and_svg_preserve_canonical_stall_type_identity(tmp_path: Path) -> None:
    layout = _example_layout()
    dxf_path = tmp_path / "layout.dxf"
    svg_path = tmp_path / "layout.svg"

    write_dxf(layout, dxf_path)
    write_svg(layout, svg_path)

    document = ezdxf.readfile(dxf_path)
    stall_entities = list(document.modelspace().query('LWPOLYLINE[layer=="STALLS"]'))
    assert len(stall_entities) == layout.stall_count
    first_xdata = [tag.value for tag in stall_entities[0].get_xdata("OPENPARKCAD")]
    assert first_xdata == [
        "parking_stall",
        layout.stalls[0].id,
        layout.stalls[0].stall_type_id or layout.site.stall.id,
    ]

    svg = svg_path.read_text(encoding="utf-8")
    assert f'data-stall-id="{layout.stalls[0].id}"' in svg
    assert f'data-stall-type-id="{layout.stalls[0].stall_type_id or layout.site.stall.id}"' in svg


def test_dxf_aisles_use_role_layers_and_xdata(tmp_path: Path) -> None:
    data = json.loads(Path("examples/dogleg_obstacle_site.json").read_text(encoding="utf-8"))
    layout = generate_layout(site_from_dict(data))
    dxf_path = tmp_path / "dogleg.dxf"
    write_dxf(layout, dxf_path)

    document = ezdxf.readfile(dxf_path)
    roles = {aisle.role for aisle in layout.aisles}
    if "jog" in roles:
        jog_entities = list(document.modelspace().query('LWPOLYLINE[layer=="AISLES_JOG"]'))
        assert jog_entities
        xdata = [tag.value for tag in jog_entities[0].get_xdata("OPENPARKCAD")]
        assert xdata[0] == "parking_aisle"
        assert xdata[2] == "jog"
    main_entities = list(document.modelspace().query('LWPOLYLINE[layer=="AISLES_MAIN"]'))
    assert main_entities

    svg_path = tmp_path / "dogleg.svg"
    write_svg(layout, svg_path)
    svg = svg_path.read_text(encoding="utf-8")
    assert "A-MAIN" in svg
    if any(aisle.role == "jog" for aisle in layout.aisles):
        assert "A-JOG" in svg or "A-JOG-001" in svg


def test_cli_exposes_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "openparkcad 0.3.0"
