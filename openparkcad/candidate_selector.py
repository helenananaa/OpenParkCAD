from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.candidate_catalog import (
    BASE_AISLE_ROLES,
    LOOP_BUNDLE_BONUS,
    SELECTOR_VERSION,
    STALL_MODULE_KIND,
    STALL_MODULE_ROLE,
    VARIABLE_AISLE_ROLES,
    parse_selector_num_workers,
    requested_selector_backend,
    selection_class,
)
from openparkcad.models import CandidateObject, SiteSpec
from openparkcad.scoring import SHADOW_STALL_WEIGHT, segment_family_mix_weight, stall_family_weight
from openparkcad.site_constraints import classified_contact_adjustment


def select_candidate_objects(objects: list[CandidateObject], site: SiteSpec | None = None) -> dict[str, object]:
    requested, parse_fallback = requested_selector_backend(
        None if site is None else site.optimization.get("selector_backend")
    )
    eligible = [_selection_record(item, site) for item in objects if _eligible(item)]
    branch_records = sorted([item for item in eligible if item["role"] == "branch"], key=_selection_sort_key)
    connector_records = sorted([item for item in eligible if item["role"] == "connector"], key=_selection_sort_key)
    module_records = sorted([item for item in eligible if item["kind"] == STALL_MODULE_KIND], key=_selection_sort_key)
    units = _selection_units(branch_records, connector_records)
    max_branches = _max_branches(site)
    base_selected_ids = [item.id for item in objects if _is_base_selected(item)]

    mix_weight = segment_family_mix_weight(site)
    greedy_choice = _greedy_choice(units, module_records, max_branches, mix_weight)
    backend = "greedy"
    fallback_reason = parse_fallback
    choice = greedy_choice
    provenance = {
        "backend": "greedy",
        "seed": None,
        "num_workers": None,
        "time_limit_seconds": None,
        "objective_bound": None,
        "gap": None,
        "status": "optimal_greedy",
    }

    if requested == "cpsat" and parse_fallback is None:
        from openparkcad.candidate_cpsat import try_solve_cpsat

        solved, reason = try_solve_cpsat(
            branch_records=branch_records,
            connector_records=connector_records,
            module_records=module_records,
            max_branches=max_branches,
            time_limit_seconds=_selector_time_limit(site),
            seed=_selector_seed(site),
            num_workers=_selector_num_workers(site),
            source_id=_source_id,
            connects=_connects,
            mix_weight=mix_weight,
        )
        if solved is not None:
            backend = "cpsat"
            fallback_reason = None
            choice = solved
            provenance = dict(solved["solver_provenance"])
            choice = {
                **solved,
                "selected_bundles": _bundles_from_selection(
                    connector_records,
                    branch_records,
                    set(solved["selected_ids"]),
                ),
            }
        else:
            fallback_reason = reason or "cpsat_backend_failed"

    selected_ids = list(choice["selected_ids"])
    selected_set = set(selected_ids)
    selected_branch_sources = {str(item) for item in choice["selected_branch_sources"]}
    selected_branch_count = int(choice["selected_branch_count"])
    selected_bundles = list(choice["selected_bundles"])
    rejected = _rejected_records(
        branch_records,
        connector_records,
        module_records,
        selected_set,
        selected_branch_sources,
        selected_branch_count,
        max_branches,
    )
    return {
        "version": SELECTOR_VERSION,
        "strategy": (
            "cpsat_aisle_skeleton_selector"
            if backend == "cpsat"
            else "greedy_bundle_aware_aisle_skeleton_selector"
        ),
        "status": "shadow_only",
        "backend": backend,
        "requested_backend": requested,
        "backend_fallback_reason": fallback_reason,
        "base_roles": sorted(BASE_AISLE_ROLES),
        "variable_roles": sorted(VARIABLE_AISLE_ROLES | {STALL_MODULE_ROLE}),
        "base_selected_ids": base_selected_ids,
        "base_selected_count": len(base_selected_ids),
        "solver_provenance": provenance,
        "eligible_count": len(eligible),
        "eligible_branch_count": len(branch_records),
        "eligible_connector_count": len(connector_records),
        "eligible_bundle_count": len([unit for unit in units if unit["type"] == "loop_bundle"]),
        "selected_count": len(selected_ids),
        "selected_branch_count": selected_branch_count,
        "selected_connector_count": len([item for item in connector_records if str(item["id"]) in selected_set]),
        "selected_module_count": len([item for item in module_records if str(item["id"]) in selected_set]),
        "selected_module_ids": [str(item["id"]) for item in module_records if str(item["id"]) in selected_set],
        "selected_bundle_count": len(selected_bundles),
        "selected_bundles": selected_bundles,
        "max_branches": max_branches,
        "selected_ids": selected_ids,
        "selected_branch_source_ids": sorted(selected_branch_sources),
        "selected_score_total": sum(float(item["score"]) for item in eligible if str(item["id"]) in selected_set),
        "segment_family_mix_weight": mix_weight,
        "rejected": rejected,
        "notes": _selection_notes(backend, mix_weight),
    }


