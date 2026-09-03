"""Side-effect-free evaluation of one isolated layout candidate context."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from openparkcad.candidate_layout_preview import (
    build_candidate_layout_preview,
    candidate_layout_preview_layout,
)
from openparkcad.candidate_network_preview import build_candidate_network_preview
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.candidate_snapshot import (
    attach_official_selection_evidence,
    build_candidate_objects,
    rebuild_official_layout_from_selection,
)
from openparkcad.engineering_validation import build_engineering_validation
from openparkcad.layout_candidates import (
    LayoutCandidateContext,
    LayoutCandidateEvaluation,
    copy_layout,
)
from openparkcad.maneuver_validation import validate_maneuvers
from openparkcad.models import LayoutResult
from openparkcad.operational_quality import operational_quality_report
from openparkcad.scoring import score_layout, score_total
from openparkcad.road_traversal import apply_road_traversal, road_traversal_satisfied
from openparkcad.site_constraints import validate_site_constraints
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def evaluate_layout_candidate(
    context: LayoutCandidateContext,
    *,
    selector_time_limit_seconds: float | None = None,
) -> LayoutCandidateEvaluation:
    """Run catalog → selector → preview → official rebuild → checks → score.

    Returns data only. Does not write files, mutate the input context, or call
    ``generate_layout``.
    """
    started = time.perf_counter()
    site = context.site
    if selector_time_limit_seconds is not None:
        optimization = dict(site.optimization)
        optimization["selector_time_limit_seconds"] = float(selector_time_limit_seconds)
        site = replace(site, optimization=optimization)
    layout = copy_layout(context.template_layout, site=site)
    template = _revalidate_candidate(copy_layout(context.template_layout, site=site), context.candidate_id)
    template_score = score_total(template) if template.score else None
    objects = build_candidate_objects(layout)
    selection = select_candidate_objects(objects, layout.site)
    enriched = replace(layout, candidate_objects=objects, candidate_selection=selection)
    enriched = replace(enriched, candidate_network_preview=build_candidate_network_preview(enriched))
    enriched = replace(enriched, candidate_layout_preview=build_candidate_layout_preview(enriched))

    requested = selection.get("requested_backend")
    actual = selection.get("backend")
    fallback = selection.get("backend_fallback_reason")
    rebuilt: LayoutResult | None = None
    failure_class: str | None = None
    used_template = True
    try:
        preview_layout = candidate_layout_preview_layout(enriched)
        rebuilt = rebuild_official_layout_from_selection(enriched, preview_layout)
        rebuilt = _revalidate_candidate(rebuilt, context.candidate_id)
        used_template = False
    except Exception as exc:
        failure_class = f"rebuild_failed:{type(exc).__name__}"
        rebuilt = template
        used_template = True

    if rebuilt is not None and not used_template:
        selector_better = _finite_score(rebuilt.score) and (
            template_score is None
            or not _layout_has_minimum(template)
            or float(rebuilt.score.get("total", 0.0)) > float(template_score)
        )
        if not selector_better:
            rebuilt = template
            used_template = True
        else:
            rebuilt = attach_official_selection_evidence(enriched, rebuilt)

    score = dict(rebuilt.score) if rebuilt is not None and rebuilt.score else None
    if rebuilt is not None and not _finite_score(score):
        failure_class = failure_class or "non_finite_score"
    checks = _checks(rebuilt)
    return LayoutCandidateEvaluation(
        candidate_id=context.candidate_id,
        spine_id=context.spine_id,
        requested_backend=str(requested) if requested is not None else None,
        actual_backend=str(actual) if actual is not None else None,
        fallback_reason=str(fallback) if fallback else None,
        preview=dict(enriched.candidate_layout_preview),
        rebuilt_layout=rebuilt,
        checks=checks,
        score=score,
        duration_seconds=time.perf_counter() - started,
        failure_class=failure_class,
        used_template=used_template,
        provenance=dict(selection.get("solver_provenance") or {}),
        selection=dict(selection),
        template_score_total=template_score,
    )


def _layout_has_minimum(layout: LayoutResult) -> bool:
    return layout.stall_count > 0 and bool(layout.aisles)


def _revalidate_candidate(layout: LayoutResult, candidate_id: str) -> LayoutResult:
    maneuver = validate_maneuvers(layout)
    graph = validate_traffic_graph(build_traffic_graph(layout), layout)
    site_constraints = validate_site_constraints(layout)
    operational = operational_quality_report(layout)
    validated = replace(
        layout,
        maneuver_validation=maneuver,
        graph_validation=graph,
        site_constraint_validation=site_constraints,
        operational_quality=operational,
    )
    validated = apply_road_traversal(validated, result_layout_id=candidate_id, source_layout_id=candidate_id)
    validated = replace(
        validated,
        engineering_validation=build_engineering_validation(validated, result_scope=f"candidate:{candidate_id}"),
    )
    return replace(validated, score=score_layout(validated))


def _finite_score(score: dict[str, Any] | None) -> bool:
    if not isinstance(score, dict) or "total" not in score:
        return False
    try:
        value = float(score["total"])
    except (TypeError, ValueError):
        return False
    return value == value and value not in {float("inf"), float("-inf")}


def _checks(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None:
        return {
            "graph": {"executed": False, "valid": None},
            "maneuver": {"executed": False, "valid": None},
            "vehicle": {"executed": False, "valid": None},
            "site_quota": {"executed": False, "valid": None},
            "engineering": {"executed": False, "valid": None},
            "operational": {"executed": False, "valid": None},
            "road_traversal": {"executed": False, "valid": None, "status": None},
        }
    vehicle = (
        layout.maneuver_validation.get("vehicle_validation")
        if isinstance(layout.maneuver_validation, dict)
        else {}
    )
    vehicle = vehicle if isinstance(vehicle, dict) else {}
    return {
        "graph": {
            "executed": "valid" in layout.graph_validation,
            "valid": layout.graph_validation.get("valid"),
        },
        "maneuver": {
            "executed": "valid" in layout.maneuver_validation,
            "valid": layout.maneuver_validation.get("valid"),
        },
        "vehicle": {
            "executed": "valid" in vehicle or bool(vehicle.get("checks")),
            "valid": vehicle.get("valid"),
        },
        "site_quota": {
            "executed": "valid" in layout.site_constraint_validation,
            "valid": layout.site_constraint_validation.get("valid"),
        },
        "engineering": {
            "executed": "valid" in layout.engineering_validation,
            "valid": layout.engineering_validation.get("valid"),
            "result_scope": layout.engineering_validation.get("result_scope"),
        },
        "operational": {
            "executed": "valid" in layout.operational_quality,
            "valid": layout.operational_quality.get("valid"),
        },
        "road_traversal": {
            "executed": bool(layout.road_traversal_validation.get("executed")),
            "valid": layout.road_traversal_validation.get("valid"),
            "status": layout.road_traversal_validation.get("status"),
            "requested": layout.road_traversal_validation.get("requested"),
            "satisfied": road_traversal_satisfied(layout),
        },
        "score_total": score_total(layout) if layout.score else None,
    }
