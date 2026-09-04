from __future__ import annotations

from pathlib import Path

import pytest

from openparkcad.delivery_manifest import HUMAN_NOT_REVIEWED, MANIFEST_VERSION, build_delivery_manifest
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from tests.test_cli_and_exporters import _valid_site_data


def test_manifest_traces_input_profile_and_output_hashes(tmp_path: Path) -> None:
    raw = _valid_site_data()
    layout = generate_layout(site_from_dict(raw))
    dxf = tmp_path / "layout.dxf"
    dxf.write_bytes(b"dxf-bytes")
    manifest = build_delivery_manifest(
        layout,
        input_bytes=__import__("json").dumps(raw).encode("utf-8"),
        output_paths={"dxf": dxf},
    )
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["input_digest"]
    assert manifest["package_version"]
    assert manifest["rule_profile"]["id"] == "private_surface_lot_v1"
    assert manifest["official_layout"]["stall_count"] == layout.stall_count
    assert manifest["output_hashes"]["dxf"]["sha256"]
    assert manifest["human_review"]["status"] == HUMAN_NOT_REVIEWED
    assert manifest["human_review"]["algorithm_pass_is_not_human_approval"] is True
    assert manifest["field_effect_claimed"] is False
    assert manifest["synthetic_trial"] is True


def test_human_review_cannot_be_auto_approved() -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    with pytest.raises(ValueError, match="cannot mark human review"):
        build_delivery_manifest(layout, human_review={"status": "approved"})
    with pytest.raises(ValueError, match="cannot mark human review"):
        build_delivery_manifest(layout, human_review={"status": "human_approved"})
    downgraded = build_delivery_manifest(layout, human_review={"status": "algorithm_pass"})
    assert downgraded["human_review"]["status"] == HUMAN_NOT_REVIEWED


def test_solve_writes_delivery_manifest(tmp_path: Path) -> None:
    from openparkcad import cli
    from tests.test_cli_and_exporters import _solve_args, _write_site, _valid_site_data

    site = _write_site(tmp_path / "site.json", _valid_site_data())
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    manifest_path = tmp_path / "out" / "manifest.json"
    review_path = tmp_path / "out" / "review-bundle.json"
    args.extend(["--delivery-manifest", str(manifest_path), "--review-bundle", str(review_path)])
    assert cli.main(args) == 0
    payload = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == MANIFEST_VERSION
    assert payload["output_hashes"]["dxf"]["sha256"]
    assert payload["output_hashes"]["svg"]["sha256"]
    assert payload["output_hashes"]["report"]["sha256"]
    assert payload["output_hashes"]["review_bundle"]["sha256"]
    assert payload["human_review"]["status"] == HUMAN_NOT_REVIEWED
    assert payload["synthetic_trial"] is True
    assert payload["field_effect_claimed"] is False
