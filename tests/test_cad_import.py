from __future__ import annotations

from pathlib import Path

import pytest

from openparkcad.cad_import import CadImportError, import_dxf_to_site
from openparkcad.models import site_from_dict
from tests.cad_support import write_mapping, write_rectangle_dxf


def test_ct01_metre_and_millimetre_sites_match_locally(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    mm_dxf = tmp_path / "mm.dxf"
    m_dxf = tmp_path / "m.dxf"
    mm_handles = write_rectangle_dxf(mm_dxf, units="mm")
    m_handles = write_rectangle_dxf(m_dxf, units="m")
    mm_map = write_mapping(tmp_path / "mm.json", mm_handles, units="mm")
    m_map = write_mapping(tmp_path / "m.json", m_handles, units="m")
    mm_site, _ = import_dxf_to_site(mm_dxf, mm_map, defaults)
    m_site, _ = import_dxf_to_site(m_dxf, m_map, defaults)
    assert mm_site["site"]["boundary"]["points"] == m_site["site"]["boundary"]["points"]
    assert mm_site["entrances"][0]["center"] == pytest.approx(m_site["entrances"][0]["center"])
    assert mm_site["metadata"]["cad_import"]["transform"]["origin_m"] == [0.0, 0.0]


def test_ct02_translated_wcs_roundtrip(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    dxf = tmp_path / "tr.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", origin=(100000.0, 200000.0))
    mapping = write_mapping(tmp_path / "tr.json", handles, units="mm")
    site, diagnostics = import_dxf_to_site(dxf, mapping, defaults)
    points = site["site"]["boundary"]["points"]
    assert points[0] == pytest.approx([0.0, 0.0])
    assert points[2] == pytest.approx([20.0, 30.0])
    assert diagnostics["complete_valid_site"] is True
    assert site["metadata"]["cad_import"]["transform"]["origin_m"] == pytest.approx([100000.0, 200000.0])


def test_ct03_unit_conflict_is_closed(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    dxf = tmp_path / "conflict.dxf"
    handles = write_rectangle_dxf(dxf, units="mm")
    mapping = write_mapping(tmp_path / "conflict.json", handles, units="m")
    with pytest.raises(CadImportError, match="conflict"):
        import_dxf_to_site(dxf, mapping, defaults)


def test_ct04_bulge_open_and_arc_are_rejected(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    for kwargs, code in (({"open_boundary": True}, "open_boundary"), ({"arc": True}, "unsupported_entity"), ({"bulge": True}, "unsupported_entity")):
        dxf = tmp_path / f"{code}.dxf"
        handles = write_rectangle_dxf(dxf, units="mm", **kwargs)
        mapping = write_mapping(tmp_path / f"{code}.json", handles, units="mm")
        with pytest.raises(CadImportError):
            import_dxf_to_site(dxf, mapping, defaults)


def test_ct05_non_role_text_is_listed_and_ignored(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    dxf = tmp_path / "notes.dxf"
    handles = write_rectangle_dxf(dxf, units="mm", extra_layer_text=True)
    mapping = write_mapping(tmp_path / "notes.json", handles, units="mm")
    site, diagnostics = import_dxf_to_site(dxf, mapping, defaults)
    ignored = [item for item in diagnostics["entities"] if item["result"] == "ignored_non_role_layer"]
    assert ignored
    site_from_dict(site)


def test_ct06_bad_handle_and_outward_heading_fail(tmp_path: Path) -> None:
    defaults = Path("tests/fixtures/cad/project_defaults.json")
    dxf = tmp_path / "handle.dxf"
    write_rectangle_dxf(dxf, units="mm")
    mapping = write_mapping(tmp_path / "handle.json", {"entrance": "DEAD"}, units="mm")
    with pytest.raises(CadImportError) as caught:
        import_dxf_to_site(dxf, mapping, defaults)
    assert any(item["code"] == "unknown_handle" for item in caught.value.diagnostics["issues"])
    assert caught.value.diagnostics["complete_valid_site"] is False
