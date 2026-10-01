"""Synthesized side-pocket passing bays and narrow two-way unlock."""

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec, VehicleSpec
from openparkcad.phase1_support import is_phase1_aisle_class, passing_bay_synthesis_enabled
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        id="passenger-car",
        length=4.8,
        width=1.9,
        wheelbase=2.8,
        min_turning_radius=5.5,
        turning_radius_reference="outer_front_wheel",
        track_width=1.6,
        front_overhang=1.0,
        rear_overhang=1.0,
    )


def _narrow_two_way_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="narrow-two-way-passing-bays",
        boundary=[(0, 0), (20, 0), (20, 80), (0, 80)],
        stall=StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=3.5,
        margin=0.0,
        vehicle=_vehicle(),
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(10, 0),
                width=6.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="narrow-two-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="two_way",
                enabled=True,
            )
        ],
        fixed_aisle_class="narrow-two-way",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "enable_passing_bay_synthesis": True,
            "passing_bay_spacing": 25,
            "operational_min_passing_bays": 2,
            "operational_quality_mode": "score_only",
            **optimization,
        },
    )


def test_passing_bay_synthesis_enables_narrow_two_way_class():
    site = _narrow_two_way_site()
    assert passing_bay_synthesis_enabled(site) is True
    assert is_phase1_aisle_class(site.aisle_classes[0], site=site) is True

    site_off = _narrow_two_way_site(enable_passing_bay_synthesis=False, operational_min_passing_bays=None)
    assert passing_bay_synthesis_enabled(site_off) is False
    assert is_phase1_aisle_class(site_off.aisle_classes[0], site=site_off) is False


def test_passing_bay_synthesis_places_bays_and_features():
    layout = generate_layout(_narrow_two_way_site())

    assert layout.stall_count > 0
    pass_aisles = [aisle for aisle in layout.aisles if aisle.role == "passing_bay"]
    assert len(pass_aisles) >= 2
    assert all(aisle.parent_aisle_id == "A-MAIN" for aisle in pass_aisles)

    features = [
        feature
        for feature in layout.site.site_features
        if isinstance(feature, dict) and feature.get("type") == "passing_bay"
    ]
    assert len(features) == len(pass_aisles)
    assert all(feature.get("source") == "synthesized" for feature in features)

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)
    assert validation["valid"] is True
    assert "A-PASS-001" not in validation.get("unreachable_aisles", [])

    summary = layout.operational_quality.get("narrow_two_way_summary", {})
    assert summary.get("is_narrow_two_way") is True
    assert summary.get("usable_passing_bay_count", 0) >= 2
    assert summary.get("passing_bay_shortage_count", 1) == 0

    field_support = build_input_diagnostics(layout.site, layout)["field_support"]
    assert field_support["aisles.narrow_two_way"] == "active"
    assert field_support["aisles.passing_bay_synthesis"] == "active"
    assert field_support["aisles.fixed_wide_two_way"] == "available"


def test_passing_bay_aisles_are_non_circulation_graph_nodes():
    layout = generate_layout(_narrow_two_way_site())
    graph = build_traffic_graph(layout)
    pass_ids = {aisle.id for aisle in layout.aisles if aisle.role == "passing_bay"}
    graph_aisle_refs = {node.ref_id for node in graph.nodes if node.kind != "entrance"}
    assert pass_ids.isdisjoint(graph_aisle_refs)
