"""Multi-spine layout search: ranking, budget, and official-result coordination."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from openparkcad.candidate_snapshot import finalize_official_selection_evidence
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.layout_candidates import (
    LayoutCandidateContext,
    LayoutCandidateEvaluation,
    spine_family,
    spine_geometries_equivalent,
    stall_assignment_payload,
)
from openparkcad.layout_evaluation import evaluate_layout_candidate
from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.scoring import score_layout, score_total

EvaluateFn = Callable[..., LayoutCandidateEvaluation]
CollectFn = Callable[[SiteSpec], list[LayoutCandidateContext]]
LegacyFn = Callable[[SiteSpec], LayoutResult]
TimeFn = Callable[[], float]


@dataclass(frozen=True)
class LayoutSearchConfig:
    mode: str = "legacy"
    top_k: int = 4
    refinement_budget_seconds: float = 10.0


def parse_layout_search_mapping(optimization: dict[str, Any] | None) -> LayoutSearchConfig:
    if not isinstance(optimization, dict) or "layout_search" not in optimization:
        return LayoutSearchConfig()
    raw = optimization.get("layout_search")
    if raw is None:
        return LayoutSearchConfig()
    if not isinstance(raw, dict):
        raise ValueError("optimization.layout_search must be an object")
    mode = raw.get("mode", "legacy")
    if mode is None:
        mode = "legacy"
    if not isinstance(mode, str) or mode not in {"legacy", "multi_spine"}:
        raise ValueError("optimization.layout_search.mode must be 'legacy' or 'multi_spine'")
    top_k = raw.get("top_k", 4)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("optimization.layout_search.top_k must be a positive integer")
    budget = raw.get("refinement_budget_seconds", 10.0)
    if isinstance(budget, bool):
        raise ValueError("optimization.layout_search.refinement_budget_seconds must be a positive finite number")
    try:
        budget_value = float(budget)
    except (TypeError, ValueError) as exc:
        raise ValueError("optimization.layout_search.refinement_budget_seconds must be a positive finite number") from exc
    if not math.isfinite(budget_value) or budget_value <= 0:
        raise ValueError("optimization.layout_search.refinement_budget_seconds must be a positive finite number")
    return LayoutSearchConfig(mode=mode, top_k=top_k, refinement_budget_seconds=budget_value)


def read_layout_search(site: SiteSpec) -> LayoutSearchConfig:
    return parse_layout_search_mapping(site.optimization)


def layout_search_report(layout: LayoutResult) -> dict[str, Any]:
    existing = getattr(layout, "layout_search", {}) or {}
    if isinstance(existing, dict) and existing.get("version") == "layout-search-1":
        return dict(existing)
    config = read_layout_search(layout.site)
    return {
        "version": "layout-search-1",
        "mode": config.mode if config.mode == "multi_spine" else "legacy",
        "status": "not_requested" if config.mode == "legacy" else "completed",
        "baseline": None,
        "counts": None,
        "budget": None,
        "candidates": [],
        "best_preview_candidate_id": None,
        "official_candidate_id": None,
        "publication": {
            "promotion_requested": bool(layout.site.optimization.get("promote_candidate_layout_preview", False)),
            "replaced": False,
            "reason": "legacy_path",
        },
        "quality_delta": None,
    }


def search_multi_spine(
    site: SiteSpec,
    config: LayoutSearchConfig | None = None,
    *,
    time_fn: TimeFn | None = None,
    evaluate_fn: EvaluateFn | None = None,
    collect_fn: CollectFn | None = None,
    legacy_fn: LegacyFn | None = None,
) -> LayoutResult:
    config = config or read_layout_search(site)
    clock = time_fn or time.perf_counter
    evaluate = evaluate_fn or evaluate_layout_candidate
    reuse_legacy_collect = collect_fn is None and legacy_fn is None
    if collect_fn is None:
        from openparkcad.generator import collect_layout_candidate_contexts

        collect = collect_layout_candidate_contexts
    else:
        collect = collect_fn
    if legacy_fn is None:
        from openparkcad.generator import generate_layout_legacy

        legacy = generate_layout_legacy
    else:
        legacy = legacy_fn

    baseline_started = clock()
    collected_records: list = []
    reused_collect = False
    if reuse_legacy_collect and config.top_k > 1:
        baseline = legacy(site, context_sink=collected_records)
        reused_collect = True
    else:
        baseline = legacy(site)
    baseline_seconds = clock() - baseline_started
    promotion_requested = bool(site.optimization.get("promote_candidate_layout_preview", False))
    baseline_valid = _publishable(baseline)
    baseline_id = _baseline_candidate_id(baseline)

    report = _empty_report(config, promotion_requested)
    report["baseline"] = {
        "candidate_id": baseline_id,
        "valid": baseline_valid,
        "score_total": _score_total_or_none(baseline),
        "stall_count": baseline.stall_count,
        "generation_mode": baseline.generation_mode,
        "local_promotion": (baseline.candidate_layout_promotion or {}).get("status"),
    }
    report["budget"]["baseline_seconds"] = baseline_seconds
    report["publication"] = {
        "promotion_requested": promotion_requested,
        "replaced": False,
        "reason": "baseline_retained",
    }

    if config.top_k <= 1:
        report["status"] = "completed"
        report["official_candidate_id"] = baseline_id if baseline_valid else None
        report["publication"]["reason"] = "top_k_reuses_legacy"
        report["counts"]["retained"] = 1
        report["counts"]["evaluated"] = 1
        report["counts"]["verified"] = 1 if baseline_valid else 0
        return _with_report(baseline, report)

    collect_started = clock()
    if reused_collect:
        from openparkcad.phase1_candidates import contexts_from_collected_records

        contexts = _dedupe_contexts(contexts_from_collected_records(collected_records))
    else:
        contexts = _dedupe_contexts(collect(site))
    report["counts"]["generated"] = len(contexts)
    report["counts"]["collectable"] = len([item for item in contexts if item.template_layout.aisles])
    ranked = rank_contexts(contexts, baseline)
    retained = ranked[: config.top_k]
    report["counts"]["deduplicated"] = len(contexts)
    report["counts"]["retained"] = len(retained)
    report["counts"]["not_evaluated"] = max(len(ranked) - len(retained), 0)

    evaluations: list[LayoutCandidateEvaluation] = []
    budget_started = clock()
    budget_exhausted = False
    unfinished: list[str] = []
    matched_baseline = match_baseline_context(retained, baseline)

    for context in retained:
        if matched_baseline is not None and context.candidate_id == matched_baseline.candidate_id:
            evaluation = _evaluation_from_baseline(context, baseline)
            evaluations.append(evaluation)
            continue
        remaining = config.refinement_budget_seconds - (clock() - budget_started)
        if remaining <= 0:
            budget_exhausted = True
            unfinished.append(context.candidate_id)
            continue
        selector_limit = _selector_limit(site, remaining)
        evaluation = evaluate(context, selector_time_limit_seconds=selector_limit)
        if not _evaluation_complete(evaluation):
            unfinished.append(context.candidate_id)
            evaluations.append(evaluation)
            continue
        evaluations.append(evaluation)

    report["budget"]["configured_seconds"] = config.refinement_budget_seconds
    report["budget"]["elapsed_seconds"] = clock() - budget_started
    report["budget"]["collect_seconds"] = 0.0 if reused_collect else (budget_started - collect_started)
    report["budget"]["collect_reused_baseline_generation"] = reused_collect
    report["budget"]["exhausted"] = budget_exhausted
    report["budget"]["unfinished_candidate_ids"] = unfinished
    report["status"] = "budget_exhausted" if budget_exhausted else "completed"
    report["counts"]["evaluated"] = len(evaluations)
    report["counts"]["verified"] = len([item for item in evaluations if _evaluation_complete(item) and _publishable(item.rebuilt_layout)])

    official, publication, best_preview_id = choose_official(
        baseline,
        evaluations,
        promotion_requested=promotion_requested,
    )
    report["publication"] = publication
    report["best_preview_candidate_id"] = best_preview_id
    report["official_candidate_id"] = publication.get("official_candidate_id")
    report["candidates"] = [_candidate_summary(item, retained) for item in evaluations]
    for context in retained:
        if all(item.candidate_id != context.candidate_id for item in evaluations):
            report["candidates"].append(
                {
                    "candidate_id": context.candidate_id,
                    "spine_id": context.spine_id,
                    "retain_reason": context.retain_reason,
                    "status": "not_evaluated",
                    "reason": "budget_exhausted" if context.candidate_id in unfinished else "not_evaluated",
                }
            )
    if publication.get("replaced") and official is not baseline:
        official = _as_official(official)
    quality = _quality_delta(baseline, official, baseline_valid, publication)
    report["quality_delta"] = quality
    return _with_report(official, report)


def rank_contexts(contexts: list[LayoutCandidateContext], baseline: LayoutResult) -> list[LayoutCandidateContext]:
    unique = _dedupe_contexts(contexts)
    baseline_context = match_baseline_context(unique, baseline)
    rest = [item for item in unique if baseline_context is None or item.candidate_id != baseline_context.candidate_id]
    feasible: list[LayoutCandidateContext] = []
    infeasible: list[LayoutCandidateContext] = []
    for item in rest:
        if item.collect_status == "collected" and item.template_layout.stall_count > 0 and item.template_layout.aisles:
            feasible.append(item)
        else:
            infeasible.append(item)
    feasible.sort(key=lambda item: (-score_total(item.template_layout), item.candidate_id))
    infeasible.sort(key=lambda item: item.candidate_id)

    covered: set[tuple[Any, ...]] = set()
    diversified: list[LayoutCandidateContext] = []
    leftover: list[LayoutCandidateContext] = []
    if baseline_context is not None:
        covered.add(_family_key(baseline_context))
        baseline_context = replace(baseline_context, retain_reason="legacy_winner")
    for item in feasible:
        key = _family_key(item)
        if key not in covered:
            covered.add(key)
            diversified.append(replace(item, retain_reason="family_coverage"))
        else:
            leftover.append(replace(item, retain_reason="score_order"))
    infeasible = [replace(item, retain_reason=item.collect_status or "infeasible") for item in infeasible]
    ordered: list[LayoutCandidateContext] = []
    if baseline_context is not None:
        ordered.append(baseline_context)
    ordered.extend(diversified)
    ordered.extend(leftover)
    ordered.extend(infeasible)
    return ordered


def match_baseline_context(
    contexts: list[LayoutCandidateContext],
    baseline: LayoutResult,
) -> LayoutCandidateContext | None:
    """Return the collected context whose spine geometry matches baseline, or None."""
    assignment = stall_assignment_payload(baseline.site)
    family = spine_family(baseline)
    matches: list[LayoutCandidateContext] = []
    for item in contexts:
        item_family = item.source.get("family") or spine_family(item.template_layout)
        if item_family != family:
            continue
        if stall_assignment_payload(item.site) != assignment:
            continue
        if item.template_layout.main_entrance_id != baseline.main_entrance_id:
            continue
        if not spine_geometries_equivalent(item.template_layout, baseline):
            continue
        matches.append(item)
    if not matches:
        return None
    matches.sort(
        key=lambda item: (
            0 if item.template_layout.stall_count == baseline.stall_count else 1,
            item.candidate_id,
        )
    )
    return matches[0]


def choose_official(
    baseline: LayoutResult,
    evaluations: list[LayoutCandidateEvaluation],
    *,
    promotion_requested: bool,
) -> tuple[LayoutResult, dict[str, Any], str | None]:
    baseline_valid = _publishable(baseline)
    baseline_score = _score_total_or_none(baseline)
    completed = [
        item
        for item in evaluations
        if _evaluation_complete(item) and item.rebuilt_layout is not None and _finite_number(item.score)
    ]
    publishable = [item for item in completed if _publishable(item.rebuilt_layout)]
    best_preview = _best_evaluation(publishable)
    best_preview_id = best_preview.candidate_id if best_preview is not None else None

    if not promotion_requested:
        return (
            baseline,
            {
                "promotion_requested": False,
                "replaced": False,
                "reason": "promotion_not_requested",
                "official_candidate_id": _baseline_candidate_id(baseline) if baseline_valid else None,
            },
            best_preview_id,
        )

    if baseline_valid:
        improved = [
            item
            for item in publishable
            if _evaluation_score(item) is not None
            and baseline_score is not None
            and _evaluation_score(item) > baseline_score
        ]
        if not improved:
            reason = "tie_keeps_baseline" if best_preview is not None and _evaluation_score(best_preview) == baseline_score else "no_improvement"
            return (
                baseline,
                {
                    "promotion_requested": True,
                    "replaced": False,
                    "reason": reason,
                    "official_candidate_id": _baseline_candidate_id(baseline),
                },
                best_preview_id,
            )
        winner = _best_evaluation(improved)
        assert winner is not None and winner.rebuilt_layout is not None
        return (
            winner.rebuilt_layout,
            {
                "promotion_requested": True,
                "replaced": True,
                "reason": "higher_scoring_verified_candidate",
                "official_candidate_id": winner.candidate_id,
            },
            best_preview_id,
        )

    if publishable:
        winner = _best_evaluation(publishable)
        assert winner is not None and winner.rebuilt_layout is not None
        return (
            winner.rebuilt_layout,
            {
                "promotion_requested": True,
                "replaced": True,
                "reason": "recovered_feasible",
                "official_candidate_id": winner.candidate_id,
            },
            best_preview_id,
        )
    return (
        baseline,
        {
            "promotion_requested": True,
            "replaced": False,
            "reason": "no_valid_promotable_candidate",
            "official_candidate_id": None,
        },
        best_preview_id,
    )


def _best_evaluation(items: list[LayoutCandidateEvaluation]) -> LayoutCandidateEvaluation | None:
    if not items:
        return None
    return max(items, key=lambda item: (_evaluation_score(item) or float("-inf"), item.candidate_id))


def _evaluation_score(item: LayoutCandidateEvaluation) -> float | None:
    if not isinstance(item.score, dict):
        return None
    return _finite_or_none(item.score.get("total"))


def _publishable(layout: LayoutResult | None) -> bool:
    if layout is None:
        return False
    if layout.stall_count <= 0 or not layout.aisles:
        return False
    if not layout.graph_validation.get("valid", False):
        return False
    if not layout.maneuver_validation.get("valid", False):
        return False
    if not layout.site_constraint_validation.get("valid", False):
        return False
    engineering = layout.engineering_validation or {}
    if engineering and not engineering.get("valid", False):
        return False
    if not layout.operational_quality.get("valid", False):
        return False
    from openparkcad.road_traversal import road_traversal_satisfied

    return road_traversal_satisfied(layout)


def _evaluation_complete(evaluation: LayoutCandidateEvaluation) -> bool:
    if evaluation.rebuilt_layout is None:
        return False
    if evaluation.failure_class:
        return False
    if not _finite_number(evaluation.score):
        return False
    checks = evaluation.checks or {}
    for key in ("graph", "maneuver", "site_quota", "engineering", "operational"):
        block = checks.get(key) if isinstance(checks.get(key), dict) else {}
        if not block.get("executed"):
            return False
    layout = evaluation.rebuilt_layout
    from openparkcad.road_traversal_models import publication_requires_road_evidence

    if layout is not None and publication_requires_road_evidence(layout.site):
        block = checks.get("road_traversal") if isinstance(checks.get("road_traversal"), dict) else {}
        if not block.get("executed"):
            return False
    return True


def _finite_number(score: dict[str, Any] | None) -> bool:
    if not isinstance(score, dict):
        return False
    return _finite_or_none(score.get("total")) is not None


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _score_total_or_none(layout: LayoutResult) -> float | None:
    if layout.score:
        return _finite_or_none(layout.score.get("total"))
    return _finite_or_none(score_total(layout))


def _selector_limit(site: SiteSpec, remaining: float) -> float:
    raw = site.optimization.get("selector_time_limit_seconds", 2.0)
    try:
        configured = max(float(raw), 0.01)
    except (TypeError, ValueError):
        configured = 2.0
    return max(min(configured, float(remaining)), 0.01)


def _dedupe_contexts(contexts: list[LayoutCandidateContext]) -> list[LayoutCandidateContext]:
    seen: set[str] = set()
    unique: list[LayoutCandidateContext] = []
    for item in contexts:
        if item.candidate_id in seen:
            continue
        seen.add(item.candidate_id)
        unique.append(item)
    return unique


def _family_key(context: LayoutCandidateContext) -> tuple[Any, ...]:
    payload = context.spine_payload or {}
    return (
        payload.get("entrance_id") or context.source.get("entrance_id"),
        payload.get("exit_entrance_id"),
        payload.get("family") or context.source.get("family"),
    )


def _baseline_candidate_id(baseline: LayoutResult) -> str:
    search = getattr(baseline, "layout_search", {}) or {}
    if isinstance(search, dict) and search.get("official_candidate_id"):
        return str(search["official_candidate_id"])
    return f"baseline:{spine_family(baseline)}:{baseline.main_entrance_id}:{baseline.stall_count}"


def _evaluation_from_baseline(context: LayoutCandidateContext, baseline: LayoutResult) -> LayoutCandidateEvaluation:
    selection = dict(baseline.candidate_selection or {})
    return LayoutCandidateEvaluation(
        candidate_id=context.candidate_id,
        spine_id=context.spine_id,
        requested_backend=selection.get("requested_backend"),
        actual_backend=selection.get("backend"),
        fallback_reason=selection.get("backend_fallback_reason"),
        preview=dict(baseline.candidate_layout_preview or {}),
        rebuilt_layout=baseline,
        checks={
            "graph": {"executed": "valid" in baseline.graph_validation, "valid": baseline.graph_validation.get("valid")},
            "maneuver": {
                "executed": "valid" in baseline.maneuver_validation,
                "valid": baseline.maneuver_validation.get("valid"),
            },
            "vehicle": {"executed": True, "valid": True},
            "site_quota": {
                "executed": "valid" in baseline.site_constraint_validation,
                "valid": baseline.site_constraint_validation.get("valid"),
            },
            "engineering": {
                "executed": "valid" in baseline.engineering_validation,
                "valid": baseline.engineering_validation.get("valid"),
            },
            "operational": {
                "executed": "valid" in baseline.operational_quality,
                "valid": baseline.operational_quality.get("valid"),
            },
        },
        score=dict(baseline.score) if baseline.score else score_layout(baseline),
        duration_seconds=0.0,
        failure_class=None,
        used_template=baseline.generation_mode != "candidate_layout_promoted",
        provenance=dict((selection.get("solver_provenance") or {})),
        selection=selection,
        template_score_total=_score_total_or_none(baseline),
        retain_reason="legacy_winner",
    )


def _as_official(layout: LayoutResult) -> LayoutResult:
    engineering = build_engineering_validation(layout, result_scope="official_layout")
    return finalize_official_selection_evidence(replace(layout, engineering_validation=engineering))


def _with_report(layout: LayoutResult, report: dict[str, Any]) -> LayoutResult:
    payload = {
        "version": "layout-search-1",
        "mode": report.get("mode"),
        "status": report.get("status"),
        **report,
    }
    object.__setattr__(layout, "layout_search", payload)
    return layout


def _empty_report(config: LayoutSearchConfig, promotion_requested: bool) -> dict[str, Any]:
    return {
        "version": "layout-search-1",
        "mode": config.mode,
        "status": "completed",
        "baseline": {},
        "counts": {
            "generated": 0,
            "collectable": 0,
            "deduplicated": 0,
            "retained": 0,
            "evaluated": 0,
            "verified": 0,
            "not_evaluated": 0,
        },
        "budget": {
            "configured_seconds": config.refinement_budget_seconds,
            "elapsed_seconds": None,
            "baseline_seconds": None,
            "collect_seconds": None,
            "collect_reused_baseline_generation": False,
            "exhausted": False,
            "unfinished_candidate_ids": [],
        },
        "candidates": [],
        "best_preview_candidate_id": None,
        "official_candidate_id": None,
        "publication": {"promotion_requested": promotion_requested, "replaced": False, "reason": None},
        "quality_delta": None,
    }


def _candidate_summary(evaluation: LayoutCandidateEvaluation, retained: list[LayoutCandidateContext]) -> dict[str, Any]:
    context = next((item for item in retained if item.candidate_id == evaluation.candidate_id), None)
    source = context.source if context is not None else {}
    return {
        "candidate_id": evaluation.candidate_id,
        "spine_id": evaluation.spine_id,
        "family": source.get("family"),
        "entrance_id": source.get("entrance_id"),
        "aisle_lateral_offset": source.get("aisle_lateral_offset"),
        "dogleg_offset": source.get("dogleg_offset"),
        "retain_reason": evaluation.retain_reason or (context.retain_reason if context is not None else None),
        "requested_backend": evaluation.requested_backend,
        "actual_backend": evaluation.actual_backend,
        "fallback_reason": evaluation.fallback_reason,
        "used_template": evaluation.used_template,
        "template_score_total": evaluation.template_score_total,
        "official_score_total": _evaluation_score(evaluation),
        "valid": _publishable(evaluation.rebuilt_layout),
        "failure_class": evaluation.failure_class,
        "checks": evaluation.checks,
        "stall_count": evaluation.rebuilt_layout.stall_count if evaluation.rebuilt_layout is not None else None,
        "geometry": _evaluation_geometry(evaluation),
        "duration_seconds": evaluation.duration_seconds,
        "solver_provenance": dict(evaluation.provenance or {}),
        "selected_ids": list((evaluation.selection or {}).get("selected_ids") or []),
        "selected_branch_count": (evaluation.selection or {}).get("selected_branch_count"),
        "selected_connector_count": (evaluation.selection or {}).get("selected_connector_count"),
    }


def _evaluation_geometry(evaluation: LayoutCandidateEvaluation) -> dict[str, Any]:
    layout = evaluation.rebuilt_layout
    if layout is None:
        return {"aisles": [], "stalls": []}
    from openparkcad.review_bundle import snapshot_candidate_geometry

    return snapshot_candidate_geometry(layout)


def _quality_delta(
    baseline: LayoutResult,
    official: LayoutResult,
    baseline_valid: bool,
    publication: dict[str, Any],
) -> dict[str, Any]:
    if publication.get("reason") == "recovered_feasible":
        return {"kind": "recovered_feasible", "score_delta": None, "stall_delta": official.stall_count}
    if not baseline_valid:
        return {"kind": "no_valid_baseline", "score_delta": None, "stall_delta": None}
    official_valid = _publishable(official)
    if not official_valid:
        return {"kind": "regression", "score_delta": None, "stall_delta": None}
    base_score = _score_total_or_none(baseline)
    off_score = _score_total_or_none(official)
    if base_score is None or off_score is None:
        return {"kind": "not_available", "score_delta": None, "stall_delta": None}
    return {
        "kind": "valid_to_valid",
        "score_delta": off_score - base_score,
        "stall_delta": official.stall_count - baseline.stall_count,
    }