def _greedy_choice(
    units: list[dict[str, object]],
    module_records: list[dict[str, object]],
    max_branches: int,
    mix_weight: float = 0.0,
) -> dict[str, object]:
    selected_ids: list[str] = []
    selected_set: set[str] = set()
    selected_branch_sources: set[str] = set()
    selected_branch_count = 0
    selected_bundles: list[dict[str, object]] = []
    for unit in units:
        decision = _unit_rejection(unit, selected_set, selected_branch_sources, selected_branch_count, max_branches)
        if decision:
            continue
        _select_unit(unit, selected_ids, selected_set, selected_branch_sources)
        selected_branch_count += int(unit["branch_count"])
        if unit["type"] == "loop_bundle":
            selected_bundles.append(_bundle_report(unit))
    taken_slots: set[str] = set()
    for item in module_records:
        decision = _module_rejection(item, selected_set, taken_slots)
        if decision:
            continue
        item_id = str(item["id"])
        selected_ids.append(item_id)
        selected_set.add(item_id)
        slot = _family_slot(item)
        if slot:
            taken_slots.add(slot)
    if mix_weight:
        _prefer_uniform_sides(module_records, selected_ids, selected_set, mix_weight)
    return {
        "selected_ids": selected_ids,
        "selected_branch_sources": selected_branch_sources,
        "selected_branch_count": selected_branch_count,
        "selected_bundles": selected_bundles,
    }


def _bundles_from_selection(
    connector_records: list[dict[str, object]],
    branch_records: list[dict[str, object]],
    selected_set: set[str],
) -> list[dict[str, object]]:
    selected_branches = [item for item in branch_records if str(item["id"]) in selected_set]
    by_source = {_source_id(item): item for item in selected_branches}
    bundles: list[dict[str, object]] = []
    for connector in connector_records:
        if str(connector["id"]) not in selected_set:
            continue
        endpoints = _connects(connector)
        if len(endpoints) != 2 or any(source not in by_source for source in endpoints):
            continue
        unit = _loop_bundle_unit(connector, [by_source[source] for source in endpoints])
        bundles.append(_bundle_report(unit))
    return bundles


def _selection_notes(backend: str, mix_weight: float = 0.0) -> list[str]:
    notes = [
        "Variable selection is report-only and does not replace the generated layout yet.",
        "Base aisles (main/turnaround/jog/exit/passing_bay) are catalogued, not selector variables.",
        "Branch selection respects optimization.max_branches.",
        "Connector candidates require both endpoint branch source ids to be selected.",
        "Selected stalls are intentionally not treated as fixed blockers because a real optimizer would regenerate stalls around the selected road network.",
        "Stall-module shadow scores use per-family stall weights when optimization.weights.stall_family is set.",
        "Accessible/EV modules that cannot reach required routes or chargers are rejected when those quotas are active.",
    ]
    if mix_weight:
        notes.append(
            "A non-zero weights.segment_family_mix compares mixed per-segment families against a uniform family on each aisle side."
        )
    if backend == "cpsat":
        notes.append("CP-SAT is optional (optimizer extra) and does not change the official template layout.")
    else:
        notes.append("Loop bundles compete with single branch candidates before final selected ids are emitted.")
        notes.append("selector_backend=cpsat uses OR-Tools when installed, otherwise fail-closes to greedy.")
    return notes


