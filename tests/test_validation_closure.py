from dataclasses import replace

import pytest

import openparkcad.phase1_candidates as phase1_candidates
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, ParkingAisle, SiteSpec, StallSpec
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def _site(*, constraints=None, optimization=None, margin=0.0) -> SiteSpec:
    return SiteSpec(
        name="validation-closure",
        boundary=[(0, 0), (36, 0), (36, 38), (0, 38)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=margin,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(18, 0),
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
        constraints=constraints or {},
        optimization=optimization
        or {
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
        },
    )


def test_hard_reject_excludes_operationally_invalid_candidate_from_final_layout():
    optimization = {
        "heading_deltas_degrees": [0],
        "entrance_offsets": [0],
        "enable_branches": False,
        "operational_quality_mode": "hard_reject",
        "operational_max_risk_score": 0,
        "operational_turnaround_dependency_risk": 1,
    }

    rejected = generate_layout(_site(optimization=optimization))
    score_only = generate_layout(
        _site(optimization={**optimization, "operational_quality_mode": "score_only"})
    )

    assert score_only.stall_count > 0
    assert rejected.stall_count == 0
    assert rejected.aisles == []
    assert rejected.selected_stall_type_id is None
    assert rejected.selected_stall_assignment == {}
    assert rejected.stall_type_attempts[0]["selected"] is False
    assert rejected.stall_type_attempts[0]["layout_valid"] is False
    assert rejected.stall_type_attempts[0]["operational_valid"] is False
    assert rejected.attempts[0].graph_valid is True
    assert rejected.operational_quality["valid"] is False
    assert rejected.operational_quality["risk_exceeds_limit"] is True
    assert rejected.operational_quality["result_scope"] == "best_rejected_candidate"
    assert rejected.operational_quality["rejected_candidate_stall_count"] == score_only.stall_count


def test_empty_layout_is_not_selected_even_when_reports_are_formally_valid():
    base = _site()
    too_small = replace(
        base,
        boundary=[(0, 0), (12, 0), (12, 10), (0, 10)],
        entrances=[replace(base.entrances[0], center=(6, 0))],
    )

    layout = generate_layout(too_small)

    assert layout.stall_count == 0
    assert layout.aisles == []
    assert layout.graph_validation["valid"] is True
    assert layout.maneuver_validation["valid"] is True
    assert layout.operational_quality["valid"] is True
    assert layout.selected_stall_type_id is None
    assert layout.selected_stall_assignment == {}
    assert layout.stall_type_attempts[0]["layout_valid"] is False
    assert layout.stall_type_attempts[0]["selected"] is False


@pytest.mark.parametrize(
    ("relationship", "metadata"),
    [
        ("parent", {"parent_aisle_id": "A-MAIN"}),
        ("connected", {"connected_aisle_ids": ("A-MAIN",)}),
    ],
)
def test_declared_aisle_connection_requires_geometric_contact(relationship, metadata):
    layout = generate_layout(_site())
    disjoint = ParkingAisle(
        id="A-DISJOINT",
        polygon=[(29, 29), (35, 29), (35, 35), (29, 35)],
        angle_degrees=0.0,
        role="branch",
        **metadata,
    )
    broken = replace(layout, aisles=[*layout.aisles, disjoint])

    graph = build_traffic_graph(broken)
    validation = validate_traffic_graph(graph, broken)

    assert not any(
        {edge.from_node_id, edge.to_node_id} == {"N-AISLE-A-MAIN", "N-AISLE-A-DISJOINT"}
        for edge in graph.edges
    )
    assert validation["valid"] is False
    assert "aisle_connections_do_not_touch" in validation["errors"]
    assert "A-DISJOINT" in validation["unreachable_aisles"]
    assert len(validation["disconnected_aisle_connections"]) == 1
    disconnected = validation["disconnected_aisle_connections"][0]
    assert disconnected["aisle_id"] == "A-DISJOINT"
    assert disconnected["related_aisle_id"] == "A-MAIN"
    assert disconnected["relationship"] == relationship
    assert disconnected["distance"] > 0


def test_declared_entrance_connection_requires_geometric_contact():
    layout = generate_layout(_site(constraints={"circulation": {"allow_dead_end_aisles": True}}))
    disjoint = ParkingAisle(
        id="A-DISJOINT-ENTRANCE",
        polygon=[(29, 29), (35, 29), (35, 35), (29, 35)],
        angle_degrees=90.0,
        role="main",
        connected_to_entrance_id="main",
    )
    broken = replace(layout, aisles=[*layout.aisles, disjoint])

    graph = build_traffic_graph(broken)
    validation = validate_traffic_graph(graph, broken)

    assert not any(
        {edge.from_node_id, edge.to_node_id}
        == {"N-ENTRANCE-main", "N-AISLE-A-DISJOINT-ENTRANCE"}
        for edge in graph.edges
    )
    assert validation["valid"] is False
    assert "entrance_connections_do_not_touch" in validation["errors"]
    assert validation["disconnected_entrance_connections"][0]["aisle_id"] == "A-DISJOINT-ENTRANCE"
    assert validation["disconnected_entrance_connections"][0]["entrance_id"] == "main"
    assert validation["disconnected_entrance_connections"][0]["distance"] > 0


def test_entrance_connection_allows_configured_site_setback_gap():
    layout = generate_layout(_site(margin=0.2))

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)

    assert validation["valid"] is True
    assert validation["disconnected_entrance_connections"] == []


