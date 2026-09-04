from __future__ import annotations

from openparkcad.layout_locks import locks_satisfied
from openparkcad.project_service import ProjectService
from tests.road_traversal_support import through_layout


def test_locked_main_aisle_regenerate_accepts_matching_geometry() -> None:
    layout = through_layout(enabled=False)
    service = ProjectService(generate_fn=lambda site: layout)
    service.accept_layout(layout)
    lock = service.lock_main_aisle("A-MAIN")
    result = service.regenerate()
    assert result.status == "accepted"
    ok, conflicts = locks_satisfied(result.layout, [lock])
    assert ok is True
    assert conflicts == []
    assert result.layout.aisles[0].polygon == layout.aisles[0].polygon
