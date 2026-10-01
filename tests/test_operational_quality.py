from openparkcad.models import AisleClassSpec, EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.operational_quality import operational_quality_report
from openparkcad.scoring import score_layout


def _quality_layout(site: SiteSpec | None = None) -> LayoutResult:
    return LayoutResult(
        site=site
        or SiteSpec(
            name="operational-quality",
            boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
            stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
            aisle_width=6.0,
            margin=0.0,
        ),
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
            ),
            ParkingAisle(
                id="A-BRANCH-001",
                polygon=[(8, 4), (14, 4), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="branch",
                parent_aisle_id="A-MAIN",
            ),
        ],
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(13, 7), (15.5, 7), (15.5, 12), (13, 12)],
                angle_degrees=90.0,
                served_by_aisle_id="A-BRANCH-001",
                aisle_side="right",
            )
        ],
    )


def _route_layout(optimization: dict | None = None) -> LayoutResult:
    site = SiteSpec(
        name="route-quality",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(0, 7),
                width=7.0,
                heading_degrees=0.0,
            )
        ],
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="main",
            ),
            ParkingAisle(
                id="A-TURNAROUND",
                polygon=[(20, 4), (26, 4), (26, 10), (20, 10)],
                angle_degrees=0.0,
                role="turnaround",
                parent_aisle_id="A-MAIN",
            ),
        ],
        stalls=[
            ParkingStall(
                id="P-ROUTE-001",
                polygon=[(8, 10), (10.5, 10), (10.5, 15), (8, 15)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def _directional_trap_layout(optimization: dict | None = None) -> LayoutResult:
    site = SiteSpec(
        name="directional-trap",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry",
                mode="entry_only",
                center=(0, 7),
                width=7.0,
                heading_degrees=0.0,
            ),
            EntranceSpec(
                id="exit",
                mode="exit_only",
                center=(30, 7),
                width=7.0,
                heading_degrees=180.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-one-way",
                width=6.0,
                directionality="one_way",
            )
        ],
        fixed_aisle_class="wide-one-way",
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="entry",
            )
        ],
        stalls=[
            ParkingStall(
                id="P-DIR-001",
                polygon=[(8, 10), (10.5, 10), (10.5, 15), (8, 15)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def _narrow_two_way_layout(
    optimization: dict | None = None,
    site_features: list[dict] | None = None,
) -> LayoutResult:
    site = SiteSpec(
        name="narrow-two-way",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=3.5,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(0, 7),
                width=4.0,
                heading_degrees=0.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="narrow-two-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="narrow-two-way",
        site_features=site_features or [],
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 7.5), (0, 7.5)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="main",
            )
        ],
        stalls=[
            ParkingStall(
                id="P-NARROW-001",
                polygon=[(8, 7.5), (10.5, 7.5), (10.5, 12.5), (8, 12.5)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def _narrow_two_way_branch_layout(optimization: dict | None = None) -> LayoutResult:
    layout = _narrow_two_way_layout(optimization=optimization)
    return LayoutResult(
        site=layout.site,
        aisles=[
            *layout.aisles,
            ParkingAisle(
                id="A-BRANCH",
                polygon=[(8, 4), (11.5, 4), (11.5, 14), (8, 14)],
                angle_degrees=90.0,
                role="branch",
                parent_aisle_id="A-MAIN",
            ),
        ],
        stalls=layout.stalls,
    )


def test_phase5q_operational_quality_reports_junction_stall_conflicts():
    report = operational_quality_report(_quality_layout())

    assert report["version"] == "phase5r-1"
    assert report["status"] == "report_only"
    assert report["mode"] == "score_only"
    assert report["valid"] is True
    assert report["junction_count"] == 1
    assert report["junction_conflict_count"] == 1
    assert report["risk_score"] == 1.0
    assert report["route_risk_score"] == 0.0
    assert report["directionality_risk_score"] == 0.0
    assert report["narrow_two_way_risk_score"] == 0.0
    assert report["route_summary"]["checked_stall_count"] == 0
    assert report["directionality_summary"]["checked_stall_count"] == 1
    assert report["narrow_two_way_summary"]["is_narrow_two_way"] is False
    assert report["route_summary_risks"] == []
    assert report["promotion_blockers"] == []
    assert report["blocking_conflicts"] == []
    assert report["junctions"][0]["conflicting_stalls"][0]["stall_id"] == "P-001"


def test_phase5g_operational_quality_reports_route_lengths_without_default_penalty():
    report = operational_quality_report(_route_layout())

    assert report["route_risks"]["status"] == "active"
    assert report["route_risks"]["version"] == "phase5f-1"
    assert report["route_risks"]["checked_stall_count"] == 1
    assert report["route_risk_score"] == 0.0
    assert report["route_risks"]["stall_route_risk_score"] == 0.0
    assert report["route_risks"]["summary_risk_score"] == 0.0
    assert report["route_summary_risks"] == []
    summary = report["route_summary"]
    assert summary["checked_stall_count"] == 1
    assert summary["average_route_length"] == 20.0
    assert summary["max_route_length"] == 20.0
    assert summary["max_entry_path_length"] == 10.0
    assert summary["max_exit_path_length"] == 10.0
    assert summary["longest_route_stall_id"] == "P-ROUTE-001"
    assert summary["turnaround_dependency_count"] == 1
    assert summary["turnaround_dependency_ratio"] == 1.0
    assert summary["long_route_ratio"] == 0.0
    assert summary["issue_counts"] == {}
    route = report["route_risks"]["routes"][0]
    assert route["stall_id"] == "P-ROUTE-001"
    assert route["entry_path_length"] == 10.0
    assert route["exit_path_length"] == 10.0
    assert route["route_length"] == 20.0
    assert route["depends_on_dead_end_turnaround"] is True
    assert route["issues"] == []


def test_phase5g_operational_directionality_reports_trap_without_default_penalty():
    report = operational_quality_report(_directional_trap_layout())

    assert report["directionality_risks"]["status"] == "active"
    assert report["directionality_risks"]["version"] == "phase5g-1"
    assert report["directionality_risk_score"] == 0.0
    assert report["directionality_summary"]["node_issue_count"] == 1
    assert report["directionality_summary"]["stall_issue_count"] == 1
    assert report["directionality_summary"]["stall_issue_ratio"] == 1.0
    assert report["directionality_summary"]["one_way_trap_node_count"] == 1
    assert report["directionality_risks"]["node_issues"][0]["issue"] == "one_way_trap"
    assert report["directionality_risks"]["stall_issues"][0]["issue"] == "stall_on_one_way_trap"
    assert report["directionality_summary_risks"] == []


def test_phase5g_operational_directionality_issue_can_gate_promotion():
    layout = _directional_trap_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_directionality_issue_risk": 1,
            "operational_missing_route_risk": 0,
        }
    )

    report = operational_quality_report(layout)

    assert report["directionality_risk_score"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "directionality_stall")
    assert conflict["stall_id"] == "P-DIR-001"
    assert conflict["issue"] == "stall_on_one_way_trap"


def test_phase5g_operational_directionality_issue_ratio_can_gate_promotion():
    layout = _directional_trap_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_directionality_issue_ratio": 0.5,
            "operational_missing_route_risk": 0,
        }
    )

    report = operational_quality_report(layout)

    assert report["directionality_risk_score"] == 1.0
    assert report["directionality_summary_risks"][0]["issue"] == "directionality_stall_issue_ratio_exceeds_limit"
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "directionality_summary")
    assert conflict["stall_issue_ratio"] == 1.0


