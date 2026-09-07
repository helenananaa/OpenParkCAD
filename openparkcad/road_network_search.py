"""Outer search over legacy baseline plus opt-in parallel_ladder skeletons (N7)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.road_network_config import (
    DEFAULT_REFINEMENT_BUDGET_SECONDS,
    RoadNetworkConfig,
    parse_road_network_mapping,
    positive_finite_number,
)
from openparkcad.scoring import score_total
from openparkcad.skeleton_identity import official_skeleton_mapping
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons

REPORT_VERSION = "road-network-search-1"
OFFICIAL_GENERATION_MODE = "parallel_ladder"


def road_network_requested(site: SiteSpec) -> bool:
    raw = (site.optimization or {}).get("road_network")
    if not isinstance(raw, dict):
        return False
    return parse_road_network_mapping(raw).enabled is True


def apply_road_network_search(site: SiteSpec, baseline: LayoutResult) -> LayoutResult:
    raw = (site.optimization or {}).get("road_network")
    if not isinstance(raw, dict):
        return baseline
    config = parse_road_network_mapping(raw)
    if config.enabled is not True:
        return baseline
    families = list(config.families)
    if "parallel_ladder" not in families:
        report = _empty_report(requested=True, executed=True, families=families)
        report["notes"] = ["families exclude parallel_ladder"]
        return _with_report(baseline, report)

    promotion = bool((site.optimization or {}).get("promote_candidate_layout_preview"))
    ladder = generate_parallel_ladder_skeletons(site, config=raw)
    from openparkcad.generator import _finalize_candidate, _layout_valid

    budget_seconds = _refinement_budget_seconds(site, config)
    max_full = config.max_full_evaluations
    backend = str((site.optimization or {}).get("selector_backend") or "greedy")
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
                evaluated.append(_incomplete_skeleton_row(rest))
            break
        item_started = time.perf_counter()
        catalog = build_and_select_ladder_modules(site, candidate.skeleton, backend=backend)
        layout = layout_from_skeleton(site, candidate.skeleton, catalog.selected_stalls)
        layout = _finalize_candidate(layout)
        valid = _layout_valid(layout)
        row = _evaluated_skeleton_row(candidate, layout, catalog, valid, time.perf_counter() - item_started)
        evaluated.append(row)
        if not promotion:
            row["selected_reason"] = "promotion_off"
            continue
        if not valid:
            row["selected_reason"] = "candidate_invalid"
            continue
        if not _layout_valid(official) and valid:
            official = layout
            row["selected_reason"] = "recovered_feasible"
            continue
        if _layout_valid(official) and score_total(layout) > score_total(official) + 1e-6:
            official = layout
            row["selected_reason"] = "promoted"
        else:
            row["selected_reason"] = "valid_not_better"

    fully_evaluated = sum(1 for item in evaluated if not item.get("incomplete"))
    report = {
        "version": REPORT_VERSION,
        "requested": True,
        "executed": True,
        "families": families,
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
        "baseline_retained": official is baseline or official.generation_mode != OFFICIAL_GENERATION_MODE,
        "official_mapping": official_skeleton_mapping(official) if official.generation_mode == OFFICIAL_GENERATION_MODE else None,
    }
    return _with_report(official, report)


def _refinement_budget_seconds(site: SiteSpec, config: RoadNetworkConfig) -> float:
    if config.refinement_budget_seconds is not None:
        return config.refinement_budget_seconds
    search = site.optimization.get("layout_search") if isinstance(site.optimization, dict) else None
    if isinstance(search, dict) and search.get("refinement_budget_seconds") is not None:
        return positive_finite_number(
            search["refinement_budget_seconds"],
            field="optimization.layout_search.refinement_budget_seconds",
        )
    return DEFAULT_REFINEMENT_BUDGET_SECONDS


def _incomplete_skeleton_row(candidate: Any) -> dict[str, Any]:
    return {
        "skeleton_id": candidate.skeleton.skeleton_id,
        "family": candidate.skeleton.family,
        "source": "parallel_ladder",
        "prefilter_score": candidate.prefilter_score,
        "stall_count": None,
        "score_total": None,
        "valid": False,
        "incomplete": True,
        "selected_reason": "budget_exhausted",
        "failure_class": "budget_exhausted",
        "elapsed_seconds": 0.0,
        "selector": None,
        "modules": None,
        "gates": None,
    }


def _evaluated_skeleton_row(candidate: Any, layout: LayoutResult, catalog: Any, valid: bool, elapsed: float) -> dict[str, Any]:
    road = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    selector = (catalog.provenance or {}).get("selector") or {
        "requested": catalog.requested_backend,
        "actual": catalog.actual_backend,
    }
    failure = None
    if not valid:
        if road.get("requested") and road.get("status") == "incomplete":
            failure = "budget_exhausted"
        elif road.get("requested") and road.get("valid") is not True:
            failure = "road_traversal_failed"
        elif graph.get("valid") is False:
            failure = "traffic_graph_invalid"
        else:
            failure = "candidate_invalid"
    return {
        "skeleton_id": candidate.skeleton.skeleton_id,
        "family": candidate.skeleton.family,
        "source": "parallel_ladder",
        "prefilter_score": candidate.prefilter_score,
        "stall_count": layout.stall_count,
        "score_total": score_total(layout) if layout.score else None,
        "valid": valid,
        "incomplete": False,
        "selected_reason": None,
        "failure_class": failure,
        "elapsed_seconds": elapsed,
        "selector": selector,
        "modules": {
            "module_count": len(catalog.modules),
            "conflict_count": len(catalog.conflicts),
            "selected_count": len(catalog.selected_ids),
            "official_stall_count": len(catalog.selected_stalls),
        },
        "gates": {
            "graph": graph.get("valid"),
            "vehicle": maneuver.get("valid"),
            "site_constraints": (layout.site_constraint_validation or {}).get("valid")
            if isinstance(layout.site_constraint_validation, dict)
            else None,
            "engineering": engineering.get("valid"),
            "operational": (layout.operational_quality or {}).get("valid")
            if isinstance(layout.operational_quality, dict)
            else None,
            "road_traversal": {
                "requested": road.get("requested"),
                "status": road.get("status"),
                "valid": road.get("valid"),
            },
        },
    }


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
