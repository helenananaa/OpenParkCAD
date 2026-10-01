from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from openparkcad.layout_benchmark import (
    EXPECTED_CASE_COUNT,
    MANIFEST_VERSION,
    NOT_AVAILABLE,
    apply_optimization_overlay,
    benchmark_exit_code,
    load_manifest,
    run_benchmark,
    sha256_file,
)
from tests.ortools_util import require_ortools

REPO_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_MANIFEST = REPO_ROOT / "benchmarks" / "layout_v0_4.json"
TINY_VALID = "tests/fixtures/benchmark/tiny_valid.json"
TINY_REJECT = "tests/fixtures/benchmark/tiny_vehicle_reject.json"


def _mini_manifest(path: Path, cases: list[dict[str, object]]) -> Path:
    payload = {
        "version": MANIFEST_VERSION,
        "smoke_case_ids": [cases[0]["case_id"]],
        "cases": cases,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _case(case_id: str, rel_path: str, expectation: str, required_checks: list[str]) -> dict[str, object]:
    return {
        "case_id": case_id,
        "path": rel_path,
        "provenance": "synthetic",
        "tags": ["protocol"],
        "expectation": expectation,
        "required_checks": required_checks,
    }


def test_official_manifest_lists_all_explicit_existing_inputs() -> None:
    manifest = load_manifest(OFFICIAL_MANIFEST, repo_root=REPO_ROOT, expected_case_count=EXPECTED_CASE_COUNT)
    assert manifest["version"] == MANIFEST_VERSION
    assert len(manifest["cases"]) == EXPECTED_CASE_COUNT
    paths = [case["path"] for case in manifest["cases"]]
    assert len(set(paths)) == EXPECTED_CASE_COUNT
    assert not any("*" in path or "?" in path for path in paths)
    example_paths = sorted(
        path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "examples").glob("*.json")
    )
    fixture_paths = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests/fixtures/v0_3").glob("*.json")
    )
    assert set(example_paths).issubset(set(paths))
    assert set(fixture_paths).issubset(set(paths))
    assert set(manifest["smoke_case_ids"]) == {
        "phase0-site",
        "dogleg-obstacle",
        "dogleg-one-way-dual-entrance",
        "tight-rear-court",
        "offset-gate-quota",
    }


