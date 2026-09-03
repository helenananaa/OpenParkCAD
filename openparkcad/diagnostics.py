from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openparkcad.candidate_catalog import SNAPSHOT_VERSION, stall_module_segment_stalls
from openparkcad.scoring import segment_family_mix_weight
from openparkcad.models import LayoutResult, SiteSpec, is_articulated_vehicle
from openparkcad.contact_retarget import contact_retarget_requested
from openparkcad.site_constraints import (
    accessible_route_dimensions_requested,
    has_hard_accessible_route_geometry,
    has_hard_charger_geometry,
)
from openparkcad.phase1_support import (
    boolean_opt,
    entry_capable_entrances,
    exit_capable_entrances,
    fixed_aisle_class,
    is_phase1_aisle_class,
    passing_bay_synthesis_enabled,
    phase1_unsupported_inputs,
)
from openparkcad.site_constraints import declared_constraint_geometries
from openparkcad.traffic_graph import traffic_graph_summary


def build_input_diagnostics(site: SiteSpec, layout: LayoutResult | None = None) -> dict[str, Any]:
    active_fields = [
        "name",
        "units",
        "site.boundary polygon",
        "site.obstacles polygon",
        "parking.active stall width/length",
        "parking.active stall allowed_angles",
        "aisles.selected width",
        "constraints.setbacks.site_boundary",
    ]
    if _phase1_main_aisle_active(layout):
        active_fields.append("entrance-connected main aisle")
    elif site.entrances:
        active_fields.append("entrances drawn in diagnostics")

    parsed_future_fields: list[str] = []
    warnings: list[str] = []

    if site.standards:
        parsed_future_fields.append("standards")
        warnings.append("standards are parsed as metadata only; legal compliance is not checked yet")
    phase1_active = _phase1_main_aisle_active(layout)

    if site.entrances and not phase1_active:
        parsed_future_fields.append("entrances")
        warnings.append("entrances are parsed and drawn, but aisle connectivity to entrances is not enforced yet")
    elif phase1_active:
        warnings.append("entrance-to-main-aisle connection is active; Phase 2 graph reachability is now reported")
    vehicle_mode = _vehicle_validation_mode(site, layout)
    vehicle_status = _vehicle_field_status(site, layout)
    if site.vehicle:
        active_fields.append("vehicles.design_vehicle")
        if vehicle_mode == "exact_swept_path":
            if is_articulated_vehicle(site.vehicle):
                warnings.append(
                    "articulated design vehicles have no exact swept-path template; a requested swept-path check "
                    "fails closed instead of running the rigid bicycle stall templates"
                )
            else:
                warnings.append(
                    f"vehicle validation {'uses' if layout else 'requests'} the supported perpendicular-90, "
                    "acute-angled reverse-in, parallel reverse S-curve, and T-end reverse-in bicycle templates with exact "
                    "constant-curvature pose integration and a conservative sampled body envelope; this is not a driver, "
                    "tyre, dynamics, or jurisdictional simulation"
                )
        elif vehicle_mode == "conservative_analytic":
            if is_articulated_vehicle(site.vehicle):
                warnings.append(
                    f"vehicle validation {'uses' if layout else 'requests'} articulated combination fit, "
                    "steady-state trailer off-tracking versus aisle width, and a tractor-arc-plus-trailer reverse "
                    "bound; it does not validate a spatial tractor-trailer swept path"
                )
            else:
                warnings.append(
                    f"vehicle validation {'uses' if layout else 'requests'} rear-axle turning-radius conversion and "
                    "conservative analytic fit/reverse "
                    "bounds; it does not validate a spatial swept path or aisle-centerline crossing"
                )
        else:
            warnings.append(
                "design-vehicle data is parsed and available, but no vehicle-level check was requested; legacy "
                "maneuver envelopes remain proxies"
            )
        if vehicle_status == "active_failed":
            warnings.append("a requested vehicle-level check failed and is fail-closed for final layout validity")
    elif any(_requested_vehicle_checks(site).values()):
        warnings.append("vehicle-level validation was requested without a design vehicle and will fail closed")
    if site.site_features:
        warnings.append(
            f"hard site-feature scopes are {'enforced' if layout else 'available for enforcement'} for stall, aisle, "
            "and/or swept-path geometry as declared; "
            "advisory/draw-only features remain non-blocking, and passing-bay markers also feed Phase 5Q proxies"
        )
    if site.pedestrian_and_emergency:
        warnings.append(
            f"hard pedestrian, accessible, fire, and emergency route scopes are "
            f"{'enforced' if layout else 'available for enforcement'} as declared; advisory or "
            "future-priority routes are drawn/reported without becoming hard exclusions"
        )
    if site.stall.drive_over or site.stall.blocked_sides or site.stall.access_sides != ("front",):
        parsed_future_fields.append("parking.stall access behavior")
        warnings.append("stall access_sides, blocked_sides, and drive_over are parsed but not enforced yet")
    if site.aisle_classes:
        parsed_future_fields.append("aisles.classes")
    if site.aisle_selection_mode != "fixed":
        warnings.append("aisle selection modes beyond fixed are documented but not optimized yet")
    if site.constraints.get("maneuvering"):
        warnings.append(
            "maneuver requests are fail-closed for the supported perpendicular-90, angled reverse-in, "
            "parallel reverse S-curve, and T-end reverse-in templates; unsupported stall families or missing "
            "required vehicle geometry cannot pass through a proxy fallback"
        )
    if site.optimization:
        parsed_future_fields.append("optimization")
        warnings.append("optimization weights are active for Phase 1 scoring; candidate generation is still deliberately narrow")
    if layout and layout.operational_quality:
        mode = layout.operational_quality.get("mode", "score_only")
        warnings.append(f"operational quality checks are active in {mode} mode for Phase 5Q")
    unsupported = phase1_unsupported_inputs(site)
    warnings.extend(f"{item['field']} unsupported in Phase 1: {item['reason']}" for item in unsupported)

    return {
        "source_format": site.source_format,
        "active_fields": active_fields,
        "parsed_future_fields": sorted(set(parsed_future_fields)),
        "warnings": warnings,
        "field_support": _field_support(site, layout),
        "constraint_status": _constraint_status(site, layout),
        "entrances": [asdict(item) for item in site.entrances],
        "vehicle": asdict(site.vehicle) if site.vehicle else None,
        "aisle_selection": {
            "mode": site.aisle_selection_mode,
            "fixed_class": site.fixed_aisle_class,
            "effective_width": site.aisle_width,
        },
        "heading_selection": {
            "selected_heading_degrees": layout.selected_heading_degrees if layout else None,
            "selected_heading_delta_degrees": layout.selected_heading_delta_degrees if layout else None,
            "candidate_heading_deltas_degrees": site.optimization.get("heading_deltas_degrees", [-20, -10, 0, 10, 20]),
            "selected_entrance_offset": layout.selected_entrance_offset if layout else None,
            "candidate_entrance_offsets": site.optimization.get("entrance_offsets", "auto"),
        },
        "branch_selection": {
            "enabled": site.optimization.get("enable_branches", True),
            "selected_side": layout.selected_branch_side if layout else None,
            "selected_start_u": layout.selected_branch_start_u if layout else None,
            "selected_length": layout.selected_branch_length if layout else None,
            "selected_branches": layout.selected_branches if layout else [],
            "selected_connectors": layout.selected_connectors if layout else [],
            "candidate_start_positions": site.optimization.get("branch_start_positions", "auto"),
            "branch_sides": site.optimization.get("branch_sides", ["left", "right"]),
            "branch_start_step": site.optimization.get("branch_start_step", "auto"),
            "max_branches": site.optimization.get("max_branches", 2),
            "enable_connectors": site.optimization.get("enable_connectors", True),
            "connector_allow_outer_stall_row": site.optimization.get("connector_allow_outer_stall_row", True),
            "connector_inset_depths": site.optimization.get("connector_inset_depths", "auto"),
        },
        "stall_type_selection": {
            "selected_stall_type_id": layout.selected_stall_type_id if layout else site.stall.id,
            "selected_stall_assignment": layout.selected_stall_assignment if layout else {},
            "active_stall_type_id": site.stall.id,
            "candidate_stall_type_ids": [stall.id for stall in _stall_candidates(site)],
            "attempts": layout.stall_type_attempts if layout else [],
            "assignment_attempts": layout.stall_assignment_attempts if layout else [],
        },
        "score": layout.score if layout else {},
        "candidate_snapshot": {
            "version": SNAPSHOT_VERSION if layout else None,
            "object_count": len(layout.candidate_objects) if layout else 0,
            "conflict_count": _candidate_conflict_count(layout),
            "connector_candidate_count": _connector_candidate_count(layout),
            "synthetic_connector_candidate_count": _synthetic_connector_candidate_count(layout),
            "selection_version": layout.candidate_selection.get("version") if layout else None,
            "selection_status": layout.candidate_selection.get("status") if layout else None,
            "selection_backend": layout.candidate_selection.get("backend") if layout else None,
            "selection_requested_backend": layout.candidate_selection.get("requested_backend") if layout else None,
            "selection_selected_count": layout.candidate_selection.get("selected_count", 0) if layout else 0,
            "selection_selected_branch_count": layout.candidate_selection.get("selected_branch_count", 0) if layout else 0,
            "selection_selected_connector_count": layout.candidate_selection.get("selected_connector_count", 0) if layout else 0,
            "selection_selected_bundle_count": layout.candidate_selection.get("selected_bundle_count", 0) if layout else 0,
            "selection_base_selected_count": layout.candidate_selection.get("base_selected_count", 0) if layout else 0,
            "active": bool(layout and layout.candidate_objects),
        },
        "candidate_network_preview": {
            "version": layout.candidate_network_preview.get("version") if layout else None,
            "status": layout.candidate_network_preview.get("status") if layout else None,
            "aisle_count": layout.candidate_network_preview.get("aisle_count", 0) if layout else 0,
            "shadow_aisle_count": layout.candidate_network_preview.get("shadow_aisle_count", 0) if layout else 0,
            "shadow_turnaround_count": layout.candidate_network_preview.get("shadow_turnaround_count", 0) if layout else 0,
            "connector_count": layout.candidate_network_preview.get("connector_count", 0) if layout else 0,
            "loop_connector_count": layout.candidate_network_preview.get("loop_connector_count", 0) if layout else 0,
            "suppressed_turnaround_count": layout.candidate_network_preview.get("suppressed_turnaround_count", 0) if layout else 0,
            "valid_no_internal_conflicts": layout.candidate_network_preview.get("valid_no_internal_conflicts") if layout else None,
            "validation_valid": _preview_validation_valid(layout),
            "validation_errors": _preview_validation_errors(layout),
            "active": bool(layout and layout.candidate_network_preview),
        },
        "candidate_layout_preview": {
            "version": layout.candidate_layout_preview.get("version") if layout else None,
            "status": layout.candidate_layout_preview.get("status") if layout else None,
            "aisle_count": layout.candidate_layout_preview.get("aisle_count", 0) if layout else 0,
            "stall_count": layout.candidate_layout_preview.get("stall_count", 0) if layout else 0,
            "score_total": _layout_preview_score_total(layout),
            "score_delta": _layout_preview_score_delta(layout),
            "promotion_eligible": _layout_preview_promotion_eligible(layout),
            "validation_valid": _layout_preview_validation_valid(layout),
            "validation_errors": _layout_preview_validation_errors(layout),
            "active": bool(layout and layout.candidate_layout_preview),
        },
        "candidate_layout_promotion": layout.candidate_layout_promotion if layout else {},
        "maneuver_validation": layout.maneuver_validation if layout else None,
        "vehicle_validation": _vehicle_validation_diagnostic(site, layout),
        "site_constraint_validation": layout.site_constraint_validation if layout else None,
        "engineering_validation": layout.engineering_validation if layout else None,
        "operational_quality": layout.operational_quality if layout else None,
        "active_stall_type": asdict(site.stall),
        "unsupported_phase1_inputs": unsupported,
        "stall_access": _stall_access(layout),
        "aisle_connectivity": _aisle_connectivity(layout),
        "traffic_graph": traffic_graph_summary(layout) if layout else None,
    }


