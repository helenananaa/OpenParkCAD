from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import math

from shapely import affinity
from shapely.geometry import LineString, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import (
    area_overlaps,
    available_area,
    branch_aisle_polygon,
    branch_turnaround_polygon,
    branch_with_turnaround,
    entrance_throat_polygon,
    main_aisle_polygon,
    main_aisle_with_turnaround,
    normalized_local_box_to_world,
    polygon_points,
    turnaround_polygon,
)
from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.phase1_support import (
    aisle_directionality,
    angled_module_angle,
    boolean_opt as _boolean_opt,
    entry_capable_entrances,
    exit_capable_entrances,
    module_angle_allowed,
    passing_bay_synthesis_enabled,
    phase1_unsupported_inputs,
    supports_phase1_aisle,
    supports_phase1_stall,
)


FinalizeLayout = Callable[[LayoutResult], LayoutResult]
LayoutValid = Callable[[LayoutResult], bool]
LayoutScoreTotal = Callable[[LayoutResult], float]


@dataclass(frozen=True)
class Phase1Candidate:
    layout: LayoutResult
    entrance_id: str | None
    heading_degrees: float
    heading_delta_degrees: float
    entrance_offset: float
    branch_candidates: list[dict[str, object]]
    aisle_lateral_offset: float = 0.0


@dataclass(frozen=True)
class ConnectorGeometry:
    polygon: object
    u_min: float
    u_max: float
    center_v: float
    side: str
    inset_depth: float
    pattern: str = "same_side_u"
    v_min: float = 0.0
    v_max: float = 0.0


def iter_phase1_candidates(
    site: SiteSpec,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    context_sink: list | None = None,
) -> list[Phase1Candidate]:
    candidates: list[Phase1Candidate] = []
    for entrance in entry_capable_entrances(site):
        for heading_delta in _heading_deltas(site):
            heading = entrance.heading_degrees + heading_delta
            for offset in _entrance_offsets(site, entrance):
                offset_entrance = _offset_entrance(entrance, offset)
                for lateral in _aisle_lateral_offsets(site):
                    layout, branch_candidates = _generate_for_entrance(
                        site,
                        offset_entrance,
                        heading,
                        heading_delta,
                        offset,
                        finalize_layout,
                        layout_valid,
                        score_total,
                        aisle_lateral_offset=lateral,
                        context_sink=context_sink,
                    )
                    candidates.append(
                        Phase1Candidate(
                            layout=layout,
                            entrance_id=entrance.id,
                            heading_degrees=heading,
                            heading_delta_degrees=heading_delta,
                            entrance_offset=offset,
                            branch_candidates=branch_candidates,
                            aisle_lateral_offset=lateral,
                        )
                    )
    return candidates


def contexts_from_collected_records(records: list) -> list:
    """Turn phase-1 sink records into isolated layout candidate contexts."""
    from openparkcad.layout_candidates import context_from_layout

    contexts = []
    for record in records:
        layout = record["layout"]
        status = "collected"
        reason = None
        if not layout.aisles:
            status = "rejected"
            reason = "no_road_geometry"
        elif layout.stall_count <= 0:
            status = "requires_layout_rebuild"
            reason = "insufficient_stalls"
        contexts.append(
            context_from_layout(
                layout,
                source=dict(record.get("source") or {}),
                branch_diagnostics=list(record.get("branch_diagnostics") or []),
                collect_status=status,
                reject_reason=reason,
            )
        )
    return contexts


def collect_phase1_candidate_contexts(
    site: SiteSpec,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
) -> list:
    """Collect complete spine contexts before local ranking discards them."""
    sink: list[dict[str, object]] = []
    iter_phase1_candidates(site, finalize_layout, layout_valid, score_total, context_sink=sink)
    return contexts_from_collected_records(sink)


def _record_collected_spine(
    context_sink: list | None,
    layout: LayoutResult,
    *,
    family: str,
    aisle_lateral_offset: float,
    dogleg_offset: float | None,
    entrance_id: str | None,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    branch_diagnostics: list | None = None,
) -> None:
    if context_sink is None:
        return
    from openparkcad.layout_candidates import copy_layout

    context_sink.append(
        {
            "layout": copy_layout(layout),
            "source": {
                "family": family,
                "aisle_lateral_offset": float(aisle_lateral_offset),
                "dogleg_offset": None if dogleg_offset is None else float(dogleg_offset),
                "entrance_id": entrance_id,
                "heading_degrees": heading_degrees,
                "heading_delta_degrees": heading_delta_degrees,
                "entrance_offset": entrance_offset,
            },
            "branch_diagnostics": list(branch_diagnostics or []),
        }
    )


def _generate_for_entrance(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    aisle_lateral_offset: float = 0.0,
    context_sink: list | None = None,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    if not supports_phase1_aisle(site) or not supports_phase1_stall(site) or entrance.width + 1e-9 < site.aisle_width:
        return _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset, finalize_layout), []

    # Geometry frame may be shifted laterally; the real entrance stays for graph contact via throat.
    geom_entrance = _lateral_shift_entrance(entrance, heading_degrees, aisle_lateral_offset)
    available = available_area(site)
    start = max(site.margin, 0.0)
    max_extent = _max_clear_aisle_length(site, available, geom_entrance, heading_degrees, start)
    min_length = start + site.aisle_width * 2
    t_end_spec = _t_end_stall_spec(site, role="main")
    # Always reserve end-bay depth when t_end placement is active so caps fit
    # beyond the turnaround instead of being clipped by max aisle extent.
    aisle_length = _aisle_length_with_t_end_reserve(
        max_extent,
        min_length,
        t_end_spec,
        force_reserve=t_end_spec is not None,
        envelope_clearance=_t_end_envelope_clearance(site),
    )
    if aisle_length <= min_length:
        dogleg = _generate_dogleg_for_entrance(
            site,
            entrance,
            geom_entrance,
            heading_degrees,
            heading_delta_degrees,
            entrance_offset,
            available,
            start,
            finalize_layout,
            layout_valid,
            score_total,
            aisle_lateral_offset=aisle_lateral_offset,
            context_sink=context_sink,
        )
        if dogleg is not None:
            return dogleg
        empty = _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset, finalize_layout)
        _record_collected_spine(
            context_sink,
            empty,
            family="straight",
            aisle_lateral_offset=aisle_lateral_offset,
            dogleg_offset=None,
            entrance_id=entrance.id,
            heading_degrees=heading_degrees,
            heading_delta_degrees=heading_delta_degrees,
            entrance_offset=entrance_offset,
        )
        return empty, []

    main_body = main_aisle_polygon(site, geom_entrance, heading_degrees, start, aisle_length)
    turnaround = turnaround_polygon(site, geom_entrance, heading_degrees, aisle_length)
    throat = entrance_throat_polygon(site, entrance, geom_entrance, heading_degrees, start)
    parts = [main_body, turnaround]
    if not throat.is_empty:
        parts.append(throat)
    combined = unary_union(parts)
    if not _geometry_fits(available, combined):
        dogleg = _generate_dogleg_for_entrance(
            site,
            entrance,
            geom_entrance,
            heading_degrees,
            heading_delta_degrees,
            entrance_offset,
            available,
            start,
            finalize_layout,
            layout_valid,
            score_total,
            aisle_lateral_offset=aisle_lateral_offset,
            context_sink=context_sink,
        )
        if dogleg is not None:
            return dogleg
        empty = _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset, finalize_layout)
        _record_collected_spine(
            context_sink,
            empty,
            family="straight",
            aisle_lateral_offset=aisle_lateral_offset,
            dogleg_offset=None,
            entrance_id=entrance.id,
            heading_degrees=heading_degrees,
            heading_delta_degrees=heading_delta_degrees,
            entrance_offset=entrance_offset,
        )
        return empty, []
    main_aisle = unary_union([main_body, throat]) if not throat.is_empty else main_body
    aisles = [
        ParkingAisle(
            id="A-MAIN",
            polygon=polygon_points(main_aisle),
            angle_degrees=heading_degrees,
            role="main",
            connected_to_entrance_id=entrance.id,
            directionality=aisle_directionality(site),
        ),
        ParkingAisle(
            id="A-TURNAROUND",
            polygon=polygon_points(turnaround),
            angle_degrees=heading_degrees,
            role="turnaround",
            parent_aisle_id="A-MAIN",
            directionality=aisle_directionality(site),
        ),
    ]
    exit_aisle = _build_exit_aisle(
        site,
        available,
        entry_entrance=entrance,
        geom_entrance=geom_entrance,
        heading_degrees=heading_degrees,
        aisle_length=aisle_length,
        turnaround=turnaround,
    )
    if exit_aisle is not None:
        aisles.append(exit_aisle)
    side_stalls = _stalls_along_main_aisle(
        site,
        available,
        geom_entrance,
        heading_degrees,
        start_u=site.aisle_width,
        end_u=aisle_length - site.aisle_width,
    )
    end_stalls = _t_end_stalls_for_main(
        site,
        available,
        geom_entrance,
        heading_degrees,
        aisle_length=aisle_length,
        start_index=len(side_stalls) + 1,
        occupied=unary_union([main_aisle, turnaround] + [ShapelyPolygon(s.polygon) for s in side_stalls]),
    )
    stalls = _renumber_stalls(side_stalls + end_stalls)

    base = finalize_layout(
        LayoutResult(
            site=site,
            stalls=stalls,
            aisles=aisles,
            selected_angle_degrees=heading_degrees,
            generation_mode="phase1_main_aisle",
            main_entrance_id=entrance.id,
            selected_heading_degrees=heading_degrees,
            selected_heading_delta_degrees=heading_delta_degrees,
            selected_entrance_offset=entrance_offset,
            unsupported_phase1_inputs=phase1_unsupported_inputs(site),
        )
    )
    branched = _with_best_branch(
        site,
        available,
        geom_entrance,
        heading_degrees,
        base,
        aisle_length,
        finalize_layout,
        layout_valid,
        score_total,
    )
    straight_layout, straight_diags = branched
    straight_layout = _with_passing_bays(
        site,
        available,
        geom_entrance,
        heading_degrees,
        straight_layout,
        spine_segments=(("A-MAIN", 0.0, float(aisle_length), 0.0),),
        finalize_layout=finalize_layout,
        layout_valid=layout_valid,
    )
    _record_collected_spine(
        context_sink,
        straight_layout,
        family="straight",
        aisle_lateral_offset=aisle_lateral_offset,
        dogleg_offset=None,
        entrance_id=entrance.id,
        heading_degrees=heading_degrees,
        heading_delta_degrees=heading_delta_degrees,
        entrance_offset=entrance_offset,
        branch_diagnostics=straight_diags,
    )
    # If the straight spine is short because of obstacles, a dogleg may recover depth.
    if _dogleg_enabled(site) and site.obstacles:
        dogleg = _generate_dogleg_for_entrance(
            site,
            entrance,
            geom_entrance,
            heading_degrees,
            heading_delta_degrees,
            entrance_offset,
            available,
            start,
            finalize_layout,
            layout_valid,
            score_total,
            aisle_lateral_offset=aisle_lateral_offset,
            context_sink=context_sink,
        )
        if dogleg is not None:
            dogleg_layout, dogleg_diags = dogleg
            if layout_valid(dogleg_layout) and (
                not layout_valid(straight_layout) or score_total(dogleg_layout) > score_total(straight_layout)
            ):
                return dogleg_layout, dogleg_diags + straight_diags
    return straight_layout, straight_diags


def _dogleg_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get("enable_main_aisle_dogleg")
    if raw is None:
        return bool(site.obstacles) and bool(site.optimization.get("prefer_obstacle_clearance", False))
    return _boolean_opt(raw)


def _with_passing_bays(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    layout: LayoutResult,
    *,
    spine_segments: tuple[tuple[str, float, float, float], ...],
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
) -> LayoutResult:
    """Attach optional side-pocket passing bays along main spine segments.

    Each segment is ``(parent_aisle_id, start_u, end_u, v_center)`` in the
    entrance local frame. Bays are recorded as ``role=passing_bay`` aisles and
    as ``site_features`` so Phase 5Q can count them.
    """
    if not passing_bay_synthesis_enabled(site):
        return layout
    if layout.stall_count <= 0 and not layout.aisles:
        return layout

    bay_length = _passing_bay_length(site)
    bay_width = _passing_bay_width(site)
    spacing = _passing_bay_spacing(site)
    min_bays = _passing_bay_min_count(site)
    half = site.aisle_width / 2

    # Hard conflicts: non-parent aisles/turnarounds/exits (not stalls — bays may
    # displace a few stalls and are re-checked after placement).
    foreign_aisles = [
        ShapelyPolygon(aisle.polygon)
        for aisle in layout.aisles
        if aisle.role not in {"main", "passing_bay"}
        or (aisle.role == "main" and aisle.id not in {seg[0] for seg in spine_segments})
    ]
    foreign = unary_union(foreign_aisles) if foreign_aisles else ShapelyPolygon()
    bay_aisles: list[ParkingAisle] = []
    bay_features: list[dict[str, object]] = []
    bay_polys = []
    index = 1
    for parent_id, start_u, end_u, v_center in spine_segments:
        span = float(end_u) - float(start_u)
        if span < bay_length + site.aisle_width:
            continue
        # Keep clear of turnaround / entrance throats.
        u_lo = float(start_u) + site.aisle_width
        u_hi = float(end_u) - site.aisle_width - bay_length
        if u_hi <= u_lo:
            continue
        positions = _passing_bay_positions(u_lo, u_hi, bay_length, spacing)
        for u0 in positions:
            side = "left" if index % 2 else "right"
            direction = 1.0 if side == "left" else -1.0
            outer = float(v_center) + direction * (half + bay_width)
            inner = float(v_center) + direction * half
            v0, v1 = sorted((inner, outer))
            pocket = normalized_local_box_to_world(
                (u0, v0, u0 + bay_length, v1),
                entrance,
                heading_degrees,
            )
            if not _geometry_fits(available, pocket):
                continue
            if not foreign.is_empty and area_overlaps(pocket, foreign):
                continue
            if any(area_overlaps(pocket, existing) for existing in bay_polys):
                continue
            bay_id = f"A-PASS-{index:03d}"
            feature_id = f"passing-bay-{index:03d}"
            points = polygon_points(pocket)
            bay_aisles.append(
                ParkingAisle(
                    id=bay_id,
                    polygon=points,
                    angle_degrees=heading_degrees,
                    role="passing_bay",
                    parent_aisle_id=parent_id,
                    directionality=aisle_directionality(site),
                )
            )
            bay_features.append(
                {
                    "id": feature_id,
                    "type": "passing_bay",
                    "aisle_id": parent_id,
                    "side": side,
                    "width": float(bay_width),
                    "length": float(bay_length),
                    "geometry": {"type": "polygon", "points": [list(p) for p in points]},
                    "source": "synthesized",
                }
            )
            bay_polys.append(pocket)
            index += 1

    if not bay_aisles:
        return layout
    if min_bays is not None and len(bay_aisles) < min_bays:
        # Still keep whatever bays fit; operational quality reports shortage.
        pass

    bay_union = unary_union(bay_polys)
    kept_stalls = [
        stall for stall in layout.stalls if not area_overlaps(ShapelyPolygon(stall.polygon), bay_union)
    ]
    kept_stalls = _renumber_stalls(kept_stalls)
    existing_features = [item for item in layout.site.site_features if isinstance(item, dict)]
    new_site = replace(
        layout.site,
        site_features=existing_features + bay_features,
    )
    # Rebuild with updated site/features so Phase 5Q sees usable bay markers.
    updated = replace(
        layout,
        site=new_site,
        stalls=kept_stalls,
        aisles=list(layout.aisles) + bay_aisles,
    )
    result = finalize_layout(updated)
    if layout_valid(result) or not layout_valid(layout):
        return result
    return layout


