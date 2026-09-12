"""Strict optimization.road_network parsing. Booleans are not integers; NaN is not a budget."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

ALLOWED_FAMILIES = frozenset({"legacy", "parallel_ladder"})
ALLOWED_POLICIES = frozenset({"entry_end", "both_ends"})
KNOWN_KEYS = frozenset(
    {
        "enabled",
        "families",
        "max_skeletons",
        "dominant_axis_count",
        "max_parallel_aisles",
        "max_full_evaluations",
        "refinement_budget_seconds",
        "cross_aisle_policy",
        "allow_one_way_loop",
    }
)
DEFAULT_MAX_SKELETONS = 16
DEFAULT_DOMINANT_AXIS_COUNT = 2
DEFAULT_MAX_PARALLEL_AISLES = 6
DEFAULT_MAX_FULL_EVALUATIONS = 8
DEFAULT_REFINEMENT_BUDGET_SECONDS = 20.0
DEFAULT_FAMILIES = ("legacy",)
DEFAULT_POLICY = "entry_end"


@dataclass(frozen=True)
class RoadNetworkConfig:
    enabled: bool = False
    families: tuple[str, ...] = DEFAULT_FAMILIES
    max_skeletons: int = DEFAULT_MAX_SKELETONS
    dominant_axis_count: int = DEFAULT_DOMINANT_AXIS_COUNT
    max_parallel_aisles: int = DEFAULT_MAX_PARALLEL_AISLES
    max_full_evaluations: int = DEFAULT_MAX_FULL_EVALUATIONS
    refinement_budget_seconds: float | None = None
    cross_aisle_policy: str = DEFAULT_POLICY
    allow_one_way_loop: bool = False


def parse_road_network_mapping(raw: Any) -> RoadNetworkConfig:
    if raw is None:
        return RoadNetworkConfig()
    if not isinstance(raw, dict):
        raise ValueError("optimization.road_network must be an object")
    unknown = sorted(set(raw) - KNOWN_KEYS)
    if unknown:
        raise ValueError(f"optimization.road_network has unknown keys: {', '.join(unknown)}")

    enabled = _optional_bool(raw, "enabled", False)
    families = _optional_families(raw)
    policy = _optional_policy(raw)
    allow_loop = _optional_bool(raw, "allow_one_way_loop", False)
    if allow_loop or policy == "one_way_loop":
        raise ValueError("allow_one_way_loop is unsupported")
    return RoadNetworkConfig(
        enabled=enabled,
        families=families,
        max_skeletons=_optional_positive_int(raw, "max_skeletons", DEFAULT_MAX_SKELETONS),
        dominant_axis_count=_optional_positive_int(raw, "dominant_axis_count", DEFAULT_DOMINANT_AXIS_COUNT),
        max_parallel_aisles=_optional_positive_int(raw, "max_parallel_aisles", DEFAULT_MAX_PARALLEL_AISLES),
        max_full_evaluations=_optional_positive_int(raw, "max_full_evaluations", DEFAULT_MAX_FULL_EVALUATIONS),
        refinement_budget_seconds=_optional_positive_finite(raw, "refinement_budget_seconds"),
        cross_aisle_policy=policy,
        allow_one_way_loop=False,
    )


def _optional_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise ValueError(f"optimization.road_network.{key} must be a boolean")
    return value


def _optional_positive_int(raw: dict[str, Any], key: str, default: int) -> int:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"optimization.road_network.{key} must be a positive integer")
    if value < 1:
        raise ValueError(f"optimization.road_network.{key} must be a positive integer")
    return value


def positive_finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a positive finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{field} must be a positive finite number")
    return parsed


def _optional_positive_finite(raw: dict[str, Any], key: str) -> float | None:
    if key not in raw:
        return None
    return positive_finite_number(raw[key], field=f"optimization.road_network.{key}")


def _optional_families(raw: dict[str, Any]) -> tuple[str, ...]:
    if "families" not in raw:
        return DEFAULT_FAMILIES
    families = raw["families"]
    if not isinstance(families, list) or not families or any(not isinstance(item, str) for item in families):
        raise ValueError("optimization.road_network.families must be an array of strings")
    illegal = [item for item in families if item not in ALLOWED_FAMILIES]
    if illegal:
        raise ValueError(f"unknown road_network family: {illegal[0]}")
    return tuple(families)


def _optional_policy(raw: dict[str, Any]) -> str:
    if "cross_aisle_policy" not in raw:
        return DEFAULT_POLICY
    policy = raw["cross_aisle_policy"]
    if policy == "one_way_loop":
        raise ValueError("allow_one_way_loop is unsupported")
    if not isinstance(policy, str) or policy not in ALLOWED_POLICIES:
        raise ValueError("cross_aisle_policy must be entry_end or both_ends")
    return policy
