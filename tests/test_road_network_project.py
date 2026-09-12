from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict
from openparkcad.project_model import load_project, save_project, site_dict_from_layout
from openparkcad.project_service import ProjectService
from openparkcad.review_bundle import build_review_bundle
from tests.test_parallel_ladder_search import _with_network
from tests.v0_5_parallel_ladder_support import load_case_site

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ladder():
    layout = generate_layout(_with_network(load_case_site("N-T01"), enabled=True, promote=True))
    assert layout.generation_mode == "parallel_ladder"
    return layout


@pytest.mark.parametrize("network", [
    None, False, {"enabled": None}, {"enabled": 1}, {"families": []}, {"families": ["maze"]},
    {"families": None}, {"max_skeletons": False}, {"max_skeletons": 1.9},
    {"max_skeletons": None}, {"max_full_evaluations": 0}, {"refinement_budget_seconds": 0},
    {"refinement_budget_seconds": None}, {"cross_aisle_policy": "one_way_loop"},
    {"allow_one_way_loop": True}, {"unknown_field": 1},
])
def test_public_schema_and_parser_reject_invalid_network(network):
    data = site_dict_from_layout(load_empty_layout())
    data["optimization"]["road_network"] = network
    schema = json.loads((ROOT / "schema/openparkcad-input.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(data))
    assert any("road_network" in list(error.path) for error in errors)
    with pytest.raises(ValueError, match="road_network|cross_aisle_policy|allow_one_way_loop"):
        site_from_dict(data)


def load_empty_layout():
    from openparkcad.models import LayoutResult
    return LayoutResult(site=load_case_site("N-T01"), stalls=[])


def test_main_schema_embeds_the_shipped_fragment_without_network_resolution():
    schema = json.loads((ROOT / "schema/openparkcad-input.schema.json").read_text())
    fragment = json.loads((ROOT / "schema/road-network.schema.json").read_text())
    assert schema["$defs"]["roadNetwork"] == {
        k: v for k, v in fragment.items() if k not in {"$schema", "$id", "title"}
    }
    Draft202012Validator.check_schema(schema)
    for config in [{}, {"enabled": False}, {"enabled": True, "families": ["parallel_ladder"]}]:
        data = site_dict_from_layout(load_empty_layout())
        data["optimization"]["road_network"] = config
        Draft202012Validator(schema).validate(data)
        site_from_dict(data)


def test_reopen_preserves_mapping_and_exports_exact_ladder(ladder, tmp_path):
    service = ProjectService()
    # The original input need not contain the solver's internal skeleton metadata.
    raw = site_dict_from_layout(ladder)
    raw["metadata"].pop("skeleton_id", None)
    raw["metadata"].pop("skeleton_family", None)
    service.accept_layout(ladder, site=raw)
    road = next(aisle for aisle in ladder.aisles if aisle.role == "branch")
    lock = service.lock_road(road.id)
    assert lock.project_object_id == service.state.object_ids[road.id]
    path = tmp_path / "ladder.json"
    save_project(service.state, path)
    reopened = ProjectService(load_project(path))
    assert reopened.state.object_sources == service.state.object_sources
    assert reopened.state.revisions[-1].locks == service.state.revisions[-1].locks
    exported = reopened.export_accepted()
    assert exported.aisles == ladder.aisles and exported.stalls == ladder.stalls
    assert exported.site.metadata["skeleton_id"] == ladder.site.metadata["skeleton_id"]
    assert exported.layout_search == ladder.layout_search
    result = reopened.regenerate()
    assert result.status == "accepted"
    assert next(a for a in result.layout.aisles if a.id == road.id).polygon == road.polygon
    assert reopened.state.object_ids[road.id] == lock.project_object_id
    # A new obstruction cannot publish relocated locked pavement.
    reopened.add_obstacle([list(p) for p in road.polygon])
    before = reopened.last_accepted
    rejected = reopened.regenerate()
    assert rejected.status == "conflict"
    assert reopened.last_accepted is before
    save_project(reopened.state, path)
    again = ProjectService(load_project(path))
    assert again.last_accepted.aisles == before.aisles


def test_successful_regeneration_reopens_with_updated_site(ladder, tmp_path):
    service = ProjectService(generate_fn=lambda site, **kw: replace(ladder, site=site))
    service.accept_layout(ladder)
    # Out-of-bound obstacle has no layout effect, but must survive acceptance.
    service.add_obstacle([[100, 100], [101, 100], [101, 101], [100, 101]])
    assert service.regenerate().status == "accepted"
    path = tmp_path / "project.json"
    save_project(service.state, path)
    reopened = ProjectService(load_project(path))
    assert reopened.last_accepted.site.obstacles == service.last_accepted.site.obstacles
    assert reopened.state.accepted_site == reopened.state.revisions[-1].site


def test_same_segment_name_in_new_skeleton_does_not_alias_project_object(ladder):
    service = ProjectService()
    service.accept_layout(ladder)
    original = deepcopy(service.state.object_ids)
    service.accept_layout(ladder)
    assert service.state.object_ids == original
    metadata = {**ladder.site.metadata, "skeleton_id": "different-skeleton"}
    service.accept_layout(replace(ladder, site=replace(ladder.site, metadata=metadata)))
    assert all(service.state.object_ids[key] != old for key, old in original.items())


def test_review_has_frozen_geometry_for_all_evaluated_skeletons(ladder):
    bundle = build_review_bundle(ladder)
    candidates = {c["skeleton_id"]: c for c in bundle["candidates"] if c.get("skeleton_id")}
    rows = ladder.layout_search["road_network_search"]["skeletons"]
    for row in rows:
        item = candidates[row["skeleton_id"]]
        if row.get("incomplete"):
            assert item["status"] == "not_solved" and not item["geometry"]["aisles"]
        else:
            assert item["geometry"] == row["geometry"]
            assert item["stall_count"] == row["stall_count"]
    assert sum(c["official"] for c in bundle["candidates"]) == 1


def test_site_serialization_preserves_enforced_fields(ladder):
    from openparkcad.models import SiteAreaSpec
    site = replace(ladder.site,
        standards={"rule_profile": "private_surface_lot_trial_v1"},
        parking_quotas={"ev_min": 2},
        reserved_areas=(SiteAreaSpec("reserved", "reserved", {"type": "polygon", "points": [[1, 1], [2, 1], [2, 2]]}),),
        site_features=[{"id": "marker", "type": "marker"}],
        pedestrian_and_emergency={"accessible_routes": []},
    )
    restored = site_from_dict(site_dict_from_layout(replace(ladder, site=site)))
    for field in ["standards", "parking_quotas", "reserved_areas", "site_features", "pedestrian_and_emergency",
                  "stall_candidates", "aisle_classes", "vehicle", "optimization"]:
        assert getattr(restored, field) == getattr(site, field)