def _passing_bay_length(site: SiteSpec) -> float:
    raw = site.optimization.get("passing_bay_length")
    if raw is not None:
        try:
            return max(float(raw), site.aisle_width)
        except (TypeError, ValueError):
            pass
    vehicle_len = float(site.vehicle.length) if site.vehicle is not None else 5.0
    return max(6.0, vehicle_len + 1.0)


def _passing_bay_width(site: SiteSpec) -> float:
    raw = site.optimization.get("passing_bay_width")
    if raw is not None:
        try:
            return max(float(raw), 1.5)
        except (TypeError, ValueError):
            pass
    vehicle_w = float(site.vehicle.width) if site.vehicle is not None else 1.9
    return max(2.0, vehicle_w + 0.4)


def _passing_bay_spacing(site: SiteSpec) -> float:
    raw = site.optimization.get("passing_bay_spacing")
    if raw is None:
        raw = site.optimization.get("operational_max_passing_bay_spacing")
    if raw is not None:
        try:
            return max(float(raw), site.aisle_width * 2)
        except (TypeError, ValueError):
            pass
    return max(28.0, site.aisle_width * 6)


def _passing_bay_min_count(site: SiteSpec) -> int | None:
    raw = site.optimization.get("operational_min_passing_bays")
    if raw is None:
        return None
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return None


def _passing_bay_positions(u_lo: float, u_hi: float, bay_length: float, spacing: float) -> list[float]:
    if u_hi <= u_lo:
        return []
    positions: list[float] = []
    # First bay near the start of the clear run, then every spacing.
    u = u_lo
    while u <= u_hi + 1e-9:
        positions.append(round(u, 6))
        u += max(spacing, bay_length + 1.0)
    # Ensure a bay near the far end when the run is long enough for two+.
    far = round(u_hi, 6)
    if not positions or far - positions[-1] > spacing * 0.5:
        if far not in positions:
            positions.append(far)
    return sorted(set(positions))


def _generate_dogleg_for_entrance(
    site: SiteSpec,
    entrance: EntranceSpec,
    geom_entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    available,
    start: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    aisle_lateral_offset: float = 0.0,
    context_sink: list | None = None,
) -> tuple[LayoutResult, list[dict[str, object]]] | None:
    """Build a centerline-front + lateral jog + offset-rear main aisle around obstacles.

    Keeps the real entrance on centerline, then jogs around a blocking obstacle and
    continues on a parallel rear segment with its own turnaround.
    """
    if not _dogleg_enabled(site) or not site.obstacles:
        return None
    # Dogleg is defined in the unshifted entry frame so the entrance stays on-spine.
    frame = entrance
    width = site.aisle_width
    min_front = start + width
    center_max = _max_clear_aisle_length(site, available, frame, heading_degrees, start)
    if center_max < min_front:
        # Even a short front stub is blocked; cannot dogleg from entrance.
        return None

    best: LayoutResult | None = None
    best_diag: list[dict[str, object]] = []
    offsets = _dogleg_offset_candidates(site, frame, heading_degrees)
    for offset in offsets:
        if abs(offset) <= 1e-9:
            continue
        # Keep the lateral jog fully in the still-clear front band so it does not
        # cut through the blocking obstacle that ends the centerline.
        break_u = max(min_front, center_max - width)
        if break_u + width / 2 > center_max + 1e-6:
            break_u = max(min_front, center_max - width / 2)
        rear_end = _max_clear_offset_aisle_length(
            site, available, frame, heading_degrees, break_u, offset
        )
        if rear_end < break_u + width * 2:
            continue
        t_end_spec = _t_end_stall_spec(site, role="main")
        rear_end = _aisle_length_with_t_end_reserve(
            rear_end,
            break_u + width * 2,
            t_end_spec,
            force_reserve=t_end_spec is not None,
            envelope_clearance=_t_end_envelope_clearance(site),
        )
        if rear_end < break_u + width * 2:
            continue
        front = normalized_local_box_to_world(
            (start, -width / 2, break_u, width / 2), frame, heading_degrees
        )
        # Thin lateral corridor at break_u (width along u, span along v).
        v0, v1 = sorted((0.0, offset))
        jog = normalized_local_box_to_world(
            (break_u - width / 2, v0 - width / 2, break_u + width / 2, v1 + width / 2),
            frame,
            heading_degrees,
        )
        rear = normalized_local_box_to_world(
            (break_u, offset - width / 2, rear_end, offset + width / 2),
            frame,
            heading_degrees,
        )
        turnaround = normalized_local_box_to_world(
            (rear_end - width, offset - width, rear_end, offset + width),
            frame,
            heading_degrees,
        )
        combined = unary_union([front, jog, rear, turnaround])
        if not _geometry_fits(available, combined):
            continue

        directionality = aisle_directionality(site)
        aisles = [
            ParkingAisle(
                id="A-MAIN",
                polygon=polygon_points(front),
                angle_degrees=heading_degrees,
                role="main",
                connected_to_entrance_id=entrance.id,
                directionality=directionality,
            ),
            ParkingAisle(
                id="A-JOG",
                polygon=polygon_points(jog),
                angle_degrees=heading_degrees,
                role="jog",
                parent_aisle_id="A-MAIN",
                directionality=directionality,
            ),
            ParkingAisle(
                id="A-MAIN-REAR",
                polygon=polygon_points(rear),
                angle_degrees=heading_degrees,
                role="main",
                parent_aisle_id="A-JOG",
                directionality=directionality,
            ),
            ParkingAisle(
                id="A-TURNAROUND",
                polygon=polygon_points(turnaround),
                angle_degrees=heading_degrees,
                role="turnaround",
                parent_aisle_id="A-MAIN-REAR",
                directionality=directionality,
            ),
        ]
        exit_aisle = _build_exit_aisle(
            site,
            available,
            entry_entrance=entrance,
            geom_entrance=frame,
            heading_degrees=heading_degrees,
            aisle_length=rear_end,
            turnaround=turnaround,
        )
        if exit_aisle is not None:
            # Parent the exit off the dogleg turnaround (already default).
            aisles.append(exit_aisle)

        front_stalls = _stalls_along_main_aisle(
            site,
            available,
            frame,
            heading_degrees,
            start_u=start + width * 0.5,
            end_u=break_u - width * 0.5,
            served_by_aisle_id="A-MAIN",
            v_center=0.0,
        )
        rear_stalls = _stalls_along_main_aisle(
            site,
            available,
            frame,
            heading_degrees,
            start_u=break_u + width * 0.5,
            end_u=rear_end - width * 0.5,
            served_by_aisle_id="A-MAIN-REAR",
            v_center=offset,
        )
        occupied = unary_union(
            [ShapelyPolygon(aisle.polygon) for aisle in aisles]
            + [ShapelyPolygon(stall.polygon) for stall in front_stalls + rear_stalls]
        )
        end_stalls = _t_end_stalls_for_main(
            site,
            available,
            frame,
            heading_degrees,
            aisle_length=rear_end,
            start_index=len(front_stalls) + len(rear_stalls) + 1,
            occupied=occupied,
            v_center=offset,
        )
        stalls = _renumber_stalls(front_stalls + rear_stalls + end_stalls)
        base = finalize_layout(
            LayoutResult(
                site=site,
                stalls=stalls,
                aisles=aisles,
                selected_angle_degrees=heading_degrees,
                generation_mode="phase1_main_aisle_dogleg",
                main_entrance_id=entrance.id,
                selected_heading_degrees=heading_degrees,
                selected_heading_delta_degrees=heading_delta_degrees,
                selected_entrance_offset=entrance_offset,
                unsupported_phase1_inputs=phase1_unsupported_inputs(site),
            )
        )
        diagnostic = {
            "reason": "dogleg_candidate",
            "dogleg_offset": float(offset),
            "break_u": float(break_u),
            "rear_end": float(rear_end),
            "stall_count": base.stall_count,
            "graph_valid": bool(base.graph_validation.get("valid", False)),
        }
        if not layout_valid(base):
            diagnostic["reason"] = "dogleg_invalid"
            best_diag.append(diagnostic)
            if base.aisles:
                _record_collected_spine(
                    context_sink,
                    base,
                    family="dogleg",
                    aisle_lateral_offset=aisle_lateral_offset,
                    dogleg_offset=float(offset),
                    entrance_id=entrance.id,
                    heading_degrees=heading_degrees,
                    heading_delta_degrees=heading_delta_degrees,
                    entrance_offset=entrance_offset,
                    branch_diagnostics=[diagnostic],
                )
            continue
        # Score-positive branches on front (centerline) and rear (offset) spines,
        # sharing one max_branches budget; connectors are applied per parent spine.
        layout, branch_diags = _with_dogleg_branches(
            site,
            available,
            frame,
            heading_degrees,
            base,
            break_u=break_u,
            rear_end=rear_end,
            offset=offset,
            finalize_layout=finalize_layout,
            layout_valid=layout_valid,
            score_total=score_total,
        )
        layout = _with_passing_bays(
            site,
            available,
            frame,
            heading_degrees,
            layout,
            spine_segments=(
                ("A-MAIN", float(start), float(break_u), 0.0),
                ("A-MAIN-REAR", float(break_u), float(rear_end), float(offset)),
            ),
            finalize_layout=finalize_layout,
            layout_valid=layout_valid,
        )
        diagnostic["stall_count"] = layout.stall_count
        diagnostic["graph_valid"] = bool(layout.graph_validation.get("valid", False))
        diagnostic["branch_count"] = len(layout.selected_branches)
        diagnostic["branch_diagnostics"] = branch_diags
        if not layout_valid(layout):
            diagnostic["reason"] = "dogleg_branch_invalid"
            # Fall back to the spine-only dogleg if branches break validity.
            layout = base
            diagnostic["stall_count"] = layout.stall_count
            diagnostic["graph_valid"] = bool(layout.graph_validation.get("valid", False))
            diagnostic["branch_count"] = 0
        _record_collected_spine(
            context_sink,
            layout,
            family="dogleg",
            aisle_lateral_offset=aisle_lateral_offset,
            dogleg_offset=float(offset),
            entrance_id=entrance.id,
            heading_degrees=heading_degrees,
            heading_delta_degrees=heading_delta_degrees,
            entrance_offset=entrance_offset,
            branch_diagnostics=list(branch_diags) if isinstance(branch_diags, list) else [diagnostic],
        )
        if best is None or score_total(layout) > score_total(best):
            best = layout
            best_diag = [diagnostic]
        else:
            best_diag.append(diagnostic)

    # Multi-jog: chain additional lateral jogs when a second obstacle blocks the
    # first offset rear spine (staggered obstacles, alternating clear corridors).
    multi = _generate_multi_jog_layout(
        site,
        entrance,
        frame,
        heading_degrees,
        heading_delta_degrees,
        entrance_offset,
        available,
        start,
        finalize_layout,
        layout_valid,
        score_total,
        aisle_lateral_offset=aisle_lateral_offset,
        context_sink=context_sink,
    )
    if multi is not None:
        multi_layout, multi_diag = multi
        _record_collected_spine(
            context_sink,
            multi_layout,
            family="multi_jog",
            aisle_lateral_offset=aisle_lateral_offset,
            dogleg_offset=None,
            entrance_id=entrance.id,
            heading_degrees=heading_degrees,
            heading_delta_degrees=heading_delta_degrees,
            entrance_offset=entrance_offset,
            branch_diagnostics=[multi_diag] if isinstance(multi_diag, dict) else list(multi_diag or []),
        )
        if layout_valid(multi_layout) and (
            best is None or score_total(multi_layout) > score_total(best)
        ):
            best = multi_layout
            best_diag = [multi_diag] + best_diag

    if best is None:
        return None
    return best, best_diag


def _max_dogleg_jogs(site: SiteSpec) -> int:
    raw = site.optimization.get("max_dogleg_jogs", 3)
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 3


def _max_clear_strip_end(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    v_center: float,
) -> float:
    """Longest strip end_u at ``v_center`` (no turnaround requirement)."""
    width = site.aisle_width
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(_module_step_width(site), 0.5)
    best = start_u
    length = start_u + width
    while length <= diagonal * 1.5:
        strip = normalized_local_box_to_world(
            (start_u, v_center - width / 2, length, v_center + width / 2),
            entrance,
            heading_degrees,
        )
        if not _geometry_fits(available, strip):
            break
        best = length
        length += step
    low = best
    high = min(max(best + step, length), diagonal * 1.5)
    for _ in range(16):
        mid = (low + high) / 2
        strip = normalized_local_box_to_world(
            (start_u, v_center - width / 2, mid, v_center + width / 2),
            entrance,
            heading_degrees,
        )
        if _geometry_fits(available, strip):
            low = mid
        else:
            high = mid
    return low


def _jog_geometry_fits(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    break_u: float,
    v0: float,
    v1: float,
) -> bool:
    width = site.aisle_width
    va, vb = sorted((v0, v1))
    jog = normalized_local_box_to_world(
        (break_u - width / 2, va - width / 2, break_u + width / 2, vb + width / 2),
        entrance,
        heading_degrees,
    )
    return _geometry_fits(available, jog)


def _plan_multi_jog_spine(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]] | None:
    """Plan main segments ``(u0, u1, v)`` and jogs ``(break_u, v_from, v_to)``."""
    width = site.aisle_width
    max_jogs = _max_dogleg_jogs(site)
    offset_choices = [0.0, *_dogleg_offset_candidates(site, entrance, heading_degrees)]
    # unique, preserve order preference by |v| small first for center start
    seen: set[float] = set()
    offsets: list[float] = []
    for value in offset_choices:
        key = round(value, 6)
        if key in seen:
            continue
        seen.add(key)
        offsets.append(float(value))

    current_v = 0.0
    current_u = float(start)
    mains: list[tuple[float, float, float]] = []
    jogs: list[tuple[float, float, float]] = []

    for step in range(max_jogs + 1):
        strip_end = _max_clear_strip_end(
            site, available, entrance, heading_degrees, current_u, current_v
        )
        # Prefer terminal ends that also fit a turnaround when this is the last segment.
        terminal_end = _max_clear_offset_aisle_length(
            site, available, entrance, heading_degrees, current_u, current_v
        )
        if strip_end < current_u + width:
            break

        can_jog = step < max_jogs
        break_u = max(current_u + width, strip_end - width)
        if break_u + width / 2 > strip_end + 1e-6:
            break_u = max(current_u + width, strip_end - width / 2)

        best_jump: tuple[float, float] | None = None
        if can_jog and strip_end > current_u + width:
            for next_v in offsets:
                if abs(next_v - current_v) < width * 0.5 - 1e-9:
                    continue
                if not _jog_geometry_fits(
                    site, available, entrance, heading_degrees, break_u, current_v, next_v
                ):
                    continue
                next_end = _max_clear_strip_end(
                    site, available, entrance, heading_degrees, break_u, next_v
                )
                next_terminal = _max_clear_offset_aisle_length(
                    site, available, entrance, heading_degrees, break_u, next_v
                )
                gain = max(next_end, next_terminal)
                if gain < break_u + width * 2:
                    continue
                # Only jog if it recovers clear depth beyond the current strip end.
                if gain <= strip_end + width * 0.5:
                    continue
                if best_jump is None or gain > best_jump[0]:
                    best_jump = (gain, next_v)

        if best_jump is None:
            end_u = max(terminal_end, strip_end)
            if end_u < current_u + width * 2 and not mains:
                return None
            if end_u < current_u + width:
                break
            mains.append((current_u, end_u, current_v))
            break

        mains.append((current_u, break_u, current_v))
        jogs.append((break_u, current_v, best_jump[1]))
        current_u = break_u
        current_v = best_jump[1]
    else:
        # Hit max jog iterations without a clean terminal break.
        end_u = _max_clear_offset_aisle_length(
            site, available, entrance, heading_degrees, current_u, current_v
        )
        if end_u >= current_u + width:
            mains.append((current_u, end_u, current_v))

    if len(mains) < 1 or len(jogs) < 1:
        return None
    # Multi-jog product requires at least two jogs; single-jog stays on classic path.
    if len(jogs) < 2:
        return None
    return mains, jogs


