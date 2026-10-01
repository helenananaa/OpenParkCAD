"""Sequential paired validation of the frozen synthetic dense-repair corpus.

Measures a fixed generated ladder, not a whole-site optimum or human CAD parity.
Each cell gets a fresh worker and the same 60-second validation budget.
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

from openparkcad.generator import _finalize_candidate, _layout_valid
from openparkcad.ladder_repair import repair_ladder_layout
from openparkcad.layout_benchmark import git_identity
from openparkcad.models import site_from_dict
from openparkcad.road_network_config import parse_road_network_mapping
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons

ROOT = Path(__file__).resolve().parents[1]


def cell(path: Path, mode: str):
    started = time.perf_counter()
    data = json.loads(path.read_text(encoding="utf-8"))
    site = site_from_dict(data)
    config = parse_road_network_mapping(site.optimization["road_network"])
    candidates = generate_parallel_ladder_skeletons(site).candidates
    if not candidates:
        return {"outcome": "no_skeleton", "accepted_stalls": 0}
    skeleton = candidates[0].skeleton
    catalog = build_and_select_ladder_modules(site, skeleton, backend="cpsat")
    raw = layout_from_skeleton(site, skeleton, catalog.selected_stalls)
    if mode == "on":
        layout, repair = repair_ladder_layout(raw, config.repair, backend="cpsat", deadline=started + 60)
        valid = repair["accepted"] and _layout_valid(layout)
    else:
        layout = _finalize_candidate(raw, deadline=started + 60)
        repair = None
        valid = _layout_valid(layout)
    road = layout.road_traversal_validation or {}
    return {
        "outcome": "valid" if valid else "not_accepted", "mode": mode,
        "input_stalls": len(catalog.selected_stalls), "output_stalls": layout.stall_count,
        "accepted_stalls": layout.stall_count if valid else 0,
        "road_status": road.get("status"), "road_coverage": road.get("stall_coverage"),
        "layout_identity": road.get("layout_identity"),
        "elapsed_seconds": time.perf_counter() - started, "repair": repair,
        "skeleton_id": skeleton.skeleton_id, "module_selector": catalog.provenance["selector"],
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
    manifest = json.loads((ROOT / "tests/fixtures/dense_repair/manifest.json").read_text(encoding="utf-8"))
    plan = {"source": identity, "python": sys.executable, "repeats": 3, "validation_budget_seconds": 60,
            "worker_hard_timeout_seconds": 130, "corpus": manifest, "human_reference": "not_available",
            "timing_scope": "host_contended_sequential_functional_comparison"}
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    results = []
    for case in manifest["cases"]:
        path = ROOT / case["path"].replace("\\", "/")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["input_sha256"]
        for repeat in range(1, 4):
            for mode in ["off", "on"]:
                if git_identity(ROOT) != identity:
                    raise ValueError("Source changed during corpus run")
                target = out / f"{case['id']}-{mode}-{repeat}.json"
                command = [sys.executable, "-I", str(Path(__file__).resolve()), "--input", str(path),
                           "--mode", mode, "--out", str(target)]
                process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = process.communicate(timeout=130)
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
                result.update(case_id=case["id"], split=case["split"], mode=mode, repeat=repeat)
                target.write_text(json.dumps(result, indent=2))
                results.append(result)
                (out / "progress.json").write_text(json.dumps({"done": len(results), "total": 24,
                                                               "last": target.name, "outcome": result["outcome"]}, indent=2))
    summary = {"source": identity, "cells": len(results), "cases": {}, "human_reference": "not_available"}
    for case in manifest["cases"]:
        rows = [r for r in results if r["case_id"] == case["id"]]
        summary["cases"][case["id"]] = {
            mode: [{k: row.get(k) for k in ["outcome", "accepted_stalls", "input_stalls", "output_stalls",
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