def test_manifest_rejects_glob_paths(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bad.json"
    _mini_manifest(
        manifest_path,
        [
            _case("globbed", "examples/*.json", "valid_with_required_checks", ["graph"]),
        ],
    )
    try:
        load_manifest(manifest_path, repo_root=REPO_ROOT, expected_case_count=1)
    except ValueError as exc:
        assert "explicit relative path" in str(exc)
        assert "glob" in str(exc)
    else:
        raise AssertionError("glob path must be rejected")


def test_overlay_only_touches_optimization_and_keeps_source_intact() -> None:
    original = json.loads((REPO_ROOT / TINY_VALID).read_text(encoding="utf-8"))
    overlay = {"selector_backend": "cpsat", "promote_candidate_layout_preview": True}
    effective = apply_optimization_overlay(original, overlay)
    assert "selector_backend" not in original["optimization"]
    assert original["optimization"]["promote_candidate_layout_preview"] is False
    assert effective["optimization"]["selector_backend"] == "cpsat"
    assert effective["optimization"]["promote_candidate_layout_preview"] is True
    assert effective["name"] == original["name"]


def test_runner_protocol_valid_reject_exception_and_immutability(tmp_path: Path) -> None:
    manifest_path = tmp_path / "mini.json"
    _mini_manifest(
        manifest_path,
        [
            _case("tiny-valid", TINY_VALID, "valid_with_required_checks", ["graph", "maneuver", "site", "engineering", "operational"]),
            _case("tiny-reject", TINY_REJECT, "reject_vehicle_policy", ["vehicle"]),
            _case("tiny-exception", TINY_VALID, "valid_with_required_checks", ["graph"]),
        ],
    )
    source = REPO_ROOT / TINY_VALID
    before = sha256_file(source)
    out_dir = tmp_path / "run"
    payload = run_benchmark(
        manifest_path=manifest_path,
        profile="legacy",
        subset="full",
        repeats=1,
        timeout_seconds=60.0,
        out_dir=out_dir,
        repo_root=REPO_ROOT,
        variant_filter=("legacy-greedy",),
        expected_case_count=3,
        extra_env={"OPENPARKCAD_BENCHMARK_DEBUG_RAISE_CASE": "tiny-exception"},
    )
    by_id = {item["case_id"]: item for item in payload["runs"]}
    valid = json.loads((out_dir / "cases/tiny-valid/legacy-greedy/1/result.json").read_text(encoding="utf-8"))
    reject = json.loads((out_dir / "cases/tiny-reject/legacy-greedy/1/result.json").read_text(encoding="utf-8"))
    injected = json.loads((out_dir / "cases/tiny-exception/legacy-greedy/1/result.json").read_text(encoding="utf-8"))

    assert by_id["tiny-valid"]["outcome"] == "valid"
    assert valid["expectation_met"] is True
    assert valid["result_scope"] == "official_layout"
    valid_dir = out_dir / "cases/tiny-valid/legacy-greedy/1"
    assert (valid_dir / "layout.dxf").stat().st_size > 0
    assert (valid_dir / "layout.svg").stat().st_size > 0
    assert (valid_dir / "report.json").stat().st_size > 0
    official = json.loads((valid_dir / "report.json").read_text(encoding="utf-8"))
    assert official["engineering_validation"]["result_scope"] == "official_layout"
    assert official["stall_count"] == valid["final_result"]["stall_count"]

    assert by_id["tiny-reject"]["outcome"] == "invalid"
    assert reject["expectation_met"] is True
    assert reject["result_scope"] == "benchmark_diagnostic"
    reject_dir = out_dir / "cases/tiny-reject/legacy-greedy/1"
    assert not (reject_dir / "layout.dxf").exists()
    assert not (reject_dir / "layout.svg").exists()
    assert not (reject_dir / "report.json").exists()
    assert reject["checks"]["vehicle"]["executed"] is True
    assert reject["checks"]["vehicle"]["valid"] is False

    assert by_id["tiny-exception"]["outcome"] == "exception"
    assert injected["expectation_met"] is False
    assert injected["result_scope"] == "benchmark_diagnostic"
    assert "injected benchmark exception" in str(injected["error"])
    assert not (out_dir / "cases/tiny-exception/legacy-greedy/1/layout.dxf").exists()

    assert sha256_file(source) == before
    assert valid["identity"]["source_input_mutated"] is False
    assert reject["identity"]["source_input_mutated"] is False
    unexpected_ids = {item["case_id"] for item in payload["summary"]["unexpected"]}
    assert "tiny-valid" not in unexpected_ids
    assert "tiny-reject" not in unexpected_ids
    assert "tiny-exception" in unexpected_ids
    reasons = payload["summary"]["exit_nonzero_reasons"]
    assert "expectation_failures_or_errors" in reasons
    assert benchmark_exit_code(payload) != 0


def test_runner_records_timeout_without_official_triple(tmp_path: Path) -> None:
    manifest_path = tmp_path / "mini.json"
    _mini_manifest(
        manifest_path,
        [_case("tiny-timeout", TINY_VALID, "valid_with_required_checks", ["graph"])],
    )
    source = REPO_ROOT / TINY_VALID
    before = sha256_file(source)
    out_dir = tmp_path / "timeout-run"
    payload = run_benchmark(
        manifest_path=manifest_path,
        profile="legacy",
        subset="full",
        repeats=1,
        timeout_seconds=1.0,
        out_dir=out_dir,
        repo_root=REPO_ROOT,
        variant_filter=("legacy-greedy",),
        expected_case_count=1,
        extra_env={
            "OPENPARKCAD_BENCHMARK_DEBUG_SLEEP_CASE": "tiny-timeout",
            "OPENPARKCAD_BENCHMARK_DEBUG_SLEEP_SECONDS": "20",
        },
    )
    timeout = json.loads((out_dir / "cases/tiny-timeout/legacy-greedy/1/result.json").read_text(encoding="utf-8"))
    assert payload["runs"][0]["outcome"] == "timeout"
    assert timeout["outcome"] == "timeout"
    assert timeout["backend"]["requested_backend"] == "greedy"
    assert timeout["expectation_met"] is False
    assert timeout["result_scope"] == "benchmark_diagnostic"
    assert not (out_dir / "cases/tiny-timeout/legacy-greedy/1/layout.dxf").exists()
    assert not (out_dir / "cases/tiny-timeout/legacy-greedy/1/report.json").exists()
    assert sha256_file(source) == before
    assert benchmark_exit_code(payload) != 0


def test_cpsat_greedy_fallback_is_evidence_not_success_sample(tmp_path: Path) -> None:
    block_root = tmp_path / "blocked"
    package = block_root / "ortools"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise ImportError('benchmark simulated missing OR-Tools')\n", encoding="utf-8")
    manifest_path = tmp_path / "mini.json"
    _mini_manifest(
        manifest_path,
        [_case("tiny-valid", TINY_VALID, "valid_with_required_checks", ["graph", "maneuver", "site", "engineering"])],
    )
    pythonpath = str(block_root)
    existing = os.environ.get("PYTHONPATH")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    out_dir = tmp_path / "run"
    payload = run_benchmark(
        manifest_path=manifest_path,
        profile="legacy",
        subset="full",
        repeats=1,
        timeout_seconds=30.0,
        out_dir=out_dir,
        repo_root=REPO_ROOT,
        variant_filter=("legacy-cpsat",),
        expected_case_count=1,
        extra_env={"PYTHONPATH": pythonpath},
    )
    result = json.loads((out_dir / "cases/tiny-valid/legacy-cpsat/1/result.json").read_text(encoding="utf-8"))
    assert result["backend"]["requested_backend"] == "cpsat"
    assert result["backend"]["actual_backend"] == "greedy"
    assert result["backend"]["fallback_reason"] == "cpsat_backend_unavailable"
    assert result["cpsat_success_sample"] is False
    assert payload["summary"]["cpsat_success_count"] == 0
    assert payload["summary"]["cpsat_fallback_count"] == 1
    assert payload["summary"]["comparison_incomplete"] is True
    assert benchmark_exit_code(payload) != 0


def test_multi_benchmark_records_measured_costs_and_actual_solver_controls(tmp_path: Path) -> None:
    require_ortools()
    manifest_path = _mini_manifest(
        tmp_path / "mini.json",
        [_case("tiny-valid", TINY_VALID, "valid_with_required_checks", ["graph", "site", "engineering"])],
    )
    out = tmp_path / "run"
    run = run_benchmark(
        manifest_path=manifest_path, profile="multi", subset="full", repeats=1,
        timeout_seconds=30.0, out_dir=out, repo_root=REPO_ROOT, variant_filter=("multi-cpsat",),
    )
    assert benchmark_exit_code(run) == 0
    case_dir = out / "cases/tiny-valid/multi-cpsat/1"
    result = json.loads((case_dir / "result.json").read_text(encoding="utf-8"))
    report = json.loads((case_dir / "report.json").read_text(encoding="utf-8"))
    assert result["backend"]["actual_backend"] == "cpsat"
    assert result["backend"]["selector_seed"] == 17
    assert result["backend"]["selector_num_workers"] == 1
    assert result["backend"]["selector_time_limit_seconds"] == 2.0
    budget = report["layout_search"]["budget"]
    assert result["cost"]["baseline_seconds"] == budget["baseline_seconds"] > 0
    assert result["cost"]["refinement_seconds"] == budget["elapsed_seconds"] >= 0
    assert result["cost"]["collect_seconds"] == budget["collect_seconds"] == 0
    assert result["cost"]["rebuild_seconds"] == NOT_AVAILABLE
    with (out / "summary.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert float(row["baseline_seconds"]) == budget["baseline_seconds"]
    assert float(row["refinement_seconds"]) == budget["elapsed_seconds"]


def test_tools_scripts_invoke_shipped_runner(tmp_path: Path) -> None:
    import subprocess
    import sys

    manifest_path = tmp_path / "mini.json"
    _mini_manifest(
        manifest_path,
        [_case("tiny-valid", TINY_VALID, "valid_with_required_checks", ["graph", "maneuver", "site", "engineering"])],
    )
    out_dir = tmp_path / "cli-run"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "benchmark_layouts.py"),
            "--manifest",
            str(manifest_path),
            "--profile",
            "legacy",
            "--subset",
            "full",
            "--repeats",
            "1",
            "--timeout-seconds",
            "30",
            "--out",
            str(out_dir),
            "--variant",
            "legacy-greedy",
            "--repo-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads((out_dir / "cases/tiny-valid/legacy-greedy/1/result.json").read_text(encoding="utf-8"))
    assert result["outcome"] == "valid"
    assert (out_dir / "run.json").is_file()
    assert (out_dir / "summary.csv").is_file()
    assert (out_dir / "comparison.md").is_file()
