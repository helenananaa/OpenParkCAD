from __future__ import annotations

from openparkcad.layout_benchmark import classify_expectation, extract_checks
from openparkcad.road_traversal import apply_road_traversal
from tests.road_traversal_support import through_layout


def test_required_road_traversal_check_extracts_coverage_and_reasons() -> None:
    passed = apply_road_traversal(through_layout())
    checks = extract_checks(passed)
    road = checks["road_traversal"]
    assert road["executed"] is True
    assert road["requested"] is True
    assert road["status"] == "passed"
    assert road["valid"] is True
    assert road["stall_coverage"] == passed.stall_count
    assert road["coverage_ratio"] == 1.0

    case = {
        "expectation": "valid_with_required_checks",
        "required_checks": ["road_traversal"],
    }
    payload = {"checks": checks}
    assert classify_expectation(case, "valid", payload) is True

    blocked = apply_road_traversal(through_layout(extra_obstacle=[(8.0, 2.2), (14.0, 2.2), (14.0, 7.8), (8.0, 7.8)]))
    assert classify_expectation(
        {"expectation": "invalid_with_required_checks", "required_checks": ["road_traversal"]},
        "invalid",
        {"checks": extract_checks(blocked)},
    ) is True
    blocked_checks = extract_checks(blocked)["road_traversal"]
    assert blocked_checks["executed"] is True
    assert blocked_checks["status"] != "passed"
    assert blocked_checks["valid"] is not True
    assert blocked_checks["failure_reasons"]