def _constraint_status(site: SiteSpec, layout: LayoutResult | None) -> list[dict[str, str]]:
    entrance_status = {
        "constraint": "entrance to main aisle",
        "status": "active" if _phase1_main_aisle_active(layout) else "future",
        "note": (
            f"Main aisle starts from entrance {layout.main_entrance_id}."
            if _phase1_main_aisle_active(layout)
            else "Entrances are parsed and drawn but not connected to aisle graph yet."
        ),
    }
    turnaround_status = {
        "constraint": "dead-end turnaround",
        "status": "active" if _phase1_main_aisle_active(layout) else "future",
        "note": (
            "A conservative turnaround pad is reserved at the end of each generated dead-end aisle."
            if _phase1_main_aisle_active(layout)
            else "Turnaround geometry is only created by the Phase 1 main aisle generator."
        ),
    }
    return [
        {
            "constraint": "geometry containment",
            "status": "active",
            "note": "Generated stall and aisle access polygons are checked against usable area.",
        },
        {
            "constraint": "obstacle avoidance",
            "status": _constraint_source_status(site, layout, {"obstacle"}),
            "note": (
                "Hard polygon obstacles use the larger of their declared clearance and the project obstacle setback."
            ),
        },
        {
            "constraint": "declared site exclusions",
            "status": _site_constraint_status(layout),
            "note": (
                "Hard obstacle, reserved-area, feature, and route scopes are checked against the exact official "
                "stall/aisle layout; advisory authority or priority remains non-blocking."
            ),
        },
        entrance_status,
        turnaround_status,
        {
            "constraint": "stall-to-aisle association",
            "status": "active" if _all_stalls_have_aisles(layout) else "future",
            "note": (
                "Each generated stall records the aisle that serves its front access side."
                if _all_stalls_have_aisles(layout)
                else "Stall-to-aisle association is only available after Phase 1 layout generation."
            ),
        },
        {
            "constraint": "phase1 aisle connectivity",
            "status": "active" if _phase1_main_aisle_active(layout) else "future",
            "note": (
                "Generated Phase 1 aisles are main/turnaround/branch pieces attached to the entrance-connected main aisle."
                if _phase1_main_aisle_active(layout)
                else "Full aisle graph reachability is not implemented yet."
            ),
        },
        {
            "constraint": "road traversal",
            "status": _road_traversal_status(site, layout),
            "note": (
                "Requested interior journeys are fail-closed for every retained stall."
                if _road_traversal_status(site, layout) in {"active", "active_failed", "unsupported", "incomplete"}
                else "Road traversal is not requested; existing validity is unchanged."
            ),
        },
        {
            "constraint": "full aisle graph reachability",
            "status": _traffic_graph_status(layout),
            "note": (
                "Phase 2A validates generated aisle and stall reachability from the traffic graph."
                if _traffic_graph_status(layout) == "active"
                else "Traffic graph reachability is only available after layout generation."
            ),
        },
        {
            "constraint": "stall maneuver access envelope",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3A checks a conservative rectangular access envelope from each stall front into its serving aisle."
                if _maneuver_status(layout) == "active"
                else "Maneuver access envelope checks are only available after layout generation."
            ),
        },
        {
            "constraint": "stall turning sweep proxy",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3B checks an expanded aisle-side envelope near each stall front to approximate low-speed turning clearance."
                if _maneuver_status(layout) == "active"
                else "Turning sweep proxy checks are only available after layout generation."
            ),
        },
        {
            "constraint": "maneuver rule dispatch",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3C-1 dispatches stalls to explicit active or future maneuver rules."
                if _maneuver_status(layout) == "active"
                else "Maneuver rule dispatch is only available after layout generation."
            ),
        },
        {
            "constraint": "angled stall maneuver proxy",
            "status": _angled_maneuver_status(site, layout),
            "note": (
                "Phase 3C-2 validates angled stall access envelopes for generated main-aisle and branch stalls."
                if _angled_maneuver_status(site, layout) == "active"
                else "Angled stall maneuver validation is available only when the active stall family is angled."
            ),
        },
        {
            "constraint": "parallel stall maneuver proxy",
            "status": _parallel_maneuver_status(site, layout),
            "note": (
                "Parallel stalls use a traffic-side rectangular access/turn proxy on main and branch aisles; "
                "this is not an S-curve reverse-parallel path or vehicle swept-path template."
                if _parallel_maneuver_status(site, layout) == "active"
                else "Parallel stall maneuver validation is available only when the active stall family is parallel."
            ),
        },
        {
            "constraint": "operational quality risk report",
            "status": _operational_quality_status(layout),
            "note": (
                "Phase 5R reports junction, entrance-throat, route, route-summary, directionality, narrow two-way exposure, passing bay geometry/spacing/entrance-junction/mid-aisle-junction meeting risks, narrow two-way junction-merge risks, and pedestrian-conflict proximity/crossing proxies; configured modes can score, gate promotion, or hard-reject risky layouts."
                if _operational_quality_status(layout) == "active"
                else "Operational quality checks are only available after layout generation."
            ),
        },
        {
            "constraint": "v0.3 engineering validation contract",
            "status": _engineering_validation_status(layout),
            "note": (
                "Combines vehicle, hard site, quota, authority, advisory, unsupported, and failed-rule evidence for "
                "the exact official layout into one versioned fail-closed decision."
            ),
        },
        {
            "constraint": "vehicle turning radius",
            "status": _vehicle_check_status(site, layout, "turning_radius"),
            "note": (
                "When requested, the declared radius is resolved to the rear-axle-center path. "
                "outer_front_wheel inputs require explicit wheelbase and track width and fail closed if conversion "
                "cannot be audited."
            ),
        },
        {
            "constraint": "vehicle swept path",
            "status": _vehicle_check_status(site, layout, "swept_path"),
            "note": (
                "Optional exact mode supports perpendicular-90 reverse-in, acute-angled reverse-in, parallel "
                "reverse S-curve, and T-end reverse-in: exact constant-curvature bicycle poses are wrapped by a "
                "conservative sampled body envelope and checked against drivable, boundary, centerline, and "
                "hard-exclusion geometry. Articulated vehicles have no exact template and fail closed."
            ),
        },
        {
            "constraint": "vehicle reverse distance",
            "status": _vehicle_check_status(site, layout, "reverse_distance"),
            "note": (
                "Exact mode measures its template path; conservative mode uses a fail-closed quarter-turn-plus-stall "
                "upper bound."
            ),
        },
        {
            "constraint": "narrow two-way deadlock",
            "status": "future",
            "note": "Narrow two-way aisles should remain disabled until graph deadlock checks exist.",
        },
        {
            "constraint": "pedestrian and emergency reservations",
            "status": _constraint_source_status(
                site,
                layout,
                {"pedestrian_route", "accessible_route", "fire_lane", "access_route", "emergency_access_route"},
            ),
            "note": (
                "Hard declared route scopes exclude stalls, aisles, and/or swept paths; advisory/future-priority "
                "routes stay non-blocking. Required route declarations without usable hard geometry fail closed."
            ),
        },
        {
            "constraint": "accessible and EV minimum quotas",
            "status": _quota_status(site, layout),
            "note": (
                "Positive minimums count final stalls by explicit stall-type classifications or charging features; "
                "an unmet or unsupported positive quota invalidates the layout."
            ),
        },
        {
            "constraint": "accessible route usability",
            "status": _route_usability_status(layout, "accessible_route"),
            "note": (
                "When accessible_min is positive, classified accessible stalls must geometrically reach a hard "
                "accessible route within constraints.accessible_route_touch_tolerance. Not slope, width, or ADA."
            ),
        },
        {
            "constraint": "accessible route continuity",
            "status": _route_usability_status(layout, "accessible_route_continuity"),
            "note": (
                "When accessible_min is positive, accessible-route pieces that serve classified stalls must form "
                "one contact network, and declared accessible_routes.connects destinations must be reachable from "
                "that network. Not slope, width, or ADA."
            ),
        },
        {
            "constraint": "accessible route dimensions",
            "status": _route_usability_status(layout, "accessible_route_dimensions"),
            "note": (
                "Declared min_width is checked against polyline_buffer width. Declared max_slope fail-closes "
                "because elevation is not modeled. Not ADA certification."
            ),
        },
        {
            "constraint": "emergency access connectivity",
            "status": _route_usability_status(layout, "emergency_access"),
            "note": (
                "When emergency_access_required is set, hard fire/access routes must geometrically reach an "
                "entrance gate within constraints.emergency_route_touch_tolerance. Not apparatus swept path."
            ),
        },
        {
            "constraint": "EV charger usability",
            "status": _route_usability_status(layout, "ev_charger"),
            "note": (
                "When ev_min is positive and hard charging posts are placed, classified EV stalls must "
                "geometrically reach a charger within constraints.ev_charger_touch_tolerance. Not electrical "
                "or equipment-layout certification."
            ),
        },
    ]


