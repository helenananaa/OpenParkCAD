"""N1 frozen v0.5 cases: source labels and current-insufficient / solved predicates."""

from __future__ import annotations

from openparkcad.generator import generate_layout
from tests.v0_5_parallel_ladder_support import (
    CASES,
    RECT_HUMAN_PARKING_AISLES,
    both_end_cross_aisles,
    case_source,
    distinct_parallel_parking_aisle_count,
    hard_aisle_width,
    has_real_cross_aisle,
    l_shape_current_is_insufficient,
    l_shape_is_solved,
    load_case_payload,
    load_case_site,
    published_aisles_respect_hard_width,
    rectangle_current_is_insufficient,
    rectangle_is_solved,
    site_width,
    tight_site_cannot_fit_two_aisles,
    turn_reject_obstacle_blocks_intended_t,
    two_aisle_clear_width,
    uses_second_l_wing,
)


def test_frozen_case_set_is_two_positives_and_two_hard_rejects() -> None:
    kinds = [case["kind"] for case in CASES.values()]
    splits = {case_id: case["split"] for case_id, case in CASES.items()}
    assert kinds.count("positive") == 2
    assert kinds.count("hard_reject") == 2
    assert splits["N-T01"] == "development"
    assert splits["N-T03"] == "development"
    assert splits["N-T02"] == "holdout"
    assert splits["N-T04"] == "holdout"


def test_every_case_is_labeled_synthetic_and_not_real() -> None:
    for case_id, spec in CASES.items():
        payload = load_case_payload(case_id)
        source = case_source(payload)
        expectations = payload["regression_expectations"]
        assert spec["source"] == "synthetic"
        assert source == "synthetic"
        assert expectations["source"] == "synthetic"
        assert "real" not in source.lower()
        assert "licensed" not in source.lower()
        notes = str((payload.get("standards") or {}).get("notes") or "")
        assert "licensed" not in notes.lower() or "not a licensed" in notes.lower()


def test_no_site_independent_stall_count_kpi() -> None:
    for case_id in CASES:
        payload = load_case_payload(case_id)
        expectations = payload["regression_expectations"]
        assert "not_a_kpi" in expectations
        assert "stall" in str(expectations["not_a_kpi"]).lower()
        assert "minimum_stall_count" not in expectations
        assert RECT_HUMAN_PARKING_AISLES == 3


def test_nt01_current_single_spine_is_not_the_human_ladder() -> None:
    site = load_case_site("N-T01")
    layout = generate_layout(site)
    heading = site.entrances[0].heading_degrees

    assert rectangle_current_is_insufficient(layout, site)
    assert distinct_parallel_parking_aisle_count(layout, heading) < RECT_HUMAN_PARKING_AISLES
    assert not both_end_cross_aisles(layout, heading)
    # Solved predicate is encoded now; N4 is what must make it true.
    assert rectangle_is_solved(layout, site) is False
    assert not (distinct_parallel_parking_aisle_count(layout, heading) >= 2 and has_real_cross_aisle(layout, heading))


def test_nt02_current_layout_leaves_the_east_wing_unused() -> None:
    site = load_case_site("N-T02")
    layout = generate_layout(site)

    assert l_shape_current_is_insufficient(layout, site)
    assert uses_second_l_wing(layout, site) is False
    assert l_shape_is_solved(layout, site) is False


def test_nt03_two_hard_aisles_do_not_fit_and_width_must_not_shrink() -> None:
    site = load_case_site("N-T03")
    layout = generate_layout(site)

    assert tight_site_cannot_fit_two_aisles(site)
    assert site_width(site) == 10.0
    assert hard_aisle_width(site) == 6.0
    assert two_aisle_clear_width(site) == 12.0
    assert published_aisles_respect_hard_width(layout, site)
    heading = site.entrances[0].heading_degrees
    assert distinct_parallel_parking_aisle_count(layout, heading) < 2


def test_nt04_intended_t_obstacle_is_present_for_later_envelope_rejection() -> None:
    site = load_case_site("N-T04")
    payload = load_case_payload("N-T04")
    layout = generate_layout(site)

    assert turn_reject_obstacle_blocks_intended_t(site)
    intended = payload["metadata"]["v0_5"]["intended_t"]
    assert intended["obstacle_id"] == "t-fillet-block"
    assert intended["junction"] == [11, 40]
    assert published_aisles_respect_hard_width(layout, site)
    # Contact is not a passed turn; N5 must keep this fail-closed.
    assert payload["regression_expectations"]["solved_when"].startswith("an undeclared")