def _selector_time_limit(site: SiteSpec | None) -> float:
    raw = 2.0 if site is None else site.optimization.get("selector_time_limit_seconds", 2.0)
    try:
        return max(float(raw), 0.01)
    except (TypeError, ValueError):
        return 2.0


def _selector_seed(site: SiteSpec | None) -> int | None:
    if site is None:
        return None
    raw = site.optimization.get("selector_seed")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _selector_num_workers(site: SiteSpec | None) -> int | None:
    if site is None:
        return None
    return parse_selector_num_workers(site.optimization)


def _eligible(candidate: CandidateObject) -> bool:
    if selection_class(candidate) != "variable" or candidate.geometry is None or candidate.status == "invalid":
        return False
    if candidate.kind == STALL_MODULE_KIND:
        return True
    return candidate.kind == "aisle_skeleton" and candidate.role in VARIABLE_AISLE_ROLES


def _is_base_selected(candidate: CandidateObject) -> bool:
    return selection_class(candidate) == "base" and candidate.status == "selected" and candidate.kind == "aisle"


def _selection_record(candidate: CandidateObject, site: SiteSpec | None = None) -> dict[str, object]:
    score, usability_rejection = _shadow_score(candidate, site)
    return {
        "id": candidate.id,
        "kind": candidate.kind,
        "role": candidate.role,
        "status": candidate.status,
        "score": score,
        "conflict_ids": list(candidate.conflict_ids),
        "score_features": candidate.score_features,
        "metadata": {
            "source_id": candidate.metadata.get("source_id"),
            "reason": candidate.metadata.get("reason"),
            "side": candidate.metadata.get("side"),
            "start_u": candidate.metadata.get("start_u"),
            "length": candidate.metadata.get("length"),
            "connects": candidate.metadata.get("connects"),
            "connector_inset_depth": candidate.metadata.get("connector_inset_depth"),
            "parent_aisle_id": candidate.metadata.get("parent_aisle_id"),
            "parent_is_base": candidate.metadata.get("parent_is_base"),
            "parent_candidate_id": candidate.metadata.get("parent_candidate_id"),
            "aisle_side": candidate.metadata.get("aisle_side"),
            "served_by_aisle_id": candidate.metadata.get("served_by_aisle_id"),
            "family_slot": candidate.metadata.get("family_slot"),
            "stall_type_id": candidate.metadata.get("stall_type_id"),
            "stall_family": candidate.metadata.get("stall_family"),
            "shadow_family": candidate.metadata.get("shadow_family"),
            "selection_class": selection_class(candidate),
            "kind": candidate.kind,
            "usability_rejection": usability_rejection,
        },
    }


def _selection_sort_key(item: dict[str, object]) -> tuple[float, str]:
    return (-float(item["score"]), str(item["id"]))


def _selection_units(branch_records: list[dict[str, object]], connector_records: list[dict[str, object]]) -> list[dict[str, object]]:
    units = [_single_branch_unit(item) for item in branch_records]
    best_branch_by_source = _best_branch_by_source(branch_records)
    for connector in connector_records:
        connects = _connects(connector)
        if len(connects) != 2:
            continue
        branches = [best_branch_by_source.get(source_id) for source_id in connects]
        if not all(branches):
            continue
        branch_items = [branch for branch in branches if isinstance(branch, dict)]
        if len({_source_id(branch) for branch in branch_items}) != 2:
            continue
        if _internal_conflicts([*branch_items, connector]):
            continue
        units.append(_loop_bundle_unit(connector, branch_items))
    return sorted(units, key=lambda item: (-float(item["score"]), str(item["id"])))