def _field_support(site: SiteSpec, layout: LayoutResult | None) -> dict[str, str]:
    support = {
        "version": "active",
        "name": "active",
        "units": "active",
        "standards": "parsed_not_enforced",
        "site.boundary.polygon": "active",
        "site.boundary.curve_loop": "future",
        "site.obstacles.polygon": _constraint_source_status(site, layout, {"obstacle"}),
        "site.reserved_areas": _constraint_source_status(site, layout, {"reserved_area"}),
        "site_features": _constraint_source_status(site, layout, {"site_feature"}),
        "entrances": "active" if _phase1_main_aisle_active(layout) else ("drawn_not_enforced" if site.entrances else "future"),
        "pedestrian_and_emergency.pedestrian_routes": _pedestrian_status(site, "pedestrian_routes", layout),
        "pedestrian_and_emergency.accessible_routes": _pedestrian_status(site, "accessible_routes", layout),
        "pedestrian_and_emergency.fire_lanes": _pedestrian_status(site, "fire_lanes", layout),
        "pedestrian_and_emergency.access_routes": _pedestrian_status(site, "access_routes", layout),
        "pedestrian_and_emergency.accessible_route_usability": _route_usability_field_status(
            site, layout, "accessible_route", requested=_accessible_route_requested(site)
        ),
        "pedestrian_and_emergency.accessible_route_continuity": _route_usability_field_status(
            site, layout, "accessible_route_continuity", requested=_accessible_route_requested(site)
        ),
        "pedestrian_and_emergency.accessible_route_dimensions": _route_usability_field_status(
            site, layout, "accessible_route_dimensions", requested=_accessible_route_dimensions_requested(site)
        ),
        "pedestrian_and_emergency.emergency_access_connectivity": _route_usability_field_status(
            site, layout, "emergency_access", requested=_emergency_access_requested(site)
        ),
        "vehicles.design_vehicle": _vehicle_field_status(site, layout),
        "vehicles.articulated": _articulated_vehicle_status(site, layout),
        "parking.standard_perpendicular": "active",
        "parking.stall_type_classifications": _classification_status(site, layout),
        "parking.quotas.accessible_min": _quota_key_status(site, layout, "accessible_min"),
        "parking.quotas.ev_min": _quota_key_status(site, layout, "ev_min"),
        "parking.ev_charger_usability": _route_usability_field_status(
            site, layout, "ev_charger", requested=_ev_charger_usability_requested(site)
        ),
        "parking.accessible_contact_filter": _contact_filter_status(
            layout, requested=_accessible_route_requested(site) and has_hard_accessible_route_geometry(site)
        ),
        "parking.ev_contact_filter": _contact_filter_status(
            layout, requested=_ev_charger_usability_requested(site)
        ),
        "parking.contact_retarget": _contact_filter_status(
            layout, requested=contact_retarget_requested(site)
        ),
        "parking.stall_type_candidate_selection": "active" if len(_stall_candidates(site)) > 1 else "available",
        "parking.stall_type_segment_assignment": "active" if layout and layout.stall_assignment_attempts else ("available" if len(_stall_candidates(site)) > 1 else "future"),
        "parking.angled_maneuver_proxy": _angled_maneuver_status(site, layout),
        "parking.angled_main_aisle_generation": _angled_generation_status(site, layout),
        "parking.angled_branch_generation": _angled_branch_generation_status(site, layout),
        "parking.angled_connector_generation": _connector_stall_generation_status(site, layout, "angled"),
        "parking.parallel_maneuver_proxy": _parallel_maneuver_status(site, layout),
        "parking.parallel_main_aisle_generation": _parallel_generation_status(site, layout),
        "parking.parallel_branch_generation": _parallel_branch_generation_status(site, layout),
        "parking.parallel_connector_generation": _connector_stall_generation_status(site, layout, "parallel"),
        "parking.t_end": _t_end_status(site, layout),
        "parking.t_end_maneuver_proxy": _t_end_maneuver_status(site, layout),
        "parking.t_end_caps": (
            "active"
            if _boolean_opt(site.optimization.get("enable_t_end_caps", False)) and layout and layout.stall_count > 0
            else ("available" if not _boolean_opt(site.optimization.get("enable_t_end_caps", False)) else "active_failed")
        ),
        "parking.maneuver_rule_dispatch": _maneuver_status(layout),
        "parking.drive_over": "parsed_not_enforced",
        "parking.access_sides": (
            "active"
            if _has_active_stall_family(site, layout, "parallel")
            else "parsed_not_enforced"
        ),
        "parking.blocked_sides": "parsed_not_enforced",
        "aisles.fixed_wide_two_way": _fixed_aisle_mode_status(
            site, layout, "two_way", capacities={"two_vehicle"}
        ),
        "aisles.fixed_one_way": _fixed_aisle_mode_status(site, layout, "one_way"),
        "entrances.dual_entry_exit": _dual_entrance_status(site, layout),
        "aisles.heading_candidate_selection": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.entrance_offset_selection": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.main_aisle_lateral_offsets": (
            "active"
            if layout and (
                _boolean_opt(site.optimization.get("enable_main_aisle_lateral_offsets", False))
                or isinstance(site.optimization.get("main_aisle_lateral_offsets"), list)
            )
            else "available"
        ),
        "aisles.same_side_connectors": "active" if layout and site.optimization.get("enable_connectors", True) else "available",
        "aisles.opposite_side_connectors": (
            "active"
            if layout and site.optimization.get("enable_connectors", True) and site.optimization.get("enable_opposite_connectors", True)
            else "available"
        ),
        "aisles.single_branch_candidate": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.multiple_branch_candidates": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.narrow_one_way": _narrow_aisle_status(site, layout, "one_way"),
        "aisles.narrow_two_way": _narrow_aisle_status(site, layout, "two_way"),
        "aisles.passing_bay_synthesis": _passing_bay_synthesis_status(site, layout),
        "aisles.main_aisle_dogleg": _generation_mode_status(layout, "phase1_main_aisle_dogleg"),
        "aisles.main_aisle_multi_jog": _generation_mode_status(layout, "phase1_main_aisle_multi_jog"),
        "constraints.geometry_containment": "active",
        "constraints.entrance_to_main_aisle": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.dead_end_turnaround": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.stall_to_aisle_association": "active" if _all_stalls_have_aisles(layout) else "future",
        "constraints.phase1_aisle_connectivity": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.road_traversal": _road_traversal_status(site, layout),
        "constraints.full_aisle_graph_reachability": _traffic_graph_status(layout),
        "constraints.maneuver_access_envelope": _maneuver_status(layout),
        "constraints.turning_sweep_proxy": _maneuver_status(layout),
        "constraints.maneuver_l_shape_fallback": _maneuver_status(layout),
        "constraints.maneuver_rule_dispatch": _maneuver_status(layout),
        "constraints.operational_quality": _operational_quality_status(layout),
        "constraints.junction_conflict_points": _operational_quality_status(layout),
        "constraints.entrance_throat_blockage": _operational_quality_status(layout),
        "constraints.operational_route_risk": _operational_quality_status(layout),
        "constraints.operational_route_summary": _operational_quality_status(layout),
        "constraints.operational_directionality_risk": _operational_quality_status(layout),
        "constraints.operational_narrow_two_way_risk": _operational_quality_status(layout),
        "constraints.operational_pedestrian_conflict": _pedestrian_conflict_status(site, layout),
        "constraints.site_hard_exclusions": _site_constraint_status(layout),
        "constraints.turning_radius": _vehicle_check_status(site, layout, "turning_radius"),
        "constraints.swept_path": _vehicle_check_status(site, layout, "swept_path"),
        "constraints.reverse_distance": _vehicle_check_status(site, layout, "reverse_distance"),
        "optimization.weights": "active" if layout and layout.score else ("available" if site.optimization else "future"),
        "optimization.score_breakdown": "active" if _phase1_main_aisle_active(layout) else "future",
        "optimization.candidate_objects": "active" if layout and layout.candidate_objects else "future",
        "optimization.candidate_conflict_matrix": "active" if layout and layout.candidate_objects else "future",
        "optimization.discrete_candidate_catalog": "active" if layout and layout.candidate_objects else "future",
        "optimization.stall_modules": (
            "active"
            if layout and any(item.kind == "stall_module" for item in layout.candidate_objects)
            else "available"
        ),
        "optimization.stall_module_segment_stalls": (
            "active" if layout and stall_module_segment_stalls(site.optimization) > 0 else "available"
        ),
        "optimization.mixed_segment_scoring": _mixed_segment_scoring_status(site, layout),
        "optimization.selector_backend": (
            "active"
            if layout and layout.candidate_selection.get("backend") in {"greedy", "cpsat"}
            else "available"
        ),
        "optimization.selector_backend_cpsat": _cpsat_backend_status(layout),
        "optimization.selector_num_workers": _selector_num_workers_status(site, layout),
        "optimization.layout_search": _layout_search_status(site, layout),
        "optimization.layout_search.mode": _layout_search_field_status(site, layout, "mode"),
        "optimization.layout_search.top_k": _layout_search_field_status(site, layout, "top_k"),
        "optimization.layout_search.refinement_budget_seconds": _layout_search_field_status(
            site, layout, "refinement_budget_seconds"
        ),
        "optimization.shadow_candidate_selector": "active" if layout and layout.candidate_selection else "future",
        "optimization.candidate_network_preview": "active" if layout and layout.candidate_network_preview else "future",
        "optimization.candidate_shadow_branch_turnarounds": "active" if _shadow_turnarounds_active(layout) else "future",
        "optimization.connector_inset_depths": "active" if layout and site.optimization.get("enable_connectors", True) else "future",
        "optimization.connector_l_shape_end_stalls": "active" if layout and site.optimization.get("enable_connectors", True) else "future",
        "optimization.operational_risk_weight": "active" if layout else "future",
        "optimization.operational_quality_mode": "active" if layout else "future",
        "optimization.operational_max_risk_score": "active" if layout else "future",
        "optimization.operational_max_route_length": "available" if layout else "future",
        "optimization.operational_turnaround_dependency_risk": "available" if layout else "future",
        "optimization.operational_max_turnaround_dependency_ratio": "available" if layout else "future",
        "optimization.operational_turnaround_dependency_ratio_risk": "available" if layout else "future",
        "optimization.operational_max_average_route_length": "available" if layout else "future",
        "optimization.operational_average_route_length_risk": "available" if layout else "future",
        "optimization.operational_max_long_route_ratio": "available" if layout else "future",
        "optimization.operational_long_route_ratio_risk": "available" if layout else "future",
        "optimization.operational_directionality_issue_risk": "available" if layout else "future",
        "optimization.operational_max_directionality_issue_ratio": "available" if layout else "future",
        "optimization.operational_directionality_issue_ratio_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_issue_risk": "available" if layout else "future",
        "optimization.operational_max_narrow_two_way_stall_ratio": "available" if layout else "future",
        "optimization.operational_narrow_two_way_stall_ratio_risk": "available" if layout else "future",
        "optimization.operational_min_passing_bays": "available" if layout else "future",
        "optimization.operational_passing_bay_shortage_risk": "available" if layout else "future",
        "optimization.operational_passing_bay_touch_tolerance": "available" if layout else "future",
        "optimization.operational_min_passing_bay_area": "available" if layout else "future",
        "optimization.operational_passing_bay_geometry_issue_risk": "available" if layout else "future",
        "optimization.operational_max_passing_bay_spacing": "available" if layout else "future",
        "optimization.operational_passing_bay_spacing_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_meeting_gap_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_junction_merge_risk": "available" if layout else "future",
        "optimization.operational_pedestrian_conflict_risk": "available" if layout else "future",
        "optimization.operational_pedestrian_conflict_clearance": "available" if layout else "future",
        "optimization.candidate_layout_preview": "active" if layout and layout.candidate_layout_preview else "future",
        "optimization.candidate_layout_preview_scoring": "active" if _layout_preview_comparison(layout) else "future",
        "optimization.promote_candidate_layout_preview": _candidate_layout_promotion_status(layout),
        "diagnostics.report": "active",
        "diagnostics.engineering_validation": _engineering_validation_status(layout),
        "diagnostics.svg_candidate_network_preview": "active" if layout and layout.candidate_network_preview else "future",
        "diagnostics.debug_layers": "active",
    }
    return support


