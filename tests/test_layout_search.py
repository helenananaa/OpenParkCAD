from __future__ import annotations

from dataclasses import replace

from openparkcad.layout_candidates import LayoutCandidateContext, LayoutCandidateEvaluation
from openparkcad.layout_benchmark import NOT_AVAILABLE, search_fields
from openparkcad.layout_search import (
    choose_official,
    match_baseline_context,
    rank_contexts,
    read_layout_search,
    search_multi_spine,
)
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec


def _site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="search-unit",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, allowed_angles=(90.0,)),
        optimization=optimization,
    )


def _layout(*, score: float, stalls: int = 2, valid: bool = True, name: str = "A") -> LayoutResult:
    stall_list = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=[(0, index), (2.5, index), (2.5, index + 5), (0, index + 5)],
            angle_degrees=90.0,
            served_by_aisle_id="A-MAIN",
        )
        for index in range(stalls)
    ]
    aisles = [
        ParkingAisle(
            id="A-MAIN",
            polygon=[(8, 0), (12, 0), (12, 16), (8, 16)],
            angle_degrees=90.0,
            role="main",
            connected_to_entrance_id="main",
        )
    ]
    validation = {"valid": valid, "errors": [] if valid else ["blocked"]}
    return LayoutResult(
        site=_site(),
        stalls=stall_list,
        aisles=aisles,
        generation_mode="phase1_main_aisle",
        main_entrance_id="main",
        selected_heading_degrees=90.0,
        score={"total": score},
        graph_validation=dict(validation),
        maneuver_validation=dict(validation),
        site_constraint_validation=dict(validation),
        engineering_validation={"valid": valid, "result_scope": "official_layout", "rules": {"failed": []}},
        operational_quality={"valid": valid},
        candidate_selection={"backend": "greedy", "requested_backend": "greedy", "backend_fallback_reason": None},
    )


def _context(candidate_id: str, layout: LayoutResult, *, family: str, entrance: str = "main", score: float | None = None) -> LayoutCandidateContext:
    if score is not None:
        layout = replace(layout, score={"total": score})
    return LayoutCandidateContext(
        candidate_id=candidate_id,
        spine_id=f"spine-{candidate_id}",
        site=layout.site,
        template_layout=layout,
        source={"family": family, "entrance_id": entrance, "aisle_lateral_offset": 0.0},
        collect_status="collected",
        spine_payload={"family": family, "entrance_id": entrance, "exit_entrance_id": None, "aisles": [{"id": "A-MAIN"}]},
    )


def _evaluation(candidate_id: str, layout: LayoutResult, *, score: float) -> LayoutCandidateEvaluation:
    layout = replace(layout, score={"total": score})
    return LayoutCandidateEvaluation(
        candidate_id=candidate_id,
        spine_id=f"spine-{candidate_id}",
        requested_backend="greedy",
        actual_backend="greedy",
        fallback_reason=None,
        preview={},
        rebuilt_layout=layout,
        checks={
            "graph": {"executed": True, "valid": True},
            "maneuver": {"executed": True, "valid": True},
            "vehicle": {"executed": True, "valid": True},
            "site_quota": {"executed": True, "valid": True},
            "engineering": {"executed": True, "valid": True},
            "operational": {"executed": True, "valid": True},
        },
        score={"total": score},
        duration_seconds=0.1,
        failure_class=None,
        used_template=False,
        selection={"backend": "greedy", "requested_backend": "greedy"},
    )


def test_read_layout_search_defaults_to_legacy() -> None:
    config = read_layout_search(_site())
    assert config.mode == "legacy"
    assert config.top_k == 4
    assert config.refinement_budget_seconds == 10.0


def test_read_layout_search_rejects_illegal_values() -> None:
    try:
        read_layout_search(_site(layout_search={"mode": "mystery"}))
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("illegal mode must raise")
    try:
        read_layout_search(_site(layout_search={"mode": "multi_spine", "top_k": True}))
    except ValueError as exc:
        assert "top_k" in str(exc)
    else:
        raise AssertionError("boolean top_k must raise")
    try:
        read_layout_search(_site(layout_search={"mode": "multi_spine", "refinement_budget_seconds": 0}))
    except ValueError as exc:
        assert "refinement_budget_seconds" in str(exc)
    else:
        raise AssertionError("non-positive budget must raise")


def test_search_fields_read_layout_search_counts() -> None:
    layout = replace(
        _layout(score=10, stalls=2),
        layout_search={
            "counts": {"generated": 8, "deduplicated": 8, "retained": 4, "evaluated": 3},
            "budget": {"exhausted": False},
            "official_candidate_id": "cand-x",
        },
        candidate_selection={"backend": "greedy"},
    )
    fields = search_fields(layout)
    assert fields["generated_count"] == 8
    assert fields["deduplicated_count"] == 8
    assert fields["retained_count"] == 4
    assert fields["evaluated_count"] == 3
    assert fields["budget_exhausted"] is False
    assert fields["official_candidate_id"] == "cand-x"
    assert fields["candidate_selection_backend"] == "greedy"
    assert "collect_seconds" in fields
    assert "collect_reused_baseline_generation" in fields
    empty = search_fields(replace(_layout(score=1, stalls=1), layout_search={}))
    assert empty["generated_count"] == NOT_AVAILABLE
    assert empty["official_candidate_id"] == NOT_AVAILABLE


