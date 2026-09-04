from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.road_skeleton import (
    ROAD_SKELETON_VERSION,
    SkeletonValidationError,
    allowed_movements,
    copy_skeleton,
    make_movement,
    make_node,
    make_segment,
    make_skeleton,
    turn_allowed,
)
from openparkcad.road_skeleton_validation import (
    CODE_COINCIDENT_CENTERLINE,
    CODE_DECLARED_CONNECTION_WITHOUT_CONTACT,
    CODE_DUPLICATE_ID,
    CODE_EMPTY_CENTERLINE,
    CODE_ENTRANCE_PORT_MISMATCH,
    CODE_NON_POSITIVE_WIDTH,
    CODE_UNDECLARED_INTERSECTION,
    validate_skeleton,
)


def _site() -> SiteSpec:
    return SiteSpec(
        name="skeleton-site",
        boundary=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(id="south", mode="shared", center=(8.0, 0.0), width=8.0, heading_degrees=90.0),
        ],
        aisle_classes=[
            AisleClassSpec(id="wide-two-way-no-cross", width=6.0, capacity="two_vehicle", directionality="two_way"),
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    )


def _straight_pair(*, width: float = 6.0, directionality: str = "two_way", with_movement: bool = True):
    n0 = make_node("N0", "entrance_port", (8.0, 0.0), heading_degrees=90.0, source_id="south")
    n1 = make_node("N1", "junction", (8.0, 20.0), heading_degrees=90.0)
    n2 = make_node("N2", "terminal", (24.0, 20.0), heading_degrees=0.0)
    spine = make_segment(
        "S-SPINE",
        "parking_aisle",
        "N0",
        "N1",
        ((8.0, 0.0), (8.0, 20.0)),
        width,
        directionality,
        parking_sides=("left", "right"),
        source={"b": 1, "a": 2},
    )
    cross = make_segment(
        "S-CROSS",
        "cross_aisle",
        "N1",
        "N2",
        ((8.0, 20.0), (24.0, 20.0)),
        width,
        directionality,
        parking_sides=("none",),
        source={"a": 2, "b": 1},
    )
    movements = []
    if with_movement:
        movements.append(make_movement("M-LEFT", "S-SPINE", "S-CROSS", "N1", "left"))
    return [n0, n1, n2], [spine, cross], movements


def _make(*, width=6.0, directionality="two_way", with_movement=True, source=None, **kwargs):
    nodes, segments, movements = _straight_pair(width=width, directionality=directionality, with_movement=with_movement)
    return make_skeleton(
        family="parallel_ladder",
        nodes=nodes,
        segments=segments,
        movements=movements,
        entrance_ids=("south",),
        source=source if source is not None else {"seed": 1, "note": "orig"},
        site=_site(),
        **kwargs,
    )


def test_same_payload_has_stable_id_independent_of_python_hash_and_order() -> None:
    first = _make(source={"k2": "b", "k1": "a"})
    second = _make(source={"k1": "a", "k2": "b"})
    nodes, segments, movements = _straight_pair()
    reordered = make_skeleton(
        family="parallel_ladder",
        nodes=list(reversed(nodes)),
        segments=list(reversed(segments)),
        movements=list(reversed(movements)),
        entrance_ids=("south",),
        source={"k1": "a", "k2": "b"},
        site=_site(),
    )
    assert first.skeleton_id == second.skeleton_id == reordered.skeleton_id
    assert first.skeleton_id == f"{ROAD_SKELETON_VERSION}:{first.payload_digest}"
    assert first.payload_digest == second.payload_digest
    assert len(first.payload_digest) == 64
    assert all(char in "0123456789abcdef" for char in first.payload_digest)
    module_source = inspect.getsource(inspect.getmodule(make_skeleton))
    assert "hashlib.sha256" in module_source
    assert "def stable_digest" in module_source
    assert "Python ``hash()``" in module_source


def test_width_direction_or_movement_changes_id() -> None:
    base = _make()
    wider = _make(width=7.0)
    one_way = _make(directionality="one_way")
    no_move = _make(with_movement=False)
    assert base.skeleton_id != wider.skeleton_id
    assert base.skeleton_id != one_way.skeleton_id
    assert base.skeleton_id != no_move.skeleton_id


