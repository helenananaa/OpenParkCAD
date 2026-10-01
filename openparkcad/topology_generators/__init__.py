"""Topology generators. N3 ships the legacy adapter; parallel_ladder follows in N4."""

from openparkcad.topology_generators.legacy_adapter import (
    LEGACY_ADAPTER_VERSION,
    adapt_layout_to_skeleton,
)
from openparkcad.topology_generators.parallel_ladder import (
    GENERATOR_VERSION,
    generate_parallel_ladder_skeletons,
    read_ladder_config,
)

__all__ = [
    "GENERATOR_VERSION",
    "LEGACY_ADAPTER_VERSION",
    "adapt_layout_to_skeleton",
    "generate_parallel_ladder_skeletons",
    "read_ladder_config",
]