def test_phase5q_operational_narrow_two_way_reports_without_default_penalty():
    report = operational_quality_report(_narrow_two_way_layout())

    assert report["narrow_two_way_risks"]["status"] == "active"
    assert report["narrow_two_way_risks"]["version"] == "phase5q-1"
    assert report["narrow_two_way_risk_score"] == 0.0
    assert report["narrow_two_way_summary"]["is_narrow_two_way"] is True
    assert report["narrow_two_way_summary"]["narrow_two_way_aisle_count"] == 1
    assert report["narrow_two_way_summary"]["affected_stall_count"] == 1
    assert report["narrow_two_way_summary"]["affected_stall_ratio"] == 1.0
    assert report["narrow_two_way_summary"]["passing_bay_model_available"] is False
    assert report["narrow_two_way_summary"]["passing_bay_marker_count"] == 0
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["passing_bay_spacing_checked"] is True
    assert report["narrow_two_way_summary"]["longest_passing_bay_gap"] == 20.0
    assert report["narrow_two_way_summary"]["longest_passing_bay_gap_type"] == "no_passing_bay_full_aisle"
    assert report["narrow_two_way_summary"]["passing_bay_endpoint_gap_count"] == 0
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 0
    assert report["narrow_two_way_risks"]["passing_bays"] == []
    spacing = report["narrow_two_way_risks"]["passing_bay_spacing"][0]
    assert spacing["longest_unserved_gap"] == 20.0
    assert spacing["longest_unserved_gap_type"] == "no_passing_bay_full_aisle"
    assert spacing["gaps"][0]["segment_type"] == "no_passing_bay_full_aisle"
    assert spacing["gaps"][0]["network_segment_type"] == "entrance_to_terminal"
    assert spacing["gaps"][0]["start_network_kind"] == "entrance_throat"
    assert spacing["gaps"][0]["end_network_kind"] == "aisle_terminal"
    assert spacing["gaps"][0]["exceeds_limit"] is False
    assert report["narrow_two_way_risks"]["meeting_risks"] == []
    assert report["narrow_two_way_risks"]["aisle_issues"][0]["issue"] == "narrow_two_way_without_passing_bay_model"
    assert report["narrow_two_way_risks"]["stall_issues"][0]["issue"] == "stall_served_by_narrow_two_way_aisle_without_passing_bay_model"
    assert report["narrow_two_way_summary_risks"] == []


