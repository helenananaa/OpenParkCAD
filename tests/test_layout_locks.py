from __future__ import annotations

from openparkcad.layout_locks import lock_from_aisle, lock_from_stalls, locks_satisfied
from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall
from tests.road_traversal_support import through_layout


def test_ut03_main_aisle_lock_keeps_geometry() -> None:
    layout = through_layout()
    lock = lock_from_aisle(layout.aisles[0])
    ok, conflicts = locks_satisfied(layout, [lock])
    assert ok is True
    assert conflicts == []
    moved = LayoutResult(
        site=layout.site,
        stalls=layout.stalls,
        aisles=[
            ParkingAisle(
                id=layout.aisles[0].id,
                polygon=[(0.0, 10.0), (40.0, 10.0), (40.0, 16.0), (0.0, 16.0)],
                angle_degrees=0.0,
                role="main",
                directionality="two_way",
            )
        ],
    )
    ok, conflicts = locks_satisfied(moved, [lock])
    assert ok is False
    assert conflicts[0]["reason"] == "locked_main_aisle_geometry_changed"


def test_ut04_stall_group_lock_conflicts_when_missing() -> None:
    layout = through_layout()
    lock = lock_from_stalls(layout.stalls, lock_id="g1", project_object_id="group-1")
    ok, _ = locks_satisfied(layout, [lock])
    assert ok is True
    empty = LayoutResult(site=layout.site, stalls=[], aisles=layout.aisles)
    ok, conflicts = locks_satisfied(empty, [lock])
    assert ok is False
    assert conflicts[0]["reason"] == "locked_stall_group_moved_or_missing"


def test_ut05_project_object_id_does_not_follow_renumbered_official_id() -> None:
    layout = through_layout()
    lock = lock_from_stalls(layout.stalls, lock_id="g1", project_object_id="stable-group")
    renumbered = LayoutResult(
        site=layout.site,
        stalls=[ParkingStall("P-999", layout.stalls[0].polygon, 90.0, served_by_aisle_id="A-MAIN")],
        aisles=layout.aisles,
    )
    ok, conflicts = locks_satisfied(renumbered, [lock])
    assert ok is True
    assert conflicts == []
    assert lock.project_object_id == "stable-group"
    assert lock.project_object_id != "P-999"
