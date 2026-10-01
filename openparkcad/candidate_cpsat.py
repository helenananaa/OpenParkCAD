from __future__ import annotations

from openparkcad.candidate_catalog import LOOP_BUNDLE_BONUS

SCORE_SCALE = 1000


def import_cp_model():
    """Import OR-Tools CP-SAT. Isolated so tests can simulate a missing extra."""
    from ortools.sat.python import cp_model

    return cp_model


def try_solve_cpsat(
    *,
    branch_records: list[dict[str, object]],
    connector_records: list[dict[str, object]],
    module_records: list[dict[str, object]] | None = None,
    max_branches: int,
    time_limit_seconds: float,
    seed: int | None,
    source_id,
    connects,
    mix_weight: float = 0.0,
    num_workers: int | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """Solve the shadow aisle-skeleton catalog with CP-SAT.

    Returns ``(choice, None)`` on success or ``(None, fallback_reason)``.
    Does not import OR-Tools until called.
    """
    try:
        cp_model = import_cp_model()
    except ImportError:
        return None, "cpsat_backend_unavailable"
    except Exception:
        return None, "cpsat_backend_failed"

    module_records = module_records or []
    variables = {str(item["id"]): item for item in [*branch_records, *connector_records, *module_records]}
    try:
        model = cp_model.CpModel()
        x = {item_id: model.NewBoolVar(item_id) for item_id in variables}

        branches_by_source: dict[str, list[str]] = {}
        for branch in branch_records:
            branches_by_source.setdefault(str(source_id(branch)), []).append(str(branch["id"]))
        for ids in branches_by_source.values():
            model.Add(sum(x[item_id] for item_id in ids) <= 1)

        if branch_records:
            model.Add(sum(x[str(item["id"])] for item in branch_records) <= max_branches)

        for connector in connector_records:
            connector_id = str(connector["id"])
            endpoints = connects(connector)
            if len(endpoints) != 2:
                model.Add(x[connector_id] == 0)
                continue
            for endpoint in endpoints:
                endpoint_ids = branches_by_source.get(endpoint, [])
                if not endpoint_ids:
                    model.Add(x[connector_id] == 0)
                    break
                model.Add(x[connector_id] <= sum(x[item_id] for item_id in endpoint_ids))

        for module in module_records:
            module_id = str(module["id"])
            metadata = module.get("metadata", {})
            if not isinstance(metadata, dict) or metadata.get("parent_is_base"):
                continue
            parent = metadata.get("parent_candidate_id")
            if isinstance(parent, str) and parent in x:
                model.Add(x[module_id] <= x[parent])
            else:
                model.Add(x[module_id] == 0)

        slots: dict[str, list[str]] = {}
        for module in module_records:
            metadata = module.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            slot = metadata.get("family_slot")
            if not slot:
                continue
            slots.setdefault(str(slot), []).append(str(module["id"]))
        for ids in slots.values():
            if len(ids) > 1:
                model.Add(sum(x[item_id] for item_id in ids) <= 1)

        seen_pairs: set[tuple[str, str]] = set()
        for item in variables.values():
            left_id = str(item["id"])
            for raw_conflict in item.get("conflict_ids", []):
                right_id = str(raw_conflict)
                if right_id not in x or right_id == left_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                model.Add(x[pair[0]] + x[pair[1]] <= 1)

        objective_terms = []
        for item in variables.values():
            scaled = int(round(float(item["score"]) * SCORE_SCALE))
            objective_terms.append(scaled * x[str(item["id"])])
        loop_bonus = int(round(LOOP_BUNDLE_BONUS * SCORE_SCALE))
        for connector in connector_records:
            objective_terms.append(loop_bonus * x[str(connector["id"])])
        if mix_weight:
            objective_terms.extend(_mixed_side_objective_terms(model, x, module_records, mix_weight))
        if objective_terms:
            model.Maximize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit_seconds)
        if seed is not None:
            solver.parameters.random_seed = int(seed)
        if num_workers is not None:
            solver.parameters.num_search_workers = int(num_workers)
        status = solver.Solve(model)
    except Exception:
        return None, "cpsat_backend_failed"

    if status == cp_model.INFEASIBLE:
        return None, "cpsat_backend_infeasible"
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return None, "cpsat_backend_no_solution"

    selected_ids = [
        item_id
        for item_id, var in sorted(x.items())
        if solver.Value(var) == 1
    ]
    selected_set = set(selected_ids)
    selected_branch_items = [item for item in branch_records if str(item["id"]) in selected_set]
    selected_branch_sources = sorted({str(source_id(item)) for item in selected_branch_items})
    selected_ids = _ordered_selected_ids(variables, selected_ids)

    objective = float(solver.ObjectiveValue()) / SCORE_SCALE
    bound = float(solver.BestObjectiveBound()) / SCORE_SCALE
    gap = abs(bound - objective) / max(abs(objective), 1.0)
    status_name = "optimal" if status == cp_model.OPTIMAL else "feasible"
    choice = {
        "selected_ids": selected_ids,
        "selected_set": selected_set,
        "selected_branch_sources": selected_branch_sources,
        "selected_branch_count": len(selected_branch_sources),
        "solver_provenance": {
            "backend": "cpsat",
            "seed": seed,
            "num_workers": int(num_workers) if num_workers is not None else None,
            "time_limit_seconds": float(time_limit_seconds),
            "objective_bound": bound,
            "gap": gap,
            "status": status_name,
            "objective": objective,
            "wall_time_seconds": float(solver.WallTime()),
        },
    }
    return choice, None


def _module_side_key(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    slot = str(metadata.get("family_slot") or "")
    if "|seg" in slot:
        return slot.rsplit("|seg", 1)[0]
    parent = metadata.get("parent_candidate_id") or metadata.get("served_by_aisle_id")
    side = metadata.get("aisle_side")
    if parent and side:
        return f"{parent}|{side}"
    return slot


def _mixed_side_objective_terms(model, x: dict, module_records: list[dict[str, object]], mix_weight: float):
    """Add a mix-side binary: 1 when two or more stall families are selected on a side."""
    scaled = int(round(float(mix_weight) * SCORE_SCALE))
    if scaled == 0:
        return []
    by_side: dict[str, dict[str, list[str]]] = {}
    for item in module_records:
        item_id = str(item["id"])
        if item_id not in x:
            continue
        metadata = item.get("metadata", {})
        family = ""
        if isinstance(metadata, dict):
            family = str(metadata.get("stall_family") or "")
        key = _module_side_key(item)
        if not key or not family:
            continue
        by_side.setdefault(key, {}).setdefault(family, []).append(item_id)
    terms = []
    for side, families in by_side.items():
        if len(families) < 2:
            continue
        family_flags = []
        for family, ids in families.items():
            flag = model.NewBoolVar(f"mix-family-{side.replace('|', '_')}-{family}")
            model.AddMaxEquality(flag, [x[item_id] for item_id in ids])
            family_flags.append(flag)
        mixed = model.NewBoolVar(f"mix-side-{side.replace('|', '_')}")
        family_sum = sum(family_flags)
        model.Add(family_sum >= 2).OnlyEnforceIf(mixed)
        model.Add(family_sum <= 1).OnlyEnforceIf(mixed.Not())
        terms.append(scaled * mixed)
    return terms


def _ordered_selected_ids(variables: dict[str, dict[str, object]], selected_ids: list[str]) -> list[str]:
    return sorted(
        selected_ids,
        key=lambda item_id: (-float(variables[item_id]["score"]), item_id),
    )
