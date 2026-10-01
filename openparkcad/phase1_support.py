from __future__ import annotations

from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec


def boolean_opt(raw: object) -> bool:
    """Parse an optimization/constraint flag; string falsey values are False."""
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no", "off"}
    return bool(raw)


def entry_capable_entrances(site: SiteSpec) -> list[EntranceSpec]:
    return [
        entrance
        for entrance in site.entrances
        if entrance.mode in {"shared", "entry_only"} or "enter" in entrance.allowed_movements
    ]


def exit_capable_entrances(site: SiteSpec) -> list[EntranceSpec]:
    return [
        entrance
        for entrance in site.entrances
        if entrance.mode in {"shared", "exit_only"} or "exit" in entrance.allowed_movements
    ]


def supports_phase1_aisle(site: SiteSpec) -> bool:
    if site.aisle_selection_mode != "fixed":
        return False
    fixed_class = fixed_aisle_class(site)
    if fixed_class is None:
        return False
    return is_phase1_aisle_class(fixed_class, site=site)


def supports_phase1_stall(site: SiteSpec) -> bool:
    stall = site.stall
    if stall.drive_over:
        return False
    if stall.family == "perpendicular":
        return (
            "front" in stall.access_sides
            and "front" not in stall.blocked_sides
            and module_angle_allowed(90.0, stall.allowed_angles)
        )
    if stall.family == "angled":
        return (
            "front" in stall.access_sides
            and "front" not in stall.blocked_sides
            and angled_module_angle(stall.allowed_angles) is not None
        )
    if stall.family == "parallel":
        return parallel_access_supported(stall.access_sides, stall.blocked_sides)
    if stall.family == "t_end":
        return (
            "front" in stall.access_sides
            and "front" not in stall.blocked_sides
            and module_angle_allowed(90.0, stall.allowed_angles)
        )
    return False


def phase1_unsupported_inputs(site: SiteSpec) -> list[dict[str, str]]:
    """Return Phase 1 support notes for accepted-but-not-generated inputs."""
    issues: list[dict[str, str]] = []
    fixed_class = fixed_aisle_class(site)
    if site.aisle_selection_mode != "fixed":
        issues.append(
            {
                "field": "aisles.selection_mode",
                "value": site.aisle_selection_mode,
                "reason": "Phase 1 only generates a fixed aisle class (two-way or one-way).",
            }
        )
    if fixed_class is None:
        issues.append(
            {
                "field": "aisles.fixed_class",
                "value": str(site.fixed_aisle_class),
                "reason": "Phase 1 needs a resolvable fixed aisle class.",
            }
        )
    elif not is_phase1_aisle_class(fixed_class, site=site):
        issues.append(
            {
                "field": f"aisles.classes.{fixed_class.id}",
                "value": f"capacity={fixed_class.capacity}, directionality={fixed_class.directionality}, enabled={fixed_class.enabled}",
                "reason": (
                    "Phase 1 supports enabled two_vehicle/two_way aisles, enabled one_way aisles "
                    "with single_vehicle/one_vehicle/two_vehicle capacity, or narrow two-way "
                    "(single_vehicle + two_way) when optimization.enable_passing_bay_synthesis is true."
                ),
            }
        )

    stall = site.stall
    if stall.family not in {"perpendicular", "angled", "parallel", "t_end"}:
        issues.append(
            {
                "field": "parking.active_stall.family",
                "value": stall.family,
                "reason": "Current generation supports perpendicular, angled, parallel, and t_end stalls only.",
            }
        )
    if stall.family in {"perpendicular", "t_end"} and not module_angle_allowed(90.0, stall.allowed_angles):
        issues.append(
            {
                "field": "parking.active_stall.allowed_angles",
                "value": ",".join(str(angle) for angle in stall.allowed_angles),
                "reason": "Phase 1 only places 90-degree stalls for perpendicular and t_end families.",
            }
        )
    if stall.family == "angled" and angled_module_angle(stall.allowed_angles) is None:
        issues.append(
            {
                "field": "parking.active_stall.allowed_angles",
                "value": ",".join(str(angle) for angle in stall.allowed_angles),
                "reason": "Angled stall generation needs an angle between 0 and 90 degrees.",
            }
        )
    if stall.drive_over:
        issues.append(
            {
                "field": "parking.active_stall.drive_over",
                "value": "true",
                "reason": "Drive-over painted stalls require later maneuver/traffic logic.",
            }
        )
    if stall.family == "parallel":
        if not parallel_access_supported(stall.access_sides, stall.blocked_sides):
            issues.append(
                {
                    "field": "parking.active_stall.access_sides",
                    "value": ",".join(stall.access_sides),
                    "reason": "Parallel stalls need at least one unblocked traffic-side access (left, right, or front).",
                }
            )
    else:
        if "front" not in stall.access_sides:
            issues.append(
                {
                    "field": "parking.active_stall.access_sides",
                    "value": ",".join(stall.access_sides),
                    "reason": "Phase 1 only models aisle-facing front access for perpendicular, angled, and t_end stalls.",
                }
            )
        if "front" in stall.blocked_sides:
            issues.append(
                {
                    "field": "parking.active_stall.blocked_sides",
                    "value": ",".join(stall.blocked_sides),
                    "reason": "The aisle-facing side cannot be blocked in the Phase 1 maneuver approximation.",
                }
            )
    return issues


