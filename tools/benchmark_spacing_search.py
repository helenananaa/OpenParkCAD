"""Sequential paired comparison of delete-only repair and geometric spacing search.

Uses the same shortlisted base ladder and fixed budgets; not a global optimum or human CAD comparison.
Each cell uses an isolated worker, a 240-second outer budget and 120-second road budget.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from openparkcad.generator import _layout_valid
from openparkcad.layout_benchmark import git_identity
from openparkcad.models import site_from_dict, LayoutResult
from openparkcad.road_network_search import apply_road_network_search

ROOT = Path(__file__).resolve().parents[1]


def cell(path: Path, mode: str):
    started = time.perf_counter()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["optimization"]["road_network"]["spacing_search"]["enabled"] = mode == "on"
    site = site_from_dict(data)
    layout = apply_road_network_search(site, LayoutResult(site=site, stalls=[]))
    road = layout.road_traversal_validation or {}
    search = layout.layout_search["road_network_search"]
    valid = _layout_valid(layout)
    return {
        "outcome": "valid" if valid else "not_accepted", "mode": mode,
        "accepted_stalls": layout.stall_count if valid else 0,
        "road_status": road.get("status"), "road_coverage": road.get("stall_coverage"),
        "layout_identity": road.get("layout_identity"),
        "elapsed_seconds": time.perf_counter() - started,
        "search": search,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--input")
    parser.add_argument("--mode", choices=["off", "on"])
    args = parser.parse_args()
    out = Path(args.out)
    if args.input:
        out.write_text(json.dumps(cell(Path(args.input), args.mode), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    identity = git_identity(ROOT)
    if identity["dirty"]:
        raise ValueError("Freeze the source commit before running the corpus")
    manifest = json.loads((ROOT / "tests/fixtures/spacing_search/manifest.json").read_text(encoding="utf-8"))
    plan = {"source": identity, "python": sys.executable, "repeats": 3, "validation_budget_seconds": 120, "outer_search_budget_seconds": 240,
            "worker_hard_timeout_seconds": 300, "corpus": manifest, "human_reference": "not_available",
            "timing_scope": "host_contended_sequential_functional_comparison"}
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    results = []
    for case in manifest["cases"]:
        path = ROOT / case["path"].replace("\\", "/")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
        for repeat in range(1, 4):
            for mode in ["off", "on"]:
                if git_identity(ROOT) != identity:
                    raise ValueError("Source changed during corpus run")
                target = out / f"{case['id']}-{mode}-{repeat}.json"
                command = [sys.executable, "-I", str(Path(__file__).resolve()), "--input", str(path),
                           "--mode", mode, "--out", str(target)]
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = process.communicate(timeout=300)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                    else:
                        process.kill()
                    process.communicate()
                    result = {"outcome": "timeout", "accepted_stalls": 0}
                else:
                    result = json.loads(target.read_text(encoding="utf-8")) if process.returncode == 0 and target.exists() else {
                        "outcome": "error", "accepted_stalls": 0, "error": stderr, "stdout": stdout,
                    }
                result.update(case_id=case["id"], split=case.get("split", "synthetic"), mode=mode, repeat=repeat)
                target.write_text(json.dumps(result, indent=2))
                results.append(result)
                (out / "progress.json").write_text(json.dumps({"done": len(results), "total": 24,
                                                               "last": target.name, "outcome": result["outcome"]}, indent=2))
    summary = {"source": identity, "cells": len(results), "cases": {}, "human_reference": "not_available"}
    for case in manifest["cases"]:
        rows = [r for r in results if r["case_id"] == case["id"]]
        summary["cases"][case["id"]] = {
            mode: [{k: row.get(k) for k in ["outcome", "accepted_stalls", 
                                           "road_coverage", "elapsed_seconds", "layout_identity"]}
                   for row in rows if row["mode"] == mode] for mode in ["off", "on"]
        }
    summary["execution_errors"] = sum(r["outcome"] in {"timeout", "error"} for r in results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if summary["execution_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
