from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from openparkcad.candidate_snapshot import candidate_snapshot_report
from openparkcad.cli import _final_layout_errors, _write_output_set
from openparkcad.generator import collect_layout_candidate_contexts, generate_layout, generate_layout_legacy
from openparkcad.layout_search import match_baseline_context
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec, VehicleSpec, site_from_dict
from openparkcad.scoring import score_total
from tests.ortools_util import require_ortools

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_EXAMPLE = REPO_ROOT / "examples" / "multi_spine_comparison_site.json"


def _aisle_class() -> AisleClassSpec:
    return AisleClassSpec(
        id="wide-two-way-no-cross",
        width=6.0,
        capacity="two_vehicle",
        directionality="two_way",
    )


def _simple_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="search-integration",
        boundary=[(0, 0), (36, 0), (36, 40), (0, 40)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[EntranceSpec(id="main", mode="shared", center=(18, 0), width=8.0, heading_degrees=90.0)],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "main_aisle_lateral_offsets": [0.0, 6.0],
            **optimization,
        },
    )


def _geometry_key(layout) -> tuple:
    return (
        layout.stall_count,
        tuple(stall.id for stall in layout.stalls),
        tuple((aisle.id, aisle.role, tuple(aisle.polygon)) for aisle in layout.aisles),
        round(score_total(layout), 6),
        bool(_final_layout_errors(layout)),
    )


def test_default_generate_layout_matches_legacy_wrapper() -> None:
    site = _simple_site()
    default = generate_layout(site)
    legacy = generate_layout_legacy(site)
    assert _geometry_key(default) == _geometry_key(legacy)
    assert not default.layout_search or default.layout_search.get("mode") in {None, "legacy"}


def test_explicit_legacy_mode_matches_wrapper() -> None:
    site = _simple_site(layout_search={"mode": "legacy", "top_k": 4})
    assert _geometry_key(generate_layout(site)) == _geometry_key(generate_layout_legacy(site))


def test_top_k_one_reuses_legacy_official_geometry() -> None:
    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 1, "refinement_budget_seconds": 10.0},
        promote_candidate_layout_preview=True,
    )
    legacy = generate_layout_legacy(site)
    multi = generate_layout(site)
    assert _geometry_key(legacy) == _geometry_key(multi)
    assert multi.layout_search["mode"] == "multi_spine"
    assert multi.layout_search["publication"]["reason"] == "top_k_reuses_legacy"


def test_promotion_off_keeps_baseline_official_object() -> None:
    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 30.0},
        promote_candidate_layout_preview=False,
    )
    legacy = generate_layout_legacy(replace(site, optimization={**site.optimization, "layout_search": {"mode": "legacy"}}))
    multi = generate_layout(site)
    assert _geometry_key(legacy) == _geometry_key(multi)
    assert multi.layout_search["publication"]["replaced"] is False
    assert multi.layout_search["publication"]["reason"] == "promotion_not_requested"
    assert multi.layout_search["candidates"]


def _t04_comparison_site(**extra) -> SiteSpec:
    """One site where template ranking keeps a 65-stall spine and module rebuild wins at 67."""
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.5, family="angled", allowed_angles=(60.0,))
    return SiteSpec(
        name="multi-spine-comparison",
        boundary=[(0, 0), (86, 0), (86, 70), (0, 70)],
        stall=perpendicular,
        stall_candidates=(perpendicular, angled),
        aisle_width=6.0,
        margin=0.0,
        vehicle=VehicleSpec(
            id="passenger-car",
            length=4.8,
            width=1.9,
            wheelbase=2.8,
            min_turning_radius=5.5,
            turning_radius_reference="outer_front_wheel",
            track_width=1.6,
            front_overhang=1.0,
            rear_overhang=1.0,
        ),
        constraints={
            "maneuvering": {
                "require_turning_radius_check": True,
                "require_swept_path_check": False,
                "max_reverse_distance": 12.0,
            }
        },
        entrances=[EntranceSpec(id="main", mode="shared", center=(43, 0), width=8.0, heading_degrees=90.0)],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": True,
            "max_branches": 1,
            "branch_sides": ["left"],
            "branch_start_positions": [24, 42, 60],
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "main_aisle_lateral_offsets": [0.0, 6.0],
            "promote_candidate_layout_preview": True,
            "selector_backend": "greedy",
            "layout_search": {"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 20.0},
            "weights": {"stall_count": 100, "aisle_area": 0, "dead_end_length": 0, "branch_count": 0},
            **extra,
        },
    )


