from __future__ import annotations

import json
from pathlib import Path

from openparkcad import cli
from openparkcad.review_bundle import BUNDLE_VERSION, build_review_bundle
from openparkcad.road_traversal import apply_road_traversal
from tests.road_traversal_support import through_layout
from tests.test_cli_and_exporters import _solve_args, _valid_site_data, _write_site


def test_ut01_bundle_candidates_come_from_one_solve() -> None:
    layout = apply_road_traversal(through_layout())
    bundle = build_review_bundle(layout)
    assert bundle["version"] == BUNDLE_VERSION
    assert bundle["official"]["stall_count"] == layout.stall_count
    ids = [item["candidate_id"] for item in bundle["candidates"]]
    assert bundle["official"]["candidate_id"] in ids
    official = next(item for item in bundle["candidates"] if item["official"])
    assert official["geometry"]["stalls"]
    assert official["scores_from_evaluation"] is True
    assert official["status"] in {"official", "unsupported", "over_budget", "failed"}


def test_ut02_failures_and_journeys_are_addressable() -> None:
    layout = apply_road_traversal(through_layout())
    bundle = build_review_bundle(layout)
    assert bundle["journeys"]
    assert bundle["journeys"][0]["stall_id"] == "P-001"
    assert bundle["journeys"][0]["trajectory"]
    blocked = apply_road_traversal(through_layout(extra_obstacle=[(8.0, 2.2), (14.0, 2.2), (14.0, 7.8), (8.0, 7.8)]))
    failed = build_review_bundle(blocked)
    assert failed["failures"]
    assert failed["candidates"][0]["status"] != "official" or failed["road_traversal"]["status"] != "passed"


def test_solve_writes_review_bundle_with_official_layout(tmp_path: Path) -> None:
    site = _write_site(tmp_path / "site.json", _valid_site_data())
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    bundle_path = tmp_path / "out" / "review-bundle.json"
    args.extend(["--review-bundle", str(bundle_path)])
    assert cli.main(args) == 0
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert bundle["version"] == BUNDLE_VERSION
    assert bundle["official"]["stall_count"] == report["stall_count"]
    assert dxf_path.is_file() and svg_path.is_file()