def _generate_multi_jog_layout(
    site: SiteSpec,
    entrance: EntranceSpec,
    frame: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    available,
    start: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    aisle_lateral_offset: float = 0.0,
    context_sink: list | None = None,
) -> tuple[LayoutResult, dict[str, object]] | None:
    plan = _plan_multi_jog_spine(site, available, frame, heading_degrees, start)
    if plan is None:
        return None
    mains, jogs = plan
    width = site.aisle_width
    t_end_spec = _t_end_stall_spec(site, role="main")
    last_u0, last_u1, last_v = mains[-1]
    mains[-1] = (
        last_u0,
        _aisle_length_with_t_end_reserve(
            last_u1,
            last_u0 + width * 2,
            t_end_spec,
            force_reserve=t_end_spec is not None,
            envelope_clearance=_t_end_envelope_clearance(site),
        ),
        last_v,
    )
    directionality = aisle_directionality(site)

    aisles: list[ParkingAisle] = []
    polys = []
    parent_id: str | None = None
    main_ids: list[str] = []
    spine_segments: list[tuple[str, float, float, float]] = []

    for index, (u0, u1, v_center) in enumerate(mains, start=1):
        if index == 1:
            aisle_id = "A-MAIN"
        elif index == len(mains):
            aisle_id = "A-MAIN-REAR"
        else:
            aisle_id = f"A-MAIN-{index:03d}"
        body = normalized_local_box_to_world(
            (u0, v_center - width / 2, u1, v_center + width / 2),
            frame,
            heading_degrees,
        )
        polys.append(body)
        aisles.append(
            ParkingAisle(
                id=aisle_id,
                polygon=polygon_points(body),
                angle_degrees=heading_degrees,
                role="main",
                connected_to_entrance_id=entrance.id if index == 1 else None,
                parent_aisle_id=parent_id,
                directionality=directionality,
            )
        )
        main_ids.append(aisle_id)
        spine_segments.append((aisle_id, float(u0), float(u1), float(v_center)))
        parent_id = aisle_id

        if index <= len(jogs):
            break_u, v_from, v_to = jogs[index - 1]
            va, vb = sorted((v_from, v_to))
            jog = normalized_local_box_to_world(
                (break_u - width / 2, va - width / 2, break_u + width / 2, vb + width / 2),
                frame,
                heading_degrees,
            )
            if not _geometry_fits(available, jog):
                return None
            polys.append(jog)
            jog_id = f"A-JOG-{index:03d}" if len(jogs) > 1 else "A-JOG"
            aisles.append(
                ParkingAisle(
                    id=jog_id,
                    polygon=polygon_points(jog),
                    angle_degrees=heading_degrees,
                    role="jog",
                    parent_aisle_id=aisle_id,
                    directionality=directionality,
                )
            )
            parent_id = jog_id

    last_u0, last_u1, last_v = mains[-1]
    if last_u1 < last_u0 + width * 2:
        return None
    turnaround = normalized_local_box_to_world(
        (last_u1 - width, last_v - width, last_u1, last_v + width),
        frame,
        heading_degrees,
    )
    combined = unary_union(polys + [turnaround])
    if not _geometry_fits(available, combined):
        return None
    last_main_id = main_ids[-1]
    aisles.append(
        ParkingAisle(
            id="A-TURNAROUND",
            polygon=polygon_points(turnaround),
            angle_degrees=heading_degrees,
            role="turnaround",
            parent_aisle_id=last_main_id,
            directionality=directionality,
        )
    )
    exit_aisle = _build_exit_aisle(
        site,
        available,
        entry_entrance=entrance,
        geom_entrance=frame,
        heading_degrees=heading_degrees,
        aisle_length=last_u1,
        turnaround=turnaround,
    )
    if exit_aisle is not None:
        aisles.append(exit_aisle)

    stalls: list[ParkingStall] = []
    for aisle_id, u0, u1, v_center in spine_segments:
        segment_stalls = _stalls_along_main_aisle(
            site,
            available,
            frame,
            heading_degrees,
            start_u=u0 + width * 0.5,
            end_u=u1 - width * 0.5,
            served_by_aisle_id=aisle_id,
            v_center=v_center,
        )
        stalls.extend(segment_stalls)
    last_u0, last_u1, last_v = mains[-1]
    occupied = unary_union(polys + [ShapelyPolygon(stall.polygon) for stall in stalls])
    end_stalls = _t_end_stalls_for_main(
        site,
        available,
        frame,
        heading_degrees,
        aisle_length=last_u1,
        start_index=len(stalls) + 1,
        occupied=occupied,
        v_center=last_v,
    )
    stalls = _renumber_stalls(stalls + end_stalls)

    base = finalize_layout(
        LayoutResult(
            site=site,
            stalls=stalls,
            aisles=aisles,
            selected_angle_degrees=heading_degrees,
            generation_mode="phase1_main_aisle_multi_jog",
            main_entrance_id=entrance.id,
            selected_heading_degrees=heading_degrees,
            selected_heading_delta_degrees=heading_delta_degrees,
            selected_entrance_offset=entrance_offset,
            unsupported_phase1_inputs=phase1_unsupported_inputs(site),
        )
    )
    diagnostic: dict[str, object] = {
        "reason": "multi_jog_candidate",
        "jog_count": len(jogs),
        "main_segments": [
            {"id": aisle_id, "u0": u0, "u1": u1, "v": v}
            for aisle_id, u0, u1, v in spine_segments
        ],
        "stall_count": base.stall_count,
        "graph_valid": bool(base.graph_validation.get("valid", False)),
    }
    if not layout_valid(base):
        diagnostic["reason"] = "multi_jog_invalid"
        return None

    # Branches across all main spine segments under one max_branches budget.
    multi_spines: list[dict[str, object]] = []
    for aisle_id, u0, u1, v_center in spine_segments:
        span = float(u1) - float(u0)
        if span < width * 3:
            continue
        multi_spines.append(
            {
                "parent_aisle_id": aisle_id,
                "entrance": _lateral_shift_entrance(frame, heading_degrees, float(v_center)),
                "main_aisle_length": float(u1),
                "branch_u_min": float(u0) + width,
            }
        )
    layout, branch_diags = _with_multi_spine_branches(
        site,
        available,
        heading_degrees,
        base,
        multi_spines,
        finalize_layout=finalize_layout,
        layout_valid=layout_valid,
        score_total=score_total,
    )
    layout = _with_passing_bays(
        site,
        available,
        frame,
        heading_degrees,
        layout,
        spine_segments=tuple(spine_segments),
        finalize_layout=finalize_layout,
        layout_valid=layout_valid,
    )
    diagnostic["stall_count"] = layout.stall_count
    diagnostic["graph_valid"] = bool(layout.graph_validation.get("valid", False))
    diagnostic["branch_count"] = len(layout.selected_branches)
    diagnostic["branch_diagnostics"] = branch_diags
    if not layout_valid(layout):
        layout = base
        diagnostic["stall_count"] = layout.stall_count
        diagnostic["graph_valid"] = bool(layout.graph_validation.get("valid", False))
        diagnostic["branch_count"] = 0
    return layout, diagnostic


def _adaptive_dogleg_offsets_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get("enable_adaptive_dogleg_offsets")
    if raw is None:
        # On when dogleg is obstacle-driven or clearance preference is set.
        return bool(site.obstacles) and (
            _dogleg_enabled(site) or bool(site.optimization.get("prefer_obstacle_clearance", False))
        )
    return _boolean_opt(raw)


def _dogleg_offset_candidates(
    site: SiteSpec,
    entrance: EntranceSpec | None = None,
    heading_degrees: float | None = None,
) -> tuple[float, ...]:
    """Lateral offset trials for dogleg / multi-jog spines.

    Combines explicit ``dogleg_offsets`` (or ±1/2 aisle widths) with optional
    adaptive offsets derived from obstacle envelopes in the entrance frame.
    """
    w = site.aisle_width
    values: list[float] = []
    raw = site.optimization.get("dogleg_offsets")
    if isinstance(raw, list):
        for item in raw:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                continue
    if not values:
        values.extend((-w, w, -2 * w, 2 * w))

    if (
        entrance is not None
        and heading_degrees is not None
        and _adaptive_dogleg_offsets_enabled(site)
        and site.obstacles
    ):
        values.extend(
            _adaptive_offsets_from_obstacles(site, entrance, float(heading_degrees))
        )

    # Keep finite, non-zero offsets within a generous site-span budget.
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    span = max(max_x - min_x, max_y - min_y, w * 4)
    cap = max(span * 0.55, w * 4)
    cleaned = {
        round(float(v), 6)
        for v in values
        if abs(float(v)) > 1e-9 and abs(float(v)) <= cap + 1e-9
    }
    if not cleaned:
        cleaned = {-w, w, -2 * w, 2 * w}
    return tuple(sorted(cleaned))


def _adaptive_offsets_from_obstacles(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
) -> tuple[float, ...]:
    """Suggest lateral offsets that clear obstacles blocking the entry spine."""
    half = site.aisle_width / 2
    margin = max(0.25, site.aisle_width * 0.15)
    # Obstacles that cross the centerline band within a near-field u range.
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    u_horizon = diagonal * 1.1

    suggested: list[float] = []
    for obstacle in site.obstacles:
        if len(obstacle) < 3:
            continue
        local_vs: list[float] = []
        local_us: list[float] = []
        for point in obstacle:
            u, v = _world_to_local((float(point[0]), float(point[1])), entrance, heading_degrees)
            local_us.append(u)
            local_vs.append(v)
        if not local_vs:
            continue
        u_min, u_max = min(local_us), max(local_us)
        v_min, v_max = min(local_vs), max(local_vs)
        # Ignore far-side or lateral-only obstacles that never hit the spine band.
        if u_max < site.aisle_width or u_min > u_horizon:
            continue
        if v_max < -half - margin or v_min > half + margin:
            continue
        # Clear to the left of the obstacle (positive v in our local frame).
        clear_left = v_max + half + margin
        clear_right = v_min - half - margin
        for candidate in (
            clear_left,
            clear_left + half,
            clear_right,
            clear_right - half,
        ):
            if abs(candidate) > half * 0.5:
                suggested.append(float(candidate))
    return tuple(suggested)


def _max_clear_offset_aisle_length(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    v_center: float,
) -> float:
    """Longest rear aisle end_u for a parallel strip centered at ``v_center``."""
    width = site.aisle_width
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(_module_step_width(site), 0.5)
    best = start_u
    length = start_u + width * 2
    # Grow the offset strip even through zones where a temporary end-turnaround
    # would not fit; only require strip+turnaround for accepted terminal lengths.
    while length <= diagonal * 1.5:
        strip = normalized_local_box_to_world(
            (start_u, v_center - width / 2, length, v_center + width / 2),
            entrance,
            heading_degrees,
        )
        if not _geometry_fits(available, strip):
            break
        turn = normalized_local_box_to_world(
            (length - width, v_center - width, length, v_center + width),
            entrance,
            heading_degrees,
        )
        if _geometry_fits(available, unary_union([strip, turn])):
            best = length
        length += step
    # Refine the best terminal end found above.
    low = best
    high = min(max(best + step, length), diagonal * 1.5)
    for _ in range(16):
        mid = (low + high) / 2
        strip = normalized_local_box_to_world(
            (start_u, v_center - width / 2, mid, v_center + width / 2),
            entrance,
            heading_degrees,
        )
        turn = normalized_local_box_to_world(
            (mid - width, v_center - width, mid, v_center + width),
            entrance,
            heading_degrees,
        )
        if _geometry_fits(available, strip) and _geometry_fits(available, unary_union([strip, turn])):
            low = mid
        else:
            high = mid
    return low


def _empty_layout(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    finalize_layout: FinalizeLayout,
) -> LayoutResult:
    return finalize_layout(
        LayoutResult(
            site=site,
            stalls=[],
            selected_angle_degrees=heading_degrees,
            generation_mode="phase1_main_aisle",
            main_entrance_id=entrance.id,
            selected_heading_degrees=heading_degrees,
            selected_heading_delta_degrees=heading_delta_degrees,
            selected_entrance_offset=entrance_offset,
            unsupported_phase1_inputs=phase1_unsupported_inputs(site),
        )
    )


def place_main_family_stalls(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    end_u: float,
    *,
    served_by_aisle_id: str,
    v_center: float = 0.0,
    stall_gap_m: float = 0.0,
) -> list[ParkingStall]:
    """Place one stall family along a main/spine aisle (both sides)."""
    if isinstance(stall_gap_m, bool) or not math.isfinite(stall_gap_m) or stall_gap_m < 0:
        raise ValueError("stall_gap_m must be finite and nonnegative")
    if stall_gap_m and stall_spec.family != "perpendicular":
        raise ValueError("stall_gap_m currently supports perpendicular stalls only")
    probe = replace(site, stall=stall_spec, main_stall=stall_spec)
    return _stalls_along_main_aisle(
        probe,
        available,
        entrance,
        heading_degrees,
        start_u,
        end_u,
        served_by_aisle_id=served_by_aisle_id,
        v_center=v_center,
        stall_gap_m=stall_gap_m,
    )


def _stalls_along_main_aisle(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    end_u: float,
    *,
    served_by_aisle_id: str = "A-MAIN",
    v_center: float = 0.0,
    stall_gap_m: float = 0.0,
) -> list[ParkingStall]:
    stall_spec = _main_stall(site)
    if stall_spec.family == "t_end":
        # T-end family uses only end-cap bays; side modules are empty by design.
        return []
    if stall_spec.family == "angled":
        if abs(v_center) > 1e-9:
            return []  # angled offset strip not implemented for dogleg rear
        return _angled_stalls_along_main_aisle(site, stall_spec, available, entrance, heading_degrees, start_u, end_u)
    if stall_spec.family == "parallel":
        if abs(v_center) > 1e-9:
            return _parallel_stalls_along_offset_main(
                site, stall_spec, available, entrance, heading_degrees, start_u, end_u, v_center, served_by_aisle_id
            )
        return _parallel_stalls_along_main_aisle(site, stall_spec, available, entrance, heading_degrees, start_u, end_u)
    if stall_spec.family != "perpendicular":
        return []

    stalls: list[ParkingStall] = []
    u = start_u
    half = site.aisle_width / 2
    while u + stall_spec.width <= end_u:
        if not module_angle_allowed(90.0, stall_spec.allowed_angles):
            break
        for side in ("left", "right"):
            if side == "left":
                local = (
                    u,
                    v_center + half,
                    u + stall_spec.width,
                    v_center + half + stall_spec.length,
                )
            else:
                local = (
                    u,
                    v_center - half - stall_spec.length,
                    u + stall_spec.width,
                    v_center - half,
                )
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=served_by_aisle_id,
                        aisle_side=side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.width + stall_gap_m
    return stalls