def test_phase5q_operational_narrow_two_way_detects_usable_passing_bay_markers():
    layout = _narrow_two_way_layout(
        site_features=[
            {
                "id": "bay-1",
                "type": "passing-bay",
                "aisle_id": "A-MAIN",
                "center": [12.0, 5.75],
                "width": 2.5,
                "length": 6.0,
            }
        ]
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_summary"]["passing_bay_model_available"] is True
    assert report["narrow_two_way_summary"]["passing_bay_marker_count"] == 1
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 1
    assert report["narrow_two_way_summary"]["invalid_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["longest_passing_bay_gap"] == 12.0
    assert report["narrow_two_way_summary"]["longest_passing_bay_gap_type"] == "endpoint_to_passing_bay"
    assert report["narrow_two_way_summary"]["passing_bay_endpoint_gap_count"] == 2
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 0
    assert report["narrow_two_way_risks"]["passing_bays"][0]["id"] == "bay-1"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["type"] == "passing_bay"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["usable"] is True
    assert report["narrow_two_way_risks"]["passing_bays"][0]["associated_aisle_id"] == "A-MAIN"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["geometry_source"] == "center_width_length"
    assert report["narrow_two_way_risks"]["passing_bay_spacing"][0]["projected_passing_bays"][0]["position_along_aisle"] == 12.0
    assert [gap["segment_type"] for gap in report["narrow_two_way_risks"]["passing_bay_spacing"][0]["gaps"]] == [
        "endpoint_to_passing_bay",
        "endpoint_to_passing_bay",
    ]
    assert [gap["network_segment_type"] for gap in report["narrow_two_way_risks"]["passing_bay_spacing"][0]["gaps"]] == [
        "entrance_to_refuge",
        "terminal_to_refuge",
    ]
    assert report["narrow_two_way_summary"]["meeting_risk_count"] == 0
    assert report["narrow_two_way_risks"]["aisle_issues"][0]["issue"] == "narrow_two_way_passing_bay_spacing_checked"
    assert report["narrow_two_way_risks"]["stall_issues"][0]["issue"] == "stall_served_by_narrow_two_way_aisle_pending_passing_bay_spacing_check"
    assert report["narrow_two_way_summary_risks"] == []


def test_phase5q_operational_passing_bay_shortage_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_min_passing_bays": 2,
            "operational_passing_bay_shortage_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 2.0
    assert report["narrow_two_way_summary"]["min_passing_bays"] == 2
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 2
    assert report["narrow_two_way_summary_risks"][0]["issue"] == "passing_bay_count_below_minimum"
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_summary")
    assert conflict["passing_bay_marker_count"] == 0
    assert conflict["usable_passing_bay_count"] == 0
    assert conflict["min_passing_bays"] == 2
    assert conflict["passing_bay_shortage_count"] == 2


def test_phase5q_operational_passing_bay_geometry_issue_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_passing_bay_geometry_issue_risk": 1,
        },
        site_features=[
            {
                "id": "bay-far",
                "type": "passing_bay",
                "aisle_id": "A-MAIN",
                "center": [25.0, 15.0],
                "width": 2.5,
                "length": 6.0,
            }
        ],
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["invalid_passing_bay_count"] == 1
    passing_bay = report["narrow_two_way_risks"]["passing_bays"][0]
    assert passing_bay["usable"] is False
    assert passing_bay["issues"] == ["passing_bay_not_adjacent_to_aisle"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "passing_bay")
    assert conflict["passing_bay_id"] == "bay-far"
    assert conflict["issues"] == ["passing_bay_not_adjacent_to_aisle"]


def test_phase5q_operational_passing_bay_spacing_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_passing_bay_spacing": 10,
            "operational_passing_bay_spacing_risk": 1,
        },
        site_features=[
            {
                "id": "bay-1",
                "type": "passing_bay",
                "aisle_id": "A-MAIN",
                "center": [12.0, 5.75],
                "width": 2.5,
                "length": 6.0,
            }
        ],
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary"]["passing_bay_spacing_issue_count"] == 1
    assert report["narrow_two_way_summary"]["passing_bay_spacing_exceeded_gap_count"] == 1
    spacing = report["narrow_two_way_risks"]["passing_bay_spacing"][0]
    assert spacing["issue"] == "passing_bay_spacing_exceeds_limit"
    assert spacing["exceeded_gap_count"] == 1
    assert spacing["longest_unserved_gap"] == 12.0
    assert spacing["longest_unserved_gap_type"] == "endpoint_to_passing_bay"
    assert [gap["exceeds_limit"] for gap in spacing["gaps"]] == [True, False]
    assert report["narrow_two_way_risks"]["meeting_risks"][0]["issue"] == "entrance_to_refuge_gap_exceeds_limit"
    assert report["narrow_two_way_risks"]["meeting_risks"][0]["risk_score"] == 0.0
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "passing_bay_spacing")
    assert conflict["aisle_id"] == "A-MAIN"
    assert conflict["exceeded_gap_count"] == 1
    assert conflict["longest_unserved_gap"] == 12.0
    assert conflict["longest_unserved_gap_type"] == "endpoint_to_passing_bay"
    assert conflict["max_passing_bay_spacing"] == 10.0


