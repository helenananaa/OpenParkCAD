import math

from openparkcad.contact_retarget import apply_contact_retarget
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import (
    AisleClassSpec,
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    SiteSpec,
    StallSpec,
)
from openparkcad.site_constraints import validate_parking_quotas, validate_route_usability


def _standard() -> StallSpec:
    return StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,))


def _accessible() -> StallSpec:
    return StallSpec(
        id="accessible-90",
        width=2.5,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )


def _site() -> SiteSpec:
    standard = _standard()
    accessible = _accessible()
    return SiteSpec(
        name="contact-retarget",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
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


def _layout(site: SiteSpec, x: float) -> LayoutResult:
    return LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(x, 8), (x + 2.5, 8), (x + 2.5, 13), (x, 13)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="standard-90",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )


def test_retarget_converts_route_adjacent_standard_stall_to_accessible():
    site = _site()
    layout = apply_contact_retarget(_layout(site, 2.0))

    assert layout.stalls[0].stall_type_id == "accessible-90"
    quota = validate_parking_quotas(layout)
    assert quota["actual"]["accessible"] == 1
    assert validate_route_usability(layout)["accessible_route"]["status"] == "active"


def test_retarget_skips_stalls_that_miss_the_route():
    layout = apply_contact_retarget(_layout(_site(), 18.0))

    assert layout.stalls[0].stall_type_id == "standard-90"
    assert validate_parking_quotas(layout)["actual"]["accessible"] == 0


def test_wider_accessible_stall_drops_overlapping_same_side_neighbor():
    standard = _standard()
    accessible = StallSpec(
        id="accessible-90",
        width=3.6,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )
    site = SiteSpec(
        name="wider-accessible",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency=_site().pedestrian_and_emergency,
    )
    stalls = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=[(1, y), (6, y), (6, y + 2.5), (1, y + 2.5)],
            angle_degrees=90.0,
            served_by_aisle_id="A-MAIN",
            aisle_side="left",
            stall_type_id="standard-90",
        )
        for index, y in enumerate((8.0, 10.5, 13.0), start=1)
    ]
    layout = LayoutResult(
        site=site,
        stalls=stalls,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(6, 0), (12, 0), (12, 20), (6, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    accessible_stalls = [stall for stall in result.stalls if stall.stall_type_id == "accessible-90"]
    assert len(accessible_stalls) == 1
    assert result.stall_count == 2
    assert validate_parking_quotas(result)["actual"]["accessible"] == 1
    width = max(pt[1] for pt in accessible_stalls[0].polygon) - min(pt[1] for pt in accessible_stalls[0].polygon)
    assert abs(width - 3.6) < 1e-6


def test_contact_strip_packs_two_wider_accessible_stalls_from_three_standard():
    standard = _standard()
    accessible = StallSpec(
        id="accessible-90",
        width=3.6,
        length=5.0,
        family="perpendicular",
        allowed_angles=(90.0,),
        classifications=("accessible",),
    )
    site = SiteSpec(
        name="accessible-strip",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 2},
        pedestrian_and_emergency=_site().pedestrian_and_emergency,
    )
    stalls = [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=[(1, y), (6, y), (6, y + 2.5), (1, y + 2.5)],
            angle_degrees=90.0,
            served_by_aisle_id="A-MAIN",
            aisle_side="left",
            stall_type_id="standard-90",
        )
        for index, y in enumerate((8.0, 10.5, 13.0), start=1)
    ]
    layout = LayoutResult(
        site=site,
        stalls=stalls,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(6, 0), (12, 0), (12, 20), (6, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    accessible_stalls = [stall for stall in result.stalls if stall.stall_type_id == "accessible-90"]
    assert len(accessible_stalls) == 2
    assert result.stall_count == 2
    assert validate_parking_quotas(result)["actual"]["accessible"] == 2
    assert validate_route_usability(result)["accessible_route"]["status"] == "active"
    for stall in accessible_stalls:
        width = max(pt[1] for pt in stall.polygon) - min(pt[1] for pt in stall.polygon)
        assert abs(width - 3.6) < 1e-6


def test_empty_contact_frontage_synthesizes_accessible_stall_on_existing_aisle():
    standard = _standard()
    accessible = _accessible()
    site = SiteSpec(
        name="empty-contact-frontage",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[2.5, 6], [2.5, 18]], "width": 0.8},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(14, 8), (19, 8), (19, 10.5), (14, 10.5)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="right",
                stall_type_id="standard-90",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    accessible_stalls = [stall for stall in result.stalls if stall.stall_type_id == "accessible-90"]
    assert len(accessible_stalls) == 1
    assert result.stall_count == 2
    assert any(stall.stall_type_id == "standard-90" for stall in result.stalls)
    assert accessible_stalls[0].served_by_aisle_id == "A-MAIN"
    assert [aisle.id for aisle in result.aisles] == ["A-MAIN"]
    assert min(pt[0] for pt in accessible_stalls[0].polygon) < 8.0
    assert validate_parking_quotas(result)["actual"]["accessible"] == 1
    assert validate_route_usability(result)["accessible_route"]["status"] == "active"


def test_empty_contact_frontage_synthesizes_parallel_accessible_stall():
    standard = StallSpec(id="standard-parallel", width=2.5, length=6.0, family="parallel", allowed_angles=(0.0,))
    accessible = StallSpec(
        id="accessible-parallel",
        width=2.5,
        length=6.0,
        family="parallel",
        allowed_angles=(0.0,),
        classifications=("accessible",),
    )
    site = SiteSpec(
        name="parallel-empty-frontage",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[5.0, 6], [5.0, 20]], "width": 0.8},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(14, 8), (16.5, 8), (16.5, 14), (14, 14)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="right",
                stall_type_id="standard-parallel",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 24), (8, 24)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    accessible_stalls = [stall for stall in result.stalls if stall.stall_type_id == "accessible-parallel"]
    assert len(accessible_stalls) == 1
    assert result.stall_count == 2
    assert any(stall.stall_type_id == "standard-parallel" for stall in result.stalls)
    assert [aisle.id for aisle in result.aisles] == ["A-MAIN"]
    along = max(pt[1] for pt in accessible_stalls[0].polygon) - min(pt[1] for pt in accessible_stalls[0].polygon)
    depth = max(pt[0] for pt in accessible_stalls[0].polygon) - min(pt[0] for pt in accessible_stalls[0].polygon)
    assert abs(along - 6.0) < 1e-6
    assert abs(depth - 2.5) < 1e-6
    assert min(pt[0] for pt in accessible_stalls[0].polygon) < 8.0
    assert validate_parking_quotas(result)["actual"]["accessible"] == 1
    assert validate_route_usability(result)["accessible_route"]["status"] == "active"