def is_phase1_aisle_class(aisle_class: AisleClassSpec, site: SiteSpec | None = None) -> bool:
    """Whether the fixed aisle class may drive the current template generator."""
    if not aisle_class.enabled:
        return False
    if aisle_class.directionality == "two_way":
        if aisle_class.capacity == "two_vehicle":
            return True
        # Narrow two-way is opt-in and requires synthesized (or declared) passing bays.
        if aisle_class.capacity in {"single_vehicle", "one_vehicle"}:
            return site is not None and passing_bay_synthesis_enabled(site)
        return False
    if aisle_class.directionality == "one_way":
        return aisle_class.capacity in {"single_vehicle", "one_vehicle", "two_vehicle"}
    return False


def passing_bay_synthesis_enabled(site: SiteSpec) -> bool:
    """Whether the generator may place side-pocket passing bays on long spines."""
    raw = site.optimization.get("enable_passing_bay_synthesis")
    if raw is None:
        # Auto-enable when a narrow two-way class is selected and min bay count is set.
        fixed = fixed_aisle_class(site)
        if (
            fixed is not None
            and fixed.enabled
            and fixed.directionality == "two_way"
            and fixed.capacity in {"single_vehicle", "one_vehicle"}
            and site.optimization.get("operational_min_passing_bays") is not None
        ):
            return True
        return False
    return boolean_opt(raw)


def aisle_directionality(site: SiteSpec) -> str:
    """Directionality of the active fixed aisle class (defaults to two_way)."""
    fixed_class = fixed_aisle_class(site)
    if fixed_class is None:
        return "two_way"
    return fixed_class.directionality if fixed_class.directionality in {"one_way", "two_way"} else "two_way"


def one_way_allows_reverse_egress(site: SiteSpec) -> bool:
    """Whether one-way aisles still allow reverse egress for stall entry/exit.

    Parking templates commonly keep reverse egress on one-way modules. When false,
    the traffic graph is strict one-way and dead-end templates fail closed without
    a loop or second exit.
    """
    circulation = site.constraints.get("circulation", {})
    if not isinstance(circulation, dict):
        return True
    return boolean_opt(circulation.get("one_way_allows_reverse_egress", True))


def module_angle_allowed(angle: float, allowed_angles: tuple[float, ...]) -> bool:
    normalized = angle % 180
    return any(abs(normalized - (allowed % 180)) <= 1e-6 for allowed in allowed_angles)


def angled_module_angle(allowed_angles: tuple[float, ...]) -> float | None:
    for angle in allowed_angles:
        normalized = angle % 180
        if 1e-6 < normalized < 90.0 - 1e-6:
            return normalized
    return None


def parallel_access_supported(access_sides: tuple[str, ...], blocked_sides: tuple[str, ...] = ()) -> bool:
    """Parallel stalls enter from the traffic side (left/right) or a declared front."""
    traffic_sides = {"left", "right", "front"}
    access = set(access_sides) & traffic_sides
    blocked = set(blocked_sides)
    return bool(access - blocked)


def fixed_aisle_class(site: SiteSpec):
    if not site.fixed_aisle_class:
        return site.aisle_classes[0] if site.aisle_classes else None
    for aisle_class in site.aisle_classes:
        if aisle_class.id == site.fixed_aisle_class:
            return aisle_class
    return None