def _single_branch_unit(branch: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"UNIT-{branch['id']}",
        "type": "single_branch",
        "score": float(branch["score"]),
        "items": [branch],
        "branch_sources": [_source_id(branch)],
        "branch_count": 1,
        "connector_ids": [],
    }


def _loop_bundle_unit(connector: dict[str, object], branches: list[dict[str, object]]) -> dict[str, object]:
    branch_sources = [_source_id(branch) for branch in branches]
    loop_bonus = LOOP_BUNDLE_BONUS
    score = sum(float(branch["score"]) for branch in branches) + float(connector["score"]) + loop_bonus
    return {
        "id": f"UNIT-LOOP-{connector['id']}",
        "type": "loop_bundle",
        "score": score,
        "loop_bonus": loop_bonus,
        "items": [*branches, connector],
        "branch_sources": branch_sources,
        "branch_count": len(branch_sources),
        "connector_ids": [str(connector["id"])],
    }


def _best_branch_by_source(branch_records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    best: dict[str, dict[str, object]] = {}
    for branch in branch_records:
        source_id = _source_id(branch)
        if source_id not in best or float(branch["score"]) > float(best[source_id]["score"]):
            best[source_id] = branch
    return best


def _internal_conflicts(items: list[dict[str, object]]) -> bool:
    item_ids = {str(item["id"]) for item in items}
    for item in items:
        if item_ids.intersection(str(conflict_id) for conflict_id in item["conflict_ids"]):
            return True
    return False


def _unit_rejection(
    unit: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> dict[str, object] | None:
    score = float(unit["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    if selected_branch_count + int(unit["branch_count"]) > max_branches:
        return {"reason": "exceeds_max_branches", "conflicts_with": [], "max_branches": max_branches}
    duplicate_sources = sorted(set(_branch_sources(unit)).intersection(selected_branch_sources))
    if duplicate_sources:
        return {
            "reason": "duplicate_branch_source_selected",
            "conflicts_with": [],
            "branch_source_ids": duplicate_sources,
        }
    conflicts = sorted(set(_conflict_ids(unit)).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return None


def _select_unit(
    unit: dict[str, object],
    selected_ids: list[str],
    selected_set: set[str],
    selected_branch_sources: set[str],
) -> None:
    for item in unit["items"]:
        item_id = str(item["id"])
        if item_id in selected_set:
            continue
        selected_ids.append(item_id)
        selected_set.add(item_id)
        if item["role"] == "branch":
            selected_branch_sources.add(_source_id(item))


def _rejected_records(
    branch_records: list[dict[str, object]],
    connector_records: list[dict[str, object]],
    module_records: list[dict[str, object]],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> list[dict[str, object]]:
    rejected: list[dict[str, object]] = []
    for item in branch_records:
        if str(item["id"]) in selected_set:
            continue
        rejected.append({**item, **_branch_rejection_reason(item, selected_set, selected_branch_sources, selected_branch_count, max_branches)})
    for item in connector_records:
        if str(item["id"]) in selected_set:
            continue
        rejected.append({**item, **_connector_rejection_reason(item, selected_set, selected_branch_sources)})
    taken_slots = {_family_slot(item) for item in module_records if str(item["id"]) in selected_set}
    taken_slots.discard("")
    for item in module_records:
        if str(item["id"]) in selected_set:
            continue
        rejected.append(
            {
                **item,
                **(
                    _module_rejection(item, selected_set, taken_slots)
                    or {"reason": "not_selected_by_bundle_competition", "conflicts_with": []}
                ),
            }
        )
    return rejected


def _module_parent_selected(item: dict[str, object], selected_set: set[str]) -> bool:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return False
    if metadata.get("parent_is_base"):
        return True
    parent = metadata.get("parent_candidate_id")
    return isinstance(parent, str) and parent in selected_set


def _module_rejection(
    item: dict[str, object],
    selected_set: set[str],
    taken_slots: set[str],
) -> dict[str, object] | None:
    metadata = item.get("metadata", {})
    usability_rejection = metadata.get("usability_rejection") if isinstance(metadata, dict) else None
    if usability_rejection:
        return {"reason": str(usability_rejection), "conflicts_with": []}
    score = float(item["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    if not _module_parent_selected(item, selected_set):
        return {"reason": "module_parent_not_selected", "conflicts_with": []}
    slot = _family_slot(item)
    if slot and slot in taken_slots:
        return {"reason": "module_family_slot_taken", "conflicts_with": []}
    conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return None


def _family_slot(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    raw = metadata.get("family_slot")
    return str(raw) if raw else ""


def _module_side_key(item: dict[str, object]) -> str:
    slot = _family_slot(item)
    if "|seg" in slot:
        return slot.rsplit("|seg", 1)[0]
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return slot
    parent = metadata.get("parent_candidate_id") or metadata.get("served_by_aisle_id")
    side = metadata.get("aisle_side")
    if parent and side:
        return f"{parent}|{side}"
    return slot


def _module_family(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("stall_family") or "")


def _prefer_uniform_sides(
    module_records: list[dict[str, object]],
    selected_ids: list[str],
    selected_set: set[str],
    mix_weight: float,
) -> None:
    """If a mixed side scores worse than the best uniform family, keep the uniform set.

    Independent per-segment picks stay when ``mix_weight`` is 0 (caller skips this).
    """
    by_side: dict[str, list[dict[str, object]]] = {}
    for item in module_records:
        key = _module_side_key(item)
        if key:
            by_side.setdefault(key, []).append(item)
    for side_modules in by_side.values():
        selected_on_side = [item for item in side_modules if str(item["id"]) in selected_set]
        families = {_module_family(item) for item in selected_on_side if _module_family(item)}
        if len(families) <= 1:
            continue
        mixed_score = sum(float(item["score"]) for item in selected_on_side) + mix_weight
        best_ids: list[str] | None = None
        best_score = mixed_score
        available_families = {_module_family(item) for item in side_modules if _module_family(item)}
        for family in sorted(available_families):
            uniform = _uniform_side_modules(side_modules, family, selected_set)
            if not uniform:
                continue
            uniform_score = sum(float(item["score"]) for item in uniform)
            if uniform_score > best_score + 1e-9:
                best_score = uniform_score
                best_ids = [str(item["id"]) for item in uniform]
        if best_ids is None:
            continue
        for item in selected_on_side:
            item_id = str(item["id"])
            if item_id in selected_set:
                selected_set.remove(item_id)
            if item_id in selected_ids:
                selected_ids.remove(item_id)
        for item_id in best_ids:
            if item_id not in selected_set:
                selected_ids.append(item_id)
                selected_set.add(item_id)


def _uniform_side_modules(
    side_modules: list[dict[str, object]],
    family: str,
    selected_set: set[str],
) -> list[dict[str, object]]:
    by_slot: dict[str, dict[str, object]] = {}
    for item in side_modules:
        if _module_family(item) != family:
            continue
        if float(item["score"]) <= 0:
            continue
        if not _module_parent_selected(item, selected_set):
            continue
        slot = _family_slot(item) or str(item["id"])
        current = by_slot.get(slot)
        if current is None or float(item["score"]) > float(current["score"]):
            by_slot[slot] = item
    return list(by_slot.values())


def _branch_rejection_reason(
    item: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> dict[str, object]:
    score = float(item["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    source_id = _source_id(item)
    if source_id in selected_branch_sources:
        return {
            "reason": "duplicate_branch_source_selected",
            "conflicts_with": [],
            "branch_source_id": source_id,
        }
    if selected_branch_count >= max_branches:
        return {"reason": "exceeds_max_branches", "conflicts_with": [], "max_branches": max_branches}
    conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return {"reason": "not_selected_by_bundle_competition", "conflicts_with": []}


def _connector_rejection_reason(
    item: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
) -> dict[str, object]:
    score = float(item["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    missing = sorted(set(_connects(item)) - selected_branch_sources)
    if missing:
        return {
            "reason": "connector_dependency_not_selected",
            "conflicts_with": [],
            "missing_branch_source_ids": missing,
        }
    conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return {"reason": "not_selected_by_bundle_competition", "conflicts_with": []}


def _bundle_report(unit: dict[str, object]) -> dict[str, object]:
    return {
        "id": unit["id"],
        "type": unit["type"],
        "score": unit["score"],
        "loop_bonus": unit.get("loop_bonus", 0.0),
        "selected_ids": [str(item["id"]) for item in unit["items"]],
        "branch_source_ids": _branch_sources(unit),
        "connector_ids": list(unit["connector_ids"]),
    }


def _branch_sources(unit: dict[str, object]) -> list[str]:
    return [str(item) for item in unit["branch_sources"]]


def _conflict_ids(unit: dict[str, object]) -> list[str]:
    conflicts: list[str] = []
    for item in unit["items"]:
        conflicts.extend(str(conflict_id) for conflict_id in item["conflict_ids"])
    return conflicts


def _source_id(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return str(item["id"])
    return str(metadata.get("source_id") or item["id"])


def _connects(item: dict[str, object]) -> list[str]:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("connects", [])
    if not isinstance(raw, list | tuple):
        return []
    return [str(value) for value in raw]


def _max_branches(site: SiteSpec | None) -> int:
    if site is None:
        return 2
    raw = site.optimization.get("max_branches", 2)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 2


def _shadow_score(candidate: CandidateObject, site: SiteSpec | None = None) -> tuple[float, str | None]:
    if candidate.kind == STALL_MODULE_KIND:
        stall_count = float(candidate.score_features.get("stall_count", 0.0))
        family = str(candidate.metadata.get("stall_family") or "")
        weight = stall_family_weight(site, family, default=SHADOW_STALL_WEIGHT)
        score = stall_count * weight - _geometry_area(candidate) * 0.2
        if site is None:
            return score, None
        extra, rejection = classified_contact_adjustment(
            site,
            str(candidate.metadata.get("stall_type_id") or ""),
            _module_stall_polygons(candidate),
        )
        if rejection:
            return 0.0, rejection
        return score + stall_count * extra, None
    stall_delta = _stall_delta(candidate)
    area_penalty = _geometry_area(candidate) * 0.2
    connector_bonus = 25.0 if candidate.role == "connector" and candidate.metadata.get("removed_turnarounds") else 0.0
    return stall_delta * SHADOW_STALL_WEIGHT + connector_bonus - area_penalty, None


def _module_stall_polygons(candidate: CandidateObject) -> list:
    generated = candidate.metadata.get("generated_stalls", [])
    polygons: list = []
    if isinstance(generated, list):
        for item in generated:
            if isinstance(item, dict) and item.get("geometry"):
                polygons.append(item["geometry"])
    if polygons:
        return polygons
    if candidate.geometry is not None:
        return [candidate.geometry]
    return []


def _stall_delta(candidate: CandidateObject) -> float:
    if candidate.role == "connector":
        added = float(candidate.score_features.get("added_stalls", 0.0))
        removed = float(candidate.score_features.get("removed_stalls", 0.0))
        return added - removed
    stall_count = candidate.score_features.get("stall_count")
    base_stall_count = candidate.score_features.get("base_stall_count")
    if stall_count is not None and base_stall_count is not None:
        return float(stall_count) - float(base_stall_count)
    added = float(candidate.score_features.get("added_stalls", 0.0))
    removed = float(candidate.score_features.get("removed_stalls", 0.0))
    return added - removed


def _geometry_area(candidate: CandidateObject) -> float:
    if candidate.geometry is None:
        return 0.0
    return float(ShapelyPolygon(candidate.geometry).area)
