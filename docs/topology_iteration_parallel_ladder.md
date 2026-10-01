# Topology iteration: parallel-ladder family

Status: N1 frozen cases. This is not a claim that `parallel_ladder` is
implemented. Runtime behavior remains the current single-spine / multi-spine
templates until N4–N7 exit.

All four cases are **synthetic**. No licensed real CAD is in this repository
([real-site protocol](real_site_case_protocol.md)). Synthetic pass/fail must
not be reported as field effect, code compliance, or constructability.

Development set: N-T01, N-T03. Holdout set: N-T02, N-T04. Do not retune
development geometry using holdout outcomes and then claim generalization.

Predicates used by tests live in `tests/v0_5_parallel_ladder_support.py`.

## N-T01 — wide rectangle, both-end cross aisles (development, positive)

- Source type: synthetic (`examples/parallel_ladder_rect_site.json`)
- Current failure: the official layout is a single entrance-aligned spine,
  optionally with perpendicular branches. It does not produce three parallel
  parking aisles plus east-west cross aisles at both ends.
- Human reference: three north-south parking aisles (double-loaded 16 m bands
  fit in the 56 m width) and a real cross aisle at the entry end and the far
  end.
- Hard constraints: aisle width 6.0 m, stall 2.5 × 5.0 m perpendicular-90,
  passenger-car outer-front-wheel radius 5.5 m. Do not shrink the hard width.
- Expected output: a `parallel_ladder` skeleton with at least two distinct
  parking aisles and a real connecting cross aisle (N-T01 floor). The human
  reference remains three aisles + both ends; stall count is not a universal KPI.
- Resource budget: `max_skeletons` 16, refinement 20 s, N0 single-case timeout 180 s.
- Non-goals: free-form networks, one-way loops, arbitrary junctions.
- Exit: `rectangle_is_solved` is true for at least one fully checked candidate
  after N4–N7; current `rectangle_current_is_insufficient` stays true on the
  N0 generator.

## N-T02 — L-shape second wing (holdout, positive)

- Source type: synthetic (`examples/parallel_ladder_l_site.json`)
- Current failure: entrance is in the west/north wing; the single spine travels
  north and leaves the east wing (`x=36–90`, `y=0–28`) unused.
- Human reference: both wings receive circulation; a limited orthogonal
  connection joins them. N4 requires coverage of the second wing, not stalls.
- Hard constraints: same 6.0 m aisle and 5.0 m stall depth as N-T01.
- Expected output: at least one ladder candidate whose road polygons overlap
  the east wing by ≥ 8 m² (`uses_second_l_wing`).
- Resource budget: same as N-T01.
- Non-goals: jointly optimizing multiple entrances; filling the L with a maze.
- Exit: `l_shape_is_solved` on a retained candidate; current layout fails it.

## N-T03 — too narrow for two aisles (development, hard reject)

- Source type: synthetic (`tests/fixtures/v0_5/parallel_ladder_tight_reject.json`)
- Current failure / geometric fact: site width 10 m < two × 6.0 m hard aisles
  (12 m). A two-aisle ladder is a `no_geometric_corridor` problem.
- Human reference: refuse the second aisle. A single 6.0 m spine may still
  exist; that is not a ladder success.
- Hard constraints: published aisle polygons must keep the 6.0 m hard width.
  Shrinking width to invent a second aisle is forbidden.
- Expected output: no pseudo-valid two-parking-aisle skeleton; failure class
  `no_geometric_corridor`.
- Resource budget: generation may return quickly; do not spend the full
  refinement budget inventing illegal widths.
- Non-goals: treating zero stalls as the KPI; changing the stall family to
  dodge the width limit.
- Exit: N4 hard-reject tests pass without reducing `fail_under` or deleting
  checks.

## N-T04 — graph-contact T, vehicle cannot turn (holdout, hard reject)

- Source type: synthetic (`tests/fixtures/v0_5/parallel_ladder_turn_reject.json`)
- Current failure: current templates do not classify “polygons touch, envelope
  fails”. The frozen T is a north-south aisle at `x=8` meeting an east-west
  aisle at `y=40`, with `t-fillet-block` occupying the inner fillet.
- Human reference: contact is allowed as a cheap graph prefilter; the design
  vehicle (radius 5.5 m) cannot complete the T without hitting the block.
- Hard constraints: undeclared geometric intersection must not allow a turn;
  requested `road_traversal` must not report `status=passed`.
- Expected output: `road_traversal_failed` or `junction_movement_unsupported`;
  never a published valid T road.
- Resource budget: same N0 ceilings; incomplete/timeout is not a pass.
- Non-goals: opening `one_way_loop`; claiming graph reachability as a path
  proof.
- Exit: N-T09 holds after N5. N1 only freezes the geometry and the predicate.

## Shared non-goals and budget

- No site-independent “add N stalls” KPI.
- `allow_one_way_loop` stays false until N5 fully supports it.
- Performance ceilings frozen in `docs/verification/v0_5_n0_baseline.json`.
- Real vs synthetic sources are never mixed in reports.