def _assert_official_checks_executed(layout) -> None:
    graph = layout.graph_validation if isinstance(layout.graph_validation, dict) else {}
    maneuver = layout.maneuver_validation if isinstance(layout.maneuver_validation, dict) else {}
    vehicle = maneuver.get("vehicle_validation") if isinstance(maneuver.get("vehicle_validation"), dict) else {}
    site = layout.site_constraint_validation if isinstance(layout.site_constraint_validation, dict) else {}
    engineering = layout.engineering_validation if isinstance(layout.engineering_validation, dict) else {}
    operational = layout.operational_quality if isinstance(layout.operational_quality, dict) else {}
    quota = site.get("quota") if isinstance(site.get("quota"), dict) else {}
    rules = engineering.get("rules") if isinstance(engineering.get("rules"), dict) else {}
    active = rules.get("active") if isinstance(rules.get("active"), list) else []
    requested = vehicle.get("requested") if isinstance(vehicle.get("requested"), dict) else {}
    vehicle_checks = vehicle.get("checks") if isinstance(vehicle.get("checks"), list) else []

    assert graph.get("valid") is True
    assert isinstance(graph.get("errors"), list)
    assert int(graph.get("node_count") or 0) > 0
    assert int(graph.get("edge_count") or 0) > 0
    assert int(graph.get("stall_access_count") or 0) > 0

    assert maneuver.get("valid") is True
    assert int(maneuver.get("checked_stalls") or 0) > 0

    assert vehicle.get("valid") is True
    assert int(vehicle.get("checked_stalls") or 0) > 0
    assert requested.get("turning_radius") is True
    assert len(vehicle_checks) > 0

    assert site.get("valid") is True
    assert "conflicts" in site
    assert quota.get("valid") is True

    assert engineering.get("valid") is True
    assert engineering.get("result_scope") == "official_layout"
    assert active
    assert "vehicle.turning_radius" in {item.get("id") for item in active if isinstance(item, dict)}

    assert operational.get("valid") is True
    assert operational.get("version")


def _assert_official_candidate_checks_executed(layout, *, expected_backend: str | None = None) -> None:
    search = layout.layout_search if isinstance(layout.layout_search, dict) else {}
    publication = search.get("publication") if isinstance(search.get("publication"), dict) else {}
    winner_id = publication.get("official_candidate_id")
    candidates = search.get("candidates") if isinstance(search.get("candidates"), list) else []
    winner = next((item for item in candidates if item.get("candidate_id") == winner_id), None)
    assert winner is not None
    checks = winner.get("checks") if isinstance(winner.get("checks"), dict) else {}
    for key in ("graph", "maneuver", "vehicle", "site_quota", "engineering", "operational"):
        block = checks.get(key) if isinstance(checks.get(key), dict) else {}
        assert block.get("executed") is True, key
        assert block.get("valid") is True, key
    assert winner.get("used_template") is False
    if expected_backend is not None:
        assert winner.get("actual_backend") == expected_backend
    assert int(winner.get("stall_count") or 0) == layout.stall_count


def test_multi_spine_reuses_legacy_phase1_generation() -> None:
    import openparkcad.generator as generator_mod
    import openparkcad.phase1_candidates as phase1_mod

    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 20.0},
        promote_candidate_layout_preview=True,
    )
    real = phase1_mod.iter_phase1_candidates
    calls = {"n": 0}

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    with patch.object(phase1_mod, "iter_phase1_candidates", wrapped), patch.object(
        generator_mod, "iter_phase1_candidates", wrapped
    ):
        multi = generate_layout(site)
    assert calls["n"] == 1
    assert multi.layout_search["budget"]["collect_reused_baseline_generation"] is True
    assert multi.layout_search["counts"]["generated"] >= 2
    fields_from_layout = __import__("openparkcad.layout_benchmark", fromlist=["search_fields"]).search_fields(multi)
    assert fields_from_layout["generated_count"] == multi.layout_search["counts"]["generated"]
    assert fields_from_layout["collect_reused_baseline_generation"] is True


