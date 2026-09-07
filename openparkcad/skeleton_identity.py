"""Official object → skeleton source mapping without editing project_model."""

from __future__ import annotations

from typing import Any

from openparkcad.models import LayoutResult


def official_skeleton_mapping(layout: LayoutResult) -> dict[str, Any]:
    meta = layout.site.metadata if isinstance(layout.site.metadata, dict) else {}
    objects: dict[str, dict[str, Any]] = {}
    for aisle in layout.aisles:
        objects[aisle.id] = {
            "kind": "aisle",
            "role": aisle.role,
            "directionality": aisle.directionality,
            "source_segment_id": aisle.id,
        }
    for stall in layout.stalls:
        objects[stall.id] = {
            "kind": "stall",
            "served_by_aisle_id": stall.served_by_aisle_id,
            "source_segment_id": stall.served_by_aisle_id,
        }
    return {
        "skeleton_id": meta.get("skeleton_id"),
        "family": meta.get("skeleton_family"),
        "generation_mode": layout.generation_mode,
        "objects": objects,
    }
