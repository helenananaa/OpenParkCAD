from __future__ import annotations

from dataclasses import dataclass
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.operational_quality import operational_risk_score


@dataclass(frozen=True)
class ScoreWeights:
    stall_count: float
    aisle_area: float
    heading_delta: float
    entrance_offset: float
    branch_count: float
    dead_end_length: float
    operational_risk: float
    connector_loop: float = 0.0
    obstacle_clearance: float = 0.0
    segment_family_mix: float = 0.0


DEFAULT_WEIGHTS = {
    "max_stalls": ScoreWeights(
        stall_count=120.0,
        aisle_area=-0.4,
        heading_delta=-1.0,
        entrance_offset=-1.0,
        branch_count=-5.0,
        dead_end_length=-0.2,
        operational_risk=-0.1,
        connector_loop=0.0,
        obstacle_clearance=0.0,
    ),
    "balanced": ScoreWeights(
        stall_count=100.0,
        aisle_area=-1.0,
        heading_delta=-2.0,
        entrance_offset=-3.0,
        branch_count=-15.0,
        dead_end_length=-0.5,
        operational_risk=-0.25,
        connector_loop=0.0,
        obstacle_clearance=0.0,
    ),
    "conservative": ScoreWeights(
        stall_count=90.0,
        aisle_area=-1.5,
        heading_delta=-4.0,
        entrance_offset=-6.0,
        branch_count=-30.0,
        dead_end_length=-1.0,
        operational_risk=-1.0,
        connector_loop=0.0,
        obstacle_clearance=0.0,
    ),
}


def score_layout(layout: LayoutResult) -> dict[str, float]:
    metrics = _metrics(layout)
    return score_metrics(layout.site, metrics)


SHADOW_STALL_WEIGHT = 100.0
DEFAULT_UNIFORM_SEGMENT_MIX_WEIGHT = -50.0
_FAMILY_COUNT_PREFIX = "stall_count_family_"


def score_metrics(site: SiteSpec, metrics: dict[str, float]) -> dict[str, float]:
    weights = _weights_for_site(site)
    family_counts = {
        key[len(_FAMILY_COUNT_PREFIX) :]: value
        for key, value in metrics.items()
        if key.startswith(_FAMILY_COUNT_PREFIX)
    }
    if family_counts:
        stall_value = sum(
            count * stall_family_weight(site, family, default=weights.stall_count)
            for family, count in family_counts.items()
        )
    else:
        stall_value = metrics["stall_count"] * weights.stall_count
    aisle_area_penalty = metrics["aisle_area"] * weights.aisle_area
    heading_penalty = metrics["heading_delta"] * weights.heading_delta
    entrance_offset_penalty = metrics["entrance_offset"] * weights.entrance_offset
    branch_penalty = metrics["branch_count"] * weights.branch_count
    dead_end_penalty = metrics["dead_end_length"] * weights.dead_end_length
    operational_risk_penalty = metrics.get("operational_risk", 0.0) * weights.operational_risk
    connector_loop_value = metrics.get("connector_loop_count", 0.0) * weights.connector_loop
    obstacle_clearance_value = metrics.get("obstacle_clearance", 0.0) * weights.obstacle_clearance
    mixed_segment_side_count = float(metrics.get("mixed_segment_side_count", 0.0))
    segment_family_mix_penalty = mixed_segment_side_count * weights.segment_family_mix
    total = (
        stall_value
        + aisle_area_penalty
        + heading_penalty
        + entrance_offset_penalty
        + branch_penalty
        + dead_end_penalty
        + operational_risk_penalty
        + connector_loop_value
        + obstacle_clearance_value
        + segment_family_mix_penalty
    )

    breakdown = {
        "total": total,
        "stall_value": stall_value,
        "aisle_area_penalty": aisle_area_penalty,
        "heading_penalty": heading_penalty,
        "entrance_offset_penalty": entrance_offset_penalty,
        "branch_penalty": branch_penalty,
        "dead_end_penalty": dead_end_penalty,
        "operational_risk_penalty": operational_risk_penalty,
        "connector_loop_value": connector_loop_value,
        "obstacle_clearance_value": obstacle_clearance_value,
        "segment_family_mix_penalty": segment_family_mix_penalty,
        "stall_count": metrics["stall_count"],
        "aisle_area": metrics["aisle_area"],
        "heading_delta": metrics["heading_delta"],
        "entrance_offset": metrics["entrance_offset"],
        "branch_count": metrics["branch_count"],
        "dead_end_length": metrics["dead_end_length"],
        "operational_risk": metrics.get("operational_risk", 0.0),
        "connector_loop_count": metrics.get("connector_loop_count", 0.0),
        "obstacle_clearance": metrics.get("obstacle_clearance", 0.0),
        "mixed_segment_side_count": mixed_segment_side_count,
    }
    breakdown.update({f"{_FAMILY_COUNT_PREFIX}{family}": count for family, count in family_counts.items()})
    return breakdown