def test_baseline_match_uses_spine_geometry_not_shared_aisle_ids() -> None:
    site = _simple_site(
        main_aisle_lateral_offsets=[-6.0, 0.0, 6.0],
        layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 20.0},
        promote_candidate_layout_preview=True,
    )
    baseline = generate_layout_legacy(site)
    contexts = [item for item in collect_layout_candidate_contexts(site) if item.template_layout.aisles]
    laterals = {float(item.source["aisle_lateral_offset"]) for item in contexts}
    assert {-6.0, 0.0, 6.0} <= laterals
    matched = match_baseline_context(contexts, baseline)
    assert matched is not None
    assert float(matched.source["aisle_lateral_offset"]) == 0.0
    plus6 = next(item for item in contexts if abs(float(item.source["aisle_lateral_offset"]) - 6.0) < 1e-9)
    assert match_baseline_context([plus6], baseline) is None
    claimed = [item for item in contexts if match_baseline_context([item], baseline) is not None]
    assert [float(item.source["aisle_lateral_offset"]) for item in claimed] == [0.0]
    multi = generate_layout(site)
    winners = [item for item in multi.layout_search["candidates"] if item.get("retain_reason") == "legacy_winner"]
    assert len(winners) == 1
    assert float(winners[0]["aisle_lateral_offset"]) == 0.0


def test_promoted_official_layout_keeps_original_selection_evidence(tmp_path: Path) -> None:
    site = _t04_comparison_site()
    multi = generate_layout(site)
    assert multi.layout_search["publication"]["replaced"] is True
    assert any(aisle.role == "branch" for aisle in multi.aisles)
    assert multi.selected_branches
    branch_ids = {branch["id"] for branch in multi.selected_branches}
    official_ids = {aisle.id for aisle in multi.aisles}
    assert branch_ids <= official_ids
    selection = multi.candidate_selection
    assert selection.get("selected_ids")
    assert selection.get("solver_provenance")
    stored_ids = list(selection["selected_ids"])
    with patch("openparkcad.candidate_snapshot.select_candidate_objects") as mocked:
        mocked.side_effect = AssertionError("report must serialize the stored selection")
        snapshot = candidate_snapshot_report(multi)
        _write_output_set(multi, tmp_path / "layout.dxf", tmp_path / "layout.svg", tmp_path / "report.json")
        assert mocked.call_count == 0
    assert snapshot["selection"]["selected_ids"] == stored_ids
    assert snapshot["selection"].get("solver_provenance") == selection.get("solver_provenance")
    winner = next(
        item
        for item in multi.layout_search["candidates"]
        if item.get("candidate_id") == multi.layout_search["publication"]["official_candidate_id"]
    )
    assert winner.get("selected_ids") == stored_ids
    assert winner.get("solver_provenance") == selection.get("solver_provenance")
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    promotion = payload["candidate_layout_promotion"]
    assert promotion["status"] == "promoted"
    assert promotion["official_output_replaced"] is True
    assert promotion["pre_promotion_selected_ids"] == stored_ids
    assert promotion["pre_promotion_solver_provenance"] == selection["solver_provenance"]
    assert set(promotion["candidate_to_official_aisle_ids"].values()) == official_ids
    assert set(promotion["preview_to_official_aisle_ids"].values()) == official_ids
    assert set(promotion["preview_to_official_stall_ids"].values()) == {stall.id for stall in multi.stalls}
    by_stall_id = {stall.id: stall for stall in multi.stalls}
    for preview in payload["candidate_layout_preview"]["stalls"]:
        official = by_stall_id[promotion["preview_to_official_stall_ids"][preview["id"]]]
        assert [list(point) for point in official.polygon] == preview["geometry"]
        assert official.served_by_aisle_id == promotion["preview_to_official_aisle_ids"][preview["served_by_aisle_id"]]
    for field in ("candidate_network_preview", "candidate_layout_preview"):
        assert payload[field]["status"] == "promoted_to_official"
        assert payload[field]["official_output_replaced"] is True
        assert set(payload[field]["official_aisle_ids"]) == official_ids
    preview = payload["candidate_layout_preview"]
    assert preview["score"] == payload["score"]
    assert preview["validation"]["status"] == "official"
    for field in ("maneuver_validation", "site_constraint_validation", "engineering_validation", "operational_quality"):
        assert preview["validation"][field] == payload[field]
    assert payload["input_diagnostics"]["field_support"]["optimization.promote_candidate_layout_preview"] == "active"


