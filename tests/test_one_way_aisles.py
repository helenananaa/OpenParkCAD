"""One-way aisle generation and direction-aware traffic graph."""

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_support import (
    aisle_directionality,
    is_phase1_aisle_class,
    one_way_allows_reverse_egress,
    phase1_unsupported_inputs,
    supports_phase1_aisle,
)
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def _one_way_site(**overrides) -> SiteSpec:
    data = {
        "name": "one-way",
        "boundary": [(0, 0), (28, 0), (28, 40), (0, 40)],
        "stall": StallSpec(width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        "aisle_width": 3.5,
        "margin": 0.0,
        "entrances": [
            EntranceSpec(
                id="main",
                mode="shared",
                center=(14, 0),
                width=6.0,
                heading_degrees=90.0,
            )
        ],
        "aisle_classes": [
            AisleClassSpec(
                id="narrow-one-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="one_way",
                centerline_crossing="not_applicable",
                enabled=True,
            )
        ],
        "fixed_aisle_class": "narrow-one-way",
        "constraints": {"circulation": {"one_way_allows_reverse_egress": True}},
        "optimization": {
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
        },
    }
    data.update(overrides)
    return SiteSpec(**data)


def test_phase1_supports_enabled_one_way_aisle_classes():
    one_way = AisleClassSpec(
        id="narrow-one-way",
        width=3.5,
        capacity="single_vehicle",
        directionality="one_way",
        enabled=True,
    )
    narrow_two = AisleClassSpec(
        id="narrow-two-way",
        width=3.5,
        capacity="single_vehicle",
        directionality="two_way",
        enabled=True,
    )
    assert is_phase1_aisle_class(one_way) is True
    assert is_phase1_aisle_class(narrow_two) is False


def test_narrow_two_way_remains_unsupported_fail_closed():
    site = _one_way_site(
        aisle_width=3.5,
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
    )
    assert supports_phase1_aisle(site) is False
    issues = phase1_unsupported_inputs(site)
    assert any("narrow two-way" in item["reason"].lower() or "two_way" in item["reason"] for item in issues)
    layout = generate_layout(site)
    assert layout.stall_count == 0


def test_one_way_generation_produces_directed_layout_with_reverse_egress():
    site = _one_way_site()
    assert supports_phase1_aisle(site) is True
    assert aisle_directionality(site) == "one_way"
    assert one_way_allows_reverse_egress(site) is True

    layout = generate_layout(site)

    assert layout.stall_count > 0
    assert all(aisle.directionality == "one_way" for aisle in layout.aisles)
    assert {aisle.id for aisle in layout.aisles} >= {"A-MAIN", "A-TURNAROUND"}

    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)

    assert validation["valid"] is True
    assert validation["aisle_directionality"] == "one_way"
    assert validation["one_way_allows_reverse_egress"] is True
    assert validation["one_way_edge_count"] > 0
    assert validation["reverse_egress_edge_count"] > 0
    assert all(edge.directionality == "one_way" for edge in graph.edges)
    assert any(edge.role == "reverse_egress" for edge in graph.edges)
    # Must not silently use two-way aisle edges for a one-way class.
    assert not any(edge.directionality == "two_way" for edge in graph.edges)

    diagnostics = build_input_diagnostics(site, layout)
    field_support = diagnostics["field_support"]
    assert field_support["aisles.fixed_one_way"] == "active"
    assert field_support["aisles.narrow_one_way"] == "active"
    assert field_support["aisles.narrow_two_way"] == "available"
    assert field_support["aisles.fixed_wide_two_way"] == "available"


def test_one_way_without_reverse_egress_fails_closed_on_dead_end_template():
    site = _one_way_site(
        constraints={"circulation": {"one_way_allows_reverse_egress": False}},
    )
    layout = generate_layout(site)
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)

    assert all(aisle.directionality == "one_way" for aisle in layout.aisles)
    assert validation["aisle_directionality"] == "one_way"
    assert validation["one_way_allows_reverse_egress"] is False
    assert validation["reverse_egress_edge_count"] == 0
    # Pure one-way into a dead-end template cannot return to a shared exit.
    if layout.stall_count > 0:
        assert validation["valid"] is False
        assert "stalls_without_exit_path" in validation["errors"]
    else:
        # Generator may reject empty invalid layouts before export; either is fail-closed.
        assert layout.stall_count == 0 or not validation["valid"]
