from __future__ import annotations

import json
from pathlib import Path

from openparkcad.generator import _finalize_candidate, _layout_valid
from openparkcad.models import site_from_dict
from openparkcad.road_traversal_models import layout_traversal_identity, parse_traversal_policy
from openparkcad.topology_generators.ladder_layout import layout_from_skeleton
from openparkcad.topology_generators.ladder_modules import build_and_select_ladder_modules
from openparkcad.topology_generators.parallel_ladder import generate_parallel_ladder_skeletons


def test_generated_ladder_all_retained_stalls_have_complete_journeys() -> None:
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/v0_5/parallel_ladder_traversable_site.json"
    site = site_from_dict(json.loads(path.read_text(encoding="utf-8")))
    skeleton = generate_parallel_ladder_skeletons(site).candidates[0].skeleton
    catalog = build_and_select_ladder_modules(site, skeleton)
    layout = _finalize_candidate(layout_from_skeleton(site, skeleton, catalog.selected_stalls))
    assert _layout_valid(layout)
    assert layout.stall_count >= 10  # Whole generated layout, not two sampled stalls.
    assert len({stall.served_by_aisle_id for stall in layout.stalls}) >= 2
    report = layout.road_traversal_validation
    assert report["status"] == "passed" and report["valid"] is True
    assert report["stall_coverage"] == report["stall_count"] == layout.stall_count
    assert report["layout_identity"] == layout_traversal_identity(layout, parse_traversal_policy(layout.site))
    assert report["template_revision"] == "orthogonal-tangents-2"
    assert not report["failures"]
    for journey in report["journeys"]:
        assert journey["valid"] and journey["continuity_valid"]
        assert journey["entrance_id"] and journey["exit_id"]
        assert journey["inbound_transition_ids"] and journey["outbound_transition_ids"]


def test_template_revision_invalidates_requested_evidence(monkeypatch) -> None:
    import openparkcad.road_traversal_models as models
    from tests.road_traversal_support import through_layout

    layout = through_layout()
    policy = parse_traversal_policy(layout.site)
    original = layout_traversal_identity(layout, policy)
    monkeypatch.setattr(models, "TEMPLATE_REVISION", "previous-template")
    assert layout_traversal_identity(layout, policy) != original


def test_dense_original_bays_do_not_ignore_occupied_neighbors() -> None:
    from dataclasses import replace
    from openparkcad.parking_motion_adapter import parking_motion_for_stall
    from openparkcad.road_transitions import build_occupancy
    from tests.v0_5_parallel_ladder_support import load_case_site

    site = load_case_site("N-T01")
    site = replace(site, constraints={**site.constraints, "road_traversal": {"enabled": True}})
    skeleton = generate_parallel_ladder_skeletons(site, config={"max_skeletons": 1, "max_parallel_aisles": 2}).candidates[0].skeleton
    catalog = build_and_select_ladder_modules(site, skeleton)
    layout = layout_from_skeleton(site, skeleton, catalog.selected_stalls)
    motion = parking_motion_for_stall(layout, layout.stalls[0], build_occupancy(layout), parse_traversal_policy(site), site.vehicle)
    assert motion.valid is False
    assert motion.reason == "swept_path_intersects_obstacle"