def test_t04_second_template_spine_wins_after_official_rebuild() -> None:
    site = _t04_comparison_site()
    assert site.optimization["selector_backend"] == "greedy"
    legacy = generate_layout_legacy(site)
    multi = generate_layout(site)

    assert not _final_layout_errors(legacy)
    assert not _final_layout_errors(multi)
    publication = multi.layout_search["publication"]
    assert publication["replaced"] is True
    assert publication["reason"] == "higher_scoring_verified_candidate"
    assert score_total(multi) > score_total(legacy)
    assert multi.stall_count > legacy.stall_count
    assert tuple(stall.id for stall in multi.stalls) != tuple(stall.id for stall in legacy.stalls)
    assert _geometry_key(multi) != _geometry_key(legacy)
    _assert_official_checks_executed(multi)
    _assert_official_candidate_checks_executed(multi, expected_backend="greedy")
    official_ids = {aisle.id for aisle in multi.aisles}
    assert all(stall.served_by_aisle_id in official_ids or stall.served_by_aisle_id is None for stall in multi.stalls)
    assert multi.layout_search["quality_delta"]["score_delta"] > 0


def test_example_multi_spine_comparison_site_promotes_on_generate_layout() -> None:
    require_ortools()
    payload = json.loads(COMPARISON_EXAMPLE.read_text(encoding="utf-8"))
    site = site_from_dict(payload)
    assert site.optimization.get("layout_search", {}).get("mode") == "multi_spine"
    assert site.optimization.get("selector_backend") == "cpsat"
    legacy = generate_layout_legacy(site)
    multi = generate_layout(site)
    assert multi.layout_search["mode"] == "multi_spine"
    assert multi.layout_search["publication"]["replaced"] is True
    assert score_total(multi) > score_total(legacy)
    assert tuple(stall.id for stall in multi.stalls) != tuple(stall.id for stall in legacy.stalls)
    _assert_official_checks_executed(multi)
    _assert_official_candidate_checks_executed(multi, expected_backend="cpsat")


def test_t06_baseline_keeps_local_promotion_gains() -> None:
    site = _simple_site(
        promote_candidate_layout_preview=True,
        layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 20.0},
    )
    legacy = generate_layout_legacy(
        replace(site, optimization={**site.optimization, "layout_search": {"mode": "legacy"}})
    )
    multi = generate_layout(site)
    assert legacy.candidate_layout_promotion.get("status") in {"promoted", "rejected", "not_requested"}
    if legacy.generation_mode == "candidate_layout_promoted":
        assert multi.generation_mode == "candidate_layout_promoted"
        assert _geometry_key(multi) == _geometry_key(legacy) or multi.layout_search["publication"]["replaced"] is True
    assert multi.layout_search["baseline"]["local_promotion"] == legacy.candidate_layout_promotion.get("status")


def test_t05_unverified_or_invalid_candidate_cannot_replace_baseline() -> None:
    from openparkcad.layout_candidates import LayoutCandidateEvaluation
    from openparkcad.layout_search import choose_official
    from openparkcad.models import LayoutResult

    baseline = generate_layout(_simple_site())
    shadow = LayoutResult(
        site=baseline.site,
        stalls=list(baseline.stalls) + list(baseline.stalls[:1]),
        aisles=list(baseline.aisles),
        generation_mode=baseline.generation_mode,
        main_entrance_id=baseline.main_entrance_id,
        score={"total": score_total(baseline) + 5000},
        graph_validation={"valid": False, "errors": ["shadow-only"]},
        maneuver_validation={"valid": True},
        site_constraint_validation={"valid": True},
        engineering_validation={"valid": False, "result_scope": "candidate:shadow"},
        operational_quality={"valid": True},
    )
    evaluation = LayoutCandidateEvaluation(
        candidate_id="cand-shadow",
        spine_id="spine-shadow",
        requested_backend="greedy",
        actual_backend="greedy",
        fallback_reason=None,
        preview={"comparison": {"promotion_eligible": True}},
        rebuilt_layout=shadow,
        checks={
            "graph": {"executed": True, "valid": False},
            "maneuver": {"executed": True, "valid": True},
            "vehicle": {"executed": True, "valid": True},
            "site_quota": {"executed": True, "valid": True},
            "engineering": {"executed": True, "valid": False},
            "operational": {"executed": True, "valid": True},
        },
        score={"total": score_total(baseline) + 5000},
        duration_seconds=0.1,
        failure_class=None,
        used_template=False,
    )
    official, publication, _ = choose_official(baseline, [evaluation], promotion_requested=True)
    assert official is baseline
    assert publication["replaced"] is False
