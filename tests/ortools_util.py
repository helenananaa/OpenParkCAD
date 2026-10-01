"""OR-Tools presence helpers for default vs optimizer CI jobs."""

from __future__ import annotations

import os

import pytest


def require_ortools() -> None:
    """Skip without OR-Tools unless the optimizer job forbids skipping."""
    if os.environ.get("OPENPARKCAD_REQUIRE_ORTOOLS") == "1":
        import ortools  # noqa: F401
        from ortools.sat.python import cp_model  # noqa: F401

        return
    pytest.importorskip("ortools")
