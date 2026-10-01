from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.site_constraints import apply_contact_filter, validate_parking_quotas, validate_route_usability


def _accessible_spec() -> StallSpec:
    return StallSpec(
        id="accessible-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )


def _standard_spec() -> StallSpec:
    return StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))


def _site(*, accessible_min: int = 1) -> SiteSpec:
    spec = _accessible_spec()
    return SiteSpec(
        name="contact-placement",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=spec,
        stall_candidates=(spec,),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": accessible_min} if accessible_min else {},
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[0.4, 6], [0.4, 18]], "width": 0.8},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )


def _stall(stall_id: str, x: float, stall_type_id: str = "accessible-90") -> ParkingStall:
    return ParkingStall(
        id=stall_id,
        polygon=[(x, 8), (x + 2.5, 8), (x + 2.5, 13), (x, 13)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MAIN",
        aisle_side="left",
        stall_type_id=stall_type_id,
    )


def _layout(site: SiteSpec, stalls: list[ParkingStall]) -> LayoutResult:
    return LayoutResult(
        site=site,
        stalls=stalls,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def _module(module_id: str, *, stall_type_id: str, x: float, stall_count: float, slot: str) -> CandidateObject:
    geometry = [(x, 8), (x + 2.5, 8), (x + 2.5, 8 + stall_count), (x, 8 + stall_count)]
    generated = [
        {
            "source_id": f"{module_id}-{index}",
            "geometry": [(x, 8 + index), (x + 2.5, 8 + index), (x + 2.5, 9 + index), (x, 9 + index)],
            "stall_type_id": stall_type_id,
        }
        for index in range(int(stall_count))
    ]
    return CandidateObject(
        id=module_id,
        kind="stall_module",
        role="stall_module",
        status="rejected",
        geometry=geometry,
        parent_ids=("C-SELECTED-A-MAIN",),
        score_features={"stall_count": stall_count, "area": 1.0},
        metadata={
            "parent_is_base": True,
            "parent_candidate_id": "C-SELECTED-A-MAIN",
            "aisle_side": "left",
            "served_by_aisle_id": "A-MAIN",
            "family_slot": slot,
            "stall_family": "perpendicular",
            "stall_type_id": stall_type_id,
            "generated_stalls": generated,
            "selection_class": "variable",
        },
    )


def test_contact_filter_drops_accessible_stalls_that_miss_the_route():
    site = _site()
    layout = apply_contact_filter(_layout(site, [_stall("P-001", 2.0), _stall("P-002", 18.0)]))

    assert [stall.id for stall in layout.stalls] == ["P-001"]
    assert layout.stalls[0].polygon[0][0] == 2.0
    assert validate_route_usability(layout)["accessible_route"]["status"] == "active"


def test_contact_filter_inactive_without_quota():
    site = _site(accessible_min=0)
    layout = apply_contact_filter(_layout(site, [_stall("P-001", 18.0)]))

    assert [stall.id for stall in layout.stalls] == ["P-001"]


def test_unreachable_accessible_module_is_rejected_when_quota_active():
    site = _site()
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=_standard_spec(),
        stall_candidates=(_standard_spec(), _accessible_spec()),
        aisle_width=site.aisle_width,
        parking_quotas=site.parking_quotas,
        pedestrian_and_emergency=site.pedestrian_and_emergency,
    )
    selection = select_candidate_objects(
        [
            _module("C-ACC", stall_type_id="accessible-90", x=18.0, stall_count=3, slot="C-SELECTED-A-MAIN|left|seg0"),
            _module("C-STD", stall_type_id="standard-90", x=10.0, stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
        ],
        site,
    )

    assert "C-ACC" not in selection["selected_ids"]
    assert "C-STD" in selection["selected_ids"]
    assert any(item["reason"] == "module_does_not_reach_accessible_route" for item in selection["rejected"])


def test_reachable_accessible_module_can_win_a_slot_with_contact_bonus():
    site = _site()
    site = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=_standard_spec(),
        stall_candidates=(_standard_spec(), _accessible_spec()),
        aisle_width=site.aisle_width,
        parking_quotas=site.parking_quotas,
        pedestrian_and_emergency=site.pedestrian_and_emergency,
    )
    selection = select_candidate_objects(
        [
            _module("C-ACC", stall_type_id="accessible-90", x=2.0, stall_count=3, slot="C-SELECTED-A-MAIN|left|seg0"),
            _module("C-STD", stall_type_id="standard-90", x=10.0, stall_count=4, slot="C-SELECTED-A-MAIN|left|seg0"),
        ],
        site,
    )

    assert "C-ACC" in selection["selected_ids"]
    assert "C-STD" not in selection["selected_ids"]


def test_field_support_reports_accessible_contact_filter():
    site = _site()
    layout = apply_contact_filter(_layout(site, [_stall("P-001", 2.0)]))
    diagnostics = build_input_diagnostics(site, layout)

    assert diagnostics["field_support"]["parking.accessible_contact_filter"] == "active"
    assert validate_parking_quotas(layout)["actual"]["accessible"] == 1
