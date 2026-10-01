from __future__ import annotations

from pathlib import Path

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from openparkcad.project_model import PROJECT_VERSION, load_project, parse_project, save_project
from openparkcad.project_service import ProjectService
from tests.road_traversal_support import through_layout
from tests.test_cli_and_exporters import _valid_site_data


def test_unsupported_project_version_is_not_guess_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text('{"version": "openparkcad-project-0", "name": "x"}', encoding="utf-8")
    try:
        load_project(path)
        raise AssertionError("expected version error")
    except ValueError as exc:
        assert "unsupported project version" in str(exc)


def test_project_roundtrip_preserves_revision_and_locks(tmp_path: Path) -> None:
    service = ProjectService()
    service.accept_layout(through_layout(enabled=False))
    service.lock_main_aisle("A-MAIN")
    path = tmp_path / "project.json"
    save_project(service.state, path)
    loaded = load_project(path)
    assert loaded.version == PROJECT_VERSION
    assert loaded.current_revision == service.state.current_revision
    assert loaded.revisions[-1].locks
    again = parse_project(loaded.to_record())
    assert again.revisions[-1].input_digest == loaded.revisions[-1].input_digest


def test_reopen_restores_accepted_layout_for_lock_and_export(tmp_path: Path) -> None:
    layout = generate_layout(site_from_dict(_valid_site_data()))
    assert layout.stall_count > 0
    main = next(aisle for aisle in layout.aisles if aisle.role == "main")
    stall_id = layout.stalls[0].id
    service = ProjectService()
    service.accept_layout(layout)
    path = tmp_path / "project.json"
    save_project(service.state, path)
    reopened = ProjectService(load_project(path))
    assert reopened.last_accepted is not None
    aisle_lock = reopened.lock_main_aisle(main.id)
    assert aisle_lock.object_id == main.id
    stall_lock = reopened.lock_stall_group([stall_id], lock_id="g-reopen")
    assert stall_lock.lock_id == "g-reopen"
    exported = reopened.export_accepted()
    assert exported.stall_count == layout.stall_count
    assert {aisle.id for aisle in exported.aisles} == {aisle.id for aisle in layout.aisles}
    assert exported.graph_validation.get("valid") is True
    assert exported.engineering_validation.get("valid") is True
