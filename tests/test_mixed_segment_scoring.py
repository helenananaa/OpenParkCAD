from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import (
    AisleClassSpec,
    CandidateObject,
    EntranceSpec,
    LayoutResult,
    ParkingStall,
    SiteSpec,
    StallSpec,
)
from openparkcad.scoring import score_layout, stall_family_weight


def _site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="mixed-segment",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
            "enable_branches": False,
            **optimization,
        },
    )


def _module(
    module_id: str,
    *,
    family: str,
    stall_count: float,
    slot: str,
    parent_is_base: bool = True,
) -> CandidateObject:
    return CandidateObject(
        id=module_id,
        kind="stall_module",
        role="stall_module",
        status="rejected",
        geometry=[(0, 0), (1, 0), (1, 1), (0, 1)],
        parent_ids=("C-SELECTED-A-MAIN",),
        score_features={"stall_count": stall_count, "area": 1.0},
        metadata={
            "parent_is_base": parent_is_base,
            "parent_candidate_id": "C-SELECTED-A-MAIN",
            "aisle_side": "left",
            "served_by_aisle_id": "A-MAIN",
            "family_slot": slot,
            "stall_family": family,
            "stall_type_id": family,
            "selection_class": "variable",
        },
    )


def test_family_weights_change_official_stall_value():
    layout = generate_layout(
        _site(weights={"stall_count": 10, "aisle_area": 0, "dead_end_length": 0, "stall_family": {"perpendicular": 7}})
    )

    assert layout.stall_count > 0
    assert layout.score["stall_value"] == layout.stall_count * 7
    assert layout.score["stall_count_family_perpendicular"] == layout.stall_count
    assert layout.score["mixed_segment_side_count"] == 0
    assert layout.score["segment_family_mix_penalty"] == 0


def test_mix_penalty_applies_once_per_mixed_aisle_side():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    parallel = StallSpec(id="parallel", width=2.5, length=6.0, family="parallel", allowed_angles=(0.0,))
    site = _site(
        weights={
            "stall_count": 0,
            "aisle_area": 0,
            "dead_end_length": 0,
            "heading_delta": 0,
            "entrance_offset": 0,
            "branch_count": 0,
            "operational_risk": 0,
            "segment_family_mix": -12,
        }
    )
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=perpendicular,
        stall_candidates=(perpendicular, parallel),
        aisle_width=site.aisle_width,
        margin=site.margin,
        entrances=site.entrances,
        aisle_classes=site.aisle_classes,
        fixed_aisle_class=site.fixed_aisle_class,
        optimization=site.optimization,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(0, 6), (2.5, 6), (2.5, 11), (0, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="standard-90",
            ),
            ParkingStall(
                id="P-002",
                polygon=[(2.5, 6), (8.5, 6), (8.5, 8.5), (2.5, 8.5)],
                angle_degrees=0.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="parallel",
            ),
        ],
    )

    score = score_layout(layout)

    assert score["mixed_segment_side_count"] == 1
    assert score["segment_family_mix_penalty"] == -12
    assert score["stall_count_family_perpendicular"] == 1
    assert score["stall_count_family_parallel"] == 1


def test_higher_family_weight_wins_a_segment_slot():
    objects = [
        _module("C-MODULE-PERP", family="perpendicular", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
        _module("C-MODULE-PAR", family="parallel", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
    ]
    site = _site(weights={"stall_family": {"parallel": 200}})

    assert stall_family_weight(site, "parallel", default=100) == 200
    selection = select_candidate_objects(objects, site)

    assert "C-MODULE-PAR" in selection["selected_ids"]
    assert "C-MODULE-PERP" not in selection["selected_ids"]
    assert any(item["reason"] == "module_family_slot_taken" for item in selection["rejected"])


def test_zero_mix_weight_keeps_independent_segment_picks():
    objects = [
        _module("C-PERP-0", family="perpendicular", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
        _module("C-PAR-0", family="parallel", stall_count=3, slot="C-SELECTED-A-MAIN|left|seg0"),
        _module("C-PERP-1", family="perpendicular", stall_count=3, slot="C-SELECTED-A-MAIN|left|seg1"),
        _module("C-PAR-1", family="parallel", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg1"),
    ]
    selection = select_candidate_objects(objects, _site())

    assert set(selection["selected_ids"]) == {"C-PERP-0", "C-PAR-1"}
    assert selection["segment_family_mix_weight"] == 0.0


def test_mix_penalty_prefers_uniform_family_on_a_side():
    objects = [
        _module("C-PERP-0", family="perpendicular", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
        _module("C-PAR-0", family="parallel", stall_count=3, slot="C-SELECTED-A-MAIN|left|seg0"),
        _module("C-PERP-1", family="perpendicular", stall_count=3, slot="C-SELECTED-A-MAIN|left|seg1"),
        _module("C-PAR-1", family="parallel", stall_count=4, slot="C-SELECTED-A-MAIN|left|seg1"),
    ]
    site = _site(weights={"segment_family_mix": -200})
    selection = select_candidate_objects(objects, site)

    assert selection["segment_family_mix_weight"] == -200
    selected = set(selection["selected_ids"])
    assert selected in ({"C-PERP-0", "C-PERP-1"}, {"C-PAR-0", "C-PAR-1"})
    assert not ({"C-PERP-0", "C-PAR-1"} <= selected)


def test_field_support_reports_mixed_segment_scoring():
    perpendicular = StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))
    angled = StallSpec(id="angled-60", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    site = _site()
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=perpendicular,
        stall_candidates=(perpendicular, angled),
        aisle_width=site.aisle_width,
        margin=site.margin,
        entrances=site.entrances,
        aisle_classes=site.aisle_classes,
        fixed_aisle_class=site.fixed_aisle_class,
        optimization=site.optimization,
    )
    pending = build_input_diagnostics(site)
    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert pending["field_support"]["optimization.mixed_segment_scoring"] == "requested_pending_layout"
    assert diagnostics["field_support"]["optimization.mixed_segment_scoring"] == "active"
    single = generate_layout(_site())
    assert build_input_diagnostics(single.site, single)["field_support"]["optimization.mixed_segment_scoring"] == "available"