def _pedestrian_status(site: SiteSpec, key: str, layout: LayoutResult | None = None) -> str:
    source_by_key = {
        "pedestrian_routes": "pedestrian_route",
        "accessible_routes": "accessible_route",
        "fire_lanes": "fire_lane",
        "access_routes": "access_route",
        "emergency_access_routes": "emergency_access_route",
    }
    source = source_by_key.get(key)
    if source is None:
        return "future"
    return _constraint_source_status(site, layout, {source})


def _declared_constraints(site: SiteSpec):
    try:
        return declared_constraint_geometries(site, include_advisory=True)
    except ValueError:
        return None


def _constraint_source_status(
    site: SiteSpec,
    layout: LayoutResult | None,
    sources: set[str],
) -> str:
    declared = _declared_constraints(site)
    if declared is None:
        if layout is not None and layout.site_constraint_validation:
            return "active_failed"
        return "declared_invalid"
    declarations = [item for item in declared if item.source in sources]
    if not declarations:
        return "available"
    if not any(item.hard for item in declarations):
        return "advisory_only"
    if layout is None or not layout.site_constraint_validation:
        return "declared_pending_layout"
    return "active" if layout.site_constraint_validation.get("valid", False) else "active_failed"


def _site_constraint_status(layout: LayoutResult | None) -> str:
    if layout is None or not layout.site_constraint_validation:
        return "available"
    return "active" if layout.site_constraint_validation.get("valid", False) else "active_failed"


