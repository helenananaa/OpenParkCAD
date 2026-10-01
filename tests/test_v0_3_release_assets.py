from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import openparkcad
from openparkcad.models import site_from_dict


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "openparkcad-input.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "v0_3"
FIXTURE_PATHS = sorted(FIXTURE_DIR.glob("*.json"))


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_input_schema_is_valid_draft_2020_12() -> None:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "acceptance does not imply" in str(schema["description"])


def test_executable_example_matches_input_schema() -> None:
    schema = _load_json(SCHEMA_PATH)
    example = _load_json(ROOT / "examples" / "phase0_site.json")
    Draft202012Validator(schema).validate(example)


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_v0_3_fixture_matches_schema_and_parser(fixture_path: Path) -> None:
    raw = _load_json(fixture_path)
    Draft202012Validator(_load_json(SCHEMA_PATH)).validate(raw)

    site = site_from_dict(raw)
    assert site.version == "0.3"
    assert site.vehicle is not None
    assert site.vehicle.wheelbase is not None
    assert site.vehicle.min_turning_radius is not None
    assert len(site.boundary) > 4

    metadata = raw["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["fixture_provenance"] == "synthetic_representative_real_site_shaped"

    maneuvering = raw["constraints"]["maneuvering"]
    assert maneuvering["require_turning_radius_check"] is True
    assert maneuvering["require_swept_path_check"] is True
    assert raw["regression_expectations"]["boundary_non_rectangular"] is True


def test_fixture_outcomes_cover_pass_vehicle_reject_and_quota_reject() -> None:
    outcomes = {
        _load_json(path)["regression_expectations"]["intended_outcome"]
        for path in FIXTURE_PATHS
    }
    assert outcomes == {
        "valid_only_when_all_requested_checks_are_active",
        "invalid",
        "invalid_if_quotas_cannot_be_preserved",
    }


def test_package_versions_and_release_documents_are_consistent() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
    assert version_match is not None
    assert version_match.group(1) == openparkcad.__version__ == "0.3.0"
    assert 'license = "MIT"' in pyproject
    assert "License :: OSI Approved :: MIT License" not in pyproject

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 OpenParkCAD contributors" in license_text

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-07-14" in changelog
    assert "## [0.3.0] - Unreleased" in changelog
