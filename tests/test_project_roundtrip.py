from __future__ import annotations

from pathlib import Path

from openparkcad.project_model import PROJECT_VERSION, load_project, parse_project, save_project
from openparkcad.project_service import ProjectService
from tests.road_traversal_support import through_layout


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
