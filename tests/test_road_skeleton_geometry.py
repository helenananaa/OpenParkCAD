from __future__ import annotations

from shapely.geometry import Point as ShapelyPoint

from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.road_skeleton import make_node, make_segment, make_skeleton
from openparkcad.road_skeleton_geometry import (
    ROAD_SKELETON_GEOMETRY_VERSION,
    derive_segment_polygon,
    derived_geometry_report,
    geometry_strategy,
)
from openparkcad.road_skeleton_validation import CODE_ROAD_OUTSIDE_DRIVEABLE, validate_skeleton


def test_centerline_buffer_does_not_fill_concave_undriveable_pocket() -> None:
    u_shape = make_segment(
        "U",
        "parking_aisle",
        "A",
        "B",
        ((0.0, 0.0), (0.0, 20.0), (20.0, 20.0), (20.0, 0.0)),
        2.0,
        "two_way",
    )
    pavement = derive_segment_polygon(u_shape)
    inside_u = ShapelyPoint(10.0, 4.0)
    on_arm = ShapelyPoint(0.0, 10.0)
    assert pavement.contains(on_arm) or pavement.covers(on_arm)
    assert not pavement.contains(inside_u)
    assert not pavement.covers(inside_u)
    convex = pavement.convex_hull
    assert convex.contains(inside_u)
    assert convex.area > pavement.area + 1.0


def test_geometry_strategy_is_versioned_centerline_buffer() -> None:
    strategy = geometry_strategy()
    assert strategy["version"] == ROAD_SKELETON_GEOMETRY_VERSION
    assert strategy["construction"] == "centerline_buffer"
    assert strategy["cap_style"] == "round"
    assert strategy["join_style"] == "round"
    assert strategy["offset"] == "half_width"


def test_report_polygons_match_derived_centerline_not_a_substitute() -> None:
    n0 = make_node("N0", "terminal", (0.0, 0.0))
    n1 = make_node("N1", "terminal", (10.0, 0.0))
    segment = make_segment("S", "parking_aisle", "N0", "N1", ((0.0, 0.0), (10.0, 0.0)), 4.0, "two_way")
    skeleton = make_skeleton(family="legacy", nodes=[n0, n1], segments=[segment], entrance_ids=())
    report = derived_geometry_report(skeleton)
    derived = derive_segment_polygon(segment)
    assert report["version"] == ROAD_SKELETON_GEOMETRY_VERSION
    assert report["segments"]["S"]["area"] == derived.area
    assert report["segments"]["S"]["wkt"] == derived.wkt


def test_road_outside_driveable_area_fail_closes() -> None:
    site = SiteSpec(
        name="small",
        boundary=[(0.0, 0.0), (12.0, 0.0), (12.0, 12.0), (0.0, 12.0)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[EntranceSpec(id="south", mode="shared", center=(6.0, 0.0), width=6.0, heading_degrees=90.0)],
        aisle_classes=[AisleClassSpec(id="wide-two-way-no-cross", width=6.0, capacity="two_vehicle", directionality="two_way")],
        fixed_aisle_class="wide-two-way-no-cross",
    )
    n0 = make_node("N0", "entrance_port", (6.0, 0.0), source_id="south")
    n1 = make_node("N1", "terminal", (40.0, 0.0))
    outside = make_segment("OUT", "parking_aisle", "N0", "N1", ((6.0, 0.0), (40.0, 0.0)), 6.0, "two_way")
    skeleton = make_skeleton(
        family="legacy",
        nodes=[n0, n1],
        segments=[outside],
        entrance_ids=("south",),
        site=site,
        strict=False,
    )
    issues = validate_skeleton(skeleton, site=site)
    assert any(issue.code == CODE_ROAD_OUTSIDE_DRIVEABLE for issue in issues)