def test_parallel_empty_fill_skips_side_that_already_has_perpendicular_stalls():
    perpendicular = _standard()
    accessible = StallSpec(
        id="accessible-parallel",
        width=2.5,
        length=6.0,
        family="parallel",
        allowed_angles=(0.0,),
        classifications=("accessible",),
    )
    site = SiteSpec(
        name="mixed-family-empty-fill",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=perpendicular,
        stall_candidates=(perpendicular, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[5.0, 12], [5.0, 20]], "width": 0.8},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(3, 1), (8, 1), (8, 3.5), (3, 3.5)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
                stall_type_id="standard-90",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 24), (8, 24)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    assert result.stall_count == 1
    assert result.stalls[0].stall_type_id == "standard-90"
    assert validate_parking_quotas(result)["actual"]["accessible"] == 0


def test_empty_contact_frontage_synthesizes_angled_accessible_stall():
    standard = StallSpec(id="standard-angled", width=2.5, length=5.0, family="angled", allowed_angles=(60.0,))
    accessible = StallSpec(
        id="accessible-angled",
        width=2.5,
        length=5.0,
        family="angled",
        allowed_angles=(60.0,),
        classifications=("accessible",),
    )
    site = SiteSpec(
        name="angled-empty-frontage",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[3.2, 6], [3.2, 20]], "width": 0.8},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(14, 8), (19, 8), (19, 10.5), (14, 10.5)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="right",
                stall_type_id="standard-angled",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(8, 0), (14, 0), (14, 24), (8, 24)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    result = apply_contact_retarget(layout)

    accessible_stalls = [stall for stall in result.stalls if stall.stall_type_id == "accessible-angled"]
    assert len(accessible_stalls) == 1
    assert result.stall_count == 2
    assert any(stall.stall_type_id == "standard-angled" for stall in result.stalls)
    assert [aisle.id for aisle in result.aisles] == ["A-MAIN"]
    xs = [pt[0] for pt in accessible_stalls[0].polygon]
    assert abs(max(xs) - 8.0) < 1e-6
    assert abs(min(xs) - (8.0 - 5.0 * math.sin(math.radians(60.0)))) < 1e-6
    assert abs(accessible_stalls[0].angle_degrees - 150.0) < 1e-6
    assert validate_parking_quotas(result)["actual"]["accessible"] == 1
    assert validate_route_usability(result)["accessible_route"]["status"] == "active"


def test_retarget_not_requested_without_classified_spec():
    standard = _standard()
    site = SiteSpec(
        name="no-accessible-type",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=standard,
        stall_candidates=(standard,),
        parking_quotas={"accessible_min": 1},
        pedestrian_and_emergency=_site().pedestrian_and_emergency,
    )
    layout = apply_contact_retarget(_layout(site, 2.0))
    assert layout.stalls[0].stall_type_id == "standard-90"


def test_generated_layout_can_meet_accessible_quota_by_retarget():
    standard = _standard()
    accessible = _accessible()
    site = SiteSpec(
        name="retarget-generate",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=standard,
        stall_candidates=(standard, accessible),
        aisle_width=6.0,
        margin=0.0,
        parking_quotas={"accessible_min": 1},
        constraints={"accessible_route_touch_tolerance": 6.0},
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
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
        pedestrian_and_emergency={
            "accessible_routes": [
                {
                    "id": "west-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[3.2, 8], [3.2, 28]], "width": 0.6},
                    "parking_allowed": False,
                    "vehicle_allowed": False,
                    "priority": "hard",
                }
            ]
        },
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    )
    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.stall_count > 0
    assert validate_parking_quotas(layout)["actual"]["accessible"] >= 1
    assert any(stall.stall_type_id == "accessible-90" for stall in layout.stalls)
    assert diagnostics["field_support"]["parking.contact_retarget"] == "active"
