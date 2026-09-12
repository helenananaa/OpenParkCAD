from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openparkcad.delivery_manifest import HUMAN_NOT_REVIEWED, MANIFEST_VERSION, build_delivery_manifest
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from tests.test_cli_and_exporters import _solve_args, _valid_site_data, _write_site


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_solve_writes_delivery_manifest(tmp_path: Path) -> None:
    from openparkcad import cli

    site = _write_site(tmp_path / "site.json", _valid_site_data())
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    manifest_path = tmp_path / "out" / "manifest.json"
    review_path = tmp_path / "out" / "review-bundle.json"
    args.extend(["--delivery-manifest", str(manifest_path), "--review-bundle", str(review_path)])
    assert cli.main(args) == 0
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == MANIFEST_VERSION
    assert payload["output_hashes"]["dxf"]["sha256"] == _file_sha256(dxf_path)
    assert payload["output_hashes"]["svg"]["sha256"] == _file_sha256(svg_path)
    assert payload["output_hashes"]["report"]["sha256"] == _file_sha256(report_path)
    assert payload["output_hashes"]["review_bundle"]["sha256"] == _file_sha256(review_path)
    assert payload["human_review"]["status"] == HUMAN_NOT_REVIEWED
    assert payload["synthetic_trial"] is True
    assert payload["field_effect_claimed"] is False
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rule_profile"]["id"] == payload["rule_profile"]["id"]


def test_manifest_generate_failure_keeps_existing_output_set(tmp_path: Path, capsys, monkeypatch) -> None:
    from openparkcad import cli

    site = _write_site(tmp_path / "site.json", _valid_site_data())
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    manifest_path = tmp_path / "out" / "manifest.json"
    review_path = tmp_path / "out" / "review-bundle.json"
    args.extend(["--delivery-manifest", str(manifest_path), "--review-bundle", str(review_path)])
    dxf_path.parent.mkdir(parents=True)
    sentinels = {
        dxf_path: "old dxf",
        svg_path: "old svg",
        report_path: "old report",
        review_path: "old review",
        manifest_path: "old manifest",
    }
    for path, content in sentinels.items():
        path.write_text(content, encoding="utf-8")

    valid_layout = SimpleNamespace(
        stall_count=1,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        operational_quality={"valid": True},
        site_constraint_validation={"valid": True},
        engineering_validation={"valid": True},
        road_traversal_validation={},
    )
    monkeypatch.setattr(cli, "generate_layout", lambda site: valid_layout)

    def render(layout, path, **_kwargs) -> None:
        Path(path).write_text("new output", encoding="utf-8")

    monkeypatch.setattr(cli, "write_dxf", render)
    monkeypatch.setattr(cli, "write_svg", render)
    monkeypatch.setattr(cli, "_write_report", render)
    monkeypatch.setattr(cli, "_write_review_bundle", render)

    def fail_manifest(*_args, **_kwargs):
        raise OSError("simulated manifest failure")

    monkeypatch.setattr("openparkcad.delivery_manifest.build_delivery_manifest", fail_manifest)

    exit_code = cli.main(args)
    captured = capsys.readouterr()
    assert exit_code == 4
    assert "simulated manifest failure" in captured.err
    for path, content in sentinels.items():
        assert path.read_text(encoding="utf-8") == content
    assert not list(dxf_path.parent.glob(".*.tmp"))
    assert not list(dxf_path.parent.glob(".*.bak"))


def test_manifest_commit_failure_restores_previous_output_set(tmp_path: Path, capsys, monkeypatch) -> None:
    from openparkcad import cli

    site = _write_site(tmp_path / "site.json", _valid_site_data())
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    manifest_path = tmp_path / "out" / "manifest.json"
    review_path = tmp_path / "out" / "review-bundle.json"
    args.extend(["--delivery-manifest", str(manifest_path), "--review-bundle", str(review_path)])
    dxf_path.parent.mkdir(parents=True)
    sentinels = {
        dxf_path: "old dxf",
        svg_path: "old svg",
        report_path: "old report",
        review_path: "old review",
        manifest_path: "old manifest",
    }
    for path, content in sentinels.items():
        path.write_text(content, encoding="utf-8")

    valid_layout = SimpleNamespace(
        stall_count=1,
        graph_validation={"valid": True},
        maneuver_validation={"valid": True},
        operational_quality={"valid": True},
        site_constraint_validation={"valid": True},
        engineering_validation={"valid": True},
        road_traversal_validation={},
    )
    monkeypatch.setattr(cli, "generate_layout", lambda site: valid_layout)

    def render(layout, path, **_kwargs) -> None:
        Path(path).write_text("new output", encoding="utf-8")

    monkeypatch.setattr(cli, "write_dxf", render)
    monkeypatch.setattr(cli, "write_svg", render)
    monkeypatch.setattr(cli, "_write_report", render)
    monkeypatch.setattr(cli, "_write_review_bundle", render)
    monkeypatch.setattr(
        "openparkcad.delivery_manifest.build_delivery_manifest",
        lambda *_args, **_kwargs: {"version": MANIFEST_VERSION, "output_hashes": {}},
    )

    real_replace = cli.os.replace

    def fail_manifest_commit(source, destination) -> None:
        if Path(destination) == manifest_path and Path(source).suffix == ".tmp":
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_manifest_commit)

    exit_code = cli.main(args)
    captured = capsys.readouterr()
    assert exit_code == 4
    assert "simulated commit failure" in captured.err
    for path, content in sentinels.items():
        assert path.read_text(encoding="utf-8") == content
    assert not list(dxf_path.parent.glob(".*.tmp"))
    assert not list(dxf_path.parent.glob(".*.bak"))