def _parallel_stalls_along_offset_main(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    end_u: float,
    v_center: float,
    served_by_aisle_id: str,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    u = start_u
    half = site.aisle_width / 2
    while u + stall_spec.length <= end_u + 1e-9:
        for side in ("left", "right"):
            if side == "left":
                local = (u, v_center + half, u + stall_spec.length, v_center + half + stall_spec.width)
            else:
                local = (u, v_center - half - stall_spec.width, u + stall_spec.length, v_center - half)
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{len(stalls) + 1:03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=served_by_aisle_id,
                        aisle_side=side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.length
    return stalls


def _parallel_stalls_along_main_aisle(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    start_u: float,
    end_u: float,
) -> list[ParkingStall]:
    """Place parallel stalls with length along the aisle and width as lateral depth."""
    stalls: list[ParkingStall] = []
    u = start_u
    while u + stall_spec.length <= end_u + 1e-9:
        for side in ("left", "right"):
            if side == "left":
                local = (u, site.aisle_width / 2, u + stall_spec.length, site.aisle_width / 2 + stall_spec.width)
            else:
                local = (u, -site.aisle_width / 2 - stall_spec.width, u + stall_spec.length, -site.aisle_width / 2)
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id="A-MAIN",
                        aisle_side=side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.length
    return stalls


def _angled_stalls_along_main_aisle(site: SiteSpec, stall_spec: StallSpec, available, entrance: EntranceSpec, heading_degrees: float, start_u: float, end_u: float) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []

    stalls: list[ParkingStall] = []
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    u = start_u
    while u + front_pitch + forward_shift <= end_u + 1e-9:
        for side in ("left", "right"):
            direction = 1 if side == "left" else -1
            front_v = direction * site.aisle_width / 2
            local_points = [
                (u, front_v),
                (u + front_pitch, front_v),
                (u + front_pitch + forward_shift, front_v + direction * lateral_depth),
                (u + forward_shift, front_v + direction * lateral_depth),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + direction * angle,
                        served_by_aisle_id="A-MAIN",
                        aisle_side=side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += front_pitch
    return stalls


def _with_dogleg_branches(
    site: SiteSpec,
    available,
    frame: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    *,
    break_u: float,
    rear_end: float,
    offset: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    """Attach branches to dogleg front and rear spines under one max_branches budget."""
    rear_frame = _lateral_shift_entrance(frame, heading_degrees, offset)
    spines = (
        {
            "parent_aisle_id": "A-MAIN",
            "entrance": frame,
            "main_aisle_length": break_u,
            "branch_u_min": None,
        },
        {
            "parent_aisle_id": "A-MAIN-REAR",
            "entrance": rear_frame,
            "main_aisle_length": rear_end,
            "branch_u_min": break_u + site.aisle_width,
        },
    )
    return _with_multi_spine_branches(
        site,
        available,
        heading_degrees,
        base,
        spines,
        finalize_layout=finalize_layout,
        layout_valid=layout_valid,
        score_total=score_total,
    )


def _with_multi_spine_branches(
    site: SiteSpec,
    available,
    heading_degrees: float,
    base: LayoutResult,
    spines: tuple[dict[str, object], ...] | list[dict[str, object]],
    *,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    """Attach score-positive branches across multiple parent spines under one budget.

    Each spine dict needs ``parent_aisle_id``, ``entrance`` (geometry frame),
    ``main_aisle_length``, and optional ``branch_u_min``.
    """
    if not site.optimization.get("enable_branches", True):
        return base, []
    if not spines:
        return base, []

    best = base
    branch_candidates: list[dict[str, object]] = []
    max_branches = _max_branches(site)
    for iteration in range(1, max_branches + 1):
        round_best = best
        round_best_score = _branch_selection_score(site, best, score_total, None)
        opposite_best = best
        opposite_best_score = round_best_score
        opposite_parent: str | None = None
        for spine in spines:
            parent_id = str(spine["parent_aisle_id"])
            existing_sides = {
                str(branch.get("side"))
                for branch in best.selected_branches
                if str(branch.get("parent_aisle_id", "A-MAIN")) == parent_id
            }
            prefer_opposite = (
                _boolean_opt(site.optimization.get("enable_opposite_end_loops", True))
                and len(existing_sides) == 1
            )
            opposite_side = None
            if prefer_opposite:
                opposite_side = "left" if "right" in existing_sides else "right"
            entrance = spine["entrance"]
            assert isinstance(entrance, EntranceSpec)
            for branch_u in _branch_start_positions(
                site,
                float(spine["main_aisle_length"]),
                min_u=spine.get("branch_u_min"),  # type: ignore[arg-type]
            ):
                for side in _branch_sides(site):
                    branch_index = _next_branch_index(best)
                    candidate, diagnostic = _branch_layout(
                        site,
                        available,
                        entrance,
                        heading_degrees,
                        best,
                        branch_u,
                        side,
                        branch_index,
                        finalize_layout,
                        layout_valid,
                        score_total,
                        parent_aisle_id=parent_id,
                    )
                    diagnostic["iteration"] = iteration
                    diagnostic["spine_parent"] = parent_id
                    branch_candidates.append(diagnostic)
                    if not candidate or not layout_valid(candidate):
                        continue
                    cand_score = _branch_selection_score(site, candidate, score_total, diagnostic)
                    if not layout_valid(round_best) or cand_score > round_best_score:
                        round_best = candidate
                        round_best_score = cand_score
                    if (
                        prefer_opposite
                        and side == opposite_side
                        and cand_score > _branch_selection_score(site, best, score_total, None)
                        and (opposite_best is best or cand_score > opposite_best_score)
                    ):
                        opposite_best = candidate
                        opposite_best_score = cand_score
                        opposite_parent = parent_id
        if opposite_best is not best and opposite_parent is not None:
            best = opposite_best
        elif round_best is not best:
            best = round_best
        else:
            break

    # Connectors must use the geometry frame of each parent spine.
    for spine in spines:
        parent_id = str(spine["parent_aisle_id"])
        entrance = spine["entrance"]
        assert isinstance(entrance, EntranceSpec)
        best = _with_best_connectors(
            site,
            available,
            entrance,
            heading_degrees,
            best,
            finalize_layout,
            layout_valid,
            score_total,
            branch_candidates,
            parent_aisle_id=parent_id,
        )
    return best, branch_candidates


def _with_best_branch(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    main_aisle_length: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    *,
    parent_aisle_id: str = "A-MAIN",
    branch_u_min: float | None = None,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    if not site.optimization.get("enable_branches", True):
        return base, []

    best = base
    branch_candidates: list[dict[str, object]] = []
    max_branches = _max_branches(site)
    for iteration in range(1, max_branches + 1):
        existing_sides = {
            str(branch.get("side"))
            for branch in best.selected_branches
            if str(branch.get("parent_aisle_id", "A-MAIN")) == parent_aisle_id
        }
        prefer_opposite = (
            _boolean_opt(site.optimization.get("enable_opposite_end_loops", True))
            and len(existing_sides) == 1
        )
        opposite_side = None
        if prefer_opposite:
            opposite_side = "left" if "right" in existing_sides else "right"

        round_best = best
        round_best_score = _branch_selection_score(site, best, score_total, None)
        opposite_best = best
        opposite_best_score = round_best_score
        for branch_u in _branch_start_positions(site, main_aisle_length, min_u=branch_u_min):
            for side in _branch_sides(site):
                branch_index = _next_branch_index(best)
                candidate, diagnostic = _branch_layout(
                    site,
                    available,
                    entrance,
                    heading_degrees,
                    best,
                    branch_u,
                    side,
                    branch_index,
                    finalize_layout,
                    layout_valid,
                    score_total,
                    parent_aisle_id=parent_aisle_id,
                )
                diagnostic["iteration"] = iteration
                branch_candidates.append(diagnostic)
                if not candidate or not layout_valid(candidate):
                    continue
                cand_score = _branch_selection_score(site, candidate, score_total, diagnostic)
                if not layout_valid(round_best) or cand_score > round_best_score:
                    round_best = candidate
                    round_best_score = cand_score
                if (
                    prefer_opposite
                    and side == opposite_side
                    and cand_score > _branch_selection_score(site, best, score_total, None)
                    and (opposite_best is best or cand_score > opposite_best_score)
                ):
                    opposite_best = candidate
                    opposite_best_score = cand_score
        # Prefer a score-improving opposite-side branch so outer end-loops can form.
        if prefer_opposite and opposite_best is not best:
            best = opposite_best
        elif round_best is not best:
            best = round_best
        else:
            break
    best = _with_best_connectors(
        site,
        available,
        entrance,
        heading_degrees,
        best,
        finalize_layout,
        layout_valid,
        score_total,
        branch_candidates,
        parent_aisle_id=parent_aisle_id,
    )
    return best, branch_candidates


def _branch_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_u: float,
    side: str,
    branch_index: int,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    *,
    parent_aisle_id: str = "A-MAIN",
) -> tuple[LayoutResult | None, dict[str, object]]:
    branch_id = f"A-BRANCH-{branch_index:03d}"
    clip = _branch_length_clip_report(site, available, entrance, heading_degrees, branch_u, side)
    max_branch_length = float(clip["clear_length"])
    t_end_spec = _t_end_stall_spec(site, role="branch")
    min_branch = site.aisle_width * 2
    branch_length = _aisle_length_with_t_end_reserve(
        max_branch_length,
        min_branch,
        t_end_spec,
        force_reserve=t_end_spec is not None,
        envelope_clearance=_t_end_envelope_clearance(site),
    )
    diagnostic: dict[str, object] = {
        "branch_id": branch_id,
        "side": side,
        "start_u": branch_u,
        "length": branch_length,
        "parent_aisle_id": parent_aisle_id,
        "base_stall_count": base.stall_count,
        "stall_count": None,
        "reason": "candidate_evaluated",
        "clear_length": clip["clear_length"],
        "open_boundary_length": clip["open_boundary_length"],
        "clip_amount": clip["clip_amount"],
        "clipped_by_exclusion": clip["clipped_by_exclusion"],
        "opposite_side_clear_length": clip["opposite_side_clear_length"],
        "prefer_side_hint": clip["prefer_side_hint"],
    }
    if branch_length <= min_branch:
        diagnostic["reason"] = (
            "branch_clipped_too_short_by_exclusion"
            if clip["clipped_by_exclusion"]
            else "branch_too_short_for_turnaround"
        )
        return None, diagnostic

    branch_aisle = branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_turnaround = branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_drivable = unary_union([branch_aisle, branch_turnaround])
    diagnostic["geometry"] = polygon_points(branch_drivable)
    diagnostic["branch_aisle_geometry"] = polygon_points(branch_aisle)
    diagnostic["branch_turnaround_geometry"] = polygon_points(branch_turnaround)
    diagnostic["branch_turnaround_id"] = f"{branch_id}-TURNAROUND"
    if not available.covers(branch_drivable):
        diagnostic["reason"] = "branch_geometry_outside_usable_area"
        return None, diagnostic
    conflict = _branch_conflict(base, branch_drivable, parent_aisle_id=parent_aisle_id)
    if conflict:
        diagnostic["reason"] = "branch_overlaps_existing_layout"
        diagnostic["conflict_id"] = conflict
        return None, diagnostic

    kept_main_stalls = [
        stall for stall in base.stalls if not area_overlaps(ShapelyPolygon(stall.polygon), branch_drivable)
    ]
    occupied = unary_union(
        [ShapelyPolygon(aisle.polygon) for aisle in base.aisles]
        + [branch_drivable]
        + [ShapelyPolygon(stall.polygon) for stall in kept_main_stalls]
    )
    branch_side_stalls = _stalls_along_branch(
        site,
        available,
        entrance,
        heading_degrees,
        branch_u,
        side,
        branch_id,
        start_t=site.aisle_width,
        end_t=branch_length - site.aisle_width,
        occupied=occupied,
        start_index=len(kept_main_stalls) + 1,
    )
    branch_end_stalls = _t_end_stalls_for_branch(
        site,
        available,
        entrance,
        heading_degrees,
        branch_u,
        side,
        branch_id,
        branch_length=branch_length,
        occupied=unary_union([occupied] + [ShapelyPolygon(s.polygon) for s in branch_side_stalls]),
        start_index=len(kept_main_stalls) + len(branch_side_stalls) + 1,
    )
    branch_stalls = branch_side_stalls + branch_end_stalls
    diagnostic["generated_stalls"] = _stall_diagnostics(branch_stalls)
    stalls = _renumber_stalls(kept_main_stalls + branch_stalls)
    aisles = list(base.aisles) + [
        ParkingAisle(
            id=branch_id,
            polygon=polygon_points(branch_aisle),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="branch",
            parent_aisle_id=parent_aisle_id,
            directionality=aisle_directionality(site),
        ),
        ParkingAisle(
            id=f"{branch_id}-TURNAROUND",
            polygon=polygon_points(branch_turnaround),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="turnaround",
            parent_aisle_id=branch_id,
            directionality=aisle_directionality(site),
        ),
    ]
    selected_branches = [
        *base.selected_branches,
        {
            "id": branch_id,
            "side": side,
            "start_u": branch_u,
            "length": branch_length,
            "parent_aisle_id": parent_aisle_id,
        },
    ]
    first_branch = selected_branches[0]
    result = finalize_layout(
        LayoutResult(
            site=site,
            stalls=stalls,
            aisles=aisles,
            selected_angle_degrees=base.selected_angle_degrees,
            generation_mode=base.generation_mode,
            main_entrance_id=base.main_entrance_id,
            selected_heading_degrees=base.selected_heading_degrees,
            selected_heading_delta_degrees=base.selected_heading_delta_degrees,
            selected_entrance_offset=base.selected_entrance_offset,
            selected_branch_side=str(first_branch["side"]),
            selected_branch_start_u=float(first_branch["start_u"]),
            selected_branch_length=float(first_branch["length"]),
            selected_branches=selected_branches,
            unsupported_phase1_inputs=base.unsupported_phase1_inputs,
        )
    )
    diagnostic["stall_count"] = result.stall_count
    graph_is_valid = bool(result.graph_validation.get("valid", False))
    maneuver_is_valid = bool(result.maneuver_validation.get("valid", False))
    operational_is_valid = bool(result.operational_quality.get("valid", False))
    layout_is_valid = layout_valid(result)
    diagnostic["graph_valid"] = graph_is_valid
    diagnostic["graph_errors"] = list(result.graph_validation.get("errors", []))
    diagnostic["maneuver_valid"] = maneuver_is_valid
    diagnostic["operational_valid"] = operational_is_valid
    diagnostic["operational_blockers"] = list(result.operational_quality.get("promotion_blockers", []))
    if not graph_is_valid:
        diagnostic["reason"] = "branch_invalid_traffic_graph"
        return result, diagnostic
    if not maneuver_is_valid:
        diagnostic["reason"] = "branch_invalid_maneuver_validation"
        return result, diagnostic
    if not layout_is_valid:
        diagnostic["reason"] = "branch_invalid_operational_quality"
        return result, diagnostic
    if score_total(result) <= score_total(base):
        diagnostic["reason"] = "branch_does_not_improve_score"
        return result, diagnostic
    diagnostic["reason"] = "branch_improves_stall_count"
    return result, diagnostic


def place_branch_family_stalls(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    branch_id: str,
    start_t: float,
    end_t: float,
    occupied,
    start_index: int = 1,
) -> list[ParkingStall]:
    """Place one stall family along a branch aisle (both stall sides)."""
    probe = replace(site, stall=stall_spec, branch_stall=stall_spec)
    return _stalls_along_branch(
        probe,
        available,
        entrance,
        heading_degrees,
        branch_u,
        side,
        branch_id,
        start_t,
        end_t,
        occupied,
        start_index,
    )


def _stalls_along_branch(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    branch_id: str,
    start_t: float,
    end_t: float,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stall_spec = _branch_stall(site)
    if stall_spec.family == "t_end":
        return []
    if stall_spec.family == "angled":
        return _angled_stalls_along_branch(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            branch_u,
            side,
            branch_id,
            start_t,
            end_t,
            occupied,
            start_index,
        )
    if stall_spec.family == "parallel":
        return _parallel_stalls_along_branch(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            branch_u,
            side,
            branch_id,
            start_t,
            end_t,
            occupied,
            start_index,
        )
    if stall_spec.family != "perpendicular":
        return []

    stalls: list[ParkingStall] = []
    t = start_t
    direction = 1 if side == "left" else -1
    while t + stall_spec.width <= end_t:
        if not module_angle_allowed(90.0, stall_spec.allowed_angles):
            break
        for stall_side in ("left", "right"):
            if stall_side == "left":
                local = (
                    branch_u + site.aisle_width / 2,
                    direction * t,
                    branch_u + site.aisle_width / 2 + stall_spec.length,
                    direction * (t + stall_spec.width),
                )
            else:
                local = (
                    branch_u - site.aisle_width / 2 - stall_spec.length,
                    direction * t,
                    branch_u - site.aisle_width / 2,
                    direction * (t + stall_spec.width),
                )
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + (90 if side == "left" else -90),
                        served_by_aisle_id=branch_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        t += stall_spec.width
    return stalls


def _parallel_stalls_along_branch(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    branch_id: str,
    start_t: float,
    end_t: float,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    t = start_t
    direction = 1 if side == "left" else -1
    branch_heading = heading_degrees + (90 if side == "left" else -90)
    while t + stall_spec.length <= end_t + 1e-9:
        for stall_side in ("left", "right"):
            if stall_side == "left":
                local = (
                    branch_u + site.aisle_width / 2,
                    direction * t,
                    branch_u + site.aisle_width / 2 + stall_spec.width,
                    direction * (t + stall_spec.length),
                )
            else:
                local = (
                    branch_u - site.aisle_width / 2 - stall_spec.width,
                    direction * t,
                    branch_u - site.aisle_width / 2,
                    direction * (t + stall_spec.length),
                )
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=branch_heading,
                        served_by_aisle_id=branch_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        t += stall_spec.length
    return stalls


def _angled_stalls_along_branch(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    branch_id: str,
    start_t: float,
    end_t: float,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []

    stalls: list[ParkingStall] = []
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    direction = 1 if side == "left" else -1
    branch_heading = heading_degrees + (90 if side == "left" else -90)
    t = start_t
    while t + front_pitch + forward_shift <= end_t + 1e-9:
        for stall_side in ("left", "right"):
            cross = 1 if stall_side == "left" else -1
            front_u = branch_u + cross * site.aisle_width / 2
            local_points = [
                (front_u, direction * t),
                (front_u, direction * (t + front_pitch)),
                (front_u + cross * lateral_depth, direction * (t + front_pitch + forward_shift)),
                (front_u + cross * lateral_depth, direction * (t + forward_shift)),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=branch_heading + cross * angle,
                        served_by_aisle_id=branch_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        t += front_pitch
    return stalls


def _with_best_connectors(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
    diagnostics: list[dict[str, object]],
    *,
    parent_aisle_id: str | None = None,
) -> LayoutResult:
    if not site.optimization.get("enable_connectors", True):
        return base
    stall_spec = _connector_stall(site)
    if stall_spec.family not in {"perpendicular", "parallel", "angled"}:
        diagnostics.append(
            {
                "reason": "connectors_not_supported_for_stall_family",
                "stall_family": stall_spec.family,
                "stall_type_id": stall_spec.id,
            }
        )
        return base
    best = base
    for branch_a, branch_b in _connector_pairs(best, parent_aisle_id=parent_aisle_id):
        pair_base = best
        pair_best = best
        for inset_depth in _connector_inset_depths(site, branch_a, branch_b):
            candidate, diagnostic = _connector_layout(
                site,
                available,
                entrance,
                heading_degrees,
                pair_base,
                branch_a,
                branch_b,
                inset_depth,
                finalize_layout,
                layout_valid,
                score_total,
            )
            diagnostics.append(diagnostic)
            if candidate and layout_valid(candidate) and _connector_is_better(
                site, pair_base, pair_best, candidate, score_total, str(diagnostic.get("connector_pattern", ""))
            ):
                pair_best = candidate
        best = pair_best
    return best


def _connector_is_better(
    site: SiteSpec,
    base: LayoutResult,
    current_best: LayoutResult,
    candidate: LayoutResult,
    score_total: LayoutScoreTotal,
    pattern: str,
) -> bool:
    """Whether candidate should replace the current best connector choice."""
    if not _layout_score_better(current_best, candidate, score_total):
        # Still allow prefer_loops end-loops that only mildly reduce stalls.
        if not (
            pattern == "opposite_end_loop"
            and site.optimization.get("prefer_loops")
            and candidate.stall_count + 8 >= base.stall_count
            and score_total(candidate) > score_total(base) - float(site.optimization.get("end_loop_score_slack", 2500.0))
        ):
            return False
        if current_best is not base and _layout_score_better(candidate, current_best, score_total):
            return False
        if current_best is not base and current_best.selected_connectors:
            # Prefer higher stall count among accepted end-loop options.
            return candidate.stall_count >= current_best.stall_count
        return True
    return True


def _layout_score_better(baseline: LayoutResult, candidate: LayoutResult, score_total: LayoutScoreTotal) -> bool:
    return score_total(candidate) > score_total(baseline)


def _connector_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
    finalize_layout: FinalizeLayout,
    layout_valid: LayoutValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult | None, dict[str, object]]:
    connector_id = f"A-CONNECTOR-{len(base.selected_connectors) + 1:03d}"
    branch_a_id = str(branch_a["id"])
    branch_b_id = str(branch_b["id"])
    main_length = _estimate_main_aisle_length(base, entrance, heading_degrees)
    connector_geometry = _connector_geometry(
        site,
        entrance,
        heading_degrees,
        branch_a,
        branch_b,
        inset_depth,
        main_aisle_length=main_length,
    )
    diagnostic: dict[str, object] = {
        "connector_id": connector_id,
        "connects": [branch_a_id, branch_b_id],
        "connector_inset_depth": float(inset_depth),
        "reason": "connector_evaluated",
    }
    if connector_geometry is None:
        diagnostic["reason"] = "connector_geometry_not_possible"
        return None, diagnostic
    connector = connector_geometry.polygon
    diagnostic["geometry"] = polygon_points(connector)
    diagnostic["connector_inset_depth"] = connector_geometry.inset_depth
    diagnostic["connector_pattern"] = connector_geometry.pattern
    diagnostic["pattern"] = connector_geometry.pattern
    diagnostic["u_min"] = float(connector_geometry.u_min)
    diagnostic["u_max"] = float(connector_geometry.u_max)
    diagnostic["center_v"] = float(connector_geometry.center_v)
    diagnostic["side"] = connector_geometry.side
    diagnostic["v_min"] = float(connector_geometry.v_min)
    diagnostic["v_max"] = float(connector_geometry.v_max)
    if not _geometry_fits(available, connector):
        diagnostic["reason"] = "connector_geometry_outside_usable_area"
        return None, diagnostic
    # Opposite short cross intentionally meets the main aisle; end-loop is routed past the main end.
    ignore_ids = {branch_a_id, branch_b_id}
    if connector_geometry.pattern in {"opposite_cross", "opposite_end_loop"}:
        ignore_ids.add("A-MAIN")
        ignore_ids.add("A-TURNAROUND")
    conflict = _connector_conflict(base, connector, ignore_ids)
    if conflict:
        diagnostic["reason"] = "connector_overlaps_existing_layout"
        diagnostic["conflict_id"] = conflict
        return None, diagnostic

    connector_drivable = connector
    # Same-side U and opposite end-loop replace dead-end turnarounds.
    # Short opposite-cross junctions keep turnarounds (mid-band bridge only).
    if connector_geometry.pattern == "opposite_cross":
        removed_turnarounds = set()
        kept_aisles = list(base.aisles)
    else:
        removed_turnarounds = _connected_turnaround_ids({branch_a_id, branch_b_id})
        kept_aisles = [
            aisle for aisle in base.aisles if aisle.id not in removed_turnarounds
        ]
        kept_aisles = _trim_endpoint_branch_aisles(kept_aisles, connector_drivable, {branch_a_id, branch_b_id})
    occupied_aisles = unary_union(
        [ShapelyPolygon(aisle.polygon) for aisle in kept_aisles]
        + [connector_drivable]
    )
    connector_stalls = _stalls_along_connector(
        site,
        available,
        entrance,
        heading_degrees,
        connector_geometry,
        connector_id,
        occupied_aisles,
        start_index=base.stall_count + 1,
    )
    connector_stall_area = (
        unary_union([ShapelyPolygon(stall.polygon) for stall in connector_stalls])
        if connector_stalls
        else ShapelyPolygon()
    )
    kept_stalls = [
        stall
        for stall in base.stalls
        if not area_overlaps(ShapelyPolygon(stall.polygon), connector_drivable)
        and not area_overlaps(ShapelyPolygon(stall.polygon), connector_stall_area)
    ]
    removed_stalls = base.stall_count - len(kept_stalls)
    diagnostic["generated_stalls"] = _stall_diagnostics(connector_stalls)
    connector_angle = heading_degrees + (
        90.0 if connector_geometry.pattern in {"opposite_cross", "opposite_end_loop"} else 0.0
    )
    connector_aisle = ParkingAisle(
        id=connector_id,
        polygon=polygon_points(connector_drivable),
        angle_degrees=connector_angle,
        role="connector",
        parent_aisle_id=branch_a_id,
        connected_aisle_ids=(branch_b_id,),
        directionality=aisle_directionality(site),
    )
    selected_connectors = [
        *base.selected_connectors,
        {
            "id": connector_id,
            "connects": [branch_a_id, branch_b_id],
            "removed_stalls": removed_stalls,
            "added_stalls": len(connector_stalls),
            "removed_turnarounds": sorted(removed_turnarounds),
            "connector_inset_depth": connector_geometry.inset_depth,
            "pattern": connector_geometry.pattern,
        },
    ]
    result = finalize_layout(
        LayoutResult(
            site=site,
            stalls=_renumber_stalls(kept_stalls + connector_stalls),
            aisles=[*kept_aisles, connector_aisle],
            selected_angle_degrees=base.selected_angle_degrees,
            generation_mode=base.generation_mode,
            main_entrance_id=base.main_entrance_id,
            selected_heading_degrees=base.selected_heading_degrees,
            selected_heading_delta_degrees=base.selected_heading_delta_degrees,
            selected_entrance_offset=base.selected_entrance_offset,
            selected_branch_side=base.selected_branch_side,
            selected_branch_start_u=base.selected_branch_start_u,
            selected_branch_length=base.selected_branch_length,
            selected_branches=base.selected_branches,
            selected_connectors=selected_connectors,
            unsupported_phase1_inputs=base.unsupported_phase1_inputs,
        )
    )
    diagnostic["stall_count"] = result.stall_count
    diagnostic["removed_stalls"] = removed_stalls
    diagnostic["added_stalls"] = len(connector_stalls)
    diagnostic["removed_turnarounds"] = sorted(removed_turnarounds)
    graph_is_valid = bool(result.graph_validation.get("valid", False))
    maneuver_is_valid = bool(result.maneuver_validation.get("valid", False))
    operational_is_valid = bool(result.operational_quality.get("valid", False))
    layout_is_valid = layout_valid(result)
    diagnostic["graph_valid"] = graph_is_valid
    diagnostic["graph_errors"] = list(result.graph_validation.get("errors", []))
    diagnostic["maneuver_valid"] = maneuver_is_valid
    diagnostic["operational_valid"] = operational_is_valid
    diagnostic["operational_blockers"] = list(result.operational_quality.get("promotion_blockers", []))
    if not graph_is_valid:
        diagnostic["reason"] = "connector_invalid_traffic_graph"
        return result, diagnostic
    if not maneuver_is_valid:
        diagnostic["reason"] = "connector_invalid_maneuver_validation"
        return result, diagnostic
    if not layout_is_valid:
        diagnostic["reason"] = "connector_invalid_operational_quality"
        return result, diagnostic
    if score_total(result) <= score_total(base):
        diagnostic["reason"] = "connector_does_not_improve_score"
        return result, diagnostic
    diagnostic["reason"] = "connector_improves_score"
    return result, diagnostic


def place_connector_family_stalls(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int = 1,
) -> list[ParkingStall]:
    """Place one stall family along a connector (same-side U supports extra families)."""
    probe = replace(site, stall=stall_spec, branch_stall=stall_spec)
    return _stalls_along_connector(
        probe,
        available,
        entrance,
        heading_degrees,
        connector,
        connector_id,
        occupied,
        start_index,
    )


def _stalls_along_connector(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stall_spec = _connector_stall(site)
    if connector.pattern == "opposite_cross":
        if stall_spec.family == "parallel":
            return _parallel_stalls_along_opposite_connector(
                site, stall_spec, available, entrance, heading_degrees, connector, connector_id, occupied, start_index
            )
        if stall_spec.family == "angled":
            return _angled_stalls_along_opposite_connector(
                site, stall_spec, available, entrance, heading_degrees, connector, connector_id, occupied, start_index
            )
        if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
            return []
        return _stalls_along_opposite_connector(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            connector,
            connector_id,
            occupied,
            start_index,
        )
    if connector.pattern == "opposite_end_loop":
        if stall_spec.family == "parallel":
            return _parallel_stalls_along_end_loop_connector(
                site, stall_spec, available, entrance, heading_degrees, connector, connector_id, occupied, start_index
            )
        if stall_spec.family == "angled":
            return _angled_stalls_along_end_loop_connector(
                site, stall_spec, available, entrance, heading_degrees, connector, connector_id, occupied, start_index
            )
        if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
            return []
        return _stalls_along_end_loop_connector(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            connector,
            connector_id,
            occupied,
            start_index,
        )
    if stall_spec.family == "parallel":
        return _parallel_stalls_along_same_side_connector(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            connector,
            connector_id,
            occupied,
            start_index,
        )
    if stall_spec.family == "angled":
        return _angled_stalls_along_same_side_connector(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            connector,
            connector_id,
            occupied,
            start_index,
        )
    if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return []

    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u = connector.u_min + throat
    end_u = connector.u_max - throat
    if end_u - u < stall_spec.width:
        return stalls

    direction = 1 if connector.side == "left" else -1
    half_width = site.aisle_width / 2
    while u + stall_spec.width <= end_u + 1e-9:
        for stall_side in ("outer", "inner"):
            if stall_side == "outer":
                v1 = connector.center_v + direction * half_width
                v2 = connector.center_v + direction * (half_width + stall_spec.length)
            else:
                v1 = connector.center_v - direction * half_width
                v2 = connector.center_v - direction * (half_width + stall_spec.length)
            stall = normalized_local_box_to_world((u, v1, u + stall_spec.width, v2), entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.width
    return stalls


def _parallel_stalls_along_same_side_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u = connector.u_min + throat
    end_u = connector.u_max - throat
    if end_u - u < stall_spec.length:
        return stalls
    direction = 1 if connector.side == "left" else -1
    half_width = site.aisle_width / 2
    while u + stall_spec.length <= end_u + 1e-9:
        for stall_side in ("outer", "inner"):
            if stall_side == "outer":
                v1 = connector.center_v + direction * half_width
                v2 = connector.center_v + direction * (half_width + stall_spec.width)
            else:
                v1 = connector.center_v - direction * half_width
                v2 = connector.center_v - direction * (half_width + stall_spec.width)
            stall = normalized_local_box_to_world((u, v1, u + stall_spec.length, v2), entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.length
    return stalls


def _angled_stalls_along_same_side_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u = connector.u_min + throat
    end_u = connector.u_max - throat
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    direction = 1 if connector.side == "left" else -1
    half_width = site.aisle_width / 2
    while u + front_pitch + forward_shift <= end_u + 1e-9:
        for stall_side in ("outer", "inner"):
            side_dir = direction if stall_side == "outer" else -direction
            front_v = connector.center_v + side_dir * half_width
            local_points = [
                (u, front_v),
                (u + front_pitch, front_v),
                (u + front_pitch + forward_shift, front_v + side_dir * lateral_depth),
                (u + forward_shift, front_v + side_dir * lateral_depth),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + side_dir * angle,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += front_pitch
    return stalls


def _stalls_along_end_loop_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    """Place stalls on the far face of the end-loop crossbar (away from the main aisle)."""
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    # End cross sits at the high-u edge of the connector envelope.
    u_front = connector.u_max - site.aisle_width
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    if end_v - v < stall_spec.width:
        return stalls
    while v + stall_spec.width <= end_v + 1e-9:
        # Skip main-aisle band.
        if abs(v + stall_spec.width / 2) <= site.aisle_width / 2 + stall_spec.width:
            v += stall_spec.width
            continue
        local = (
            u_front + site.aisle_width / 2,
            v,
            u_front + site.aisle_width / 2 + stall_spec.length,
            v + stall_spec.width,
        )
        stall = normalized_local_box_to_world(local, entrance, heading_degrees)
        if available.covers(stall) and not area_overlaps(occupied, stall):
            stalls.append(
                ParkingStall(
                    id=f"P-{start_index + len(stalls):03d}",
                    polygon=polygon_points(stall),
                    angle_degrees=heading_degrees,
                    served_by_aisle_id=connector_id,
                    aisle_side="outer",
                    stall_type_id=stall_spec.id,
                )
            )
        v += stall_spec.width
    return stalls


def _stalls_along_opposite_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    """Place 90-degree stalls on both long sides of an opposite-side cross aisle."""
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    if end_v - v < stall_spec.width:
        return stalls
    half_width = site.aisle_width / 2
    u_center = 0.5 * (connector.u_min + connector.u_max)
    while v + stall_spec.width <= end_v + 1e-9:
        # Skip the main-aisle band so cross-aisle stalls do not land on the spine.
        if abs(v + stall_spec.width / 2) <= half_width + stall_spec.width:
            v += stall_spec.width
            continue
        for stall_side, cross in (("left", 1.0), ("right", -1.0)):
            if cross > 0:
                local = (
                    u_center + half_width,
                    v,
                    u_center + half_width + stall_spec.length,
                    v + stall_spec.width,
                )
            else:
                local = (
                    u_center - half_width - stall_spec.length,
                    v,
                    u_center - half_width,
                    v + stall_spec.width,
                )
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + (90.0 if cross > 0 else -90.0),
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        v += stall_spec.width
    return stalls


def _parallel_stalls_along_opposite_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    if end_v - v < stall_spec.length:
        return stalls
    half_width = site.aisle_width / 2
    u_center = 0.5 * (connector.u_min + connector.u_max)
    while v + stall_spec.length <= end_v + 1e-9:
        if abs(v + stall_spec.length / 2) <= half_width + stall_spec.width:
            v += stall_spec.length
            continue
        for stall_side, cross in (("left", 1.0), ("right", -1.0)):
            if cross > 0:
                local = (
                    u_center + half_width,
                    v,
                    u_center + half_width + stall_spec.width,
                    v + stall_spec.length,
                )
            else:
                local = (
                    u_center - half_width - stall_spec.width,
                    v,
                    u_center - half_width,
                    v + stall_spec.length,
                )
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + (90.0 if cross > 0 else -90.0),
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        v += stall_spec.length
    return stalls


def _angled_stalls_along_opposite_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    half_width = site.aisle_width / 2
    u_center = 0.5 * (connector.u_min + connector.u_max)
    while v + front_pitch + forward_shift <= end_v + 1e-9:
        if abs(v + (front_pitch + forward_shift) / 2) <= half_width + stall_spec.width:
            v += front_pitch
            continue
        for stall_side, cross in (("left", 1.0), ("right", -1.0)):
            front_u = u_center + cross * half_width
            local_points = [
                (front_u, v),
                (front_u, v + front_pitch),
                (front_u + cross * lateral_depth, v + front_pitch + forward_shift),
                (front_u + cross * lateral_depth, v + forward_shift),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + cross * angle,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        v += front_pitch
    return stalls


def _parallel_stalls_along_end_loop_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u_front = connector.u_max - site.aisle_width
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    if end_v - v < stall_spec.length:
        return stalls
    while v + stall_spec.length <= end_v + 1e-9:
        if abs(v + stall_spec.length / 2) <= site.aisle_width / 2 + stall_spec.width:
            v += stall_spec.length
            continue
        local = (
            u_front + site.aisle_width / 2,
            v,
            u_front + site.aisle_width / 2 + stall_spec.width,
            v + stall_spec.length,
        )
        stall = normalized_local_box_to_world(local, entrance, heading_degrees)
        if available.covers(stall) and not area_overlaps(occupied, stall):
            stalls.append(
                ParkingStall(
                    id=f"P-{start_index + len(stalls):03d}",
                    polygon=polygon_points(stall),
                    angle_degrees=heading_degrees,
                    served_by_aisle_id=connector_id,
                    aisle_side="outer",
                    stall_type_id=stall_spec.id,
                )
            )
        v += stall_spec.length
    return stalls


def _angled_stalls_along_end_loop_connector(
    site: SiteSpec,
    stall_spec: StallSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []
    stalls: list[ParkingStall] = []
    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u_front = connector.u_max - site.aisle_width
    v = connector.v_min + throat
    end_v = connector.v_max - throat
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    front_u = u_front + site.aisle_width / 2
    while v + front_pitch + forward_shift <= end_v + 1e-9:
        if abs(v + (front_pitch + forward_shift) / 2) <= site.aisle_width / 2 + stall_spec.width:
            v += front_pitch
            continue
        local_points = [
            (front_u, v),
            (front_u, v + front_pitch),
            (front_u + lateral_depth, v + front_pitch + forward_shift),
            (front_u + lateral_depth, v + forward_shift),
        ]
        stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
        if available.covers(stall) and not area_overlaps(occupied, stall):
            stalls.append(
                ParkingStall(
                    id=f"P-{start_index + len(stalls):03d}",
                    polygon=polygon_points(stall),
                    angle_degrees=heading_degrees + angle,
                    served_by_aisle_id=connector_id,
                    aisle_side="outer",
                    stall_type_id=stall_spec.id,
                )
            )
        v += front_pitch
    return stalls


def _branches_for_connector_parent(
    layout: LayoutResult,
    parent_aisle_id: str | None,
) -> list[dict[str, object]]:
    if parent_aisle_id is None:
        return list(layout.selected_branches)
    return [
        branch
        for branch in layout.selected_branches
        if str(branch.get("parent_aisle_id", "A-MAIN")) == parent_aisle_id
    ]


def _connector_pairs(
    layout: LayoutResult,
    *,
    parent_aisle_id: str | None = None,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []
    scoped = _branches_for_connector_parent(layout, parent_aisle_id)
    for side in ("left", "right"):
        branches = sorted(
            [branch for branch in scoped if branch.get("side") == side],
            key=lambda item: float(item["start_u"]),
        )
        for first, second in zip(branches, branches[1:]):
            pairs.append((first, second))
    pairs.extend(_opposite_connector_pairs(layout, parent_aisle_id=parent_aisle_id))
    return pairs


def _opposite_connector_pairs(
    layout: LayoutResult,
    *,
    parent_aisle_id: str | None = None,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Pair left/right branches for opposite-side connectors.

    Includes:
    - nearly aligned stations (short cross or end-loop)
    - high-value end-loop pairs by min branch length even when not aligned
    """
    scoped = _branches_for_connector_parent(layout, parent_aisle_id)
    left = sorted(
        [branch for branch in scoped if branch.get("side") == "left"],
        key=lambda item: float(item["start_u"]),
    )
    right = sorted(
        [branch for branch in scoped if branch.get("side") == "right"],
        key=lambda item: float(item["start_u"]),
    )
    if not left or not right:
        return []

    used_right: set[str] = set()
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []
    max_align = _opposite_connector_align_tolerance(layout.site)
    for left_branch in left:
        best: dict[str, object] | None = None
        best_delta = None
        for right_branch in right:
            right_id = str(right_branch["id"])
            if right_id in used_right:
                continue
            delta = abs(float(left_branch["start_u"]) - float(right_branch["start_u"]))
            if delta > max_align + 1e-9:
                continue
            if best is None or delta < best_delta:  # type: ignore[operator]
                best = right_branch
                best_delta = delta
        if best is not None:
            used_right.add(str(best["id"]))
            pairs.append((left_branch, best))

    # Also offer the single best end-loop pair by usable outer length if not already paired.
    if _boolean_opt(layout.site.optimization.get("enable_opposite_end_loops", True)):
        best_pair: tuple[dict[str, object], dict[str, object]] | None = None
        best_score = -1.0
        for left_branch in left:
            for right_branch in right:
                score = min(float(left_branch["length"]), float(right_branch["length"]))
                span = abs(float(left_branch["start_u"]) - float(right_branch["start_u"]))
                score = score + min(span, layout.site.aisle_width * 2) * 0.1
                if score > best_score:
                    best_score = score
                    best_pair = (left_branch, right_branch)
        if best_pair is not None and best_pair not in pairs:
            pairs.append(best_pair)
    return pairs


def _opposite_connector_align_tolerance(site: SiteSpec) -> float:
    raw = site.optimization.get("opposite_connector_align_tolerance", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


def _connector_geometry(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
    main_aisle_length: float | None = None,
) -> ConnectorGeometry | None:
    side_a = str(branch_a["side"])
    side_b = str(branch_b["side"])
    if side_a == side_b:
        return _same_side_connector_geometry(site, entrance, heading_degrees, branch_a, branch_b, inset_depth)
    if {side_a, side_b} == {"left", "right"}:
        opposite_enabled = _boolean_opt(site.optimization.get("enable_opposite_connectors", True))
        end_loop_enabled = _boolean_opt(
            site.optimization.get("enable_opposite_end_loops", opposite_enabled)
        )
        if end_loop_enabled:
            end_loop = _opposite_end_loop_geometry(
                site,
                entrance,
                heading_degrees,
                branch_a,
                branch_b,
                inset_depth,
                main_aisle_length=main_aisle_length,
            )
            if end_loop is not None:
                return end_loop
        if opposite_enabled:
            return _opposite_connector_geometry(site, entrance, heading_degrees, branch_a, branch_b, inset_depth)
        return None
    return None


def _same_side_connector_geometry(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
) -> ConnectorGeometry | None:
    u1 = float(branch_a["start_u"])
    u2 = float(branch_b["start_u"])
    if abs(u2 - u1) <= site.aisle_width:
        return None
    side = str(branch_a["side"])
    direction = 1 if side == "left" else -1
    length = min(float(branch_a["length"]), float(branch_b["length"]))
    capped_inset = min(max(float(inset_depth), 0.0), _max_connector_inset_depth(site, length))
    center_v = direction * (length - site.aisle_width / 2 - capped_inset)
    u_min = min(u1, u2) - site.aisle_width / 2
    u_max = max(u1, u2) + site.aisle_width / 2
    local = (
        u_min,
        center_v - site.aisle_width / 2,
        u_max,
        center_v + site.aisle_width / 2,
    )
    return ConnectorGeometry(
        polygon=normalized_local_box_to_world(local, entrance, heading_degrees),
        u_min=u_min,
        u_max=u_max,
        center_v=center_v,
        side=side,
        inset_depth=capped_inset,
        pattern="same_side_u",
        v_min=center_v - site.aisle_width / 2,
        v_max=center_v + site.aisle_width / 2,
    )


def _opposite_connector_geometry(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
) -> ConnectorGeometry | None:
    """Short cross-link joining nearly aligned left/right branches through the main aisle.

    This is intentionally not a full-length through aisle: a long cross destroys too
    many branch stalls. The short stub creates a limited loop junction while leaving
    end turnarounds in place.
    """
    left = branch_a if str(branch_a["side"]) == "left" else branch_b
    right = branch_b if str(branch_a["side"]) == "left" else branch_a
    u_left = float(left["start_u"])
    u_right = float(right["start_u"])
    if abs(u_left - u_right) > _opposite_connector_align_tolerance(site) + 1e-9:
        return None
    length_left = float(left["length"])
    length_right = float(right["length"])
    if min(length_left, length_right) <= site.aisle_width * 2:
        return None
    # inset_depth is ignored for the short junction form; kept for API symmetry.
    _ = inset_depth
    # Span both branch stations so the cross touches both branch polygons.
    u_min = min(u_left, u_right) - site.aisle_width / 2
    u_max = max(u_left, u_right) + site.aisle_width / 2
    stub = site.aisle_width
    v_max = min(length_left, site.aisle_width / 2 + stub)
    v_min = -min(length_right, site.aisle_width / 2 + stub)
    if v_max - v_min <= site.aisle_width:
        return None
    local = (u_min, v_min, u_max, v_max)
    return ConnectorGeometry(
        polygon=normalized_local_box_to_world(local, entrance, heading_degrees),
        u_min=u_min,
        u_max=u_max,
        center_v=0.0,
        side="opposite",
        inset_depth=0.0,
        pattern="opposite_cross",
        v_min=v_min,
        v_max=v_max,
    )


def _opposite_end_loop_geometry(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
    main_aisle_length: float | None = None,
) -> ConnectorGeometry | None:
    """C-shaped outer ring joining left/right branch ends beyond the main-aisle end.

    Geometry is the union of:
    - left outer rail along +v tip out to the far end
    - right outer rail along -v tip out to the far end
    - end cross past the main turnaround, so the ring does not cut the main aisle
    """
    left = branch_a if str(branch_a["side"]) == "left" else branch_b
    right = branch_b if str(branch_a["side"]) == "left" else branch_a
    u_left = float(left["start_u"])
    u_right = float(right["start_u"])
    length_left = float(left["length"])
    length_right = float(right["length"])
    width = site.aisle_width
    if min(length_left, length_right) <= width * 2:
        return None

    max_depth = _max_connector_inset_depth(site, min(length_left, length_right))
    capped_inset = min(max(float(inset_depth), 0.0), max_depth)
    v_left = length_left - width / 2 - capped_inset
    v_right = -(length_right - width / 2 - capped_inset)
    if v_left <= width / 2 + 1e-9 or v_right >= -width / 2 - 1e-9:
        return None

    # Prefer routing past the main end when the parcel still has depth; otherwise
    # form the end cross just beyond the farther branch station (may meet main).
    main_end = float(main_aisle_length) if main_aisle_length is not None else max(u_left, u_right) + width * 3
    branch_far = max(u_left, u_right) + width
    if main_end > branch_far + width:
        # Keep the cross inside the parcel, just before the main terminus.
        u_far = min(main_end - width * 0.5, branch_far + width * 2)
    else:
        u_far = branch_far
    left_rail = (
        min(u_left, u_far) - width / 2,
        v_left - width / 2,
        max(u_left, u_far) + width / 2,
        v_left + width / 2,
    )
    right_rail = (
        min(u_right, u_far) - width / 2,
        v_right - width / 2,
        max(u_right, u_far) + width / 2,
        v_right + width / 2,
    )
    end_cross = (
        u_far - width / 2,
        v_right - width / 2,
        u_far + width / 2,
        v_left + width / 2,
    )
    polygon = unary_union(
        [
            normalized_local_box_to_world(left_rail, entrance, heading_degrees),
            normalized_local_box_to_world(right_rail, entrance, heading_degrees),
            normalized_local_box_to_world(end_cross, entrance, heading_degrees),
        ]
    )
    if polygon.is_empty or not getattr(polygon, "is_valid", True):
        return None
    return ConnectorGeometry(
        polygon=polygon,
        u_min=min(left_rail[0], right_rail[0], end_cross[0]),
        u_max=max(left_rail[2], right_rail[2], end_cross[2]),
        center_v=0.0,
        side="opposite",
        inset_depth=capped_inset,
        pattern="opposite_end_loop",
        v_min=v_right - width / 2,
        v_max=v_left + width / 2,
    )


def _estimate_main_aisle_length(layout: LayoutResult, entrance: EntranceSpec, heading_degrees: float) -> float:
    main = next((aisle for aisle in layout.aisles if aisle.id == "A-MAIN" or aisle.role == "main"), None)
    if main is None or not main.polygon:
        starts = [float(branch["start_u"]) for branch in layout.selected_branches]
        return (max(starts) if starts else layout.site.aisle_width * 4) + layout.site.aisle_width * 2
    heading = math.radians(heading_degrees)
    us = []
    for x, y in main.polygon:
        dx = x - entrance.center[0]
        dy = y - entrance.center[1]
        us.append(dx * math.cos(heading) + dy * math.sin(heading))
    return max(us) if us else layout.site.aisle_width * 4


def _connector_inset_depths(site: SiteSpec, branch_a: dict[str, object], branch_b: dict[str, object]) -> tuple[float, ...]:
    branch_length = min(float(branch_a["length"]), float(branch_b["length"]))
    max_depth = _max_connector_inset_depth(site, branch_length)
    raw = site.optimization.get("connector_inset_depths")
    if isinstance(raw, list):
        depths = []
        for item in raw:
            try:
                depths.append(float(item))
            except (TypeError, ValueError):
                continue
        return _normalized_inset_depths(depths, max_depth)
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return (0.0,)
    stall_spec = _connector_stall(site)
    if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return (0.0,)
    return _normalized_inset_depths(
        [0.0, stall_spec.length / 2, stall_spec.length, stall_spec.length * 1.5],
        max_depth,
    )


def _normalized_inset_depths(depths: list[float], max_depth: float) -> tuple[float, ...]:
    values = {round(min(max(depth, 0.0), max_depth), 6) for depth in depths}
    values.add(0.0)
    return tuple(sorted(values))


def _max_connector_inset_depth(site: SiteSpec, branch_length: float) -> float:
    return max(branch_length - site.aisle_width * 2, 0.0)


def _connector_outer_stall_depth(site: SiteSpec, branch_length: float) -> float:
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return 0.0
    stall_spec = _connector_stall(site)
    if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return 0.0
    return min(stall_spec.length, _max_connector_inset_depth(site, branch_length))


def _connector_conflict(layout: LayoutResult, connector, endpoint_branch_ids: set[str]) -> str | None:
    allowed = set(endpoint_branch_ids)
    allowed.update(f"{branch_id}-TURNAROUND" for branch_id in endpoint_branch_ids)
    for aisle in layout.aisles:
        if aisle.id in allowed:
            continue
        if area_overlaps(ShapelyPolygon(aisle.polygon), connector):
            return aisle.id
    return None


def _trim_endpoint_branch_aisles(
    aisles: list[ParkingAisle],
    connector,
    endpoint_branch_ids: set[str],
) -> list[ParkingAisle]:
    main = next((ShapelyPolygon(aisle.polygon) for aisle in aisles if aisle.role == "main"), None)
    if main is None:
        return aisles
    trimmed: list[ParkingAisle] = []
    for aisle in aisles:
        if aisle.id not in endpoint_branch_ids:
            trimmed.append(aisle)
            continue
        geometry = _trim_branch_aisle_to_connector(ShapelyPolygon(aisle.polygon), main, connector)
        if geometry is None:
            trimmed.append(aisle)
            continue
        trimmed.append(
            ParkingAisle(
                id=aisle.id,
                polygon=polygon_points(geometry),
                angle_degrees=aisle.angle_degrees,
                role=aisle.role,
                connected_to_entrance_id=aisle.connected_to_entrance_id,
                parent_aisle_id=aisle.parent_aisle_id,
                connected_aisle_ids=aisle.connected_aisle_ids,
                directionality=aisle.directionality,
            )
        )
    return trimmed


def _trim_branch_aisle_to_connector(branch, main, connector):
    axis = _branch_axis(branch, main)
    if axis is None:
        return None
    cut = _branch_connector_half_plane(branch, main, connector, axis)
    if cut is None:
        return None
    kept = branch.intersection(cut)
    components = [item for item in getattr(kept, "geoms", [kept]) if getattr(item, "area", 0.0) > 1e-6]
    if not components:
        return None
    return max(components, key=lambda item: item.area)


def _branch_connector_half_plane(branch, main, connector, axis: tuple[float, float]):
    connector_projections = [_project(point, axis) for point in connector.exterior.coords]
    if not connector_projections:
        return None
    main_projection = _project((main.centroid.x, main.centroid.y), axis)
    connector_projection = _project((connector.centroid.x, connector.centroid.y), axis)
    keep_greater = main_projection >= connector_projection
    threshold = max(connector_projections) if keep_greater else min(connector_projections)
    min_x, min_y, max_x, max_y = unary_union([branch, main, connector]).bounds
    span = max(max_x - min_x, max_y - min_y, 1.0) * 4
    return _half_plane(axis, threshold, keep_greater, span)


def _half_plane(axis: tuple[float, float], threshold: float, keep_greater: bool, span: float) -> ShapelyPolygon:
    ax, ay = axis
    px, py = -ay, ax
    origin = (ax * threshold, ay * threshold)
    direction = 1 if keep_greater else -1
    return ShapelyPolygon(
        [
            (origin[0] + px * span, origin[1] + py * span),
            (origin[0] + ax * span * direction + px * span, origin[1] + ay * span * direction + py * span),
            (origin[0] + ax * span * direction - px * span, origin[1] + ay * span * direction - py * span),
            (origin[0] - px * span, origin[1] - py * span),
        ]
    )


def _branch_axis(branch, main) -> tuple[float, float] | None:
    rectangle = branch.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    edges: list[tuple[float, float, float]] = []
    for start, end in zip(coords, coords[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        edges.append((dx * dx + dy * dy, dx, dy))
    if not edges:
        return None
    _, dx, dy = max(edges, key=lambda item: item[0])
    axis = _unit_vector(dx, dy)
    if axis is None:
        return None
    to_main = (main.centroid.x - branch.centroid.x, main.centroid.y - branch.centroid.y)
    if axis[0] * to_main[0] + axis[1] * to_main[1] < 0:
        return (-axis[0], -axis[1])
    return axis


def _unit_vector(dx: float, dy: float) -> tuple[float, float] | None:
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-9:
        return None
    return (dx / length, dy / length)


def _project(point, axis: tuple[float, float]) -> float:
    return float(point[0]) * axis[0] + float(point[1]) * axis[1]


def _connected_turnaround_ids(branch_ids: set[str]) -> set[str]:
    return {f"{branch_id}-TURNAROUND" for branch_id in branch_ids}


def _connector_throat_length(site: SiteSpec) -> float:
    raw = site.optimization.get("connector_throat_length", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


def _connector_l_shape_end_stalls_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get(
        "connector_allow_l_shape_end_stalls",
        site.optimization.get("maneuver_l_shape_fallback", True),
    )
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no", "off"}
    return bool(raw)


def _geometry_fits(available, candidate) -> bool:
    """Coverage check with a tiny area tolerance for setback-edge float noise.

    Keep the tolerance well below site-constraint overlap thresholds so a
    geometry accepted here is not later rejected as a boundary conflict.
    """
    if available.covers(candidate):
        return True
    return candidate.difference(available).area <= 1e-12


def _max_clear_aisle_length(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start: float) -> float:
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(_module_step_width(site), 0.5)
    best = start
    length = start + site.aisle_width * 2

    while length <= diagonal * 1.5:
        candidate = main_aisle_with_turnaround(site, entrance, heading_degrees, start, length)
        if not _geometry_fits(available, candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal * 1.5)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = main_aisle_with_turnaround(site, entrance, heading_degrees, start, mid)
        if _geometry_fits(available, candidate):
            low = mid
        else:
            high = mid
    return low


def _max_clear_branch_length(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str) -> float:
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(_module_step_width(site), 0.5)
    best = 0.0
    length = site.aisle_width * 2

    while length <= diagonal:
        candidate = branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, length)
        if not _geometry_fits(available, candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, mid)
        if available.covers(candidate):
            low = mid
        else:
            high = mid
    return low


def _branch_length_clip_report(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
) -> dict[str, object]:
    """Compare usable-area branch length vs boundary-only length for diagnostics.

    ``clipped_by_exclusion`` is true when hard exclusions (obstacles, etc.) shorten
    the branch relative to a pure boundary envelope.
    """
    clear_length = _max_clear_branch_length(
        site, available, entrance, heading_degrees, branch_u, side
    )
    boundary_only = ShapelyPolygon(site.boundary)
    open_length = _max_clear_branch_length(
        site, boundary_only, entrance, heading_degrees, branch_u, side
    )
    opposite = "right" if side == "left" else "left"
    opposite_clear = _max_clear_branch_length(
        site, available, entrance, heading_degrees, branch_u, opposite
    )
    clip_amount = max(0.0, float(open_length) - float(clear_length))
    clipped = clip_amount > site.aisle_width * 0.25
    prefer_hint = None
    if opposite_clear > clear_length + site.aisle_width * 0.5:
        prefer_hint = opposite
    return {
        "clear_length": float(clear_length),
        "open_boundary_length": float(open_length),
        "clip_amount": float(clip_amount),
        "clipped_by_exclusion": clipped,
        "opposite_side_clear_length": float(opposite_clear),
        "prefer_side_hint": prefer_hint,
    }


def _branch_start_positions(
    site: SiteSpec,
    main_aisle_length: float,
    *,
    min_u: float | None = None,
) -> tuple[float, ...]:
    raw = site.optimization.get("branch_start_positions")
    lower = site.aisle_width * 2 if min_u is None else max(float(min_u), site.aisle_width)
    max_u = main_aisle_length - site.aisle_width * 2
    if max_u <= lower:
        return ()
    if isinstance(raw, list):
        return tuple(sorted({float(item) for item in raw if lower <= float(item) <= max_u}))
    module_width = _module_step_width(site)
    step = float(site.optimization.get("branch_start_step", module_width * 2))
    step = max(step, module_width)
    positions: list[float] = []
    u = lower
    while u <= max_u + 1e-9:
        positions.append(round(u, 6))
        u += step
    midpoint = round((lower + max_u) / 2, 6)
    positions.append(midpoint)
    positions.append(round(max_u, 6))
    return tuple(sorted(set(positions)))


def _branch_sides(site: SiteSpec) -> tuple[str, ...]:
    raw = site.optimization.get("branch_sides")
    if isinstance(raw, list):
        sides = tuple(str(item) for item in raw if str(item) in {"left", "right"})
        return sides or ("left", "right")
    return ("left", "right")


def _max_branches(site: SiteSpec) -> int:
    raw = site.optimization.get("max_branches", 2)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    return max(value, 0)


def _next_branch_index(layout: LayoutResult) -> int:
    existing = [aisle for aisle in layout.aisles if aisle.role == "branch"]
    return len(existing) + 1


def _branch_conflict(
    layout: LayoutResult,
    branch_drivable,
    *,
    parent_aisle_id: str = "A-MAIN",
) -> str | None:
    # Parent spine overlap is expected at the branch root; parent-side stalls are
    # dropped later when they collide with the branch footprint.
    for aisle in layout.aisles:
        if aisle.id == parent_aisle_id:
            continue
        if area_overlaps(ShapelyPolygon(aisle.polygon), branch_drivable):
            return aisle.id
    for stall in layout.stalls:
        if stall.served_by_aisle_id == parent_aisle_id:
            continue
        if area_overlaps(ShapelyPolygon(stall.polygon), branch_drivable):
            return stall.id
    return None


def _heading_deltas(site: SiteSpec) -> tuple[float, ...]:
    raw = site.optimization.get("heading_deltas_degrees")
    if isinstance(raw, list):
        return tuple(float(item) for item in raw)
    return (-20.0, -10.0, 0.0, 10.0, 20.0)


def _entrance_offsets(site: SiteSpec, entrance: EntranceSpec) -> tuple[float, ...]:
    raw = site.optimization.get("entrance_offsets")
    max_offset = max((entrance.width - site.aisle_width) / 2, 0.0)
    if isinstance(raw, list):
        offsets = tuple(float(item) for item in raw)
    elif max_offset > 0:
        offsets = (-max_offset, 0.0, max_offset)
    else:
        offsets = (0.0,)
    return tuple(offset for offset in offsets if abs(offset) <= max_offset + 1e-9)


def _aisle_lateral_offsets(site: SiteSpec) -> tuple[float, ...]:
    """Candidate lateral shifts of the whole aisle network relative to the entrance.

    Explicit ``main_aisle_lateral_offsets`` wins. Otherwise enable with
    ``enable_main_aisle_lateral_offsets``, or auto-enable a small offset set when
    the site has hard obstacles (unless ``auto_lateral_offsets_for_obstacles`` is false).
    """
    raw = site.optimization.get("main_aisle_lateral_offsets")
    if isinstance(raw, list):
        values = []
        for item in raw:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(set(values))) or (0.0,)
    if _boolean_opt(site.optimization.get("enable_main_aisle_lateral_offsets", False)):
        step = site.aisle_width / 2
        return (-step, 0.0, step)
    # Auto fan-out only when explicitly enabled, or when prefer_obstacle_clearance
    # is on (keeps default two-way fixtures / promotion baselines stable).
    auto_offsets = site.optimization.get("auto_lateral_offsets_for_obstacles")
    if auto_offsets is None:
        auto_offsets = bool(site.optimization.get("prefer_obstacle_clearance", False))
    if site.obstacles and _boolean_opt(auto_offsets):
        # Fan out by up to two aisle widths so a spine obstacle can be bypassed.
        step = site.aisle_width
        return (-2 * step, -step, 0.0, step, 2 * step)
    return (0.0,)


def _offset_entrance(entrance: EntranceSpec, offset: float) -> EntranceSpec:
    normal = math.radians(entrance.heading_degrees + 90)
    center = (
        entrance.center[0] + math.cos(normal) * offset,
        entrance.center[1] + math.sin(normal) * offset,
    )
    return replace(entrance, center=center)


def _lateral_shift_entrance(entrance: EntranceSpec, heading_degrees: float, lateral_offset: float) -> EntranceSpec:
    """Shift the geometry frame perpendicular to the aisle heading."""
    if abs(lateral_offset) <= 1e-9:
        return entrance
    normal = math.radians(heading_degrees + 90)
    center = (
        entrance.center[0] + math.cos(normal) * lateral_offset,
        entrance.center[1] + math.sin(normal) * lateral_offset,
    )
    return replace(entrance, center=center)


def _build_exit_aisle(
    site: SiteSpec,
    available,
    *,
    entry_entrance: EntranceSpec,
    geom_entrance: EntranceSpec,
    heading_degrees: float,
    aisle_length: float,
    turnaround,
    parent_aisle_id: str = "A-TURNAROUND",
) -> ParkingAisle | None:
    """Attach a short exit corridor from the main turnaround to a far-end exit entrance.

    Minimal dual-entrance template: entry-connected main aisle + optional exit
    aisle at the far end. Disabled with optimization.enable_dual_entrance=false.

    Lateral proximity is measured relative to the turnaround (not the entry
    centerline origin) so dogleg/offset rear spines can still attach an exit
    that sits near the offset turnaround.
    """
    if not _boolean_opt(site.optimization.get("enable_dual_entrance", True)):
        return None
    candidates = [item for item in exit_capable_entrances(site) if item.id != entry_entrance.id]
    if not candidates:
        return None

    turnaround_poly = turnaround if hasattr(turnaround, "centroid") else ShapelyPolygon(turnaround)
    if turnaround_poly.is_empty:
        return None
    turn_u, turn_v = _world_to_local(
        (float(turnaround_poly.centroid.x), float(turnaround_poly.centroid.y)),
        geom_entrance,
        heading_degrees,
    )

    best: tuple[float, EntranceSpec, object] | None = None
    for exit_entrance in candidates:
        for corridor, route_kind in _exit_corridor_candidates(
            site,
            geom_entrance,
            heading_degrees,
            aisle_length,
            turnaround_poly,
            exit_entrance,
            turn_v=turn_v,
        ):
            if corridor is None or corridor.is_empty:
                continue
            if not _geometry_fits(available, corridor):
                continue
            if not area_overlaps(corridor, turnaround_poly) and corridor.distance(turnaround_poly) > 1e-6:
                continue
            # Prefer exits near the far end and close to the turnaround spine;
            # slight preference for shorter straight routes over elbows.
            u, v = _world_to_local(exit_entrance.center, geom_entrance, heading_degrees)
            route_penalty = 0.0 if route_kind == "straight" else site.aisle_width
            score = u - abs(v - turn_v) - corridor.distance(turnaround_poly) - route_penalty
            if best is None or score > best[0]:
                best = (score, exit_entrance, corridor)

    if best is None:
        return None
    _score, exit_entrance, corridor = best
    # Require the exit to sit toward the far half of the aisle, not next to the entry.
    exit_u, _ = _world_to_local(exit_entrance.center, geom_entrance, heading_degrees)
    if exit_u < max(aisle_length, turn_u) * 0.5:
        return None
    return ParkingAisle(
        id="A-EXIT",
        polygon=polygon_points(corridor),
        angle_degrees=heading_degrees,
        role="exit",
        parent_aisle_id=parent_aisle_id,
        connected_to_entrance_id=exit_entrance.id,
        directionality=aisle_directionality(site),
    )


def _exit_corridor_candidates(
    site: SiteSpec,
    geom_entrance: EntranceSpec,
    heading_degrees: float,
    aisle_length: float,
    turnaround_poly,
    exit_entrance: EntranceSpec,
    *,
    turn_v: float = 0.0,
):
    """Yield (corridor, route_kind) candidates from turnaround to exit.

    Tries a straight buffer first, then L-shaped elbows that stay on the
    turnaround lateral spine before crossing to the exit — useful when a
    mid-site obstacle blocks the diagonal from a dogleg rear turnaround.
    """
    start = (float(turnaround_poly.centroid.x), float(turnaround_poly.centroid.y))
    end = (float(exit_entrance.center[0]), float(exit_entrance.center[1]))
    if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-9:
        return
    exit_u, exit_v = _world_to_local(end, geom_entrance, heading_degrees)
    if exit_u + 1e-9 < aisle_length * 0.45:
        return
    half = site.aisle_width / 2
    lateral_budget = max(
        max(exit_entrance.width, site.aisle_width) + half,
        abs(turn_v) + site.aisle_width,
        site.aisle_width * 3.0,
    )
    raw_budget = site.optimization.get("exit_lateral_budget")
    if raw_budget is not None:
        try:
            lateral_budget = max(lateral_budget, float(raw_budget))
        except (TypeError, ValueError):
            pass
    if abs(exit_v - turn_v) > lateral_budget + 1e-9:
        return

    turn_u, _ = _world_to_local(start, geom_entrance, heading_degrees)
    paths: list[tuple[str, list[tuple[float, float]]]] = [
        ("straight", [start, end]),
    ]
    # Elbow along rear spine to exit u, then lateral to exit.
    if abs(exit_u - turn_u) > half or abs(exit_v - turn_v) > half:
        mid_far = _local_to_world(exit_u, turn_v, geom_entrance, heading_degrees)
        paths.append(("elbow_along_spine", [start, mid_far, end]))
        mid_side = _local_to_world(turn_u, exit_v, geom_entrance, heading_degrees)
        paths.append(("elbow_lateral_first", [start, mid_side, end]))

    for kind, points in paths:
        # Drop near-duplicate consecutive vertices.
        cleaned: list[tuple[float, float]] = []
        for point in points:
            if not cleaned or math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > 1e-9:
                cleaned.append(point)
        if len(cleaned) < 2:
            continue
        yield LineString(cleaned).buffer(half, cap_style=2, join_style=2), kind


def _exit_corridor_geometry(
    site: SiteSpec,
    geom_entrance: EntranceSpec,
    heading_degrees: float,
    aisle_length: float,
    turnaround_poly,
    exit_entrance: EntranceSpec,
    *,
    turn_v: float = 0.0,
):
    """Compatibility helper: first exit-corridor candidate polygon, if any."""
    for corridor, _kind in _exit_corridor_candidates(
        site,
        geom_entrance,
        heading_degrees,
        aisle_length,
        turnaround_poly,
        exit_entrance,
        turn_v=turn_v,
    ):
        return corridor
    return None


def _world_to_local(point: tuple[float, float], entrance: EntranceSpec, heading_degrees: float) -> tuple[float, float]:
    dx = float(point[0]) - entrance.center[0]
    dy = float(point[1]) - entrance.center[1]
    heading = math.radians(heading_degrees)
    u = dx * math.cos(heading) + dy * math.sin(heading)
    v = -dx * math.sin(heading) + dy * math.cos(heading)
    return u, v


def _local_to_world(
    u: float,
    v: float,
    entrance: EntranceSpec,
    heading_degrees: float,
) -> tuple[float, float]:
    heading = math.radians(heading_degrees)
    x = entrance.center[0] + u * math.cos(heading) - v * math.sin(heading)
    y = entrance.center[1] + u * math.sin(heading) + v * math.cos(heading)
    return (float(x), float(y))


def _local_polygon_to_world(points: list[tuple[float, float]], entrance: EntranceSpec, heading_degrees: float):
    geometry = ShapelyPolygon(points)
    rotated = affinity.rotate(geometry, heading_degrees, origin=(0, 0), use_radians=False)
    return affinity.translate(rotated, xoff=entrance.center[0], yoff=entrance.center[1])


def _renumber_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=stall.stall_type_id,
        )
        for index, stall in enumerate(stalls, start=1)
    ]


def _stall_diagnostics(stalls: list[ParkingStall]) -> list[dict[str, object]]:
    return [
        {
            "source_id": stall.id,
            "geometry": stall.polygon,
            "angle_degrees": stall.angle_degrees,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "aisle_side": stall.aisle_side,
            "stall_type_id": stall.stall_type_id,
        }
        for stall in stalls
    ]


def _main_stall(site: SiteSpec) -> StallSpec:
    return site.main_stall or site.stall


def _branch_stall(site: SiteSpec) -> StallSpec:
    return site.branch_stall or site.main_stall or site.stall


def _connector_stall(site: SiteSpec) -> StallSpec:
    return _branch_stall(site)


def _module_step_width(site: SiteSpec) -> float:
    return min(_module_pitch(_main_stall(site)), _module_pitch(_branch_stall(site)))


def _module_pitch(stall_spec: StallSpec) -> float:
    """Along-aisle spacing for one stall module of the given family."""
    if stall_spec.family == "parallel":
        return stall_spec.length
    return stall_spec.width


def _t_end_stall_spec(site: SiteSpec, role: str) -> StallSpec | None:
    """Return the stall dimensions used for end-cap bays, if any.

    - ``family == t_end`` always places end-cap stalls for that role.
    - Other families may opt in with ``optimization.enable_t_end_caps``.
    End-cap identity is reported with the active stall type id and
    ``aisle_side="end"`` so maneuver dispatch does not need a synthetic type.
    """
    active = _main_stall(site) if role == "main" else _branch_stall(site)
    if active.family == "t_end":
        return active
    if not _boolean_opt(site.optimization.get("enable_t_end_caps", False)):
        return None
    if active.family not in {"perpendicular", "angled", "parallel"}:
        return None
    # Geometry is a 90-degree end bay sized from the active module.
    return StallSpec(
        id=active.id,
        family="t_end",
        width=active.width,
        length=active.length,
        allowed_angles=(90.0,),
        drive_over=False,
        access_sides=("front",),
        blocked_sides=(),
        classifications=active.classifications,
        fixed_features=active.fixed_features,
    )


def _t_end_envelope_clearance(site: SiteSpec) -> float:
    """Extra far-edge room so a requested exact T-end envelope stays on site."""
    vehicle = site.vehicle
    if vehicle is None:
        return 0.0
    raw = site.constraints.get("maneuvering", {}) if isinstance(site.constraints, dict) else {}
    if not isinstance(raw, dict) or not _boolean_opt(raw.get("require_swept_path_check", False)):
        return 0.0
    try:
        margin = float(vehicle.swept_path_margin)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(margin) or margin < 0.0:
        return 0.0
    return margin


def _aisle_length_with_t_end_reserve(
    max_length: float,
    min_length: float,
    t_end_spec: StallSpec | None,
    *,
    force_reserve: bool,
    envelope_clearance: float = 0.0,
) -> float:
    """Reserve clear depth beyond the aisle end for t_end / end-cap bays."""
    if t_end_spec is None or not force_reserve:
        return max_length
    extra = max(float(envelope_clearance), 0.0)
    reserved = max_length - t_end_spec.length - extra
    if reserved > min_length:
        return reserved
    return max_length


def _obstacle_aware_score(site: SiteSpec, layout: LayoutResult, score_total: LayoutScoreTotal) -> float:
    """Score for greedy branch picks; lightly prefers clearance when obstacles exist."""
    total = score_total(layout)
    if not site.obstacles:
        return total
    if not _boolean_opt(site.optimization.get("auto_obstacle_clearance_for_branches", True)):
        return total
    # Import locally to avoid a circular import at module load.
    from openparkcad.scoring import _obstacle_clearance

    bonus = float(site.optimization.get("branch_obstacle_clearance_bonus", 12.0))
    return total + _obstacle_clearance(layout) * bonus


def _branch_selection_score(
    site: SiteSpec,
    layout: LayoutResult,
    score_total: LayoutScoreTotal,
    diagnostic: dict[str, object] | None = None,
) -> float:
    """Greedy branch score with soft preference for longer clear / less-clipped runs."""
    total = _obstacle_aware_score(site, layout, score_total)
    if not diagnostic:
        return total
    try:
        clear_bonus = float(site.optimization.get("branch_clear_length_bonus", 0.35))
    except (TypeError, ValueError):
        clear_bonus = 0.35
    try:
        clip_penalty = float(site.optimization.get("branch_clip_penalty", 0.2))
    except (TypeError, ValueError):
        clip_penalty = 0.2
    clear_length = diagnostic.get("clear_length")
    clip_amount = diagnostic.get("clip_amount")
    if isinstance(clear_length, int | float):
        total += float(clear_length) * clear_bonus
    if isinstance(clip_amount, int | float):
        total -= float(clip_amount) * clip_penalty
    # Prefer the unclipped side when the diagnostic already suggested it.
    if diagnostic.get("clipped_by_exclusion") and diagnostic.get("prefer_side_hint"):
        try:
            total -= float(site.optimization.get("branch_clipped_side_penalty", 8.0))
        except (TypeError, ValueError):
            total -= 8.0
    return total


def _t_end_stalls_for_main(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    aisle_length: float,
    start_index: int,
    occupied,
    *,
    v_center: float = 0.0,
) -> list[ParkingStall]:
    stall_spec = _t_end_stall_spec(site, role="main")
    if stall_spec is None:
        return []
    if not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return []

    span = 2.0 * site.aisle_width
    count = max(int(span // stall_spec.width), 0)
    if count <= 0:
        return []
    start_v = v_center - 0.5 * count * stall_spec.width
    stalls: list[ParkingStall] = []
    for index in range(count):
        v1 = start_v + index * stall_spec.width
        v2 = v1 + stall_spec.width
        local = (aisle_length, v1, aisle_length + stall_spec.length, v2)
        stall = normalized_local_box_to_world(local, entrance, heading_degrees)
        if not available.covers(stall) or area_overlaps(occupied, stall):
            continue
        stalls.append(
            ParkingStall(
                id=f"P-{start_index + len(stalls):03d}",
                polygon=polygon_points(stall),
                angle_degrees=heading_degrees,
                served_by_aisle_id="A-TURNAROUND",
                aisle_side="end",
                stall_type_id=stall_spec.id,
            )
        )
    return stalls


def _t_end_stalls_for_branch(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    branch_id: str,
    branch_length: float,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stall_spec = _t_end_stall_spec(site, role="branch")
    if stall_spec is None:
        return []
    if not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return []

    span = 2.0 * site.aisle_width
    count = max(int(span // stall_spec.width), 0)
    if count <= 0:
        return []
    branch_heading = heading_degrees + (90 if side == "left" else -90)
    start_u = branch_u - 0.5 * count * stall_spec.width
    turnaround_id = f"{branch_id}-TURNAROUND"
    stalls: list[ParkingStall] = []
    for index in range(count):
        u1 = start_u + index * stall_spec.width
        u2 = u1 + stall_spec.width
        if side == "left":
            local = (u1, branch_length, u2, branch_length + stall_spec.length)
        else:
            local = (u1, -branch_length - stall_spec.length, u2, -branch_length)
        stall = normalized_local_box_to_world(local, entrance, heading_degrees)
        if not available.covers(stall) or area_overlaps(occupied, stall):
            continue
        stalls.append(
            ParkingStall(
                id=f"P-{start_index + len(stalls):03d}",
                polygon=polygon_points(stall),
                angle_degrees=branch_heading,
                served_by_aisle_id=turnaround_id,
                aisle_side="end",
                stall_type_id=stall_spec.id,
            )
        )
    return stalls