def score_total(layout: LayoutResult) -> float:
    return layout.score.get("total", score_layout(layout)["total"])


def _weights_for_site(site: SiteSpec) -> ScoreWeights:
    objective = str(site.optimization.get("objective", "balanced"))
    base = DEFAULT_WEIGHTS.get(objective, DEFAULT_WEIGHTS["balanced"])
    overrides = site.optimization.get("weights", {})
    if not isinstance(overrides, dict):
        overrides = {}
    # Loop / clearance bonuses are opt-in so default promotion baselines stay stable.
    default_loop = base.connector_loop
    if site.optimization.get("prefer_loops"):
        default_loop = max(default_loop, 150.0)
    default_clearance = base.obstacle_clearance
    if site.optimization.get("prefer_obstacle_clearance"):
        # Optional intensity knob; weights.obstacle_clearance still wins when set.
        try:
            intensity = float(site.optimization.get("obstacle_clearance_weight", 10.0))
        except (TypeError, ValueError):
            intensity = 10.0
        default_clearance = max(default_clearance, intensity)
    return ScoreWeights(
        stall_count=float(overrides.get("stall_count", base.stall_count)),
        aisle_area=float(overrides.get("aisle_area", base.aisle_area)),
        heading_delta=float(overrides.get("heading_delta", base.heading_delta)),
        entrance_offset=float(overrides.get("entrance_offset", base.entrance_offset)),
        branch_count=float(overrides.get("branch_count", base.branch_count)),
        dead_end_length=float(overrides.get("dead_end_length", base.dead_end_length)),
        operational_risk=float(overrides.get("operational_risk", base.operational_risk)),
        connector_loop=float(overrides.get("connector_loop", default_loop)),
        obstacle_clearance=float(overrides.get("obstacle_clearance", default_clearance)),
        segment_family_mix=segment_family_mix_weight(site),
    )


def stall_family_weight(site: SiteSpec | None, family: str | None, *, default: float) -> float:
    """Per-family stall value; missing map entries keep ``default``."""
    if site is None:
        return default
    overrides = site.optimization.get("weights", {})
    if not isinstance(overrides, dict):
        return default
    raw_map = overrides.get("stall_family")
    token = str(family or "").strip().lower()
    if isinstance(raw_map, dict) and token and token in raw_map:
        try:
            return float(raw_map[token])
        except (TypeError, ValueError):
            return default
    keyed = f"stall_count_{token}" if token else ""
    if keyed and keyed in overrides:
        try:
            return float(overrides[keyed])
        except (TypeError, ValueError):
            return default
    return default


def segment_family_mix_weight(site: SiteSpec | None) -> float:
    """Penalty (usually negative) per aisle-side that mixes stall families."""
    if site is None:
        return 0.0
    overrides = site.optimization.get("weights", {})
    if isinstance(overrides, dict) and "segment_family_mix" in overrides:
        try:
            return float(overrides["segment_family_mix"])
        except (TypeError, ValueError):
            return 0.0
    if site.optimization.get("prefer_uniform_segments"):
        return DEFAULT_UNIFORM_SEGMENT_MIX_WEIGHT
    return 0.0


def stall_family_name(site: SiteSpec, stall: object) -> str:
    type_id = getattr(stall, "stall_type_id", None)
    if type_id is None and isinstance(stall, dict):
        type_id = stall.get("stall_type_id")
    specs = _active_stall_specs(site)
    if type_id and str(type_id) in specs:
        return specs[str(type_id)].family
    return site.stall.family


