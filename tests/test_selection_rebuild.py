"""Rebuild official geometry from selected skeletons using catalog source ids."""

from openparkcad.candidate_catalog import PROMOTION_VERSION
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec


def _branch_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="selection-rebuild",
        boundary=[(0, 0), (90, 0), (90, 70), (0, 70)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(45, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "branch_start_positions": [18, 32, 46],
            "branch_sides": ["left"],
            "max_branches": 2,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "weights": {
                "stall_count": 100,
                "aisle_area": 0,
                "dead_end_length": 0,
                "branch_count": 0,
            },
            **optimization,
        },
    )


def test_preview_base_excludes_generator_branch_turnarounds():
    layout = generate_layout(_branch_site())
    official_branch_caps = {
        aisle.id
        for aisle in layout.aisles
        if aisle.role == "turnaround" and aisle.parent_aisle_id and str(aisle.parent_aisle_id).startswith("A-BRANCH")
    }
    preview_aisles = layout.candidate_network_preview.get("aisles", [])
    leaked = [
        item
        for item in preview_aisles
        if item.get("source_id") in official_branch_caps and not (item.get("metadata") or {}).get("preview_generated")
    ]
    assert official_branch_caps
    assert leaked == []


def test_promotion_rewrites_official_aisles_to_catalog_source_ids():
    layout = generate_layout(_branch_site(promote_candidate_layout_preview=True))
    assert layout.generation_mode == "candidate_layout_promoted"
    assert layout.candidate_layout_promotion["version"] == PROMOTION_VERSION
    assert layout.candidate_layout_promotion["official_id_scheme"] == "catalog_source_id"
    assert layout.candidate_layout_promotion["pre_promotion_backend"] in {"greedy", "cpsat"}
    assert not any(aisle.id.startswith("PN-AISLE-") for aisle in layout.aisles)
    assert "A-MAIN" in {aisle.id for aisle in layout.aisles}
    assert all(stall.id.startswith("P-") for stall in layout.stalls)
    assert all(stall.served_by_aisle_id is None or not stall.served_by_aisle_id.startswith("PN-") for stall in layout.stalls)
    for branch in layout.selected_branches:
        assert branch["id"].startswith("A-BRANCH-")
        assert branch["id"] in {aisle.id for aisle in layout.aisles}


def test_cpsat_promotion_uses_selector_sources_on_official_layout():
    from tests.ortools_util import require_ortools

    require_ortools()
    layout = generate_layout(_branch_site(promote_candidate_layout_preview=True, selector_backend="cpsat"))
    assert layout.generation_mode == "candidate_layout_promoted"
    assert layout.candidate_layout_promotion["pre_promotion_backend"] == "cpsat"
    assert layout.candidate_layout_promotion["pre_promotion_requested_backend"] == "cpsat"
    assert not any(aisle.id.startswith("PN-AISLE-") for aisle in layout.aisles)
    assert layout.candidate_layout_promotion["pre_promotion_selected_ids"]
    official_ids = {aisle.id for aisle in layout.aisles}
    assert "A-MAIN" in official_ids
    for branch in layout.selected_branches:
        assert branch["id"] in official_ids


def test_promotion_still_requires_the_opt_in_flag():
    layout = generate_layout(_branch_site())
    assert layout.generation_mode != "candidate_layout_promoted"
    assert layout.candidate_layout_promotion["status"] == "not_requested"
