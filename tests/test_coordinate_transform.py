from __future__ import annotations

import pytest

from openparkcad.coordinate_transform import (
    LOOP_TOLERANCE_M,
    build_transform,
    resolve_source_units,
    scale_for_unit,
)


def test_metre_and_millimetre_scales() -> None:
    assert scale_for_unit("m") == 1.0
    assert scale_for_unit("mm") == 0.001


def test_same_rectangle_in_m_and_mm_and_translation_share_local_geometry() -> None:
    metres = [(0.0, 0.0), (20.0, 0.0), (20.0, 30.0), (0.0, 30.0)]
    millimetres = [(0.0, 0.0), (20000.0, 0.0), (20000.0, 30000.0), (0.0, 30000.0)]
    translated = [(100000000.0, 200000000.0), (100020000.0, 200000000.0), (100020000.0, 200030000.0), (100000000.0, 200030000.0)]
    t_m = build_transform(source_units="m", unit_source="mapping", source_boundary=metres)
    t_mm = build_transform(source_units="mm", unit_source="mapping", source_boundary=millimetres)
    t_tr = build_transform(source_units="mm", unit_source="mapping", source_boundary=translated)
    assert t_m.polygon_to_local(metres) == t_mm.polygon_to_local(millimetres)
    local_tr = t_tr.polygon_to_local(translated)
    assert local_tr[0] == pytest.approx((0.0, 0.0))
    assert local_tr[2] == pytest.approx((20.0, 30.0))
    assert t_mm.max_roundtrip_error_m(millimetres) < LOOP_TOLERANCE_M
    assert t_tr.max_roundtrip_error_m(translated) < LOOP_TOLERANCE_M


def test_unit_conflict_and_missing_are_not_guessed() -> None:
    with pytest.raises(ValueError, match="conflict"):
        resolve_source_units(mapping_units="mm", dxf_units="m")
    with pytest.raises(ValueError, match="missing"):
        resolve_source_units(mapping_units=None, dxf_units=None)
    with pytest.raises(ValueError, match="unsupported"):
        resolve_source_units(mapping_units=None, dxf_units="ft")