def _road_traversal_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    from openparkcad.road_traversal_models import parse_traversal_policy

    policy = parse_traversal_policy(site)
    if not policy.requested:
        return "not_requested"
    if layout is None:
        return "requested"
    record = layout.road_traversal_validation if isinstance(layout.road_traversal_validation, dict) else {}
    status = record.get("status")
    if status == "passed" and record.get("valid") is True:
        return "active"
    if status == "unsupported":
        return "unsupported"
    if status == "incomplete":
        return "incomplete"
    if status == "failed":
        return "active_failed"
    if not record:
        return "requested_not_executed"
    return str(status or "requested")


def _maneuvering_settings(site: SiteSpec) -> dict[str, Any]:
    raw = site.constraints.get("maneuvering", {})
    return raw if isinstance(raw, dict) else {}


def _boolean_setting(raw: object) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "false", "0", "no", "off"}
    return bool(raw)


def _declared_vehicle_requests(site: SiteSpec) -> dict[str, bool]:
    maneuvering = _maneuvering_settings(site)
    swept_path = _boolean_setting(maneuvering.get("require_swept_path_check", False))
    turning_radius = _boolean_setting(maneuvering.get("require_turning_radius_check", False))
    reverse_distance = (
        maneuvering.get("max_reverse_distance") is not None
        or bool(site.vehicle and site.vehicle.max_reverse_distance is not None)
    )
    return {
        "turning_radius": turning_radius,
        "swept_path": swept_path,
        "reverse_distance": reverse_distance,
    }


