"""Outer search over legacy baseline plus opt-in parallel_ladder skeletons (N7)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.scoring import score_total
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons

REPORT_VERSION = "road-network-search-1"
ALLOWED_FAMILIES = frozenset({"legacy", "parallel_ladder"})
DEFAULT_REFINEMENT_BUDGET_SECONDS = 20.0
DEFAULT_MAX_FULL_EVALUATIONS = 8


def road_network_requested(site: SiteSpec) -> bool:
    raw = (site.optimization or {}).get("road_network")
    return isinstance(raw, dict) and raw.get("enabled") is True


def apply_road_network_search(site: SiteSpec, baseline: LayoutResult) -> LayoutResult:
    raw = (site.optimization or {}).get("road_network")
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return baseline
    families = raw.get("families") or ["legacy"]
    if not isinstance(families, list) or any(not isinstance(item, str) for item in families):
        raise ValueError("optimization.road_network.families must be an array of strings")
    illegal = [item for item in families if item not in ALLOWED_FAMILIES]
    if illegal:
        raise ValueError(f"unknown road_network family: {illegal[0]}")
    if raw.get("enabled") is not True:
        return baseline
    if "parallel_ladder" not in families:
        report = _empty_report(requested=True, executed=True, families=families)
        report["notes"] = ["families exclude parallel_ladder"]
        return _with_report(baseline, report)

    promotion = bool(site.optimization.get("promote_candidate_layout_preview"))
    ladder = generate_parallel_ladder_skeletons(site, config=raw)
    from openparkcad.generator import _finalize_candidate, _layout_valid

    budget_seconds = _refinement_budget_seconds(site, raw)
    max_full = _max_full_evaluations(raw)
    evaluated = []
    official = baseline
    started = time.perf_counter()
    exhausted = False
    incomplete = 0
    for index, candidate in enumerate(ladder.candidates):
        if len(evaluated) >= max_full or (time.perf_counter() - started) >= budget_seconds:
            exhausted = True
            leftover = ladder.candidates[index:]
            incomplete = len(leftover)
            for rest in leftover:
                evaluated.append(
                    {
                        "skeleton_id": rest.skeleton.skeleton_id,
                        "prefilter_score": rest.prefilter_score,
                        "stall_count": None,
                        "score_total": None,
                        "valid": False,
                        "incomplete": True,
                        "selected_reason": "budget_exhausted",
                    }
                )
            break
        catalog = build_and_select_ladder_modules(site, candidate.skeleton)
        layout = layout_from_skeleton(site, candidate.skeleton, catalog.selected_stalls)
        layout = _finalize_candidate(layout)
        valid = _layout_valid(layout)
        evaluated.append(
            {
                "skeleton_id": candidate.skeleton.skeleton_id,
                "prefilter_score": candidate.prefilter_score,
                "stall_count": layout.stall_count,
                "score_total": score_total(layout) if layout.score else None,
                "valid": valid,
                "incomplete": False,
                "selected_reason": None,
            }
        )
        if not promotion:
            evaluated[-1]["selected_reason"] = "promotion_off"
            continue
        if not valid:
            evaluated[-1]["selected_reason"] = "candidate_invalid"
            continue
        if not _layout_valid(official) and valid:
            official = layout
            evaluated[-1]["selected_reason"] = "recovered_feasible"
            continue
        if _layout_valid(official) and score_total(layout) > score_total(official) + 1e-6:
            official = layout
            evaluated[-1]["selected_reason"] = "promoted"
        else:
            evaluated[-1]["selected_reason"] = "valid_not_better"

    fully_evaluated = sum(1 for item in evaluated if not item.get("incomplete"))
    report = {
        "version": REPORT_VERSION,
        "requested": True,
        "executed": True,
        "families": list(families),
        "counts": {
            "generated": ladder.counts.get("generated", 0),
            "deduplicated": ladder.counts.get("deduplicated", 0),
            "prefilter_passed": ladder.counts.get("prefilter_passed", 0),
            "retained": ladder.counts.get("retained", 0),
            "fully_evaluated": fully_evaluated,
            "verified": sum(1 for item in evaluated if item["valid"]),
            "incomplete": incomplete,
        },
        "budget": {
            "exhausted": exhausted,
            "configured_seconds": budget_seconds,
            "elapsed_seconds": time.perf_counter() - started,
            "max_full_evaluations": max_full,
        },
        "skeletons": evaluated,
        "promotion_requested": promotion,
        "baseline_retained": official is baseline or official.generation_mode != "parallel_ladder_shadow",
    }
    return _with_report(official, report)


def _refinement_budget_seconds(site: SiteSpec, raw: dict[str, Any]) -> float:
    if raw.get("refinement_budget_seconds") is not None:
        return float(raw["refinement_budget_seconds"])
    search = site.optimization.get("layout_search") if isinstance(site.optimization, dict) else None
    if isinstance(search, dict) and search.get("refinement_budget_seconds") is not None:
        return float(search["refinement_budget_seconds"])
    return DEFAULT_REFINEMENT_BUDGET_SECONDS


def _max_full_evaluations(raw: dict[str, Any]) -> int:
    value = raw.get("max_full_evaluations", DEFAULT_MAX_FULL_EVALUATIONS)
    return max(1, int(value))


def _empty_report(*, requested: bool, executed: bool, families: list[str]) -> dict[str, Any]:
    return {
        "version": REPORT_VERSION,
        "requested": requested,
        "executed": executed,
        "families": families,
        "counts": {
            "generated": 0,
            "deduplicated": 0,
            "prefilter_passed": 0,
            "retained": 0,
            "fully_evaluated": 0,
            "verified": 0,
            "incomplete": 0,
        },
        "skeletons": [],
    }


def _with_report(layout: LayoutResult, report: dict[str, Any]) -> LayoutResult:
    search = dict(layout.layout_search or {})
    search["road_network_search"] = report
    return replace(layout, layout_search=search)