def test_phase5q_operational_narrow_two_way_meeting_gap_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_passing_bay_spacing": 10,
            "operational_passing_bay_spacing_risk": 0,
            "operational_narrow_two_way_meeting_gap_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary"]["meeting_risk_count"] == 1
    assert report["narrow_two_way_summary"]["full_aisle_meeting_risk_count"] == 0
    assert report["narrow_two_way_summary"]["entrance_meeting_trap_count"] == 1
    meeting = report["narrow_two_way_risks"]["meeting_risks"][0]
    assert meeting["issue"] == "entrance_to_terminal_without_meeting_refuge"
    assert meeting["segment_type"] == "no_passing_bay_full_aisle"
    assert meeting["network_segment_type"] == "entrance_to_terminal"
    assert meeting["start_network_kind"] == "entrance_throat"
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_meeting")
    assert conflict["aisle_id"] == "A-MAIN"
    assert conflict["issue"] == "entrance_to_terminal_without_meeting_refuge"
    assert conflict["start_network_kind"] == "entrance_throat"
    assert conflict["length"] == 20.0


def test_phase5q_operational_branch_endpoint_and_parent_projection_report_junction_meeting_risks():
    layout = _narrow_two_way_branch_layout(
        {
            "operational_max_passing_bay_spacing": 5,
            "operational_passing_bay_spacing_risk": 0,
        }
    )

    report = operational_quality_report(layout)

    main_spacing = next(item for item in report["narrow_two_way_risks"]["passing_bay_spacing"] if item["aisle_id"] == "A-MAIN")
    assert main_spacing["projected_junction_count"] == 1
    assert main_spacing["projected_junctions"][0]["junction_aisle_id"] == "A-BRANCH"
    assert 8.0 <= main_spacing["projected_junctions"][0]["position_along_aisle"] <= 11.5
    assert [gap["network_segment_type"] for gap in main_spacing["gaps"]] == [
        "entrance_to_junction",
        "junction_to_terminal",
    ]
    assert [gap["segment_type"] for gap in main_spacing["gaps"]] == [
        "endpoint_to_junction",
        "endpoint_to_junction",
    ]
    branch_spacing = next(item for item in report["narrow_two_way_risks"]["passing_bay_spacing"] if item["aisle_id"] == "A-BRANCH")
    assert branch_spacing["projected_junction_count"] == 0
    gap = branch_spacing["gaps"][0]
    assert gap["network_segment_type"] == "junction_to_terminal"
    assert gap["start_network_kind"] == "aisle_junction"
    assert gap["start_network_id"] == "A-MAIN"
    main_meeting = next(
        item
        for item in report["narrow_two_way_risks"]["meeting_risks"]
        if item["aisle_id"] == "A-MAIN" and item["network_segment_type"] == "entrance_to_junction"
    )
    assert main_meeting["issue"] == "entrance_to_junction_gap_exceeds_limit"
    branch_meeting = next(item for item in report["narrow_two_way_risks"]["meeting_risks"] if item["aisle_id"] == "A-BRANCH")
    assert branch_meeting["issue"] == "junction_to_terminal_gap_exceeds_limit"
    assert branch_meeting["network_segment_type"] == "junction_to_terminal"
    assert report["narrow_two_way_summary"]["passing_bay_projected_junction_count"] == 1
    assert report["narrow_two_way_summary"]["passing_bay_junction_gap_count"] == 3
    assert report["narrow_two_way_summary"]["junction_meeting_trap_count"] == 3
    assert report["narrow_two_way_summary"]["junction_merge_issue_count"] == 1
    assert report["narrow_two_way_summary"]["junction_merge_missing_refuge_count"] == 1
    assert report["narrow_two_way_summary"]["junction_merge_overlong_approach_count"] == 1
    merge = report["narrow_two_way_risks"]["junction_merge_risks"][0]
    assert merge["junction_aisle_ids"] == ["A-BRANCH", "A-MAIN"]
    assert merge["approach_count"] == 3
    assert merge["refuge_approach_count"] == 0
    assert merge["overlong_approach_count"] == 3
    assert merge["issues"] == [
        "multi_approach_junction_without_refuge",
        "multi_approach_junction_with_overlong_approach",
    ]
    assert merge["risk_score"] == 0.0