def _requested_vehicle_checks(site: SiteSpec) -> dict[str, bool]:
    declared = _declared_vehicle_requests(site)
    return {
        "turning_radius": (
            declared["turning_radius"] or declared["swept_path"] or declared["reverse_distance"]
        ),
        "swept_path": declared["swept_path"],
        "reverse_distance": declared["reverse_distance"],
    }


def _vehicle_validation_report(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None or not isinstance(layout.maneuver_validation, dict):
        return {}
    report = layout.maneuver_validation.get("vehicle_validation", {})
    return report if isinstance(report, dict) else {}


def _vehicle_validation_mode(site: SiteSpec, layout: LayoutResult | None) -> str:
    requested = _requested_vehicle_checks(site)
    if requested["swept_path"]:
        return "exact_swept_path"
    if requested["turning_radius"] or requested["reverse_distance"]:
        return "conservative_analytic"
    return "not_requested"


def _articulated_vehicle_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not is_articulated_vehicle(site.vehicle):
        return "available"
    requested = _requested_vehicle_checks(site)
    if not any(requested.values()):
        return "parsed_available"
    if layout is None:
        return "requested_pending_layout"
    if requested["swept_path"]:
        return "active_failed"
    report = _vehicle_validation_report(layout)
    if not report or not report.get("valid", False):
        return "active_failed"
    return "active_conservative"


def _vehicle_field_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    requested = _requested_vehicle_checks(site)
    any_requested = any(requested.values())
    if site.vehicle is None:
        return "requested_missing" if any_requested else "available"
    if not any_requested:
        return "parsed_available"
    if layout is None:
        return "requested_pending_layout"
    report = _vehicle_validation_report(layout)
    if not report or not report.get("valid", False):
        return "active_failed"
    return "active_exact" if requested["swept_path"] else "active_conservative"


def _vehicle_check_status(site: SiteSpec, layout: LayoutResult | None, check: str) -> str:
    requested = _requested_vehicle_checks(site)
    if not requested.get(check, False):
        return "available"
    if layout is None:
        return "requested_pending_layout"
    report = _vehicle_validation_report(layout)
    if not report or not report.get("valid", False):
        return "active_failed"
    return "active_exact" if requested["swept_path"] else "active_conservative"


def _vehicle_validation_diagnostic(site: SiteSpec, layout: LayoutResult | None) -> dict[str, Any]:
    report = _vehicle_validation_report(layout)
    requested = _requested_vehicle_checks(site)
    return {
        "mode": _vehicle_validation_mode(site, layout),
        "status": _vehicle_field_status(site, layout),
        "requested": requested,
        "declared_requests": _declared_vehicle_requests(site),
        "fail_closed_when_requested": True,
        "checked_stalls": int(report.get("checked_stalls", 0)),
        "invalid_stall_count": int(report.get("invalid_stall_count", 0)),
        "report_version": report.get("version"),
        "scope": _vehicle_validation_scope(site, requested),
    }


def _vehicle_validation_scope(site: SiteSpec, requested: dict[str, bool]) -> str:
    if is_articulated_vehicle(site.vehicle):
        if requested["swept_path"]:
            return (
                "no exact articulated swept-path template; requested exact checks fail closed instead of "
                "running rigid bicycle stall templates"
            )
        return (
            "articulated conservative analytic: combination fit, steady-state trailer off-tracking versus "
            "aisle width, and tractor-arc-plus-trailer reverse bound"
        )
    if requested["swept_path"]:
        return (
            "perpendicular-90 reverse-in, acute-angled reverse-in, parallel reverse S-curve, and T-end "
            "reverse-in templates with exact constant-curvature pose integration and a conservative sampled "
            "body envelope"
        )
    return "rear-axle radius conversion, vehicle/stall fit, and conservative reverse-distance bound"


def _accessible_route_requested(site: SiteSpec) -> bool:
    return int(site.parking_quotas.get("accessible_min", 0) or 0) > 0


def _accessible_route_dimensions_requested(site: SiteSpec) -> bool:
    return accessible_route_dimensions_requested(site)


def _emergency_access_requested(site: SiteSpec) -> bool:
    return bool(site.pedestrian_and_emergency.get("emergency_access_required", False))


def _ev_charger_usability_requested(site: SiteSpec) -> bool:
    return int(site.parking_quotas.get("ev_min", 0) or 0) > 0 and has_hard_charger_geometry(site)


def _contact_filter_status(layout, *, requested: bool) -> str:
    if not requested:
        return "available"
    if layout is None:
        return "requested_pending_layout"
    return "active"


def _route_usability_report(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None or not isinstance(layout.site_constraint_validation, dict):
        return {}
    report = layout.site_constraint_validation.get("route_usability", {})
    return report if isinstance(report, dict) else {}


def _route_usability_status(layout: LayoutResult | None, key: str) -> str:
    report = _route_usability_report(layout)
    block = report.get(key, {})
    if not isinstance(block, dict) or not block.get("requested"):
        return "available"
    status = str(block.get("status") or "")
    if status == "active_failed":
        return "active_failed"
    if status == "active":
        return "active"
    return "requested_pending_layout"


def _route_usability_field_status(
    site: SiteSpec,
    layout: LayoutResult | None,
    key: str,
    *,
    requested: bool,
) -> str:
    if not requested:
        return "available"
    if layout is None:
        return "requested_pending_layout"
    return _route_usability_status(layout, key)


def _mixed_segment_scoring_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    weights = site.optimization.get("weights", {})
    family_weights = isinstance(weights, dict) and isinstance(weights.get("stall_family"), dict) and bool(weights.get("stall_family"))
    mix_weight = segment_family_mix_weight(site)
    families = {spec.family for spec in (site.stall_candidates or (site.stall,))}
    requested = family_weights or mix_weight != 0.0 or site.optimization.get("prefer_uniform_segments") or len(families) > 1
    if not requested:
        return "available"
    if layout is None:
        return "requested_pending_layout"
    return "active"


def _quota_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not any(value > 0 for value in site.parking_quotas.values()):
        return "available"
    if layout is None or not layout.site_constraint_validation:
        return "declared_pending_layout"
    quota = layout.site_constraint_validation.get("quota", {})
    return "active" if isinstance(quota, dict) and quota.get("valid", False) else "active_failed"


def _quota_key_status(site: SiteSpec, layout: LayoutResult | None, key: str) -> str:
    if site.parking_quotas.get(key, 0) <= 0:
        return "available"
    return _quota_status(site, layout)


def _classification_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not any(stall.classifications or stall.fixed_features for stall in _stall_candidates(site)):
        return "available"
    return "active" if layout else "declared_pending_layout"


def _stall_candidates(site: SiteSpec):
    return site.stall_candidates or (site.stall,)


def _fixed_aisle_mode_status(
    site: SiteSpec,
    layout: LayoutResult | None,
    mode: str,
    *,
    capacities: set[str] | None = None,
) -> str:
    aisle_class = fixed_aisle_class(site)
    if aisle_class is None or aisle_class.directionality != mode:
        return "available"
    if capacities is not None and aisle_class.capacity not in capacities:
        return "available"
    if _phase1_main_aisle_active(layout):
        return "active"
    return "available"


def _dual_entrance_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    entries = entry_capable_entrances(site)
    exits = exit_capable_entrances(site)
    distinct_exit = any(exit_e.id != entry_e.id for entry_e in entries for exit_e in exits)
    if not distinct_exit:
        return "available"
    if layout and any(aisle.role == "exit" and aisle.connected_to_entrance_id for aisle in layout.aisles):
        return "active"
    return "available"


def _narrow_aisle_status(site: SiteSpec, layout: LayoutResult | None, directionality: str) -> str:
    """Report status for a narrow fixed class of the given directionality."""
    aisle_class = fixed_aisle_class(site)
    if aisle_class is None:
        return "available"
    is_narrow = (
        aisle_class.directionality == directionality
        and aisle_class.capacity in {"single_vehicle", "one_vehicle"}
        and aisle_class.enabled
    )
    if not is_narrow:
        return "available"
    if not is_phase1_aisle_class(aisle_class, site=site):
        return "available"
    if _phase1_main_aisle_active(layout):
        return "active" if layout and layout.stall_count > 0 else "active_failed"
    return "available"


def _passing_bay_synthesis_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not passing_bay_synthesis_enabled(site):
        return "available"
    if layout and any(aisle.role == "passing_bay" for aisle in layout.aisles):
        return "active"
    return "available"


def _generation_mode_status(layout: LayoutResult | None, mode: str) -> str:
    if layout and layout.generation_mode == mode:
        return "active"
    return "available"


def _layout_search_requested(site: SiteSpec) -> bool:
    raw = site.optimization.get("layout_search")
    return isinstance(raw, dict) and bool(raw)


def _layout_search_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _layout_search_requested(site):
        return "available"
    report = getattr(layout, "layout_search", {}) if layout is not None else {}
    if isinstance(report, dict) and report.get("mode") == "multi_spine":
        if report.get("status") in {"completed", "budget_exhausted"}:
            return "active"
        return "active_failed"
    mode = site.optimization.get("layout_search", {})
    if isinstance(mode, dict) and mode.get("mode") == "multi_spine" and layout is None:
        return "requested_pending_layout"
    if isinstance(mode, dict) and mode.get("mode") == "legacy":
        return "available"
    return "available"


def _layout_search_field_status(site: SiteSpec, layout: LayoutResult | None, field_name: str) -> str:
    raw = site.optimization.get("layout_search")
    if not isinstance(raw, dict) or field_name not in raw:
        return "available"
    return _layout_search_status(site, layout)


def _selector_num_workers_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if "selector_num_workers" not in site.optimization:
        return "available"
    selection = layout.candidate_selection if layout and isinstance(layout.candidate_selection, dict) else {}
    provenance = selection.get("solver_provenance") if isinstance(selection.get("solver_provenance"), dict) else {}
    if selection.get("backend") == "cpsat" and provenance.get("num_workers") is not None:
        return "active"
    if selection.get("requested_backend") == "cpsat" and selection.get("backend") != "cpsat":
        return "active_failed"
    return "available"


def _cpsat_backend_status(layout: LayoutResult | None) -> str:
    if not layout or not isinstance(layout.candidate_selection, dict):
        return "available"
    selection = layout.candidate_selection
    if selection.get("backend") == "cpsat":
        return "active"
    if selection.get("requested_backend") == "cpsat":
        return "active_failed"
    return "available"


def _phase1_main_aisle_active(layout: LayoutResult | None) -> bool:
    if not layout or not layout.main_entrance_id:
        return False
    mode = layout.generation_mode or ""
    return mode.startswith("phase1_main_aisle") or mode == "candidate_layout_promoted"


def _all_stalls_have_aisles(layout: LayoutResult | None) -> bool:
    return bool(layout and layout.stalls and all(stall.served_by_aisle_id for stall in layout.stalls))


def _stall_access(layout: LayoutResult | None) -> list[dict[str, str | None]]:
    if not layout:
        return []
    return [
        {
            "stall_id": stall.id,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "aisle_side": stall.aisle_side,
            "stall_type_id": stall.stall_type_id,
        }
        for stall in layout.stalls
    ]


def _aisle_connectivity(layout: LayoutResult | None) -> list[dict[str, str | None]]:
    if not layout:
        return []
    return [
        {
            "aisle_id": aisle.id,
            "role": aisle.role,
            "connected_to_entrance_id": aisle.connected_to_entrance_id,
            "parent_aisle_id": aisle.parent_aisle_id,
            "connected_aisle_ids": ",".join(aisle.connected_aisle_ids) if aisle.connected_aisle_ids else None,
        }
        for aisle in layout.aisles
    ]


def _traffic_graph_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    return "active" if traffic_graph_summary(layout)["valid"] else "active_failed"


def _candidate_conflict_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return sum(len(candidate.conflict_ids) for candidate in layout.candidate_objects) // 2


def _connector_candidate_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return len([candidate for candidate in layout.candidate_objects if candidate.kind == "aisle_skeleton" and candidate.role == "connector"])


def _synthetic_connector_candidate_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return len(
        [
            candidate
            for candidate in layout.candidate_objects
            if candidate.kind == "aisle_skeleton"
            and candidate.role == "connector"
            and candidate.metadata.get("synthetic")
        ]
    )


def _preview_validation_valid(layout: LayoutResult | None) -> bool | None:
    if not layout:
        return None
    validation = layout.candidate_network_preview.get("validation")
    if not isinstance(validation, dict):
        return None
    return bool(validation.get("valid"))


def _preview_validation_errors(layout: LayoutResult | None) -> list[str]:
    if not layout:
        return []
    validation = layout.candidate_network_preview.get("validation")
    if not isinstance(validation, dict):
        return []
    errors = validation.get("errors", [])
    if not isinstance(errors, list):
        return []
    return [str(item) for item in errors]


def _layout_preview_validation_valid(layout: LayoutResult | None) -> bool | None:
    if not layout:
        return None
    validation = layout.candidate_layout_preview.get("validation")
    if not isinstance(validation, dict):
        return None
    return bool(validation.get("valid"))


def _layout_preview_validation_errors(layout: LayoutResult | None) -> list[str]:
    if not layout:
        return []
    validation = layout.candidate_layout_preview.get("validation")
    if not isinstance(validation, dict):
        return []
    errors = validation.get("errors", [])
    if not isinstance(errors, list):
        return []
    return [str(item) for item in errors]


def _layout_preview_comparison(layout: LayoutResult | None) -> dict[str, Any]:
    if not layout:
        return {}
    comparison = layout.candidate_layout_preview.get("comparison")
    return comparison if isinstance(comparison, dict) else {}


def _layout_preview_score_total(layout: LayoutResult | None) -> float | None:
    if not layout:
        return None
    score = layout.candidate_layout_preview.get("score")
    if not isinstance(score, dict) or not isinstance(score.get("total"), int | float):
        return None
    return float(score["total"])


def _layout_preview_score_delta(layout: LayoutResult | None) -> float | None:
    comparison = _layout_preview_comparison(layout)
    value = comparison.get("score_delta")
    return float(value) if isinstance(value, int | float) else None


def _layout_preview_promotion_eligible(layout: LayoutResult | None) -> bool | None:
    comparison = _layout_preview_comparison(layout)
    value = comparison.get("promotion_eligible")
    return bool(value) if isinstance(value, bool) else None


def _candidate_layout_promotion_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    if layout.candidate_layout_promotion.get("status") == "promoted":
        return "active"
    if layout.candidate_layout_promotion.get("requested"):
        return "active_rejected"
    return "available"


def _shadow_turnarounds_active(layout: LayoutResult | None) -> bool:
    if not layout:
        return False
    count = layout.candidate_network_preview.get("shadow_turnaround_count")
    return isinstance(count, int | float) and count > 0


def _maneuver_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    validation = layout.maneuver_validation
    if not validation:
        return "future"
    return "active" if validation.get("valid") else "active_failed"


def _pedestrian_conflict_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    has_routes = bool(site.pedestrian_and_emergency.get("pedestrian_routes") or site.pedestrian_and_emergency.get("accessible_routes"))
    if not has_routes:
        return "available"
    if layout is None:
        return "requested_pending_layout"
    report = layout.operational_quality.get("pedestrian_conflict_risks") if layout.operational_quality else None
    if isinstance(report, dict) and report.get("status") == "report_only":
        return "active"
    return "available"


def _operational_quality_status(layout: LayoutResult | None) -> str:
    if not layout or not layout.operational_quality:
        return "future"
    return "active" if layout.operational_quality.get("valid", True) else "active_failed"


def _engineering_validation_status(layout: LayoutResult | None) -> str:
    if not layout or not layout.engineering_validation:
        return "future"
    return "active" if layout.engineering_validation.get("valid", False) else "active_failed"


def _angled_maneuver_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "angled"):
        return "available"
    return _maneuver_status(layout)


def _angled_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "angled"):
        return "available"
    if not layout:
        return "future"
    return "active" if layout.stall_count > 0 else "active_failed"


