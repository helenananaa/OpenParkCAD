from pathlib import Path

import pytest

from openparkcad.models import SiteSpec
from openparkcad.rule_profiles import (
    CUSTOM_PROFILE_TOKEN,
    DEFAULT_PROFILE_ID,
    load_profile,
    parse_rule_profile,
    profile_from_site,
    resolve_profile_id,
)


def _site(**standards) -> SiteSpec:
    return SiteSpec(name="x", boundary=[(0, 0), (1, 0), (1, 1), (0, 1)], standards=dict(standards))


def test_profile_only_claims_executed_checks() -> None:
    profile = load_profile(DEFAULT_PROFILE_ID)
    assert profile.claims_check("road_traversal_if_requested")
    assert profile.claims_check("traffic_graph")
    assert not profile.claims_check("slope_or_elevation")
    assert "permit_or_code_certification" in profile.unsupported
    assert "jurisdiction_compliance" in profile.human_review_required
    assert profile.source == "software-executed-checks-only"


def test_unknown_profile_is_rejected() -> None:
    try:
        load_profile("imaginary-jurisdiction")
        raise AssertionError("expected unknown profile error")
    except ValueError as exc:
        assert "unknown rule profile" in str(exc)


def test_missing_and_custom_resolve_to_default() -> None:
    assert resolve_profile_id(None).profile_id == DEFAULT_PROFILE_ID
    assert resolve_profile_id("").profile_id == DEFAULT_PROFILE_ID
    assert resolve_profile_id("  ").profile_id == DEFAULT_PROFILE_ID
    assert resolve_profile_id(CUSTOM_PROFILE_TOKEN).profile_id == DEFAULT_PROFILE_ID
    assert profile_from_site(_site()).profile_id == DEFAULT_PROFILE_ID
    assert profile_from_site(_site(standard_profile="custom")).profile_id == DEFAULT_PROFILE_ID
    assert profile_from_site(_site(rule_profile="custom")).profile_id == DEFAULT_PROFILE_ID
    assert parse_rule_profile({}).profile_id == DEFAULT_PROFILE_ID
    assert parse_rule_profile({"id": "custom"}).profile_id == DEFAULT_PROFILE_ID


def test_explicit_unknown_profile_from_site_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown rule profile 'imaginary-jurisdiction'"):
        profile_from_site(_site(rule_profile="imaginary-jurisdiction"))
    with pytest.raises(ValueError, match="unknown rule profile 'not-a-profile'"):
        resolve_profile_id("not-a-profile")


def test_legacy_standard_profile_is_metadata_not_an_executable_profile() -> None:
    assert profile_from_site(_site(standard_profile="project-policy-only")).profile_id == DEFAULT_PROFILE_ID
    assert profile_from_site(_site(standard_profile="named-external-profile-2026")).profile_id == DEFAULT_PROFILE_ID


def test_site_custom_profile_does_not_invent_jurisdiction() -> None:
    site = _site(standard_profile="custom")
    profile = profile_from_site(site)
    assert profile.profile_id == DEFAULT_PROFILE_ID
    assert parse_rule_profile({}).profile_id == DEFAULT_PROFILE_ID


def test_solve_rejects_unknown_explicit_profile(tmp_path: Path, capsys) -> None:
    import json

    from openparkcad import cli
    from tests.test_cli_and_exporters import _solve_args, _valid_site_data, _write_site

    data = _valid_site_data()
    data["standards"] = {"rule_profile": "imaginary-jurisdiction"}
    site = _write_site(tmp_path / "site.json", data)
    args, dxf_path, svg_path, report_path = _solve_args(site, tmp_path / "out")
    exit_code = cli.main(args)
    captured = capsys.readouterr()
    assert exit_code != 0
    assert "unknown rule profile" in captured.err
    assert "imaginary-jurisdiction" in captured.err
    assert not any(path.exists() for path in (dxf_path, svg_path, report_path))
    for leftover in tmp_path.rglob("*"):
        if leftover.suffix.lower() in {".json", ".dxf", ".svg"} and leftover != site:
            payload = leftover.read_text(encoding="utf-8", errors="ignore")
            assert DEFAULT_PROFILE_ID not in payload
            if leftover.suffix.lower() == ".json":
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    assert parsed.get("rule_profile", {}).get("id") != DEFAULT_PROFILE_ID


def test_solve_accepts_legacy_standard_profile_metadata(tmp_path: Path) -> None:
    import json

    from openparkcad import cli
    from tests.test_cli_and_exporters import _solve_args, _valid_site_data, _write_site

    data = _valid_site_data()
    data["standards"] = {"standard_profile": "project-policy-only"}
    site = _write_site(tmp_path / "site.json", data)
    args, _dxf_path, _svg_path, report_path = _solve_args(site, tmp_path / "out")
    assert cli.main(args) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rule_profile"]["id"] == DEFAULT_PROFILE_ID
