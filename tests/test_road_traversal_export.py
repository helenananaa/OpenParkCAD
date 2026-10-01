from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import ezdxf

from openparkcad.cli import _write_report
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.road_traversal import apply_road_traversal, journey_trajectories
from tests.road_traversal_support import through_layout


def test_rt19_export_identities_match_report_dxf_and_svg(tmp_path: Path) -> None:
    layout = apply_road_traversal(through_layout())
    assert layout.road_traversal_validation["status"] == "passed"
    identity = layout.road_traversal_validation["layout_identity"]
    traces = journey_trajectories(layout)
    assert traces
    stall_ids = {stall_id for stall_id, _points in traces}
    assert stall_ids == {stall.id for stall in layout.stalls}

    dxf_path = tmp_path / "layout.dxf"
    svg_path = tmp_path / "layout.svg"
    report_path = tmp_path / "report.json"
    write_dxf(layout, dxf_path)
    write_svg(layout, svg_path)
    _write_report(layout, report_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["road_traversal_validation"]["layout_identity"] == identity
    assert report["road_traversal_validation"]["status"] == "passed"
    assert {item["stall_id"] for item in report["road_traversal_validation"]["journeys"]} == stall_ids
    assert {aisle["id"] for aisle in report["aisles"]} == {aisle.id for aisle in layout.aisles}

    doc = ezdxf.readfile(dxf_path)
    traj_ids = []
    for entity in doc.modelspace().query('LWPOLYLINE[layer=="TRAJECTORIES"]'):
        values = [tag.value for tag in entity.get_xdata("OPENPARKCAD")]
        traj_ids.extend(value for value in values if value in stall_ids)
        assert identity in values
    assert set(traj_ids) == stall_ids

    root = ElementTree.parse(svg_path).getroot()
    group = root.find(".//{http://www.w3.org/2000/svg}g[@id='road-traversal']")
    assert group is not None
    assert group.get("data-layout-identity") == identity
    svg_stalls = {
        item.get("data-stall-id")
        for item in group.findall("{http://www.w3.org/2000/svg}polyline")
        if item.get("data-stall-id")
    }
    assert svg_stalls == stall_ids
