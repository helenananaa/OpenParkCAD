#!/usr/bin/env python3
"""Sequential N9 §16.2 matrix: corpus × family × selector × promotion × road_traversal × 3.

Uses shipped generate_layout. Ceilings are the N0 freeze (180s hard timeout,
max_skeletons 16). Does not relax limits after seeing numbers.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from openparkcad.generator import generate_layout
from openparkcad.layout_benchmark import (
    extract_backend,
    extract_checks,
    git_identity,
    load_manifest,
)
from openparkcad.models import site_from_dict
from openparkcad.scoring import score_total

REPO = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180.0
MAX_SKELETONS = 16
REPEATS = 3
FAMILIES = ("off", "parallel_ladder")
SELECTORS = ("greedy", "cpsat")
PROMOTIONS = (False, True)
ROAD_TRAVERSALS = (False, True)

CEILINGS = {
    "single_case_hard_timeout_seconds": TIMEOUT_SECONDS,
    "max_skeletons": MAX_SKELETONS,
    "max_full_evaluations_default": 8,
    "refinement_budget_seconds_default": 20.0,
    "source": "docs/verification/v0_5_n0_baseline.json",
}


def load_cases() -> list[dict[str, Any]]:
    v04 = load_manifest(REPO / "benchmarks/layout_v0_4.json", repo_root=REPO, expected_case_count=22)
    extra = json.loads((REPO / "benchmarks/layout_v0_5.json").read_text(encoding="utf-8"))["extra_cases"]
    cases = list(v04["cases"]) + list(extra)
    for case in extra:
        path = REPO / case["path"]
        if not path.is_file():
            raise FileNotFoundError(case["path"])
    return cases


def variant_id(family: str, selector: str, promotion: bool, road: bool) -> str:
    return (
        f"fam-{family}_sel-{selector}_promo-{'on' if promotion else 'off'}_"
        f"rt-{'on' if road else 'off'}"
    )


def variants() -> list[dict[str, Any]]:
    items = []
    for family in FAMILIES:
        for selector in SELECTORS:
            for promotion in PROMOTIONS:
                for road in ROAD_TRAVERSALS:
                    items.append(
                        {
                            "variant_id": variant_id(family, selector, promotion, road),
                            "family": family,
                            "selector": selector,
                            "promotion": promotion,
                            "road_traversal": road,
                        }
                    )
    return items


def apply_variant(raw: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(raw)
    optimization = data.setdefault("optimization", {})
    if not isinstance(optimization, dict):
        optimization = {}
        data["optimization"] = optimization
    optimization["selector_backend"] = variant["selector"]
    optimization["selector_seed"] = 17
    optimization["selector_num_workers"] = 1
    optimization["selector_time_limit_seconds"] = 2.0
    optimization["promote_candidate_layout_preview"] = bool(variant["promotion"])
    if variant["family"] == "parallel_ladder":
        optimization["road_network"] = {
            "enabled": True,
            "families": ["legacy", "parallel_ladder"],
            "max_skeletons": MAX_SKELETONS,
            "max_parallel_aisles": 6,
            "dominant_axis_count": 2,
            "cross_aisle_policy": "both_ends",
            "allow_one_way_loop": False,
        }
    else:
        optimization["road_network"] = {"enabled": False, "families": ["legacy"]}
    constraints = data.setdefault("constraints", {})
    if not isinstance(constraints, dict):
        constraints = {}
        data["constraints"] = constraints
    if variant["road_traversal"]:
        road = dict(constraints.get("road_traversal") or {})
        road["enabled"] = True
        road["scope"] = "site_interior"
        road.setdefault("time_budget_seconds", 10.0)
        constraints["road_traversal"] = road
    elif isinstance(constraints.get("road_traversal"), dict):
        constraints["road_traversal"] = {**constraints["road_traversal"], "enabled": False}
    return data


def compact_layout(layout) -> dict[str, Any]:
    search = layout.layout_search if isinstance(layout.layout_search, dict) else {}
    rns = search.get("road_network_search") if isinstance(search.get("road_network_search"), dict) else {}
    counts = rns.get("counts") if isinstance(rns.get("counts"), dict) else {}
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    return {
        "stall_count": layout.stall_count,
        "aisle_count": len(layout.aisles),
        "generation_mode": layout.generation_mode,
        "score_total": score_total(layout) if layout.score else None,
        "engineering_valid": engineering.get("valid"),
        "engineering_decision": engineering.get("decision"),
        "backend": extract_backend(layout),
        "checks": extract_checks(layout),
        "road_network_search": {
            "requested": rns.get("requested"),
            "executed": rns.get("executed"),
            "families": rns.get("families"),
            "counts": counts,
            "promotion_requested": rns.get("promotion_requested"),
        },
        "aisle_ids": [aisle.id for aisle in layout.aisles],
        "stall_ids": [stall.id for stall in layout.stalls],
    }


def run_cell(input_path: Path, overlay: dict[str, Any]) -> dict[str, Any]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    effective = apply_variant(raw, overlay)
    started = time.perf_counter()
    try:
        site = site_from_dict(effective)
        layout = generate_layout(site)
    except ValueError as exc:
        return {
            "outcome": "input_error",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_seconds": time.perf_counter() - started,
            "layout": None,
        }
    except Exception as exc:
        return {
            "outcome": "exception",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_seconds": time.perf_counter() - started,
            "layout": None,
        }
    duration = time.perf_counter() - started
    checks = extract_checks(layout)
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    valid = engineering.get("valid") is True
    return {
        "outcome": "valid" if valid else "invalid",
        "error": None,
        "duration_seconds": duration,
        "layout": compact_layout(layout),
        "checks": checks,
    }


def worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--overlay-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    overlay = json.loads(Path(args.overlay_file).read_text(encoding="utf-8"))
    payload = run_cell(Path(args.input), overlay)
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload.get("outcome") in {"valid", "invalid"} else 1


def parent_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--limit", type=int, default=0, help="process only the first N cells")
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    identity = git_identity(REPO)
    (out_dir / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    (out_dir / "ceilings.json").write_text(json.dumps(CEILINGS, indent=2), encoding="utf-8")
    (out_dir / "environment.json").write_text(
        json.dumps(
            {
                "python": sys.version,
                "executable": sys.executable,
                "platform": platform.platform(),
                "pid": os.getpid(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
    cases = load_cases()
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in wanted]
        if not cases:
            raise SystemExit(f"no cases matched {sorted(wanted)}")
    vars_ = variants()
    cells: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    for case in cases:
        for variant in vars_:
            for repeat in range(1, REPEATS + 1):
                cells.append((case, variant, repeat))
    if args.limit and args.limit > 0:
        cells = cells[: args.limit]
    total = len(cells)
    (out_dir / "plan.json").write_text(
        json.dumps(
            {
                "total_cells": total,
                "cases": len(cases),
                "variants": len(vars_),
                "repeats": REPEATS,
                "timeout_seconds": TIMEOUT_SECONDS,
                "limit": args.limit or None,
                "case_id_filter": args.case_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    records: list[dict[str, Any]] = []
    done = 0
    started_all = time.perf_counter()
    for case, variant, repeat in cells:
        cell_dir = out_dir / "cases" / case["case_id"] / variant["variant_id"] / str(repeat)
        cell_dir.mkdir(parents=True, exist_ok=True)
        result_path = cell_dir / "result.json"
        source = REPO / case["path"]
        done += 1
        existing = None
        if result_path.is_file():
            try:
                existing = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = None
        durable = existing.get("outcome") in {"valid", "invalid", "timeout", "input_error"} if isinstance(existing, dict) else False
        if (
            durable
            and existing.get("case_id")
            and existing.get("variant_id")
            and existing.get("repeat") is not None
        ):
            records.append(existing)
            (out_dir / "progress.json").write_text(
                json.dumps(
                    {
                        "done": done,
                        "total": total,
                        "elapsed_seconds": time.perf_counter() - started_all,
                        "last": f"{case['case_id']}/{variant['variant_id']}/{repeat}",
                        "last_outcome": existing.get("outcome"),
                        "resumed": True,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            continue
        overlay_path = cell_dir / "overlay.json"
        overlay_path.write_text(json.dumps(variant, indent=2), encoding="utf-8")
        command = [
            args.python,
            str(Path(__file__).resolve()),
            "worker",
            "--input",
            str(source),
            "--overlay-file",
            str(overlay_path),
            "--out",
            str(result_path),
        ]
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
            )
            (cell_dir / "stdout.txt").write_text(completed.stdout or "", encoding="utf-8")
            (cell_dir / "stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
            if result_path.is_file():
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                payload = {
                    "outcome": "exception",
                    "error": f"worker exit {completed.returncode}\n{completed.stderr}",
                    "duration_seconds": time.perf_counter() - started,
                    "layout": None,
                }
                result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except subprocess.TimeoutExpired:
            payload = {
                "outcome": "timeout",
                "error": f"exceeded {TIMEOUT_SECONDS}s N0 ceiling",
                "duration_seconds": TIMEOUT_SECONDS,
                "layout": None,
            }
            result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        payload["case_id"] = case["case_id"]
        payload["variant_id"] = variant["variant_id"]
        payload["repeat"] = repeat
        payload["path"] = case["path"]
        payload["family"] = variant["family"]
        payload["selector"] = variant["selector"]
        payload["promotion"] = variant["promotion"]
        payload["road_traversal"] = variant["road_traversal"]
        result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        records.append(payload)
        elapsed = time.perf_counter() - started_all
        (out_dir / "progress.json").write_text(
            json.dumps(
                {
                    "done": done,
                    "total": total,
                    "elapsed_seconds": elapsed,
                    "last": f"{case['case_id']}/{variant['variant_id']}/{repeat}",
                    "last_outcome": payload.get("outcome"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    summary = summarize(records)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_comparison(out_dir, records, summary)
    print(f"done {len(records)}/{total} unexpected={len(summary['unexpected'])}")
    return 0 if not summary["hard_failures"] else 1


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    unexpected: list[str] = []
    hard_failures: list[str] = []
    for record in records:
        outcome = str(record.get("outcome"))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        key = f"{record.get('case_id')}/{record.get('variant_id')}/{record.get('repeat')}"
        if outcome in {"timeout", "exception"}:
            unexpected.append(key)
            hard_failures.append(key)
        if outcome == "input_error" and record.get("family") == "off":
            unexpected.append(key)
    comparisons = compare_family(records)
    return {
        "cell_count": len(records),
        "outcomes": outcomes,
        "unexpected": unexpected,
        "hard_failures": hard_failures,
        "comparisons": comparisons,
        "ceilings": CEILINGS,
    }


def compare_family(records: list[dict[str, Any]]) -> dict[str, int]:
    by_key: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for record in records:
        key = (
            record.get("case_id"),
            record.get("selector"),
            record.get("promotion"),
            record.get("road_traversal"),
            record.get("repeat"),
        )
        by_key.setdefault(key, {})[str(record.get("family"))] = record
    tallies = {"improved": 0, "tied": 0, "degraded": 0, "unresolved": 0, "incomparable": 0}
    for pair in by_key.values():
        off = pair.get("off")
        on = pair.get("parallel_ladder")
        if not off or not on:
            tallies["incomparable"] += 1
            continue
        if off.get("outcome") in {"timeout", "exception"} or on.get("outcome") in {"timeout", "exception"}:
            tallies["incomparable"] += 1
            continue
        off_valid = off.get("outcome") == "valid"
        on_valid = on.get("outcome") == "valid"
        if not off_valid and not on_valid:
            tallies["unresolved"] += 1
            continue
        if off_valid and not on_valid:
            tallies["degraded"] += 1
            continue
        if on_valid and not off_valid:
            tallies["improved"] += 1
            continue
        off_score = ((off.get("layout") or {}).get("score_total")) or 0
        on_score = ((on.get("layout") or {}).get("score_total")) or 0
        try:
            delta = float(on_score) - float(off_score)
        except (TypeError, ValueError):
            tallies["tied"] += 1
            continue
        if delta > 1e-6:
            tallies["improved"] += 1
        elif delta < -1e-6:
            tallies["degraded"] += 1
        else:
            tallies["tied"] += 1
    return tallies


def write_comparison(out_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    path = out_dir / "comparison.md"
    lines = [
        "# v0.5 N9 §16.2 matrix",
        "",
        f"- cells: {summary['cell_count']}",
        f"- outcomes: {summary['outcomes']}",
        f"- family-off vs parallel_ladder: {summary['comparisons']}",
        f"- unexpected timeout/exception: {len(summary['unexpected'])}",
        f"- hard timeout ceiling: {TIMEOUT_SECONDS}s (N0 freeze)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    with (out_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "variant_id",
                "repeat",
                "outcome",
                "duration_seconds",
                "stall_count",
                "score_total",
                "engineering_valid",
            ],
        )
        writer.writeheader()
        for record in records:
            layout = record.get("layout") or {}
            writer.writerow(
                {
                    "case_id": record.get("case_id"),
                    "variant_id": record.get("variant_id"),
                    "repeat": record.get("repeat"),
                    "outcome": record.get("outcome"),
                    "duration_seconds": record.get("duration_seconds"),
                    "stall_count": layout.get("stall_count"),
                    "score_total": layout.get("score_total"),
                    "engineering_valid": layout.get("engineering_valid"),
                }
            )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "worker":
        return worker_main(argv[1:])
    return parent_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