def test_match_baseline_context_requires_spine_geometry() -> None:
    baseline = _layout(score=50, stalls=5)
    same = _context("cand-same", baseline, family="straight", score=50)
    shifted = _layout(score=50, stalls=5)
    shifted = replace(
        shifted,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(2, 0), (6, 0), (6, 16), (2, 16)],
                angle_degrees=90.0,
                role="main",
                connected_to_entrance_id="main",
            )
        ],
    )
    other = _context("cand-offset", shifted, family="straight", score=50)
    assert match_baseline_context([other, same], baseline).candidate_id == "cand-same"
    assert match_baseline_context([other], baseline) is None


def test_rank_contexts_puts_legacy_winner_first_then_family_coverage() -> None:
    baseline = _layout(score=50, stalls=5, name="B")
    c_base = _context("cand-base", baseline, family="straight", score=50)
    c_dog = _context("cand-dog", _layout(score=40, stalls=4), family="dogleg", score=40)
    c_extra = _context("cand-extra", _layout(score=45, stalls=4), family="straight", score=45)
    c_low = _context("cand-low", _layout(score=10, stalls=1), family="multi_jog", score=10)
    ordered = rank_contexts([c_extra, c_low, c_dog, c_base], baseline)
    assert ordered[0].candidate_id == "cand-base"
    assert ordered[0].retain_reason == "legacy_winner"
    families = [item.source["family"] for item in ordered[:3]]
    assert "dogleg" in families
    assert "multi_jog" in families


def test_choose_official_keeps_baseline_when_promotion_off_or_tied() -> None:
    baseline = _layout(score=100, stalls=4)
    better = _evaluation("cand-b", _layout(score=120, stalls=5), score=120)
    official, publication, preview = choose_official(baseline, [better], promotion_requested=False)
    assert official is baseline
    assert publication["replaced"] is False
    assert publication["reason"] == "promotion_not_requested"
    assert preview == "cand-b"

    tied = _evaluation("cand-t", _layout(score=100, stalls=4), score=100)
    official, publication, _ = choose_official(baseline, [tied], promotion_requested=True)
    assert official is baseline
    assert publication["reason"] == "tie_keeps_baseline"


def test_choose_official_promotes_higher_verified_score_and_ignores_invalid_baseline_floor() -> None:
    baseline = _layout(score=100, stalls=4)
    winner = _evaluation("cand-win", _layout(score=130, stalls=6), score=130)
    official, publication, _ = choose_official(baseline, [winner], promotion_requested=True)
    assert official is winner.rebuilt_layout
    assert publication["replaced"] is True
    assert publication["reason"] == "higher_scoring_verified_candidate"

    invalid_b = _layout(score=5, stalls=0, valid=False)
    recovered = _evaluation("cand-rec", _layout(score=20, stalls=3), score=20)
    official, publication, _ = choose_official(invalid_b, [recovered], promotion_requested=True)
    assert official is recovered.rebuilt_layout
    assert publication["reason"] == "recovered_feasible"


def test_budget_exhaustion_skips_unfinished_and_keeps_baseline() -> None:
    baseline = _layout(score=80, stalls=3)
    c_base = _context("cand-base", baseline, family="straight", score=80)
    c_other = _context("cand-other", _layout(score=70, stalls=2), family="dogleg", score=70)
    calls: list[str] = []
    clock = iter([0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])

    def time_fn() -> float:
        return next(clock)

    def collect(_site: SiteSpec) -> list[LayoutCandidateContext]:
        return [c_base, c_other]

    def evaluate(context: LayoutCandidateContext, **_kwargs) -> LayoutCandidateEvaluation:
        calls.append(context.candidate_id)
        return _evaluation(context.candidate_id, context.template_layout, score=200)

    def legacy(_site: SiteSpec) -> LayoutResult:
        return baseline

    result = search_multi_spine(
        _site(layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 1.0}),
        time_fn=time_fn,
        evaluate_fn=evaluate,
        collect_fn=collect,
        legacy_fn=legacy,
    )
    assert result is baseline
    assert "cand-other" not in calls
    assert result.layout_search["status"] == "budget_exhausted"
    assert result.layout_search["publication"]["replaced"] is False
    assert "cand-other" in result.layout_search["budget"]["unfinished_candidate_ids"]


def test_top_k_one_reuses_legacy_without_evaluating_extras() -> None:
    baseline = _layout(score=90, stalls=4)
    called = {"collect": False, "evaluate": False}

    def collect(_site: SiteSpec) -> list[LayoutCandidateContext]:
        called["collect"] = True
        return []

    def evaluate(context: LayoutCandidateContext, **_kwargs) -> LayoutCandidateEvaluation:
        called["evaluate"] = True
        return _evaluation(context.candidate_id, context.template_layout, score=1)

    result = search_multi_spine(
        _site(layout_search={"mode": "multi_spine", "top_k": 1, "refinement_budget_seconds": 10.0}),
        evaluate_fn=evaluate,
        collect_fn=collect,
        legacy_fn=lambda site: baseline,
    )
    assert result is baseline
    assert called["collect"] is False
    assert called["evaluate"] is False
    assert result.layout_search["publication"]["reason"] == "top_k_reuses_legacy"
