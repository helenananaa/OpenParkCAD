"""Delivery manifest: traceable outputs that cannot forge human approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openparkcad import __version__
from openparkcad.models import LayoutResult
from openparkcad.rule_profiles import profile_from_site
from openparkcad.review_bundle import snapshot_candidate_geometry

MANIFEST_VERSION = "delivery-manifest-1"
HUMAN_NOT_REVIEWED = "not_reviewed"


def build_delivery_manifest(
    layout: LayoutResult,
    *,
    input_bytes: bytes | None = None,
    output_paths: dict[str, Path] | None = None,
    hash_from: dict[str, Path] | None = None,
    human_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile_from_site(layout.site)
    review = _normalize_human_review(human_review)
    outputs = {}
    for name, path in (output_paths or {}).items():
        target = Path(path)
        source = Path(hash_from[name]) if hash_from and name in hash_from else target
        outputs[name] = {
            "path": str(target),
            "sha256": _sha256_file(source) if source.is_file() else None,
        }
    return {
        "version": MANIFEST_VERSION,
        "package_version": __version__,
        "rule_profile": profile.to_record(),
        "input_digest": hashlib.sha256(input_bytes).hexdigest() if input_bytes is not None else None,
        "site_name": layout.site.name,
        "official_layout": {
            "generation_mode": layout.generation_mode,
            "stall_count": layout.stall_count,
            "aisle_ids": [aisle.id for aisle in layout.aisles],
            "stall_ids": [stall.id for stall in layout.stalls],
            "geometry_digest": hashlib.sha256(
                json.dumps(snapshot_candidate_geometry(layout), sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "road_traversal_status": (layout.road_traversal_validation or {}).get("status"),
            "engineering_valid": (layout.engineering_validation or {}).get("valid"),
        },
        "output_hashes": outputs,
        "human_review": review,
        "synthetic_trial": True,
        "field_effect_claimed": False,
    }


def _normalize_human_review(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(raw or {})
    status = str(payload.get("status") or HUMAN_NOT_REVIEWED)
    if status in {"approved", "human_approved", "signed"}:
        raise ValueError("software cannot mark human review as approved")
    if status in {"", "auto", "algorithm_pass", HUMAN_NOT_REVIEWED}:
        status = HUMAN_NOT_REVIEWED
    payload["status"] = status
    payload.setdefault("reviewer", None)
    payload.setdefault("notes", None)
    payload["algorithm_pass_is_not_human_approval"] = True
    return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
