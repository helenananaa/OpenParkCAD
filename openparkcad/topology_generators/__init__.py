"""Topology generators. N3 ships the legacy adapter; parallel_ladder follows in N4."""

from openparkcad.topology_generators.legacy_adapter import (
    LEGACY_ADAPTER_VERSION,
    adapt_layout_to_skeleton,
)

__all__ = ["LEGACY_ADAPTER_VERSION", "adapt_layout_to_skeleton"]
