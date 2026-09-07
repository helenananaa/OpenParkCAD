from __future__ import annotations

import importlib.util
from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict

_REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("v0_5_n9_matrix", _REPO / "tools" / "v0_5_n9_matrix.py")
_MATRIX = importlib.util.module_from_spec(_SPEC)
assert _SPEC is not None and _SPEC.loader is not None
_SPEC.loader.exec_module(_MATRIX)
REPEATS = _MATRIX.REPEATS
apply_variant = _MATRIX.apply_variant
load_cases = _MATRIX.load_cases
variants = _MATRIX.variants


def test_matrix_shape_is_corpus_times_sixteen_times_three() -> None:
    cases = load_cases()
    vars_ = variants()
    ids = {case["case_id"] for case in cases}
    assert "phase0-site" in ids
    assert "parallel-ladder-rect" in ids
    assert "parallel-ladder-l" in ids
    assert "parallel-ladder-tight-reject" in ids
    assert "parallel-ladder-turn-reject" in ids
    assert len(cases) == 24
    assert len(vars_) == 16
    assert REPEATS == 3
    assert len(cases) * len(vars_) * REPEATS == 1152
    assert {item["family"] for item in vars_} == {"off", "parallel_ladder"}
    assert {item["selector"] for item in vars_} == {"greedy", "cpsat"}


def test_family_overlay_changes_shipped_generate_path() -> None:
    raw = {
        "version": "0.3",
        "name": "matrix-overlay",
        "site": {"boundary": {"type": "polygon", "points": [[0, 0], [36, 0], [36, 40], [0, 40]]}},
        "entrances": [
            {
                "id": "main",
                "mode": "shared",
                "center": [18, 0],
                "width": 8.0,
                "heading_degrees": 90,
                "allowed_movements": ["enter", "exit"],
            }
        ],
        "parking": {
            "stall_types": [
                {"id": "standard-90", "family": "perpendicular", "width": 2.5, "length": 5.0, "allowed_angles": [90]}
            ]
        },
        "aisles": {
            "selection_mode": "fixed",
            "fixed_class": "wide-two-way-no-cross",
            "classes": [{"id": "wide-two-way-no-cross", "width": 6.0, "capacity": "two_vehicle", "directionality": "two_way"}],
        },
        "optimization": {"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    }
    off = apply_variant(
        raw,
        {"family": "off", "selector": "greedy", "promotion": False, "road_traversal": False},
    )
    on = apply_variant(
        raw,
        {"family": "parallel_ladder", "selector": "greedy", "promotion": False, "road_traversal": False},
    )
    assert off["optimization"]["road_network"]["enabled"] is False
    assert on["optimization"]["road_network"]["enabled"] is True
    assert "parallel_ladder" in on["optimization"]["road_network"]["families"]
    off_layout = generate_layout(site_from_dict(off))
    on_layout = generate_layout(site_from_dict(on))
    off_report = (off_layout.layout_search or {}).get("road_network_search")
    on_report = (on_layout.layout_search or {}).get("road_network_search") or {}
    assert not off_report
    assert on_report.get("requested") is True
    assert on_report.get("executed") is True
    assert (_REPO / "tools" / "v0_5_n9_matrix.py").is_file()