def test_phase5q_operational_narrow_two_way_junction_merge_can_gate_promotion():
    layout = _narrow_two_way_branch_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_passing_bay_spacing": 5,
            "operational_passing_bay_spacing_risk": 0,
            "operational_narrow_two_way_junction_merge_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_risks"]["junction_merge_risk_score"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_junction_merge")
    assert conflict["junction_aisle_ids"] == ["A-BRANCH", "A-MAIN"]
    assert conflict["approach_count"] == 3
    assert conflict["refuge_approach_count"] == 0
    assert conflict["overlong_approach_count"] == 3


def test_phase5q_operational_narrow_two_way_issue_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_narrow_two_way_issue_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_stall")
    assert conflict["stall_id"] == "P-NARROW-001"
    assert conflict["issue"] == "stall_served_by_narrow_two_way_aisle_without_passing_bay_model"


def test_phase5q_operational_narrow_two_way_stall_ratio_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_narrow_two_way_stall_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary_risks"][0]["issue"] == "narrow_two_way_stall_ratio_exceeds_limit"
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_summary")
    assert conflict["affected_stall_ratio"] == 1.0


def test_phase5g_operational_route_risk_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_route_length": 5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_summary"]["route_length_exceeds_limit_count"] == 1
    assert report["route_summary"]["long_route_ratio"] == 1.0
    assert report["route_summary"]["issue_counts"] == {"route_length_exceeds_limit": 1}
    assert report["risk_exceeds_limit"] is True
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    route_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "stall_route")
    assert route_conflict["stall_id"] == "P-ROUTE-001"
    assert route_conflict["issues"] == ["route_length_exceeds_limit"]


def test_phase5g_operational_turnaround_dependency_ratio_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_turnaround_dependency_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_risks"]["stall_route_risk_score"] == 0.0
    assert report["route_risks"]["summary_risk_score"] == 1.0
    assert report["route_summary_risks"][0]["issue"] == "turnaround_dependency_ratio_exceeds_limit"
    assert report["route_summary_risks"][0]["turnaround_dependency_ratio"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    summary_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "route_summary")
    assert summary_conflict["issue"] == "turnaround_dependency_ratio_exceeds_limit"


def test_phase5g_operational_average_route_length_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_average_route_length": 10,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_summary_risks"][0]["issue"] == "average_route_length_exceeds_limit"
    assert report["route_summary_risks"][0]["average_route_length"] == 20.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    summary_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "route_summary")
    assert summary_conflict["issue"] == "average_route_length_exceeds_limit"


