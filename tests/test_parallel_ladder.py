from __future__ import annotations

from openparkcad.road_skeleton import make_movement, make_node, make_segment, make_skeleton
from openparkcad.road_skeleton_validation import CODE_ENTRANCE_PORT_MISMATCH, validate_skeleton
from openparkcad.topology_generators.parallel_ladder import (
    generate_parallel_ladder_skeletons,
    skeleton_debug_json,
    skeleton_debug_svg,
)
from tests.v0_5_parallel_ladder_support import (
    distinct_parallel_parking_aisle_count,
    hard_aisle_width,
    has_real_cross_aisle,
    l_shape_is_solved,
    load_case_site,
    published_aisles_respect_hard_width,
    rectangle_is_solved,
    tight_site_cannot_fit_two_aisles,
    uses_second_l_wing,
)


def test_nt01_wide_rectangle_has_parallel_parking_and_cross() -> None:
    site = load_case_site("N-T01")
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 16})
    assert result.candidates
    heading = site.entrances[0].heading_degrees
    solved = False
    for candidate in result.candidates:
        layout = _layout_from_skeleton(site, candidate.skeleton)
        if rectangle_is_solved(layout, site) and distinct_parallel_parking_aisle_count(layout, heading) >= 2:
            assert has_real_cross_aisle(layout, heading)
            solved = True
            break
    assert solved
    assert all(abs(seg.width - hard_aisle_width(site)) < 1e-9 for cand in result.candidates for seg in cand.skeleton.segments)


def test_nt02_l_shape_uses_second_wing() -> None:
    site = load_case_site("N-T02")
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends"})
    assert result.candidates
    assert any(l_shape_is_solved(_layout_from_skeleton(site, cand.skeleton), site) for cand in result.candidates)
    assert any(uses_second_l_wing(_layout_from_skeleton(site, cand.skeleton), site) for cand in result.candidates)


def test_nt03_narrow_site_does_not_shrink_width() -> None:
    site = load_case_site("N-T03")
    assert tight_site_cannot_fit_two_aisles(site)
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends"})
    for candidate in result.candidates:
        assert all(abs(seg.width - 6.0) < 1e-9 for seg in candidate.skeleton.segments)
        layout = _layout_from_skeleton(site, candidate.skeleton)
        assert published_aisles_respect_hard_width(layout, site)
        heading = site.entrances[0].heading_degrees
        assert distinct_parallel_parking_aisle_count(layout, heading) < 2
    assert result.failure_class == "no_geometric_corridor" or all(
        cand.aisle_count < 2 for cand in result.candidates
    )


def test_nt04_obstacle_cuts_or_rejects_band() -> None:
    site = load_case_site("N-T04")
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends"})
    for candidate in result.candidates:
        assert all(abs(seg.width - site.aisle_width) < 1e-9 for seg in candidate.skeleton.segments)
        for segment in candidate.skeleton.segments:
            from shapely.geometry import Polygon as ShapelyPolygon

            obstacle = ShapelyPolygon([(12, 34), (24, 34), (24, 44), (12, 44)])
            from openparkcad.road_skeleton_geometry import derive_segment_polygon

            overlap = derive_segment_polygon(segment).intersection(obstacle).area
            assert overlap < 0.5


def test_nt05_entrance_not_contacting_cross_is_rejected() -> None:
    site = load_case_site("N-T01")
    n0 = make_node("N0", "entrance_port", (8.0, 0.0), source_id="south-gate")
    n1 = make_node("N1", "terminal", (8.0, 10.0))
    n2 = make_node("N2", "terminal", (40.0, 40.0))
    n3 = make_node("N3", "terminal", (50.0, 40.0))
    spine = make_segment("S", "parking_aisle", "N0", "N1", ((8.0, 0.0), (8.0, 10.0)), 6.0, "two_way")
    far = make_segment("C", "cross_aisle", "N2", "N3", ((40.0, 40.0), (50.0, 40.0)), 6.0, "two_way")
    skeleton = make_skeleton(
        family="parallel_ladder",
        nodes=[n0, n1, n2, n3],
        segments=[spine, far],
        movements=[make_movement("M", "S", "C", "N1", "left")],
        entrance_ids=("south-gate",),
        site=site,
        strict=False,
    )
    issues = validate_skeleton(skeleton, site=site)
    assert any(issue.code in {CODE_ENTRANCE_PORT_MISMATCH, "declared_connection_without_contact"} for issue in issues)


def test_nt06_ids_and_order_are_stable() -> None:
    site = load_case_site("N-T01")
    first = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 8})
    second = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 8})
    assert [c.skeleton.skeleton_id for c in first.candidates] == [c.skeleton.skeleton_id for c in second.candidates]


def test_nt07_max_skeletons_truncates_with_visible_counts() -> None:
    site = load_case_site("N-T01")
    limited = generate_parallel_ladder_skeletons(
        site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1, "max_parallel_aisles": 6}
    )
    assert limited.counts["retained"] <= 1
    assert limited.counts["generated"] >= limited.counts["retained"]
    if limited.counts["generated"] > 1:
        assert limited.truncated is True
        assert limited.counts["omitted_by_cap"] >= 1


def test_nt08_prefilter_score_is_not_official_score_layout() -> None:
    site = load_case_site("N-T01")
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends"})
    assert result.candidates
    payload = skeleton_debug_json(result.candidates[0])
    assert "prefilter_score" in payload
    assert "score_layout" not in payload
    assert "final_score" not in payload
    assert result.candidates[0].prefilter_score == payload["prefilter_score"]


def test_debug_svg_and_json_are_emitted() -> None:
    site = load_case_site("N-T01")
    result = generate_parallel_ladder_skeletons(site, config={"cross_aisle_policy": "both_ends", "max_skeletons": 1})
    assert result.candidates
    svg = skeleton_debug_svg(result.candidates[0], site)
    data = skeleton_debug_json(result.candidates[0])
    assert "<svg" in svg
    assert data["family"] == "parallel_ladder"
    assert data["aisle_count"] >= 2


def _layout_from_skeleton(site, skeleton):
    from openparkcad.models import LayoutResult, ParkingAisle
    from openparkcad.road_skeleton_geometry import derive_segment_polygon
    from tests.v0_5_parallel_ladder_support import aisle_centerline_heading

    aisles = []
    for segment in skeleton.segments:
        poly = derive_segment_polygon(segment)
        coords = list(poly.exterior.coords)[:-1]
        role = "main" if segment.role == "parking_aisle" else segment.role
        aisle = ParkingAisle(id=segment.id, polygon=coords, angle_degrees=90.0, role=role)
        object.__setattr__(aisle, "angle_degrees", aisle_centerline_heading(aisle))
        aisles.append(aisle)
    return LayoutResult(site=site, stalls=[], aisles=aisles)