def test_cross_process_id_matches() -> None:
    local = _make()
    repo = Path(__file__).resolve().parents[1]
    script = r"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"%s")))
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.road_skeleton import make_movement, make_node, make_segment, make_skeleton

site = SiteSpec(
    name="skeleton-site",
    boundary=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
    stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
    aisle_width=6.0,
    margin=0.0,
    entrances=[EntranceSpec(id="south", mode="shared", center=(8.0, 0.0), width=8.0, heading_degrees=90.0)],
    aisle_classes=[AisleClassSpec(id="wide-two-way-no-cross", width=6.0, capacity="two_vehicle", directionality="two_way")],
    fixed_aisle_class="wide-two-way-no-cross",
)
n0 = make_node("N0", "entrance_port", (8.0, 0.0), heading_degrees=90.0, source_id="south")
n1 = make_node("N1", "junction", (8.0, 20.0), heading_degrees=90.0)
n2 = make_node("N2", "terminal", (24.0, 20.0), heading_degrees=0.0)
spine = make_segment("S-SPINE", "parking_aisle", "N0", "N1", ((8.0, 0.0), (8.0, 20.0)), 6.0, "two_way", parking_sides=("left", "right"), source={"b": 1, "a": 2})
cross = make_segment("S-CROSS", "cross_aisle", "N1", "N2", ((8.0, 20.0), (24.0, 20.0)), 6.0, "two_way", parking_sides=("none",), source={"a": 2, "b": 1})
move = make_movement("M-LEFT", "S-SPINE", "S-CROSS", "N1", "left")
skel = make_skeleton(family="parallel_ladder", nodes=[n0, n1, n2], segments=[spine, cross], movements=[move], entrance_ids=("south",), source={"seed": 1, "note": "orig"}, site=site)
print(skel.skeleton_id)
""" % repo.as_posix()
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert completed.stdout.strip() == local.skeleton_id


def test_undeclared_intersection_does_not_allow_a_turn() -> None:
    n0 = make_node("A0", "terminal", (0.0, 10.0))
    n1 = make_node("A1", "terminal", (20.0, 10.0))
    n2 = make_node("B0", "terminal", (10.0, 0.0))
    n3 = make_node("B1", "terminal", (10.0, 20.0))
    horizontal = make_segment("H", "parking_aisle", "A0", "A1", ((0.0, 10.0), (20.0, 10.0)), 2.0, "two_way")
    vertical = make_segment("V", "cross_aisle", "B0", "B1", ((10.0, 0.0), (10.0, 20.0)), 2.0, "two_way")
    skeleton = make_skeleton(
        family="parallel_ladder",
        nodes=[n0, n1, n2, n3],
        segments=[horizontal, vertical],
        movements=(),
        entrance_ids=(),
        strict=False,
    )
    issues = validate_skeleton(skeleton)
    assert any(issue.code == CODE_UNDECLARED_INTERSECTION for issue in issues)
    assert turn_allowed(skeleton, "H", "V") is False
    assert allowed_movements(skeleton, "H", "V") == ()


def test_shared_node_without_movement_does_not_allow_turn() -> None:
    skeleton = _make(with_movement=False)
    assert turn_allowed(skeleton, "S-SPINE", "S-CROSS") is False
    assert skeleton.movements == ()


def test_declared_connection_without_contact_fail_closes() -> None:
    n0 = make_node("N0", "entrance_port", (8.0, 0.0), source_id="south")
    n1 = make_node("N1", "terminal", (8.0, 10.0))
    n2 = make_node("N2", "terminal", (30.0, 0.0))
    n3 = make_node("N3", "terminal", (30.0, 10.0))
    n_via = make_node("NV", "junction", (18.0, 5.0))
    left = make_segment("L", "parking_aisle", "N0", "N1", ((8.0, 0.0), (8.0, 10.0)), 4.0, "two_way")
    right = make_segment("R", "cross_aisle", "N2", "N3", ((30.0, 0.0), (30.0, 10.0)), 4.0, "two_way")
    movement = make_movement("M-FAKE", "L", "R", "NV", "left")
    with pytest.raises(SkeletonValidationError) as raised:
        make_skeleton(
            family="parallel_ladder",
            nodes=[n0, n1, n2, n3, n_via],
            segments=[left, right],
            movements=[movement],
            entrance_ids=("south",),
            site=_site(),
        )
    assert any(issue.code == CODE_DECLARED_CONNECTION_WITHOUT_CONTACT for issue in raised.value.issues)


def test_illegal_floats_empty_line_and_duplicate_ids_have_stable_codes() -> None:
    n0 = make_node("N0", "terminal", (0.0, 0.0))
    n1 = make_node("N1", "terminal", (10.0, 0.0))
    good = make_segment("S", "parking_aisle", "N0", "N1", ((0.0, 0.0), (10.0, 0.0)), 6.0, "two_way")
    empty = make_segment("E", "parking_aisle", "N0", "N1", ((0.0, 0.0),), 6.0, "two_way")
    coincident = make_segment("C", "parking_aisle", "N0", "N1", ((0.0, 0.0), (0.0, 0.0)), 6.0, "two_way")
    duplicate = make_skeleton(
        family="legacy",
        nodes=[n0, n0],
        segments=[good],
        strict=False,
    )
    empty_skel = make_skeleton(family="legacy", nodes=[n0, n1], segments=[empty], strict=False)
    coincident_skel = make_skeleton(family="legacy", nodes=[n0, n1], segments=[coincident], strict=False)

    dup_codes = {issue.code for issue in validate_skeleton(duplicate)}
    assert CODE_DUPLICATE_ID in dup_codes
    assert CODE_EMPTY_CENTERLINE in {issue.code for issue in validate_skeleton(empty_skel)}
    assert CODE_COINCIDENT_CENTERLINE in {issue.code for issue in validate_skeleton(coincident_skel)}

    with pytest.raises(ValueError):
        make_segment("W", "parking_aisle", "N0", "N1", ((0.0, 0.0), (10.0, 0.0)), float("nan"), "two_way")
    with pytest.raises(ValueError):
        make_node("NX", "terminal", (float("inf"), 0.0))

    non_positive = make_segment("Z", "parking_aisle", "N0", "N1", ((0.0, 0.0), (10.0, 0.0)), 0.0, "two_way")
    zero = make_skeleton(family="legacy", nodes=[n0, n1], segments=[non_positive], strict=False)
    assert CODE_NON_POSITIVE_WIDTH in {issue.code for issue in validate_skeleton(zero)}


def test_entrance_port_must_match_site() -> None:
    nodes, segments, movements = _straight_pair()
    nodes[0] = make_node("N0", "entrance_port", (8.0, 8.0), source_id="south")
    with pytest.raises(SkeletonValidationError) as raised:
        make_skeleton(
            family="parallel_ladder",
            nodes=nodes,
            segments=segments,
            movements=movements,
            entrance_ids=("south",),
            site=_site(),
        )
    assert any(issue.code == CODE_ENTRANCE_PORT_MISMATCH for issue in raised.value.issues)


def test_mutating_one_candidate_does_not_affect_another_or_site() -> None:
    site = _site()
    source = {"note": "one", "nested": {"k": 1}}
    first = _make(source=source)
    second = copy_skeleton(first)
    source["note"] = "mutated"
    source["nested"]["k"] = 99
    site.optimization["injected"] = True
    with pytest.raises(TypeError):
        first.source["note"] = "nope"  # type: ignore[index]
    assert first.source["note"] == "one"
    assert second.source["note"] == "one"
    assert first.source["nested"]["k"] == 1
    assert second.skeleton_id == first.skeleton_id
    assert "injected" not in _make().source
    copied_site_opt = dict(site.optimization)
    assert copied_site_opt.get("injected") is True
    assert first.source is not second.source


def test_generator_does_not_import_or_call_road_skeleton() -> None:
    import openparkcad.generator as generator

    source = inspect.getsource(generator)
    assert "road_skeleton" not in source
    layout = generate_layout(_site())
    assert layout.stall_count >= 0
    assert layout.generation_mode in {"phase1_main_aisle", "candidate_layout_promoted"}
