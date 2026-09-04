from __future__ import annotations

from openparkcad.road_skeleton import make_movement, make_node, make_segment, make_skeleton
from openparkcad.road_skeleton_validation import (
    CODE_DECLARED_CONNECTION_WITHOUT_CONTACT,
    CODE_ENTRANCE_PORT_MISMATCH,
    CODE_UNDECLARED_INTERSECTION,
    validate_skeleton,
)
from tests.v0_5_parallel_ladder_support import load_case_site


def test_ladder_validation_codes_are_stable() -> None:
    site = load_case_site("N-T01")
    n0 = make_node("N0", "entrance_port", (28.0, 0.0), source_id="south-gate")
    n1 = make_node("N1", "junction", (28.0, 20.0))
    n2 = make_node("N2", "terminal", (40.0, 20.0))
    n3 = make_node("NX", "terminal", (10.0, 40.0))
    n4 = make_node("NY", "terminal", (20.0, 40.0))
    spine = make_segment("S", "parking_aisle", "N0", "N1", ((28.0, 0.0), (28.0, 20.0)), 6.0, "two_way")
    cross = make_segment("C", "cross_aisle", "N1", "N2", ((28.0, 20.0), (40.0, 20.0)), 6.0, "two_way")
    ghost = make_segment("G", "parking_aisle", "NX", "NY", ((10.0, 40.0), (20.0, 40.0)), 6.0, "two_way")
    skeleton = make_skeleton(
        family="parallel_ladder",
        nodes=[n0, n1, n2, n3, n4],
        segments=[spine, cross, ghost],
        movements=[
            make_movement("M-OK", "S", "C", "N1", "left"),
            make_movement("M-FAKE", "S", "G", "N1", "left"),
        ],
        entrance_ids=("south-gate",),
        site=site,
        strict=False,
    )
    codes = {issue.code for issue in validate_skeleton(skeleton, site=site)}
    assert CODE_DECLARED_CONNECTION_WITHOUT_CONTACT in codes or CODE_UNDECLARED_INTERSECTION in codes or CODE_ENTRANCE_PORT_MISMATCH in codes
