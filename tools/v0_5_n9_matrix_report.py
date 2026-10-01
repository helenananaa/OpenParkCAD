#!/usr/bin/env python3
"""Post-process a completed N9 §16.2 matrix directory into comparison tables."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_records(out_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cases_dir = out_dir / "cases"
    if not cases_dir.is_dir():
        raise SystemExit(f"missing cases dir: {cases_dir}")
    for path in sorted(cases_dir.glob("*/*/*/result.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not payload.get("case_id"):
            continue
        records.append(payload)
    return records


def _layout(record: dict[str, Any]) -> dict[str, Any]:
    layout = record.get("layout")
    return layout if isinstance(layout, dict) else {}


def summarize_durations(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["duration_seconds"]) for item in records if item.get("duration_seconds") is not None]
    if not values:
        return {"count": 0}
    values.sort()
    return {
        "count": len(values),
        "min": values[0],
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
        "sum": sum(values),
    }


def repeat_disagreement(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (record.get("case_id"), record.get("variant_id"))
        groups[key].append(record)
    disagreements = []
    for (case_id, variant_id), items in sorted(groups.items()):
        outcomes = {item.get("outcome") for item in items}
        stalls = {_layout(item).get("stall_count") for item in items}
        if len(outcomes) > 1 or len(stalls) > 1:
            disagreements.append(
                {
                    "case_id": case_id,
                    "variant_id": variant_id,
                    "outcomes": sorted(str(item) for item in outcomes),
                    "stall_counts": sorted("none" if item is None else str(item) for item in stalls),
                    "repeats": len(items),
                }
            )
    return disagreements


def default_mode_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if (
            record.get("family") == "off"
            and record.get("selector") == "greedy"
            and record.get("promotion") is False
            and record.get("road_traversal") is False
        ):
            layout = _layout(record)
            rows.append(
                {
                    "case_id": record.get("case_id"),
                    "repeat": record.get("repeat"),
                    "outcome": record.get("outcome"),
                    "stall_count": layout.get("stall_count"),
                    "score_total": layout.get("score_total"),
                    "duration_seconds": record.get("duration_seconds"),
                }
            )
    return rows


def family_pairs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    rows = []
    for key, pair in sorted(by_key.items()):
        off = pair.get("off")
        on = pair.get("parallel_ladder")
        if not off or not on:
            rows.append({"case_id": key[0], "class": "incomparable", "reason": "missing_pair"})
            continue
        if off.get("outcome") in {"timeout", "exception"} or on.get("outcome") in {"timeout", "exception"}:
            rows.append(
                {
                    "case_id": key[0],
                    "selector": key[1],
                    "promotion": key[2],
                    "road_traversal": key[3],
                    "repeat": key[4],
                    "class": "incomparable",
                    "off_outcome": off.get("outcome"),
                    "on_outcome": on.get("outcome"),
                }
            )
            continue
        off_valid = off.get("outcome") == "valid"
        on_valid = on.get("outcome") == "valid"
        off_score = _layout(off).get("score_total") or 0
        on_score = _layout(on).get("score_total") or 0
        if not off_valid and not on_valid:
            klass = "unresolved"
        elif off_valid and not on_valid:
            klass = "degraded"
        elif on_valid and not off_valid:
            klass = "improved"
        else:
            try:
                delta = float(on_score) - float(off_score)
            except (TypeError, ValueError):
                delta = 0.0
            if delta > 1e-6:
                klass = "improved"
            elif delta < -1e-6:
                klass = "degraded"
            else:
                klass = "tied"
        rows.append(
            {
                "case_id": key[0],
                "selector": key[1],
                "promotion": key[2],
                "road_traversal": key[3],
                "repeat": key[4],
                "class": klass,
                "off_outcome": off.get("outcome"),
                "on_outcome": on.get("outcome"),
                "off_stalls": _layout(off).get("stall_count"),
                "on_stalls": _layout(on).get("stall_count"),
                "off_score": off_score,
                "on_score": on_score,
            }
        )
    return rows


def write_report(out_dir: Path) -> dict[str, Any]:
    records = load_records(out_dir)
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
    pairs = family_pairs(records)
    pair_counts: dict[str, int] = {}
    for row in pairs:
        pair_counts[str(row["class"])] = pair_counts.get(str(row["class"]), 0) + 1
    default_rows = default_mode_rows(records)
    default_invalid = [row for row in default_rows if row.get("outcome") != "valid"]
    report = {
        "cell_count": len(records),
        "outcomes": outcomes,
        "unexpected": unexpected,
        "hard_failures": hard_failures,
        "durations": summarize_durations(records),
        "family_pair_counts": pair_counts,
        "repeat_disagreements": repeat_disagreement(records),
        "default_mode_invalid": default_invalid,
        "default_mode_cells": len(default_rows),
        "source": "synthetic-only",
    }
    (out_dir / "n9-matrix-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# v0.5 N9 §16.2 matrix",
        "",
        "All effect claims are **synthetic-only**. No licensed real CAD was used.",
        "",
        f"- cells with identity: {report['cell_count']}",
        f"- outcomes: {report['outcomes']}",
        f"- family-off vs parallel_ladder: {report['family_pair_counts']}",
        f"- unexpected timeout/exception/input_error(off): {len(report['unexpected'])}",
        f"- default-mode (family off, greedy, promo off, rt off) cells: {report['default_mode_cells']}",
        f"- default-mode non-valid: {len(default_invalid)}",
        f"- repeat disagreements: {len(report['repeat_disagreements'])}",
        f"- duration seconds: {report['durations']}",
        "",
        "## Hard failures",
        "",
    ]
    if hard_failures:
        lines.extend(f"- `{item}`" for item in hard_failures)
    else:
        lines.append("- none")
    lines.extend(["", "## Default-mode non-valid", ""])
    if default_invalid:
        for row in default_invalid:
            lines.append(f"- {row}")
    else:
        lines.append("- none")
    lines.extend(["", "## Repeat disagreements", ""])
    if report["repeat_disagreements"]:
        for row in report["repeat_disagreements"]:
            lines.append(f"- {row}")
    else:
        lines.append("- none")
    degraded = [row for row in pairs if row.get("class") == "degraded"]
    improved = [row for row in pairs if row.get("class") == "improved"]
    lines.extend(["", f"## Improved pairs ({len(improved)})", ""])
    if improved:
        seen = set()
        for row in improved:
            key = (row.get("case_id"), row.get("selector"), row.get("promotion"), row.get("road_traversal"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {row.get('case_id')} sel={row.get('selector')} promo={row.get('promotion')} "
                f"rt={row.get('road_traversal')} off_stalls={row.get('off_stalls')} "
                f"on_stalls={row.get('on_stalls')}"
            )
    else:
        lines.append("- none")
    lines.extend(["", f"## Degraded pairs ({len(degraded)})", ""])
    if degraded:
        seen = set()
        for row in degraded:
            key = (row.get("case_id"), row.get("selector"), row.get("promotion"), row.get("road_traversal"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {row.get('case_id')} sel={row.get('selector')} promo={row.get('promotion')} "
                f"rt={row.get('road_traversal')} off={row.get('off_outcome')}/{row.get('off_stalls')} "
                f"on={row.get('on_outcome')}/{row.get('on_stalls')}"
            )
    else:
        lines.append("- none")
    (out_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = write_report(Path(args.out))
    print(json.dumps({"cell_count": report["cell_count"], "outcomes": report["outcomes"]}, indent=2))
    return 0 if not report["hard_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
