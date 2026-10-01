from __future__ import annotations

from openparkcad.candidate_snapshot import build_candidate_objects
from openparkcad.generator import collect_layout_candidate_contexts, generate_layout
from openparkcad.layout_candidates import context_from_layout, copy_site
from openparkcad.layout_evaluation import evaluate_layout_candidate
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec


def _base_optimization(**extra) -> dict[str, object]:
    return {
        "heading_deltas_degrees": [0],
        "entrance_offsets": [0],
        "enable_branches": False,
        "enable_connectors": False,
        "enable_t_end_caps": False,
        **extra,
    }


def _aisle_class() -> AisleClassSpec:
    return AisleClassSpec(
        id="wide-two-way-no-cross",
        width=6.0,
        capacity="two_vehicle",
        directionality="two_way",
    )


def _straight_offset_site() -> SiteSpec:
    return SiteSpec(
        name="lateral-identity",
        boundary=[(0, 0), (36, 0), (36, 40), (0, 40)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(18, 0), width=8.0, heading_degrees=90.0),
        ],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization=_base_optimization(main_aisle_lateral_offsets=[0.0, 6.0]),
    )


def _dogleg_site() -> SiteSpec:
    return SiteSpec(
        name="dogleg-identity",
        boundary=[(0, 0), (48, 0), (48, 52), (0, 52)],
        obstacles=[[(18, 10), (30, 10), (30, 28), (18, 28)]],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(24, 0), width=8.0, heading_degrees=90.0),
        ],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization=_base_optimization(
            prefer_obstacle_clearance=True,
            enable_main_aisle_dogleg=True,
            auto_lateral_offsets_for_obstacles=False,
            dogleg_offsets=[22.0, -22.0],
        ),
    )


def _branch_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="branch-isolation",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(45, 0), width=8.0, heading_degrees=90.0),
        ],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization=_base_optimization(
            enable_branches=True,
            branch_start_positions=[18, 32, 46],
            branch_sides=["left"],
            max_branches=2,
            **optimization,
        ),
    )


def _dual_family_site() -> SiteSpec:
    main = StallSpec(id="std-a", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    other = StallSpec(id="std-b", width=2.4, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    return SiteSpec(
        name="stall-family-identity",
        boundary=[(0, 0), (36, 0), (36, 40), (0, 40)],
        stall=main,
        stall_candidates=(main, other),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="main", mode="shared", center=(18, 0), width=8.0, heading_degrees=90.0),
        ],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization=_base_optimization(),
    )


def test_lateral_offset_produces_distinct_spine_ids() -> None:
    contexts = [item for item in collect_layout_candidate_contexts(_straight_offset_site()) if item.template_layout.aisles]
    laterals = {float(item.source["aisle_lateral_offset"]) for item in contexts}
    assert 0.0 in laterals
    assert 6.0 in laterals
    by_lateral = {}
    for item in contexts:
        if item.source.get("family") != "straight":
            continue
        by_lateral[float(item.source["aisle_lateral_offset"])] = item
    left = by_lateral[0.0]
    right = by_lateral[6.0]
    assert left.source["entrance_id"] == right.source["entrance_id"]
    assert left.source["heading_degrees"] == right.source["heading_degrees"]
    assert left.spine_id != right.spine_id
    assert left.spine_payload["aisles"] != right.spine_payload["aisles"]
    assert not left.spine_id.startswith("spine-") or all(ch in "0123456789abcdef" for ch in left.spine_id.split("-", 1)[1])


def test_dogleg_offsets_produce_distinct_spine_ids() -> None:
    contexts = [item for item in collect_layout_candidate_contexts(_dogleg_site()) if item.source.get("family") == "dogleg" and item.template_layout.aisles]
    offsets = {item.source.get("dogleg_offset") for item in contexts}
    assert len(offsets) >= 2
    first, second = contexts[0], contexts[1]
    assert first.source["entrance_id"] == second.source["entrance_id"]
    assert first.spine_id != second.spine_id
    assert first.spine_payload != second.spine_payload
    assert any(aisle["role"] == "jog" for aisle in first.spine_payload["aisles"])


def test_stall_family_changes_candidate_id_not_spine_id() -> None:
    contexts = [item for item in collect_layout_candidate_contexts(_dual_family_site()) if item.template_layout.aisles]
    assignments = {(item.site.main_stall or item.site.stall).id for item in contexts}
    assert "std-a" in assignments
    assert "std-b" in assignments
    by_stall = {(item.site.main_stall or item.site.stall).id: item for item in contexts if item.source.get("family") == "straight"}
    left = by_stall["std-a"]
    right = by_stall["std-b"]
    assert left.source["entrance_id"] == right.source["entrance_id"]
    assert left.source["heading_degrees"] == right.source["heading_degrees"]
    assert left.candidate_id != right.candidate_id


