"""Project operations: locks, constrained regenerate, cancel, and stale results."""

from __future__ import annotations

import threading
import time
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from openparkcad.cli import _final_layout_errors
from openparkcad.generator import generate_layout
from openparkcad.layout_locks import (
    LayoutLock,
    lock_from_aisle,
    lock_from_entrance,
    lock_from_stalls,
    lock_site_conflicts,
    locks_satisfied,
    parse_lock,
)
from openparkcad.models import LayoutResult, site_from_dict
from openparkcad.project_model import (
    ProjectRevision,
    ProjectState,
    accepted_layout_snapshot,
    input_digest,
    layout_from_project_state,
    site_dict_from_layout,
)


GenerateFn = Callable[..., LayoutResult]


@dataclass
class TaskResult:
    revision: int
    input_digest: str
    status: str
    layout: LayoutResult | None = None
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class ProjectService:
    def __init__(self, state: ProjectState | None = None, *, generate_fn: GenerateFn | None = None) -> None:
        self.state = state or ProjectState()
        self._generate = generate_fn or generate_layout
        self._lock = threading.Lock()
        self._active_revision: int | None = None
        self._cancel = threading.Event()
        self.last_accepted: LayoutResult | None = layout_from_project_state(self.state)

    def accept_layout(self, layout: LayoutResult, *, site: dict[str, Any] | None = None, locks: list[dict[str, Any]] | None = None) -> int:
        with self._lock:
            site_payload = deepcopy(site) if site is not None else site_dict_from_layout(layout)
            lock_payload = locks if locks is not None else (self.state.revisions[-1].locks if self.state.revisions else [])
            digest = input_digest(site_payload, lock_payload)
            revision = self.state.current_revision + 1
            self.state.current_revision = revision
            self.state.revisions.append(
                ProjectRevision(revision=revision, site=site_payload, locks=lock_payload, input_digest=digest, accepted_layout_ref=f"rev-{revision}")
            )
            self.state.accepted_site = site_payload
            self.state.accepted_layout = accepted_layout_snapshot(layout)
            self._remember_object_ids(layout)
            self.last_accepted = layout
            self._push_history()
            return revision

    def lock_entrance(self, entrance_id: str) -> LayoutLock:
        if self.last_accepted is None:
            raise ValueError("no accepted layout to lock")
        entrance = next((item for item in self.last_accepted.site.entrances if item.id == entrance_id), None)
        if entrance is None:
            raise ValueError(f"entrance {entrance_id} not in accepted layout")
        lock = lock_from_entrance(entrance)
        self._append_lock(lock)
        return lock

    def lock_main_aisle(self, aisle_id: str) -> LayoutLock:
        if self.last_accepted is None:
            raise ValueError("no accepted layout to lock")
        aisle = next((item for item in self.last_accepted.aisles if item.id == aisle_id), None)
        if aisle is None:
            raise ValueError(f"aisle {aisle_id} not in accepted layout")
        if self.last_accepted.generation_mode == "parallel_ladder":
            return self.lock_road(aisle_id)
        lock = replace(lock_from_aisle(aisle), project_object_id=self.state.object_ids.get(aisle.id, aisle.id))
        self._append_lock(lock)
        return lock

    def lock_road(self, aisle_id: str) -> LayoutLock:
        if self.last_accepted is None:
            raise ValueError("no accepted layout to lock")
        aisle = next((item for item in self.last_accepted.aisles if item.id == aisle_id), None)
        if aisle is None:
            raise ValueError(f"road {aisle_id} not in accepted layout")
        lock = LayoutLock(
            lock_id=f"lock-{aisle.id}", kind="road", object_id=aisle.id,
            project_object_id=self.state.object_ids.get(aisle.id, aisle.id),
            geometry=list(aisle.polygon), heading_degrees=aisle.angle_degrees,
            directionality=aisle.directionality,
        )
        self._append_lock(lock)
        return lock

    def lock_stall_group(self, stall_ids: list[str], *, lock_id: str) -> LayoutLock:
        if self.last_accepted is None:
            raise ValueError("no accepted layout to lock")
        stalls = [stall for stall in self.last_accepted.stalls if stall.id in set(stall_ids)]
        lock = lock_from_stalls(stalls, lock_id=lock_id, project_object_id=self.state.object_ids.get(stall_ids[0], stall_ids[0]))
        self._append_lock(lock)
        return lock

    def unlock(self, lock_id: str) -> None:
        with self._lock:
            if not self.state.revisions:
                return
            latest = self.state.revisions[-1]
            latest.locks = [item for item in latest.locks if item.get("lock_id") != lock_id]
            latest.input_digest = input_digest(latest.site, latest.locks)

    def add_obstacle(self, polygon: list[list[float]], *, obstacle_id: str = "user-obstacle") -> int:
        with self._lock:
            if not self.state.revisions:
                raise ValueError("project has no revision")
            site = dict(self.state.revisions[-1].site)
            site_obj = dict(site.get("site") or {})
            obstacles = list(site_obj.get("obstacles") or [])
            obstacles.append({"id": obstacle_id, "geometry": {"type": "polygon", "points": polygon}})
            site_obj["obstacles"] = obstacles
            site["site"] = site_obj
            return self._new_revision(site, self.state.revisions[-1].locks)

    def regenerate(self, *, timeout_seconds: float | None = None) -> TaskResult:
        with self._lock:
            if not self.state.revisions:
                raise ValueError("project has no revision")
            revision = self.state.current_revision
            digest = self.state.revisions[-1].input_digest
            site_payload = self.state.revisions[-1].site
            locks = [parse_lock(item) for item in self.state.revisions[-1].locks]
            self._active_revision = revision
            pending_cancel = self._cancel.is_set()
            self._cancel.clear()
        started = time.perf_counter()
        try:
            if pending_cancel:
                return TaskResult(revision=revision, input_digest=digest, status="cancelled", layout=self.last_accepted)
            if timeout_seconds is not None and timeout_seconds <= 0:
                return TaskResult(revision=revision, input_digest=digest, status="timeout", layout=self.last_accepted, error="timeout")
            site = site_from_dict(site_payload)
            layout = self._invoke_generate(site, locks)
            if timeout_seconds is not None and time.perf_counter() - started > timeout_seconds:
                return self._finish_if_current(revision, digest, TaskResult(revision=revision, input_digest=digest, status="timeout", layout=self.last_accepted, error="timeout"))
            if self._cancel.is_set():
                return TaskResult(revision=revision, input_digest=digest, status="cancelled", layout=self.last_accepted)
            ok, conflicts = locks_satisfied(layout, locks)
            conflicts = list(conflicts) + lock_site_conflicts(layout, locks)
            if not ok or conflicts:
                return self._finish_if_current(
                    revision,
                    digest,
                    TaskResult(revision=revision, input_digest=digest, status="conflict", layout=self.last_accepted, conflicts=conflicts),
                )
            errors = _final_layout_errors(layout)
            if errors:
                return self._finish_if_current(
                    revision,
                    digest,
                    TaskResult(
                        revision=revision,
                        input_digest=digest,
                        status="invalid",
                        layout=self.last_accepted,
                        error="; ".join(errors),
                    ),
                )
            return self._accept_if_current(revision, digest, layout)
        except Exception as exc:
            return self._finish_if_current(
                revision,
                digest,
                TaskResult(revision=revision, input_digest=digest, status="error", layout=self.last_accepted, error=str(exc)),
            )

    def apply_late_result(self, result: TaskResult) -> bool:
        with self._lock:
            if result.revision != self.state.current_revision:
                self.state.history.append({"event": "stale_result_ignored", "revision": result.revision, "current": self.state.current_revision})
                return False
            if result.input_digest != self.state.revisions[-1].input_digest:
                self.state.history.append({"event": "digest_mismatch_ignored", "revision": result.revision})
                return False
            if result.status == "accepted" and result.layout is not None:
                locks = [parse_lock(item) for item in self.state.revisions[-1].locks]
                if (_final_layout_errors(result.layout) or not locks_satisfied(result.layout, locks)[0]
                        or lock_site_conflicts(result.layout, locks)):
                    return False
                self.last_accepted = result.layout
                self.state.accepted_site = deepcopy(self.state.revisions[-1].site)
                self.state.accepted_layout = accepted_layout_snapshot(result.layout)
                self.state.revisions[-1].accepted_layout_ref = f"rev-{result.revision}"
                self._remember_object_ids(result.layout)
                return True
            return False

    def cancel(self) -> None:
        self._cancel.set()

    def undo(self) -> None:
        with self._lock:
            if len(self.state.undo_stack) < 2:
                return
            current = self.state.undo_stack.pop()
            self.state.redo_stack.append(current)
            previous = self.state.undo_stack[-1]
            restored = _state_from_record(previous)
            self._apply_restored_state(restored)

    def redo(self) -> None:
        with self._lock:
            if not self.state.redo_stack:
                return
            current = self.state.to_record()
            nxt = self.state.redo_stack.pop()
            self.state.undo_stack.append(current)
            restored = _state_from_record(nxt)
            self._apply_restored_state(restored)

    def export_accepted(self) -> LayoutResult:
        if self.last_accepted is None:
            raise ValueError("no accepted layout")
        from openparkcad.candidate_snapshot import _with_recomputed_validation

        rebuilt = _with_recomputed_validation(self.last_accepted)
        errors = _final_layout_errors(rebuilt)
        if errors:
            raise ValueError("; ".join(errors))
        return rebuilt

    def _invoke_generate(self, site, locks: list[LayoutLock]):
        try:
            return self._generate(site, locks=locks)
        except TypeError as exc:
            message = str(exc)
            if "locks" not in message and "unexpected keyword" not in message:
                raise
            return self._generate(site)

    def _append_lock(self, lock: LayoutLock) -> None:
        with self._lock:
            if not self.state.revisions:
                raise ValueError("project has no revision")
            site = self.state.revisions[-1].site
            locks = list(self.state.revisions[-1].locks) + [lock.to_record()]
            self._new_revision(site, locks)

    def _new_revision(self, site: dict[str, Any], locks: list[dict[str, Any]]) -> int:
        digest = input_digest(site, locks)
        revision = self.state.current_revision + 1
        self.state.current_revision = revision
        self.state.revisions.append(ProjectRevision(revision=revision, site=site, locks=locks, input_digest=digest))
        self._push_history()
        return revision

    def _accept_if_current(self, revision: int, digest: str, layout: LayoutResult) -> TaskResult:
        with self._lock:
            if revision != self.state.current_revision or digest != self.state.revisions[-1].input_digest:
                return TaskResult(revision=revision, input_digest=digest, status="stale", layout=self.last_accepted)
            self.last_accepted = layout
            self.state.accepted_site = deepcopy(self.state.revisions[-1].site)
            self.state.accepted_layout = accepted_layout_snapshot(layout)
            self.state.revisions[-1].accepted_layout_ref = f"rev-{revision}"
            self._remember_object_ids(layout)
            return TaskResult(revision=revision, input_digest=digest, status="accepted", layout=layout)

    def _finish_if_current(self, revision: int, digest: str, result: TaskResult) -> TaskResult:
        with self._lock:
            if revision != self.state.current_revision or digest != self.state.revisions[-1].input_digest:
                result.status = "stale"
                result.layout = self.last_accepted
            return result

    def _apply_restored_state(self, restored: ProjectState) -> None:
        self.state.revisions = restored.revisions
        self.state.current_revision = restored.current_revision
        self.state.accepted_site = restored.accepted_site
        self.state.accepted_layout = restored.accepted_layout
        self.state.object_ids = restored.object_ids
        self.state.object_sources = restored.object_sources
        self.last_accepted = layout_from_project_state(self.state)

    def _remember_object_ids(self, layout: LayoutResult) -> None:
        from openparkcad.skeleton_identity import official_skeleton_mapping

        mapping = official_skeleton_mapping(layout)
        if mapping.get("skeleton_id") and layout.generation_mode == "parallel_ladder":
            # S-* / P-* are local to a skeleton; identical slots in a different
            # skeleton must not silently reuse an unrelated project's object.
            for item in [*layout.aisles, *layout.stalls]:
                source = mapping["objects"][item.id]
                identity = [mapping["skeleton_id"], item.id, item.polygon]
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
                project_id = f"project:{source['kind']}:{digest}"
                self.state.object_ids[item.id] = project_id
                self.state.object_sources[project_id] = {
                    "official_object_id": item.id, "skeleton_id": mapping["skeleton_id"],
                    "skeleton_version": mapping["skeleton_version"], "family": mapping["family"],
                    **source,
                }
            return
        for aisle in layout.aisles:
            self.state.object_ids.setdefault(aisle.id, f"project:{aisle.role}:{len(self.state.object_ids)+1}")
        for stall in layout.stalls:
            self.state.object_ids.setdefault(stall.id, f"project:stall:{stall.id}")

    def _push_history(self) -> None:
        self.state.undo_stack.append(self.state.to_record())
        self.state.redo_stack.clear()


def _state_from_record(raw: dict[str, Any]) -> ProjectState:
    from openparkcad.project_model import parse_project

    return parse_project(raw)
