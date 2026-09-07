from __future__ import annotations

import math

import pytest

from openparkcad.road_network_config import parse_road_network_mapping


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"enabled": True, "max_skeletons": False}, "positive integer"),
        ({"enabled": True, "max_skeletons": 1.9}, "positive integer"),
        ({"enabled": True, "dominant_axis_count": 0}, "positive integer"),
        ({"enabled": True, "max_parallel_aisles": "2"}, "positive integer"),
        ({"enabled": True, "max_full_evaluations": False}, "positive integer"),
        ({"enabled": True, "refinement_budget_seconds": math.nan}, "positive finite"),
        ({"enabled": True, "refinement_budget_seconds": math.inf}, "positive finite"),
        ({"enabled": 1}, "boolean"),
        ({"enabled": True, "families": "parallel_ladder"}, "array of strings"),
        ({"enabled": True, "unknown_flag": True}, "unknown keys"),
    ],
)
def test_illegal_road_network_values_fail_closed(payload, match) -> None:
    with pytest.raises(ValueError, match=match):
        parse_road_network_mapping(payload)


def test_omitted_values_keep_defaults() -> None:
    parsed = parse_road_network_mapping({"enabled": True, "families": ["legacy", "parallel_ladder"]})
    assert parsed.max_skeletons == 16
    assert parsed.dominant_axis_count == 2
    assert parsed.max_parallel_aisles == 6
    assert parsed.max_full_evaluations == 8
    assert parsed.refinement_budget_seconds is None
    assert parsed.cross_aisle_policy == "entry_end"
