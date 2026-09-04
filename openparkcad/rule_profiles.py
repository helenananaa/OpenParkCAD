"""Executable-versus-unsupported rule profiles for a chosen delivery scene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROFILE_VERSION = "rule-profile-1"
DEFAULT_PROFILE_ID = "private_surface_lot_v1"

_PROFILES: dict[str, dict[str, Any]] = {
    DEFAULT_PROFILE_ID: {
        "id": DEFAULT_PROFILE_ID,
        "version": PROFILE_VERSION,
        "scene": "private surface parking lot, rigid passenger car, JSON or support-range DXF",
        "jurisdiction": "unspecified_synthetic",
        "source": "software-executed-checks-only",
        "executes": [
            "geometry_containment",
            "traffic_graph",
            "maneuver_validation",
            "site_hard_exclusions",
            "quotas_if_declared",
            "road_traversal_if_requested",
            "operational_quality_proxies",
        ],
        "unsupported": [
            "slope_or_elevation",
            "statutory_accessible_ratio",
            "fire_apparatus_swept_path",
            "charging_equipment_design",
            "permit_or_code_certification",
        ],
        "human_review_required": [
            "jurisdiction_compliance",
            "construction_documents",
            "field_survey_confirmation",
        ],
    }
}


@dataclass(frozen=True)
class RuleProfile:
    profile_id: str
    version: str
    scene: str
    jurisdiction: str
    source: str
    executes: tuple[str, ...]
    unsupported: tuple[str, ...]
    human_review_required: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "version": self.version,
            "scene": self.scene,
            "jurisdiction": self.jurisdiction,
            "source": self.source,
            "executes": list(self.executes),
            "unsupported": list(self.unsupported),
            "human_review_required": list(self.human_review_required),
        }

    def claims_check(self, name: str) -> bool:
        return name in self.executes


def parse_rule_profile(raw: dict[str, Any] | None) -> RuleProfile:
    if not raw:
        return load_profile(DEFAULT_PROFILE_ID)
    profile_id = str(raw.get("id") or DEFAULT_PROFILE_ID)
    return load_profile(profile_id)


def load_profile(profile_id: str) -> RuleProfile:
    data = _PROFILES.get(profile_id)
    if data is None:
        raise ValueError(f"unknown rule profile {profile_id!r}")
    return RuleProfile(
        profile_id=data["id"],
        version=data["version"],
        scene=data["scene"],
        jurisdiction=data["jurisdiction"],
        source=data["source"],
        executes=tuple(data["executes"]),
        unsupported=tuple(data["unsupported"]),
        human_review_required=tuple(data["human_review_required"]),
    )


def profile_from_site(site) -> RuleProfile:
    standards = site.standards if isinstance(getattr(site, "standards", None), dict) else {}
    requested = standards.get("rule_profile") or standards.get("standard_profile")
    if requested and requested != "custom" and requested in _PROFILES:
        return load_profile(str(requested))
    return load_profile(DEFAULT_PROFILE_ID)
