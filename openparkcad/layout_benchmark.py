"""Batch layout comparison: manifest, isolated worker, and parent runner."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openparkcad import __version__
from openparkcad.candidate_catalog import requested_selector_backend
from openparkcad.cli import _final_layout_errors, _write_output_set
from openparkcad.generator import generate_layout
from openparkcad.models import LayoutResult, site_from_dict
from openparkcad.scoring import score_total
from shapely.geometry import Polygon as ShapelyPolygon

MANIFEST_VERSION = "layout-benchmark-manifest-1"
RESULT_VERSION = "layout-benchmark-result-1"
NOT_AVAILABLE = "not_available"

EXPECTED_CASE_COUNT = 20
OUTCOMES = ("valid", "invalid", "input_error", "exception", "timeout")

QUALITY_PROMOTION_OVERLAY = {"promote_candidate_layout_preview": True}
SOLVER_CONTROL_OVERLAY = {"selector_seed": 17, "selector_num_workers": 1, "selector_time_limit_seconds": 2.0}

PROFILE_VARIANT_SPECS: dict[str, tuple[tuple[str, dict[str, Any]], ...]] = {
    "legacy": (
        ("legacy-greedy", {"selector_backend": "greedy", "layout_search": {"mode": "legacy"}}),
        ("legacy-cpsat", {"selector_backend": "cpsat", "layout_search": {"mode": "legacy"}}),
    ),
    "multi": (
        (
            "multi-greedy",
            {
                "selector_backend": "greedy",
                "layout_search": {"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 10.0},
            },
        ),
        (
            "multi-cpsat",
            {
                "selector_backend": "cpsat",
                "layout_search": {"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 10.0},
            },
        ),
    ),
}
PROFILE_VARIANT_SPECS["all"] = PROFILE_VARIANT_SPECS["legacy"] + PROFILE_VARIANT_SPECS["multi"]


@dataclass(frozen=True)
class BenchmarkVariant:
    variant_id: str
    optimization_overlay: dict[str, Any]


@dataclass
class BenchmarkRunRecord:
    case_id: str
    variant_id: str
    repeat: int
    outcome: str
    expectation: str
    expectation_met: bool
    case_dir: str
    result: dict[str, Any] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(
    path: Path,
    *,
    repo_root: Path,
    expected_case_count: int | None = None,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest version: {payload.get('version')!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("manifest.cases must be an array")
    if expected_case_count is not None and len(cases) != expected_case_count:
        raise ValueError(f"manifest must list exactly {expected_case_count} cases, got {len(cases)}")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"manifest.cases[{index}] must be an object")
        case_id = case.get("case_id")
        rel_path = case.get("path")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"manifest.cases[{index}].case_id is required")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        if not isinstance(rel_path, str) or not rel_path or "*" in rel_path or "?" in rel_path:
            raise ValueError(f"manifest.cases[{index}].path must be an explicit relative path, not a glob")
        normalized = rel_path.replace("\\", "/")
        source = (repo_root / normalized).resolve()
        if not source.is_file():
            raise ValueError(f"manifest case {case_id} path does not exist: {normalized}")
        try:
            source.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ValueError(f"manifest case {case_id} path escapes the repository: {normalized}") from exc
        seen_ids.add(case_id)
        case["path"] = normalized
    smoke_ids = payload.get("smoke_case_ids", [])
    if not isinstance(smoke_ids, list) or not smoke_ids:
        raise ValueError("manifest.smoke_case_ids must be a non-empty array")
    missing = [item for item in smoke_ids if item not in seen_ids]
    if missing:
        raise ValueError(f"smoke_case_ids not present in cases: {missing}")
    return payload


def profile_variants(profile: str, *, variant_filter: tuple[str, ...] = (), keep_original_promotion: bool = False) -> list[BenchmarkVariant]:
    specs = PROFILE_VARIANT_SPECS.get(profile)
    if specs is None:
        raise ValueError(f"unknown profile {profile!r}; expected one of {sorted(PROFILE_VARIANT_SPECS)}")
    variants: list[BenchmarkVariant] = []
    allowed = set(variant_filter)
    for variant_id, overlay in specs:
        if allowed and variant_id not in allowed:
            continue
        merged = copy.deepcopy(overlay)
        merged.update(SOLVER_CONTROL_OVERLAY)
        if not keep_original_promotion:
            merged.update(QUALITY_PROMOTION_OVERLAY)
        variants.append(BenchmarkVariant(variant_id=variant_id, optimization_overlay=merged))
    if not variants:
        raise ValueError(f"profile {profile!r} has no variants after filter {variant_filter!r}")
    return variants


def select_cases(manifest: dict[str, Any], subset: str) -> list[dict[str, Any]]:
    cases = list(manifest["cases"])
    if subset == "full":
        return cases
    if subset == "smoke":
        smoke_ids = list(manifest["smoke_case_ids"])
        by_id = {case["case_id"]: case for case in cases}
        return [by_id[case_id] for case_id in smoke_ids]
    raise ValueError(f"unknown subset {subset!r}; expected 'smoke' or 'full'")


def apply_optimization_overlay(data: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    clone = copy.deepcopy(data)
    optimization = clone.get("optimization")
    if optimization is None:
        optimization = {}
        clone["optimization"] = optimization
    if not isinstance(optimization, dict):
        raise ValueError("optimization must be an object")
    for key, value in overlay.items():
        if key == "layout_search" and isinstance(value, dict) and isinstance(optimization.get("layout_search"), dict):
            merged = dict(optimization["layout_search"])
            merged.update(copy.deepcopy(value))
            optimization["layout_search"] = merged
        else:
            optimization[key] = copy.deepcopy(value)
    return clone


def git_identity(repo_root: Path) -> dict[str, Any]:
    def _git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return ""
        return completed.stdout

    return {
        "commit": _git("rev-parse", "HEAD").strip() or NOT_AVAILABLE,
        "dirty": bool(_git("status", "--porcelain").strip()),
        "status": _git("status", "--short"),
    }


def _na(value: Any, *, available: bool) -> Any:
    if not available:
        return NOT_AVAILABLE
    return value


def _finite_or_na(value: Any) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if number != number or number in {float("inf"), float("-inf")}:
        return NOT_AVAILABLE
    return number


def extract_backend(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None:
        return {
            "requested_backend": NOT_AVAILABLE,
            "actual_backend": NOT_AVAILABLE,
            "fallback_reason": NOT_AVAILABLE,
            "solver_provenance": NOT_AVAILABLE,
            "selector_num_workers": NOT_AVAILABLE,
        }
    selection = layout.candidate_selection if isinstance(layout.candidate_selection, dict) else {}
    provenance = selection.get("solver_provenance") if isinstance(selection.get("solver_provenance"), dict) else {}
    requested = selection.get("requested_backend")
    actual = selection.get("backend")
    fallback = selection.get("backend_fallback_reason")
    if provenance and "num_workers" in provenance:
        workers = provenance.get("num_workers")
    else:
        workers = NOT_AVAILABLE
    return {
        "requested_backend": requested if requested is not None else NOT_AVAILABLE,
        "actual_backend": actual if actual is not None else NOT_AVAILABLE,
        "fallback_reason": fallback,
        "solver_provenance": provenance or NOT_AVAILABLE,
        "selector_num_workers": workers,
        "selector_seed": provenance.get("seed", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
        "selector_time_limit_seconds": provenance.get("time_limit_seconds", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
        "status": provenance.get("status", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
        "objective": provenance.get("objective", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
        "bound": provenance.get("objective_bound", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
        "gap": provenance.get("gap", NOT_AVAILABLE) if provenance else NOT_AVAILABLE,
    }


def extract_checks(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None:
        return {
            "graph": {"executed": False, "valid": None, "status": NOT_AVAILABLE},
            "maneuver": {"executed": False, "valid": None, "status": NOT_AVAILABLE},
            "vehicle": {"executed": False, "valid": None, "status": NOT_AVAILABLE, "requested": None},
            "site_quota": {"executed": False, "valid": None, "status": NOT_AVAILABLE},
            "engineering": {"executed": False, "valid": None, "status": NOT_AVAILABLE, "result_scope": None},
            "operational": {"executed": False, "valid": None, "status": NOT_AVAILABLE},
            "road_traversal": {"executed": False, "valid": None, "status": NOT_AVAILABLE, "requested": None},
        }

    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    vehicle = maneuver.get("vehicle_validation") if isinstance(maneuver.get("vehicle_validation"), dict) else {}
    site = layout.site_constraint_validation if isinstance(layout.site_constraint_validation, dict) else {}
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    operational = layout.operational_quality if isinstance(layout.operational_quality, dict) else {}
    requested = vehicle.get("requested") if isinstance(vehicle.get("requested"), dict) else {}
    vehicle_checks = vehicle.get("checks") if isinstance(vehicle.get("checks"), list) else []
    checked_stalls = vehicle.get("checked_stalls")
    vehicle_requested = any(bool(value) for value in requested.values()) if requested else False
    vehicle_executed = bool(vehicle_checks) or (isinstance(checked_stalls, int) and checked_stalls > 0)
    if vehicle_requested and vehicle.get("valid") is False:
        vehicle_executed = True

    quota = site.get("quota") if isinstance(site.get("quota"), dict) else {}
    return {
        "graph": {
            "executed": "valid" in graph,
            "valid": graph.get("valid") if "valid" in graph else None,
            "error_count": len(graph.get("errors", [])) if isinstance(graph.get("errors"), list) else NOT_AVAILABLE,
            "status": "executed" if "valid" in graph else NOT_AVAILABLE,
        },
        "maneuver": {
            "executed": "valid" in maneuver,
            "valid": maneuver.get("valid") if "valid" in maneuver else None,
            "invalid_stall_count": len(maneuver.get("invalid_stalls", []))
            if isinstance(maneuver.get("invalid_stalls"), list)
            else NOT_AVAILABLE,
            "status": "executed" if "valid" in maneuver else NOT_AVAILABLE,
        },
        "vehicle": {
            "executed": vehicle_executed,
            "valid": vehicle.get("valid") if "valid" in vehicle else None,
            "requested": requested or None,
            "requested_any": vehicle_requested,
            "checked_stalls": checked_stalls if isinstance(checked_stalls, int) else NOT_AVAILABLE,
            "check_count": len(vehicle_checks) if vehicle_checks else (0 if vehicle_executed else NOT_AVAILABLE),
            "status": "executed" if vehicle_executed else ("requested_not_executed" if vehicle_requested else "not_requested"),
        },
        "site_quota": {
            "executed": "valid" in site,
            "valid": site.get("valid") if "valid" in site else None,
            "error_count": len(site.get("errors", [])) if isinstance(site.get("errors"), list) else NOT_AVAILABLE,
            "quota_valid": quota.get("valid") if "valid" in quota else NOT_AVAILABLE,
            "quota_shortfall": quota.get("shortfall") if isinstance(quota.get("shortfall"), dict) else NOT_AVAILABLE,
            "status": "executed" if "valid" in site else NOT_AVAILABLE,
        },
        "engineering": {
            "executed": "valid" in engineering,
            "valid": engineering.get("valid") if "valid" in engineering else None,
            "result_scope": engineering.get("result_scope"),
            "failed_rule_count": len(engineering.get("rules", {}).get("failed", []))
            if isinstance(engineering.get("rules"), dict) and isinstance(engineering.get("rules", {}).get("failed"), list)
            else NOT_AVAILABLE,
            "status": "executed" if "valid" in engineering else NOT_AVAILABLE,
        },
        "operational": {
            "executed": "valid" in operational,
            "valid": operational.get("valid") if "valid" in operational else None,
            "risk_score": operational.get("risk_score", NOT_AVAILABLE),
            "status": "executed" if "valid" in operational else NOT_AVAILABLE,
        },
        "road_traversal": _extract_road_traversal_check(layout),
    }


def _extract_road_traversal_check(layout: LayoutResult) -> dict[str, Any]:
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    journeys = road.get("journeys") if isinstance(road.get("journeys"), list) else []
    failures = road.get("failures") if isinstance(road.get("failures"), list) else []
    stall_count = road.get("stall_count")
    coverage = road.get("stall_coverage")
    coverage_ratio = None
    if isinstance(stall_count, int) and stall_count > 0 and isinstance(coverage, int):
        coverage_ratio = coverage / stall_count
    return {
        "executed": bool(road.get("executed")),
        "valid": road.get("valid") if "valid" in road else None,
        "status": road.get("status"),
        "requested": road.get("requested"),
        "reason": road.get("reason"),
        "stall_coverage": coverage if coverage is not None else NOT_AVAILABLE,
        "stall_count": stall_count if stall_count is not None else NOT_AVAILABLE,
        "coverage_ratio": coverage_ratio if coverage_ratio is not None else NOT_AVAILABLE,
        "failure_reasons": [
            item.get("reason") for item in failures if isinstance(item, dict) and item.get("reason")
        ]
        or [item.get("reason") for item in journeys if isinstance(item, dict) and item.get("reason") and item.get("valid") is not True],
        "layout_identity": road.get("layout_identity"),
    }


def extract_layout_metrics(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None:
        return {
            "stall_count": NOT_AVAILABLE,
            "aisle_count": NOT_AVAILABLE,
            "aisle_area": NOT_AVAILABLE,
            "score_total": NOT_AVAILABLE,
            "score": NOT_AVAILABLE,
            "generation_mode": NOT_AVAILABLE,
            "stall_classifications": NOT_AVAILABLE,
            "aisle_ids": NOT_AVAILABLE,
            "official_semantic_summary": NOT_AVAILABLE,
        }
    aisle_area = 0.0
    aisle_ids = []
    for aisle in layout.aisles:
        aisle_ids.append(aisle.id)
        try:
            aisle_area += float(ShapelyPolygon(aisle.polygon).area)
        except Exception:
            aisle_area = float("nan")
            break
    classifications: dict[str, int] = {}
    for stall in layout.stalls:
        spec = None
        for candidate in (layout.site.stall_candidates or (layout.site.stall,)):
            if stall.stall_type_id and candidate.id == stall.stall_type_id:
                spec = candidate
                break
        labels = list(spec.classifications) if spec is not None else []
        if not labels:
            labels = ["unclassified"]
        for label in labels:
            classifications[str(label)] = classifications.get(str(label), 0) + 1
    score = dict(layout.score) if isinstance(layout.score, dict) else {}
    return {
        "stall_count": layout.stall_count,
        "aisle_count": len(layout.aisles),
        "aisle_area": _finite_or_na(aisle_area),
        "score_total": _finite_or_na(score.get("total", score_total(layout) if score else None)),
        "score": score or NOT_AVAILABLE,
        "generation_mode": layout.generation_mode,
        "stall_classifications": classifications,
        "aisle_ids": aisle_ids,
        "official_semantic_summary": {
            "generation_mode": layout.generation_mode,
            "main_entrance_id": layout.main_entrance_id,
            "selected_heading_degrees": layout.selected_heading_degrees,
            "selected_heading_delta_degrees": layout.selected_heading_delta_degrees,
            "selected_entrance_offset": layout.selected_entrance_offset,
            "selected_stall_type_id": layout.selected_stall_type_id,
            "selected_stall_assignment": dict(layout.selected_stall_assignment),
            "aisle_ids": aisle_ids,
            "stall_count": layout.stall_count,
            "stall_ids": [stall.id for stall in layout.stalls],
        },
    }


def search_fields(layout: LayoutResult | None) -> dict[str, Any]:
    search = getattr(layout, "layout_search", None) if layout is not None else None
    search = search if isinstance(search, dict) else {}
    counts = search.get("counts") if isinstance(search.get("counts"), dict) else {}
    budget = search.get("budget") if isinstance(search.get("budget"), dict) else {}
    report: dict[str, Any] = {}
    selection = getattr(layout, "candidate_selection", None) if layout is not None else None
    if isinstance(selection, dict) and selection:
        report["candidate_selection_backend"] = selection.get("backend", NOT_AVAILABLE)

    def _recorded(mapping: dict[str, Any], key: str) -> Any:
        if key not in mapping or mapping[key] is None:
            return NOT_AVAILABLE
        return mapping[key]

    official_id = search.get("official_candidate_id")
    return {
        "generated_count": _recorded(counts, "generated"),
        "deduplicated_count": _recorded(counts, "deduplicated"),
        "retained_count": _recorded(counts, "retained"),
        "evaluated_count": _recorded(counts, "evaluated"),
        "budget_exhausted": _recorded(budget, "exhausted"),
        "official_candidate_id": official_id if official_id else NOT_AVAILABLE,
        "collect_seconds": _recorded(budget, "collect_seconds"),
        "collect_reused_baseline_generation": _recorded(budget, "collect_reused_baseline_generation"),
        **report,
    }


def cost_fields(layout: LayoutResult | None, duration_seconds: float | None) -> dict[str, Any]:
    search = getattr(layout, "layout_search", None)
    budget = search.get("budget") if isinstance(search, dict) else None
    budget = budget if isinstance(budget, dict) else {}
    return {
        "total_seconds": _finite_or_na(duration_seconds),
        "baseline_seconds": _finite_or_na(budget.get("baseline_seconds")),
        "refinement_seconds": _finite_or_na(budget.get("elapsed_seconds")),
        "collect_seconds": _finite_or_na(budget.get("collect_seconds")),
        # Rebuild is included in refinement; it has no independent timer yet.
        "rebuild_seconds": NOT_AVAILABLE,
    }


def classify_expectation(case: dict[str, Any], outcome: str, payload: dict[str, Any]) -> bool:
    expectation = case.get("expectation")
    if outcome in {"timeout", "exception", "input_error"}:
        return False
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    if expectation == "valid_with_required_checks":
        if outcome != "valid":
            return False
        return _required_checks_executed(case, checks)
    if expectation == "invalid_with_required_checks":
        if outcome != "invalid":
            return False
        return _required_checks_executed(case, checks)
    if expectation == "reject_vehicle_policy":
        if outcome != "invalid":
            return False
        vehicle = checks.get("vehicle") if isinstance(checks.get("vehicle"), dict) else {}
        return vehicle.get("executed") is True and vehicle.get("valid") is False
    if expectation == "valid_only_if_quotas_and_contacts_satisfied":
        site_quota = checks.get("site_quota") if isinstance(checks.get("site_quota"), dict) else {}
        if outcome == "valid":
            return site_quota.get("executed") is True and site_quota.get("valid") is True
        if outcome == "invalid":
            shortfall = site_quota.get("quota_shortfall")
            has_shortfall = isinstance(shortfall, dict) and any(
                isinstance(value, int | float) and value > 0 for value in shortfall.values()
            )
            return site_quota.get("executed") is True and (site_quota.get("valid") is False or has_shortfall)
        return False
    return False


def _required_checks_executed(case: dict[str, Any], checks: dict[str, Any]) -> bool:
    required = case.get("required_checks") or []
    mapping = {
        "graph": "graph",
        "maneuver": "maneuver",
        "vehicle": "vehicle",
        "site": "site_quota",
        "quota": "site_quota",
        "contact": "site_quota",
        "engineering": "engineering",
        "operational": "operational",
        "road_traversal": "road_traversal",
    }
    for name in required:
        key = mapping.get(str(name), str(name))
        block = checks.get(key)
        if not isinstance(block, dict) or block.get("executed") is not True:
            return False
        if name == "vehicle" and block.get("status") == "requested_not_executed":
            return False
    return True


def build_result_payload(
    *,
    case: dict[str, Any],
    variant: BenchmarkVariant,
    repeat: int,
    outcome: str,
    original_sha256: str,
    effective_sha256: str | None,
    layout: LayoutResult | None,
    error: str | None,
    duration_seconds: float | None,
    result_scope: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    backend = extract_backend(layout)
    requested_from_variant, parse_fallback = requested_selector_backend(
        variant.optimization_overlay.get("selector_backend")
    )
    if backend.get("requested_backend") == NOT_AVAILABLE:
        backend["requested_backend"] = requested_from_variant
    if backend.get("fallback_reason") == NOT_AVAILABLE and layout is None:
        backend["fallback_reason"] = None
        backend["actual_backend"] = NOT_AVAILABLE
        if parse_fallback:
            backend["fallback_reason"] = parse_fallback
    checks = extract_checks(layout)
    metrics = extract_layout_metrics(layout)
    payload = {
        "version": RESULT_VERSION,
        "result_scope": result_scope,
        "outcome": outcome,
        "case_id": case["case_id"],
        "variant_id": variant.variant_id,
        "repeat": repeat,
        "provenance": case.get("provenance"),
        "tags": list(case.get("tags") or []),
        "expectation": case.get("expectation"),
        "identity": {
            **identity,
            "input_sha256": original_sha256,
            "effective_input_sha256": effective_sha256 if effective_sha256 is not None else NOT_AVAILABLE,
            "case_path": case.get("path"),
            "package_version": __version__,
        },
        "parameters": {
            "optimization_overlay": variant.optimization_overlay,
            "promote_candidate_layout_preview": variant.optimization_overlay.get(
                "promote_candidate_layout_preview", NOT_AVAILABLE
            ),
            "selector_backend_requested": variant.optimization_overlay.get("selector_backend", NOT_AVAILABLE),
            "selector_num_workers": backend.get("selector_num_workers", NOT_AVAILABLE),
            "selector_seed": backend.get("selector_seed", NOT_AVAILABLE),
            "selector_time_limit_seconds": backend.get("selector_time_limit_seconds", NOT_AVAILABLE),
        },
        "backend": backend,
        "final_result": {
            "valid": outcome == "valid",
            **metrics,
        },
        "checks": checks,
        "cost": cost_fields(layout, duration_seconds),
        "search": search_fields(layout),
        "error": error,
        "publication_errors": _final_layout_errors(layout) if layout is not None and outcome != "valid" else [],
    }
    payload["expectation_met"] = classify_expectation(case, outcome, payload)
    payload["cpsat_success_sample"] = (
        backend.get("requested_backend") == "cpsat"
        and backend.get("actual_backend") == "cpsat"
        and not backend.get("fallback_reason")
    )
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def worker_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark_case")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--variant-id", required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--overlay-file", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expectation", required=True)
    parser.add_argument("--provenance", default="synthetic")
    parser.add_argument("--path", default="")
    parser.add_argument("--required-checks", default="[]")
    parser.add_argument("--tags", default="[]")
    parser.add_argument("--commit", default=NOT_AVAILABLE)
    parser.add_argument("--dirty", default="false")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(args.input)
    started = time.perf_counter()
    identity = {
        "commit": args.commit,
        "dirty": args.dirty.lower() == "true",
        "python": sys.version,
        "platform": platform.platform(),
    }
    case = {
        "case_id": args.case_id,
        "path": args.path,
        "provenance": args.provenance,
        "tags": json.loads(args.tags),
        "expectation": args.expectation,
        "required_checks": json.loads(args.required_checks),
    }
    variant = BenchmarkVariant(
        variant_id=args.variant_id,
        optimization_overlay=json.loads(Path(args.overlay_file).read_text(encoding="utf-8")),
    )
    original_sha256 = sha256_file(input_path)
    layout: LayoutResult | None = None
    effective_sha256: str | None = None
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            payload = build_result_payload(
                case=case,
                variant=variant,
                repeat=args.repeat,
                outcome="input_error",
                original_sha256=original_sha256,
                effective_sha256=None,
                layout=None,
                error="top-level JSON value must be an object",
                duration_seconds=time.perf_counter() - started,
                result_scope="benchmark_diagnostic",
                identity=identity,
            )
            write_json(out_dir / "result.json", payload)
            return 0
        effective = apply_optimization_overlay(raw, variant.optimization_overlay)
        effective_path = out_dir / "effective-input.json"
        encoded = json.dumps(effective, indent=2)
        effective_path.write_text(encoded, encoding="utf-8")
        effective_sha256 = sha256_bytes(encoded.encode("utf-8"))
        if sha256_file(input_path) != original_sha256:
            raise RuntimeError("benchmark worker mutated the source input file")

        sleep_case = os.environ.get("OPENPARKCAD_BENCHMARK_DEBUG_SLEEP_CASE")
        if sleep_case and sleep_case == args.case_id:
            time.sleep(float(os.environ.get("OPENPARKCAD_BENCHMARK_DEBUG_SLEEP_SECONDS", "30")))
        raise_case = os.environ.get("OPENPARKCAD_BENCHMARK_DEBUG_RAISE_CASE")
        if raise_case and raise_case == args.case_id:
            raise RuntimeError("injected benchmark exception")

        try:
            site = site_from_dict(effective)
        except (KeyError, TypeError, ValueError) as exc:
            payload = build_result_payload(
                case=case,
                variant=variant,
                repeat=args.repeat,
                outcome="input_error",
                original_sha256=original_sha256,
                effective_sha256=effective_sha256,
                layout=None,
                error=str(exc),
                duration_seconds=time.perf_counter() - started,
                result_scope="benchmark_diagnostic",
                identity=identity,
            )
            write_json(out_dir / "result.json", payload)
            return 0

        layout = generate_layout(site)
        publication_errors = _final_layout_errors(layout)
        duration = time.perf_counter() - started
        if publication_errors:
            payload = build_result_payload(
                case=case,
                variant=variant,
                repeat=args.repeat,
                outcome="invalid",
                original_sha256=original_sha256,
                effective_sha256=effective_sha256,
                layout=layout,
                error="; ".join(publication_errors),
                duration_seconds=duration,
                result_scope="benchmark_diagnostic",
                identity=identity,
            )
            write_json(out_dir / "result.json", payload)
            return 0

        _write_output_set(
            layout,
            dxf_path=out_dir / "layout.dxf",
            svg_path=out_dir / "layout.svg",
            report_path=out_dir / "report.json",
        )
        payload = build_result_payload(
            case=case,
            variant=variant,
            repeat=args.repeat,
            outcome="valid",
            original_sha256=original_sha256,
            effective_sha256=effective_sha256,
            layout=layout,
            error=None,
            duration_seconds=duration,
            result_scope="official_layout",
            identity=identity,
        )
        write_json(out_dir / "result.json", payload)
        return 0
    except Exception as exc:
        payload = build_result_payload(
            case=case,
            variant=variant,
            repeat=args.repeat,
            outcome="exception",
            original_sha256=original_sha256,
            effective_sha256=effective_sha256,
            layout=layout,
            error=f"{exc}\n{traceback.format_exc()}",
            duration_seconds=time.perf_counter() - started,
            result_scope="benchmark_diagnostic",
            identity=identity,
        )
        write_json(out_dir / "result.json", payload)
        return 1


def default_worker_script(repo_root: Path) -> Path:
    return repo_root / "tools" / "benchmark_case.py"


def run_benchmark(
    *,
    manifest_path: Path,
    profile: str,
    subset: str,
    repeats: int,
    timeout_seconds: float,
    out_dir: Path,
    repo_root: Path,
    variant_filter: tuple[str, ...] = (),
    keep_original_promotion: bool = False,
    worker_script: Path | None = None,
    extra_env: dict[str, str] | None = None,
    python_executable: str | None = None,
    expected_case_count: int | None = None,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be positive")
    manifest = load_manifest(
        manifest_path,
        repo_root=repo_root,
        expected_case_count=expected_case_count,
    )
    cases = select_cases(manifest, subset)
    variants = profile_variants(profile, variant_filter=variant_filter, keep_original_promotion=keep_original_promotion)
    worker = Path(worker_script) if worker_script is not None else default_worker_script(repo_root)
    if not worker.is_file():
        raise FileNotFoundError(f"benchmark worker script not found: {worker}")
    identity = git_identity(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[BenchmarkRunRecord] = []
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    executable = python_executable or sys.executable

    for case in cases:
        source = repo_root / case["path"]
        original_sha256 = sha256_file(source)
        for variant in variants:
            for repeat in range(1, repeats + 1):
                case_dir = out_dir / "cases" / case["case_id"] / variant.variant_id / str(repeat)
                case_dir.mkdir(parents=True, exist_ok=True)
                overlay_path = case_dir / "overlay.json"
                write_json(overlay_path, variant.optimization_overlay)
                command = [
                    executable,
                    str(worker),
                    "--case-id",
                    case["case_id"],
                    "--variant-id",
                    variant.variant_id,
                    "--repeat",
                    str(repeat),
                    "--input",
                    str(source),
                    "--overlay-file",
                    str(overlay_path),
                    "--out-dir",
                    str(case_dir),
                    "--expectation",
                    str(case.get("expectation")),
                    "--provenance",
                    str(case.get("provenance", "synthetic")),
                    "--path",
                    str(case["path"]),
                    "--required-checks",
                    json.dumps(list(case.get("required_checks") or [])),
                    "--tags",
                    json.dumps(list(case.get("tags") or [])),
                    "--commit",
                    str(identity.get("commit") or NOT_AVAILABLE),
                    "--dirty",
                    "true" if identity.get("dirty") else "false",
                ]
                started = time.perf_counter()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=repo_root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                    )
                    (case_dir / "stdout.txt").write_text(completed.stdout or "", encoding="utf-8")
                    (case_dir / "stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
                    result_path = case_dir / "result.json"
                    if result_path.is_file():
                        payload = json.loads(result_path.read_text(encoding="utf-8"))
                    else:
                        payload = build_result_payload(
                            case=case,
                            variant=variant,
                            repeat=repeat,
                            outcome="exception",
                            original_sha256=original_sha256,
                            effective_sha256=None,
                            layout=None,
                            error=f"worker exit {completed.returncode} without result.json\n{completed.stderr}",
                            duration_seconds=time.perf_counter() - started,
                            result_scope="benchmark_diagnostic",
                            identity=identity,
                        )
                        write_json(result_path, payload)
                except subprocess.TimeoutExpired as exc:
                    stdout = exc.stdout.decode("utf-8") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                    stderr = exc.stderr.decode("utf-8") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                    (case_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
                    (case_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
                    existing = case_dir / "result.json"
                    if existing.is_file():
                        existing.replace(case_dir / "worker-result.json")
                    payload = build_result_payload(
                        case=case,
                        variant=variant,
                        repeat=repeat,
                        outcome="timeout",
                        original_sha256=original_sha256,
                        effective_sha256=None,
                        layout=None,
                        error=f"worker exceeded {timeout_seconds} seconds",
                        duration_seconds=timeout_seconds,
                        result_scope="benchmark_diagnostic",
                        identity=identity,
                    )
                    payload["expectation_met"] = False
                    write_json(case_dir / "result.json", payload)
                after_sha = sha256_file(source)
                payload["identity"]["input_sha256_after"] = after_sha
                payload["identity"]["source_input_mutated"] = after_sha != original_sha256
                write_json(case_dir / "result.json", payload)
                records.append(
                    BenchmarkRunRecord(
                        case_id=case["case_id"],
                        variant_id=variant.variant_id,
                        repeat=repeat,
                        outcome=str(payload.get("outcome")),
                        expectation=str(case.get("expectation")),
                        expectation_met=bool(payload.get("expectation_met")),
                        case_dir=str(case_dir),
                        result=payload,
                    )
                )

    summary = _summarize(records, profile=profile, variants=variants)
    run_payload = {
        "version": RESULT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "identity": identity,
        "manifest": {
            "path": str(manifest_path),
            "version": manifest.get("version"),
            "sha256": sha256_file(manifest_path),
            "case_count": len(manifest["cases"]),
            "subset": subset,
            "selected_case_ids": [case["case_id"] for case in cases],
        },
        "parameters": {
            "profile": profile,
            "repeats": repeats,
            "timeout_seconds": timeout_seconds,
            "keep_original_promotion": keep_original_promotion,
            "variant_ids": [variant.variant_id for variant in variants],
        },
        "summary": summary,
        "runs": [
            {
                "case_id": record.case_id,
                "variant_id": record.variant_id,
                "repeat": record.repeat,
                "outcome": record.outcome,
                "expectation": record.expectation,
                "expectation_met": record.expectation_met,
                "case_dir": record.case_dir,
                "requested_backend": record.result.get("backend", {}).get("requested_backend"),
                "actual_backend": record.result.get("backend", {}).get("actual_backend"),
                "fallback_reason": record.result.get("backend", {}).get("fallback_reason"),
                "cpsat_success_sample": record.result.get("cpsat_success_sample"),
            }
            for record in records
        ],
    }
    write_json(out_dir / "run.json", run_payload)
    _write_summary_csv(out_dir / "summary.csv", records)
    (out_dir / "comparison.md").write_text(_comparison_markdown(run_payload, records), encoding="utf-8")
    return run_payload


def _summarize(records: list[BenchmarkRunRecord], *, profile: str, variants: list[BenchmarkVariant]) -> dict[str, Any]:
    counts = {outcome: 0 for outcome in OUTCOMES}
    unexpected: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    cpsat_success = 0
    cpsat_requested = 0
    for record in records:
        counts[record.outcome] = counts.get(record.outcome, 0) + 1
        backend = record.result.get("backend") if isinstance(record.result.get("backend"), dict) else {}
        if backend.get("requested_backend") == "cpsat":
            cpsat_requested += 1
            if record.result.get("cpsat_success_sample"):
                cpsat_success += 1
            elif backend.get("actual_backend") == "greedy":
                fallbacks.append(
                    {
                        "case_id": record.case_id,
                        "variant_id": record.variant_id,
                        "repeat": record.repeat,
                        "fallback_reason": backend.get("fallback_reason"),
                    }
                )
        if not record.expectation_met:
            unexpected.append(
                {
                    "case_id": record.case_id,
                    "variant_id": record.variant_id,
                    "repeat": record.repeat,
                    "outcome": record.outcome,
                    "expectation": record.expectation,
                }
            )
    cpsat_required = any(
        requested_selector_backend(variant.optimization_overlay.get("selector_backend"))[0] == "cpsat"
        for variant in variants
    )
    incomplete = bool(cpsat_required and cpsat_requested > 0 and cpsat_success == 0)
    return {
        "run_count": len(records),
        "outcomes": counts,
        "expectation_met_count": sum(1 for record in records if record.expectation_met),
        "unexpected": unexpected,
        "cpsat_requested_count": cpsat_requested,
        "cpsat_success_count": cpsat_success,
        "cpsat_fallback_count": len(fallbacks),
        "cpsat_fallbacks": fallbacks,
        "comparison_incomplete": incomplete,
        "incomplete_reason": "cpsat_backend_unavailable" if incomplete else None,
        "exit_nonzero_reasons": _exit_reasons(unexpected, incomplete),
    }


def _exit_reasons(unexpected: list[dict[str, Any]], incomplete: bool) -> list[str]:
    reasons: list[str] = []
    if unexpected:
        reasons.append("expectation_failures_or_errors")
    if incomplete:
        reasons.append("cpsat_comparison_incomplete")
    return reasons


def benchmark_exit_code(run_payload: dict[str, Any]) -> int:
    reasons = run_payload.get("summary", {}).get("exit_nonzero_reasons") or []
    return 1 if reasons else 0


def _write_summary_csv(path: Path, records: list[BenchmarkRunRecord]) -> None:
    fieldnames = [
        "case_id",
        "variant_id",
        "repeat",
        "outcome",
        "expectation",
        "expectation_met",
        "requested_backend",
        "actual_backend",
        "fallback_reason",
        "cpsat_success_sample",
        "valid",
        "stall_count",
        "aisle_area",
        "score_total",
        "total_seconds",
        "baseline_seconds",
        "refinement_seconds",
        "collect_seconds",
        "rebuild_seconds",
        "result_scope",
        "source_input_mutated",
        "case_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            backend = record.result.get("backend") if isinstance(record.result.get("backend"), dict) else {}
            final_result = record.result.get("final_result") if isinstance(record.result.get("final_result"), dict) else {}
            cost = record.result.get("cost") if isinstance(record.result.get("cost"), dict) else {}
            identity = record.result.get("identity") if isinstance(record.result.get("identity"), dict) else {}
            writer.writerow(
                {
                    "case_id": record.case_id,
                    "variant_id": record.variant_id,
                    "repeat": record.repeat,
                    "outcome": record.outcome,
                    "expectation": record.expectation,
                    "expectation_met": record.expectation_met,
                    "requested_backend": backend.get("requested_backend"),
                    "actual_backend": backend.get("actual_backend"),
                    "fallback_reason": backend.get("fallback_reason"),
                    "cpsat_success_sample": record.result.get("cpsat_success_sample"),
                    "valid": final_result.get("valid"),
                    "stall_count": final_result.get("stall_count"),
                    "aisle_area": final_result.get("aisle_area"),
                    "score_total": final_result.get("score_total"),
                    "total_seconds": cost.get("total_seconds"),
                    "baseline_seconds": cost.get("baseline_seconds", NOT_AVAILABLE),
                    "refinement_seconds": cost.get("refinement_seconds", NOT_AVAILABLE),
                    "collect_seconds": cost.get("collect_seconds", NOT_AVAILABLE),
                    "rebuild_seconds": cost.get("rebuild_seconds", NOT_AVAILABLE),
                    "result_scope": record.result.get("result_scope"),
                    "source_input_mutated": identity.get("source_input_mutated"),
                    "case_dir": record.case_dir,
                }
            )


def _comparison_markdown(run_payload: dict[str, Any], records: list[BenchmarkRunRecord]) -> str:
    summary = run_payload["summary"]
    lines = [
        "# Layout benchmark comparison",
        "",
        f"- profile: `{run_payload['parameters']['profile']}`",
        f"- subset: `{run_payload['manifest']['subset']}`",
        f"- repeats: {run_payload['parameters']['repeats']}",
        f"- commit: `{run_payload['identity'].get('commit')}` dirty={run_payload['identity'].get('dirty')}",
        f"- runs: {summary['run_count']}",
        f"- outcomes: {json.dumps(summary['outcomes'])}",
        f"- CP-SAT requested/success/fallback: {summary['cpsat_requested_count']}/{summary['cpsat_success_count']}/{summary['cpsat_fallback_count']}",
        f"- comparison_incomplete: {summary['comparison_incomplete']}",
        "",
        "## Runs",
        "",
        "| case | variant | repeat | outcome | expected | met | requested | actual | fallback | stalls | score | seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        backend = record.result.get("backend") if isinstance(record.result.get("backend"), dict) else {}
        final_result = record.result.get("final_result") if isinstance(record.result.get("final_result"), dict) else {}
        cost = record.result.get("cost") if isinstance(record.result.get("cost"), dict) else {}
        lines.append(
            "| {case} | {variant} | {repeat} | {outcome} | {expectation} | {met} | {requested} | {actual} | {fallback} | {stalls} | {score} | {seconds} |".format(
                case=record.case_id,
                variant=record.variant_id,
                repeat=record.repeat,
                outcome=record.outcome,
                expectation=record.expectation,
                met=record.expectation_met,
                requested=backend.get("requested_backend"),
                actual=backend.get("actual_backend"),
                fallback=backend.get("fallback_reason"),
                stalls=final_result.get("stall_count"),
                score=final_result.get("score_total"),
                seconds=cost.get("total_seconds"),
            )
        )
    if summary.get("unexpected"):
        lines.extend(["", "## Unexpected outcomes", ""])
        for item in summary["unexpected"]:
            lines.append(f"- {item}")
    if summary.get("cpsat_fallbacks"):
        lines.extend(
            [
                "",
                "## CP-SAT fallback evidence",
                "",
                "These runs requested `cpsat` but actually used `greedy`. They are not CP-SAT success samples.",
                "",
            ]
        )
        for item in summary["cpsat_fallbacks"]:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def runner_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark_layouts")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILE_VARIANT_SPECS))
    parser.add_argument("--subset", default="smoke", choices=("smoke", "full"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--variant", action="append", default=[], help="Optional variant_id filter; may be repeated.")
    parser.add_argument(
        "--keep-original-promotion",
        action="store_true",
        help="Do not overlay promote_candidate_layout_preview=true.",
    )
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    manifest_path = Path(args.manifest) if Path(args.manifest).is_absolute() else repo_root / args.manifest
    expected_case_count = EXPECTED_CASE_COUNT if manifest_path.name == "layout_v0_4.json" else None
    run_payload = run_benchmark(
        manifest_path=manifest_path,
        profile=args.profile,
        subset=args.subset,
        repeats=args.repeats,
        timeout_seconds=args.timeout_seconds,
        out_dir=Path(args.out) if Path(args.out).is_absolute() else repo_root / args.out,
        repo_root=repo_root,
        variant_filter=tuple(args.variant),
        keep_original_promotion=args.keep_original_promotion,
        expected_case_count=expected_case_count,
    )
    print(f"run: {run_payload['manifest']['selected_case_ids']}")
    print(f"out: {args.out}")
    print(f"incomplete: {run_payload['summary']['comparison_incomplete']}")
    print(f"unexpected: {len(run_payload['summary']['unexpected'])}")
    return benchmark_exit_code(run_payload)