def test_phase5g_operational_long_route_ratio_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_route_length": 5,
            "operational_max_long_route_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 2.0
    assert [item["issue"] for item in report["route_summary_risks"]] == ["long_route_ratio_exceeds_limit"]
    assert report["route_summary_risks"][0]["long_route_ratio"] == 1.0
    summary_conflict = next(
        item
        for item in report["blocking_conflicts"]
        if item["source_type"] == "route_summary"
    )
    assert summary_conflict["issue"] == "long_route_ratio_exceeds_limit"


def test_phase5g_operational_quality_score_only_does_not_block_with_limit():
    site = SiteSpec(
        name="operational-score-only",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "score_only",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["risk_exceeds_limit"] is True
    assert report["valid"] is True
    assert report["promotion_blockers"] == []
    assert report["blocking_conflicts"] == []


def test_phase5g_operational_quality_promotion_gate_reports_blockers():
    site = SiteSpec(
        name="operational-gate",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["valid"] is True
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    assert report["blocking_conflicts"][0]["source_type"] == "junction"
    assert report["blocking_conflicts"][0]["stall_id"] == "P-001"


def test_phase5g_operational_quality_hard_reject_marks_layout_invalid():
    site = SiteSpec(
        name="operational-hard-reject",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "hard_reject",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["status"] == "active_failed"
    assert report["valid"] is False
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]


def test_phase5g_operational_risk_penalty_is_scoreable():
    site = SiteSpec(
        name="operational-risk-score",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "weights": {
                "stall_count": 0,
                "aisle_area": 0,
                "heading_delta": 0,
                "entrance_offset": 0,
                "branch_count": 0,
                "dead_end_length": 0,
                "operational_risk": -2,
            }
        },
    )
    layout = _quality_layout(site)

    score = score_layout(layout)

    assert score["operational_risk"] == 1.0
    assert score["operational_risk_penalty"] == -2.0
    assert score["total"] == -2.0


def test_pedestrian_conflict_not_applicable_without_walkways():
    report = operational_quality_report(_quality_layout())

    assert report["pedestrian_conflict_risks"]["status"] == "not_applicable"
    assert report["pedestrian_conflict_risk_score"] == 0.0
    assert report["risk_score"] == 1.0


def test_stall_near_walkway_scores_when_risk_weight_is_set():
    site = SiteSpec(
        name="pedestrian-conflict",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        pedestrian_and_emergency={
            "pedestrian_routes": [
                {
                    "id": "east-walk",
                    "geometry": {"type": "polyline_buffer", "points": [[16, 8], [22, 8]], "width": 0.5},
                    "parking_allowed": True,
                    "vehicle_allowed": True,
                    "priority": "advisory",
                }
            ]
        },
        optimization={
            "operational_pedestrian_conflict_clearance": 1.0,
            "operational_pedestrian_conflict_risk": 2.0,
        },
    )
    layout = _quality_layout(site)
    report = operational_quality_report(layout)

    assert report["pedestrian_conflict_risks"]["status"] == "report_only"
    assert report["pedestrian_conflict_summary"]["stall_near_count"] >= 1
    assert any(
        item["kind"] == "stall_near_pedestrian_route" and item["stall_id"] == "P-001"
        for item in report["pedestrian_conflict_risks"]["issues"]
    )
    assert report["pedestrian_conflict_risk_score"] == report["pedestrian_conflict_risk_count"] * 2.0
    assert report["risk_score"] == 1.0 + report["pedestrian_conflict_risk_score"]


def test_aisle_crossing_walkway_is_reported():
    site = SiteSpec(
        name="pedestrian-cross",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        pedestrian_and_emergency={
            "pedestrian_routes": [
                {
                    "id": "crosswalk",
                    "geometry": {"type": "polyline_buffer", "points": [[0, 7], [20, 7]], "width": 1.0},
                    "parking_allowed": True,
                    "vehicle_allowed": True,
                    "priority": "advisory",
                }
            ]
        },
        optimization={"operational_pedestrian_conflict_risk": 1.0},
    )
    report = operational_quality_report(_quality_layout(site))

    assert report["pedestrian_conflict_summary"]["aisle_cross_count"] >= 1
    assert any(item["kind"] == "aisle_crosses_pedestrian_route" for item in report["pedestrian_conflict_risks"]["issues"])
