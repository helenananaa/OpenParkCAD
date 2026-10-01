from __future__ import annotations

from openparkcad.models import CandidateObject

BASE_AISLE_ROLES = frozenset({"main", "turnaround", "jog", "exit", "passing_bay"})
VARIABLE_AISLE_ROLES = frozenset({"branch", "connector"})
STALL_MODULE_KIND = "stall_module"
STALL_MODULE_ROLE = "stall_module"

SELECTOR_VERSION = "phase4d-12"
SNAPSHOT_VERSION = "phase4d-10"
NETWORK_PREVIEW_VERSION = "phase4d-3"
PROMOTION_VERSION = "phase4d-3"
LOOP_BUNDLE_BONUS = 150.0
DEFAULT_STALL_MODULE_SEGMENT_STALLS = 4
SPINE_AISLE_ROLES = frozenset({"main", "jog", "exit", "passing_bay"})


def catalog_class(*, kind: str, role: str) -> str:
    """Classify a candidate for the discrete catalog (not a solve decision)."""
    if kind == "stall" or role == "stall":
        return "derived"
    if kind == STALL_MODULE_KIND or role == STALL_MODULE_ROLE:
        return "variable"
    if kind == "aisle_skeleton" and role in VARIABLE_AISLE_ROLES:
        return "variable"
    if kind == "aisle_skeleton" and role == "main":
        return "spine_attempt"
    if kind == "aisle":
        return "base"
    if role in BASE_AISLE_ROLES:
        return "base"
    return "other"


def selection_class(candidate: CandidateObject) -> str:
    tagged = candidate.metadata.get("selection_class")
    if isinstance(tagged, str) and tagged:
        return tagged
    return catalog_class(kind=candidate.kind, role=candidate.role)


def stall_module_segment_stalls(optimization: dict | None) -> int:
    """Max stalls per module. Missing key uses 4; explicit 0 keeps the whole strip."""
    if not isinstance(optimization, dict) or "stall_module_segment_stalls" not in optimization:
        return DEFAULT_STALL_MODULE_SEGMENT_STALLS
    raw = optimization["stall_module_segment_stalls"]
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def parse_selector_num_workers(optimization: dict | None) -> int | None:
    """Return an explicit CP-SAT worker count, or None to keep OR-Tools defaults.

    The key is optional. ``None`` / omitted leaves solver.parameters.num_search_workers
    untouched. Booleans and non-positive values are illegal.
    """
    if not isinstance(optimization, dict) or "selector_num_workers" not in optimization:
        return None
    raw = optimization["selector_num_workers"]
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError("optimization.selector_num_workers must be a positive integer")
    return raw


def requested_selector_backend(raw: object) -> tuple[str, str | None]:
    """Return (requested_backend, parse_fallback_reason).

    ``cpsat`` is a real request (second value None). The selector then runs
    CP-SAT or fail-closes to greedy with a runtime reason. Unknown strings
    stay on greedy without attempting CP-SAT.
    """
    if raw is None:
        return "greedy", None
    token = str(raw).strip().lower().replace("-", "_")
    if token in {"", "greedy", "heuristic", "shadow"}:
        return "greedy", None
    if token in {"cpsat", "cp_sat", "ortools", "or_tools"}:
        return "cpsat", None
    return "greedy", "unknown_selector_backend"