def _angled_branch_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    branch_stall = layout.site.branch_stall if layout else site.branch_stall
    if (branch_stall or site.stall).family != "angled":
        return "available"
    if not layout:
        return "future"
    return "active" if layout.selected_branches else "available"


def _parallel_maneuver_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "parallel"):
        return "available"
    return _maneuver_status(layout)


def _parallel_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "parallel"):
        return "available"
    if not layout:
        return "future"
    return "active" if layout.stall_count > 0 else "active_failed"


def _connector_stall_generation_status(site: SiteSpec, layout: LayoutResult | None, family: str) -> str:
    branch_stall = (layout.site.branch_stall if layout else site.branch_stall) or site.stall
    if branch_stall.family != family:
        return "available"
    if not _boolean_opt(site.optimization.get("enable_connectors", True)):
        return "available"
    if not layout:
        return "future"
    if any(str(stall.served_by_aisle_id or "").startswith("A-CONNECTOR") for stall in layout.stalls):
        return "active"
    return "available"


def _parallel_branch_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    branch_stall = layout.site.branch_stall if layout else site.branch_stall
    if (branch_stall or site.stall).family != "parallel":
        return "available"
    if not layout:
        return "future"
    return "active" if layout.selected_branches else "available"


def _t_end_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if _has_active_stall_family(site, layout, "t_end"):
        if not layout:
            return "future"
        return "active" if layout.stall_count > 0 else "active_failed"
    if _boolean_opt(site.optimization.get("enable_t_end_caps", False)):
        if not layout:
            return "available"
        has_end = any(stall.aisle_side == "end" for stall in layout.stalls)
        return "active" if has_end else "available"
    return "available"


def _t_end_maneuver_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if _has_active_stall_family(site, layout, "t_end") or _boolean_opt(
        site.optimization.get("enable_t_end_caps", False)
    ):
        return _maneuver_status(layout)
    return "available"


def _boolean_opt(raw: object) -> bool:
    return boolean_opt(raw)


def _has_active_stall_family(site: SiteSpec, layout: LayoutResult | None, family: str) -> bool:
    active_site = layout.site if layout else site
    return any(
        stall.family == family
        for stall in (active_site.main_stall, active_site.branch_stall, active_site.stall)
        if stall is not None
    )
