"""Bounded spacing refinements of shortlisted ladder skeletons; no dimension relaxation."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
import time

from openparkcad.models import SiteSpec
from openparkcad.road_network_config import SpacingSearchConfig
from openparkcad.road_skeleton import make_skeleton, stable_digest
from openparkcad.road_skeleton_validation import validate_skeleton
from openparkcad.site_constraints import site_usable_area
from openparkcad.topology_generators.parallel_ladder import (
    LadderGenerationResult, _build_one, _prefilter_score,
)

VERSION = "ladder-spacing-search-1"


def expand_spacing_candidates(site: SiteSpec, generation: LadderGenerationResult, config: SpacingSearchConfig,
                              *, deadline: float | None = None):
    if not config.enabled:
        return generation, None
    if site.stall.family != "perpendicular":
        raise ValueError("spacing_search supports perpendicular ladder stalls only")
    default_gap = round(2.0 * max(site.vehicle.swept_path_margin if site.vehicle else 0.0, 0.0) + .05, 9)
    road_gaps = config.inter_aisle_gaps_m if config.inter_aisle_gaps_m is not None else (0.0, default_gap)
    stall_gaps = config.stall_gaps_m if config.stall_gaps_m is not None else (0.0, default_gap)
    pairs = sorted(set(product(road_gaps, stall_gaps)),
                   key=lambda p: (0 if p == (0., 0.) else 1 if min(p) > 0 else 2, sum(p), p))
    # Always retain the unmodified candidate, even for explicitly supplied gaps.
    pairs = [(0., 0.)] + [p for p in pairs if p != (0., 0.)]
    retained_pairs = pairs[:config.max_variants]
    candidates = []
    attempts = []
    seen = set()
    usable = site_usable_area(site, "aisle")
    for base in generation.candidates:
        candidates.append(base)
        seen.add((base.skeleton.skeleton_id, 0.0))
        for road_gap, stall_gap in retained_pairs[1:]:
            row = {"base_skeleton_id": base.skeleton.skeleton_id, "inter_aisle_gap_m": road_gap,
                   "stall_gap_m": stall_gap, "status": "rejected_geometry"}
            attempts.append(row)
            if deadline is not None and time.perf_counter() >= deadline:
                row["status"] = "incomplete"
                continue
            built = _build_one(site, usable, base.axis_degrees, base.aisle_count, base.policy,
                               site.aisle_width, site.stall.length,
                               site.aisle_width + 2 * site.stall.length + road_gap,
                               max(2 * site.stall.width, 8.0))
            if built is None:
                continue
            skeleton, components = built
            candidate_id = "spacing-" + stable_digest({"skeleton_id": skeleton.skeleton_id, "stall_gap_m": stall_gap})[:24]
            skeleton = make_skeleton(
                family=skeleton.family, nodes=skeleton.nodes, segments=skeleton.segments, movements=skeleton.movements,
                entrance_ids=skeleton.entrance_ids,
                source={**dict(skeleton.source), "spacing_version": VERSION,
                        "base_skeleton_id": base.skeleton.skeleton_id,
                        "inter_aisle_gap_m": road_gap, "stall_gap_m": stall_gap,
                        "spacing_candidate_id": candidate_id},
                site=site, strict=False,
            )
            if validate_skeleton(skeleton, site=site):
                continue
            identity = (skeleton.skeleton_id, stall_gap)
            if identity in seen:
                row["status"] = "deduplicated"
                continue
            seen.add(identity)
            row.update(status="retained", skeleton_id=skeleton.skeleton_id, candidate_id=candidate_id)
            candidates.append(replace(base, skeleton=skeleton, prefilter_score=_prefilter_score(components),
                                      prefilter_components=components, candidate_id=candidate_id))
    counts = dict(generation.counts)
    counts["generated"] = counts.get("generated", 0) + len(attempts)
    counts["prefilter_passed"] = counts.get("prefilter_passed", 0) + sum(r["status"] == "retained" for r in attempts)
    counts["deduplicated"] = counts.get("deduplicated", 0) + sum(r["status"] == "deduplicated" for r in attempts)
    counts["retained"] = len(candidates)
    return replace(generation, candidates=candidates, counts=counts), {
        "version": VERSION, "requested": True, "base_retained": len(generation.candidates),
        "inter_aisle_gaps_m": list(road_gaps), "stall_gaps_m": list(stall_gaps),
        "max_variants_per_base": config.max_variants,
        "omitted_by_variant_cap": max(0, len(pairs) - len(retained_pairs)) * len(generation.candidates),
        "attempts": attempts, "stopped_after_improvement": False,
        "search_scope": "bounded_spacing_candidates_not_global_optimum",
    }