def stall_side_key(stall: object) -> str:
    if isinstance(stall, dict):
        served = stall.get("served_by_aisle_id")
        side = stall.get("aisle_side")
    else:
        served = getattr(stall, "served_by_aisle_id", None)
        side = getattr(stall, "aisle_side", None)
    if not served or not side:
        return ""
    return f"{served}|{side}"


def stall_family_score_metrics(site: SiteSpec, stalls: list) -> dict[str, float]:
    counts: dict[str, float] = {}
    sides: dict[str, set[str]] = {}
    for stall in stalls:
        family = stall_family_name(site, stall)
        counts[family] = counts.get(family, 0.0) + 1.0
        key = stall_side_key(stall)
        if key:
            sides.setdefault(key, set()).add(family)
    metrics = {
        "mixed_segment_side_count": float(sum(1 for families in sides.values() if len(families) > 1)),
    }
    for family, count in counts.items():
        metrics[f"{_FAMILY_COUNT_PREFIX}{family}"] = count
    return metrics


def _active_stall_specs(site: SiteSpec) -> dict[str, object]:
    specs: dict[str, object] = {}
    for spec in (site.main_stall, site.branch_stall, site.stall, *site.stall_candidates):
        if spec is not None and spec.id not in specs:
            specs[spec.id] = spec
    return specs


def _metrics(layout: LayoutResult) -> dict[str, float]:
    aisle_area = sum(ShapelyPolygon(aisle.polygon).area for aisle in layout.aisles)
    branch_count = float(len([aisle for aisle in layout.aisles if aisle.role == "branch"]))
    dead_end_length = sum(_dead_end_lengths(layout))
    connector_loop_count = float(
        sum(1 for connector in layout.selected_connectors if len(connector.get("connects", [])) >= 2)
    )
    metrics = {
        "stall_count": float(layout.stall_count),
        "aisle_area": aisle_area,
        "heading_delta": abs(layout.selected_heading_delta_degrees),
        "entrance_offset": abs(layout.selected_entrance_offset),
        "branch_count": branch_count,
        "dead_end_length": dead_end_length,
        "operational_risk": operational_risk_score(layout),
        "connector_loop_count": connector_loop_count,
        "obstacle_clearance": _obstacle_clearance(layout),
    }
    metrics.update(stall_family_score_metrics(layout.site, layout.stalls))
    return metrics


def _obstacle_clearance_cap(site: SiteSpec) -> float:
    raw = site.optimization.get("obstacle_clearance_cap", 12.0)
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 12.0


def _obstacle_clearance(layout: LayoutResult) -> float:
    """Mean clearance from aisle/stall geometry to hard obstacles (metres, capped)."""
    obstacles = [ShapelyPolygon(item) for item in layout.site.obstacles if len(item) >= 3]
    if not obstacles:
        return 0.0
    obstacle_union = unary_union(obstacles)
    if obstacle_union.is_empty:
        return 0.0
    parts = [ShapelyPolygon(aisle.polygon) for aisle in layout.aisles]
    parts.extend(ShapelyPolygon(stall.polygon) for stall in layout.stalls)
    if not parts:
        return 0.0
    layout_union = unary_union(parts)
    if layout_union.is_empty:
        return 0.0
    # Cap so a distant obstacle does not dominate the objective.
    cap = _obstacle_clearance_cap(layout.site)
    return float(min(layout_union.distance(obstacle_union), cap))


def _dead_end_lengths(layout: LayoutResult) -> list[float]:
    lengths = []
    connected_branch_ids = {
        branch_id
        for connector in layout.selected_connectors
        for branch_id in connector.get("connects", [])
        if isinstance(branch_id, str)
    }
    if layout.aisles:
        main = next((aisle for aisle in layout.aisles if aisle.id == "A-MAIN"), None)
        if main:
            lengths.append(_long_side_length(main.polygon))
    if layout.selected_branch_length and not layout.selected_branches:
        lengths.append(layout.selected_branch_length)
    for branch in layout.selected_branches:
        if branch.get("id") in connected_branch_ids:
            continue
        length = branch.get("length")
        if isinstance(length, int | float):
            lengths.append(float(length))
    return lengths


def _long_side_length(poly) -> float:
    points = list(poly)
    if len(points) < 2:
        return 0.0
    lengths = []
    for start, end in zip(points, points[1:] + points[:1]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        lengths.append((dx * dx + dy * dy) ** 0.5)
    return max(lengths or [0.0])