def test_mutating_one_context_does_not_change_sibling_or_input() -> None:
    original = _branch_site(enable_passing_bay_synthesis=True, passing_bay_min_count=1)
    original_features = list(original.site_features)
    contexts = [item for item in collect_layout_candidate_contexts(original) if item.template_layout.aisles]
    assert len(contexts) >= 1
    first = contexts[0]
    second = context_from_layout(first.template_layout, source=first.source, branch_diagnostics=first.branch_diagnostics)
    aisle_count = len(second.template_layout.aisles)
    first.site.site_features.append({"id": "mutated-bay", "type": "passing_bay"})
    first.branch_diagnostics.append({"reason": "mutated"})
    first.template_layout.aisles.append(first.template_layout.aisles[0])
    if first.template_layout.candidate_objects:
        object.__setattr__(first.template_layout.candidate_objects[0], "conflict_ids", ("MUTATED",))
    assert original.site_features == original_features
    assert all(item.get("id") != "mutated-bay" for item in second.site.site_features)
    assert "mutated" not in {item.get("reason") for item in second.branch_diagnostics}
    assert len(second.template_layout.aisles) == aisle_count
    assert len(first.template_layout.aisles) == aisle_count + 1
    assert not any(getattr(item, "conflict_ids", ()) == ("MUTATED",) for item in second.template_layout.candidate_objects)


def test_shared_local_ids_keep_independent_catalogs_and_parents() -> None:
    contexts = [item for item in collect_layout_candidate_contexts(_branch_site()) if item.template_layout.aisles]
    assert len(contexts) >= 1
    context = contexts[0]
    other = context_from_layout(context.template_layout, source=context.source)
    left_objects = build_candidate_objects(context.template_layout)
    right_objects = build_candidate_objects(other.template_layout)
    left_ids = {item.id for item in left_objects}
    right_ids = {item.id for item in right_objects}
    assert "A-MAIN" in {aisle.id for aisle in context.template_layout.aisles}
    assert "A-MAIN" in {aisle.id for aisle in other.template_layout.aisles}
    branch_ids = {aisle.id for aisle in context.template_layout.aisles if aisle.role == "branch"}
    if "A-BRANCH-001" in branch_ids:
        assert "A-BRANCH-001" in {aisle.id for aisle in other.template_layout.aisles if aisle.role == "branch"}
    mutated = left_objects[0]
    object.__setattr__(mutated, "conflict_ids", ("C-MUTATED",))
    object.__setattr__(mutated, "parent_ids", ("MUTATED-PARENT",))
    right_by_id = {item.id: item for item in build_candidate_objects(other.template_layout)}
    if mutated.id in right_by_id:
        assert right_by_id[mutated.id].conflict_ids != ("C-MUTATED",)
        assert right_by_id[mutated.id].parent_ids != ("MUTATED-PARENT",)
    assert left_ids
    assert right_ids


def test_collector_keeps_multiple_complete_spines_and_legacy_wrapper_unchanged() -> None:
    site = _dogleg_site()
    contexts = [item for item in collect_layout_candidate_contexts(site) if item.template_layout.aisles]
    families = {item.source.get("family") for item in contexts}
    assert "dogleg" in families or "straight" in families
    spine_ids = {item.spine_id for item in contexts}
    assert len(spine_ids) >= 2
    assert all(item.template_layout.aisles for item in contexts if item.collect_status == "collected")
    legacy = generate_layout(site)
    copied = copy_site(site)
    again = generate_layout(copied)
    assert [aisle.id for aisle in legacy.aisles] == [aisle.id for aisle in again.aisles]
    assert legacy.stall_count == again.stall_count
    assert legacy.generation_mode == again.generation_mode


def test_evaluation_does_not_write_or_mutate_context(tmp_path) -> None:
    contexts = [item for item in collect_layout_candidate_contexts(_straight_offset_site()) if item.template_layout.stall_count > 0]
    context = contexts[0]
    before_features = list(context.site.site_features)
    before_aisles = [aisle.id for aisle in context.template_layout.aisles]
    evaluation = evaluate_layout_candidate(context)
    assert evaluation.candidate_id == context.candidate_id
    assert evaluation.rebuilt_layout is not None
    assert evaluation.checks["engineering"]["result_scope"].startswith("candidate:")
    assert context.site.site_features == before_features
    assert [aisle.id for aisle in context.template_layout.aisles] == before_aisles
    assert not list(tmp_path.iterdir())
