from __future__ import annotations

from pathlib import Path

from openparkcad.models import LayoutResult, ParkingAisle
from openparkcad.project_model import load_project, save_project
from openparkcad.project_service import ProjectService, TaskResult
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from tests.road_traversal_support import through_layout
from tests.test_cli_and_exporters import _valid_site_data


def test_ut06_save_reload_undo_redo(tmp_path: Path) -> None:
    layout = through_layout(enabled=False)
    service = ProjectService()
    service.accept_layout(layout)
    first = service.state.current_revision
    service.lock_main_aisle("A-MAIN")
    second = service.state.current_revision
    assert second > first
    path = tmp_path / "project.json"
    save_project(service.state, path)
    loaded = load_project(path)
    assert loaded.current_revision == second
    assert loaded.version == "openparkcad-project-1"
    service.undo()
    assert service.state.current_revision == first
    service.redo()
    assert service.state.current_revision == second


def test_ut07_out_of_order_result_does_not_replace_current() -> None:
    layout = through_layout(enabled=False)
    service = ProjectService()
    service.accept_layout(layout)
    current = service.state.current_revision
    late = TaskResult(revision=current - 1 if current else 0, input_digest="old", status="accepted", layout=layout)
    assert service.apply_late_result(late) is False
    assert service.state.current_revision == current


def test_ut08_cancel_and_timeout_keep_last_accepted() -> None:
    layout = through_layout(enabled=False)
    service = ProjectService(generate_fn=lambda site: layout)
    service.accept_layout(layout)
    service.cancel()
    result = service.regenerate()
    assert result.status == "cancelled"
    assert result.layout is layout
    timed = service.regenerate(timeout_seconds=0)
    assert timed.status in {"timeout", "cancelled"}
    assert timed.layout is layout


def test_ut09_export_rebuilds_and_revalidates() -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    service = ProjectService()
    service.accept_layout(layout)
    exported = service.export_accepted()
    assert exported.aisles
    assert exported.graph_validation.get("valid") is True
    assert exported.engineering_validation.get("valid") is True


def test_service_from_loaded_state_exports_without_second_accept(tmp_path: Path) -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    service = ProjectService()
    service.accept_layout(layout)
    main = next(aisle for aisle in layout.aisles if aisle.role == "main")
    path = tmp_path / "project.json"
    save_project(service.state, path)
    reopened = ProjectService(load_project(path))
    reopened.lock_main_aisle(main.id)
    reopened.lock_stall_group([layout.stalls[0].id], lock_id="loaded-group")
    exported = reopened.export_accepted()
    assert exported.stall_count == layout.stall_count
    assert exported.engineering_validation.get("valid") is True


def test_constrained_regen_keeps_accepted_on_lock_conflict() -> None:
    layout = through_layout(enabled=False)

    def moved(site):
        return LayoutResult(
            site=site,
            stalls=layout.stalls,
            aisles=[
                ParkingAisle(
                    id="A-MAIN",
                    polygon=[(0.0, 12.0), (40.0, 12.0), (40.0, 18.0), (0.0, 18.0)],
                    angle_degrees=0.0,
                    role="main",
                )
            ],
            graph_validation={"valid": True},
            maneuver_validation={"valid": True},
            site_constraint_validation={"valid": True},
            operational_quality={"valid": True},
            engineering_validation={"valid": True},
        )

    service = ProjectService(generate_fn=moved)
    service.accept_layout(layout)
    service.lock_main_aisle("A-MAIN")
    result = service.regenerate()
    assert result.status == "conflict"
    assert result.layout is layout
    assert result.conflicts