def test_missing_turnaround_is_a_constraint_error_when_dead_ends_are_disallowed():
    site = _site(constraints={"circulation": {"allow_dead_end_aisles": False}})
    layout = generate_layout(site)
    without_turnaround = replace(
        layout,
        aisles=[aisle for aisle in layout.aisles if aisle.role != "turnaround"],
    )

    validation = validate_traffic_graph(build_traffic_graph(without_turnaround), without_turnaround)

    assert validation["valid"] is False
    assert "dead_end_without_turnaround" in validation["errors"]
    assert validation["dead_ends"] == [
        {
            "aisle_id": "A-MAIN",
            "node_id": "N-AISLE-A-MAIN",
            "parent_aisle_id": None,
            "turnaround_present": False,
            "status": "dead_end_without_turnaround",
        }
    ]


def test_explicitly_allowed_dead_end_does_not_require_turnaround():
    site = _site(constraints={"circulation": {"allow_dead_end_aisles": True}})
    layout = generate_layout(site)
    without_turnaround = replace(
        layout,
        aisles=[aisle for aisle in layout.aisles if aisle.role != "turnaround"],
    )

    validation = validate_traffic_graph(build_traffic_graph(without_turnaround), without_turnaround)

    assert validation["valid"] is True
    assert validation["errors"] == []
    assert validation["dead_ends"][0]["status"] == "allowed_dead_end"


def test_connector_selection_accepts_higher_score_even_with_fewer_stalls(monkeypatch):
    base = generate_layout(_site())
    candidate = replace(base, stalls=base.stalls[:-1])
    diagnostics = []
    branch_a = {"id": "A-BRANCH-001"}
    branch_b = {"id": "A-BRANCH-002"}

    monkeypatch.setattr(
        phase1_candidates,
        "_connector_pairs",
        lambda layout, parent_aisle_id=None: [(branch_a, branch_b)],
    )
    monkeypatch.setattr(
        phase1_candidates,
        "_connector_inset_depths",
        lambda site, left, right: (1.0,),
    )
    monkeypatch.setattr(
        phase1_candidates,
        "_connector_layout",
        lambda *args, **kwargs: (candidate, {"reason": "connector_improves_score"}),
    )

    selected = phase1_candidates._with_best_connectors(
        base.site,
        available=None,
        entrance=base.site.entrances[0],
        heading_degrees=90.0,
        base=base,
        finalize_layout=lambda layout: layout,
        layout_valid=lambda layout: True,
        score_total=lambda layout: 2.0 if layout is candidate else 1.0,
        diagnostics=diagnostics,
    )

    assert candidate.stall_count == base.stall_count - 1
    assert selected is candidate
    assert diagnostics == [{"reason": "connector_improves_score"}]
