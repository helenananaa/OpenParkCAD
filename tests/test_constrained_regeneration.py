from __future__ import annotations

from openparkcad.generator import generate_layout
from openparkcad.layout_locks import _polygon_mismatch, locks_satisfied
from openparkcad.models import site_from_dict
from openparkcad.project_service import ProjectService
from tests.test_cli_and_exporters import _valid_site_data


def test_ut03_locked_main_aisle_keeps_geometry_while_stalls_rechecked() -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    assert layout.stall_count > 0
    main = next(aisle for aisle in layout.aisles if aisle.role == "main")
    service = ProjectService()
    service.accept_layout(layout)
    lock = service.lock_main_aisle(main.id)
    service.add_obstacle([[1.0, 18.0], [5.5, 18.0], [5.5, 24.0], [1.0, 24.0]], obstacle_id="stall-side-block")
    result = service.regenerate()
    assert result.status == "accepted"
    assert result.layout is not layout
    ok, conflicts = locks_satisfied(result.layout, [lock])
    assert ok is True
    assert conflicts == []
    new_main = next(aisle for aisle in result.layout.aisles if aisle.id == main.id or aisle.role == "main")
    assert _polygon_mismatch(lock.geometry, new_main.polygon) is False
    assert new_main.directionality == (lock.directionality or main.directionality)
    assert result.layout.engineering_validation.get("valid") is True
    assert result.layout.graph_validation.get("valid") is True
    assert result.layout.stall_count > 0


def test_ut04_locked_stall_group_conflicts_with_new_obstacle() -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    assert layout.stalls
    stall = layout.stalls[0]
    service = ProjectService()
    service.accept_layout(layout)
    lock = service.lock_stall_group([stall.id], lock_id="g1")
    service.add_obstacle([list(point) for point in stall.polygon], obstacle_id="on-locked-stall")
    result = service.regenerate()
    assert result.status == "conflict"
    assert result.layout is layout
    assert result.conflicts
    assert any(
        item.get("reason") in {"locked_geometry_conflicts_with_site", "locked_stall_group_moved_or_missing"}
        for item in result.conflicts
    )
    ok, _ = locks_satisfied(layout, [lock])
    assert ok is True
