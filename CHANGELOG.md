# Changelog

All notable changes to OpenParkCAD are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and package versions
follow semantic versioning while the input/report contracts carry their own
version identifiers.

## [0.3.0] - Unreleased

The implementation is complete on the development branch and remains marked
unreleased until it is merged and tagged. Runtime reports, not the package
version alone, show whether a requested rule actually ran.

### Added

- Opt-in `optimization.road_network` (`enabled`, `families`, `max_skeletons`,
  `cross_aisle_policy`) generates a `parallel_ladder` RoadSkeleton family after
  the current official baseline. Default remains off. Unknown families fail
  closed. `allow_one_way_loop` stays unavailable. Reports may include
  `layout_search.road_network_search` version `road-network-search-1`.
  Synthetic-only; not a real-site or constructability claim.
- Examples `examples/parallel_ladder_rect_site.json` and
  `examples/parallel_ladder_l_site.json`.
- Optional `optimization.layout_search` (`mode=legacy|multi_spine`, `top_k`,
  `refinement_budget_seconds`) compares complete main-aisle templates after the
  legacy baseline. Default remains `legacy`. Promotion is still the only switch
  that can replace official DXF/SVG. Reports add `layout_search` version
  `layout-search-1`. Top-K and the current straight/offset/dogleg/multi-jog
  family are explicit limits, not a global layout optimum.
- Optional `optimization.selector_num_workers` for CP-SAT; omitted keeps the
  OR-Tools default. CI has a Python 3.12 `.[dev,optimizer]` job.
- Layout benchmark tools (`tools/benchmark_layouts.py`) and an explicit 20-case
  manifest (`benchmarks/layout_v0_4.json`, 22 explicit cases).
- Example `examples/multi_spine_comparison_site.json`.
- Multi-spine promotion preserves the winning selector decision and publishes
  consistent preview status, diagnostics, and preview-to-official aisle/stall
  mappings. Benchmarks record measured search stages and fix CP-SAT seed,
  worker count, and per-solve time limit for repeatable comparisons.
- Route usability (first slice): when `accessible_min` is positive, classified
  accessible stalls must geometrically reach a hard accessible route within
  `constraints.accessible_route_touch_tolerance` (default 1.5 m). When
  `emergency_access_required` is set, hard fire/access routes must reach an
  entrance gate within `constraints.emergency_route_touch_tolerance`
  (default 1.0 m). When `ev_min` is positive and hard charging posts are
  placed, classified EV stalls must reach a charger within
  `constraints.ev_charger_touch_tolerance` (default 2.0 m). Classification-only
  EV quotas without placed chargers stay count-only. Accessible-route pieces
  that serve classified stalls must form one contact network, and declared
  `accessible_routes.connects` destinations must be reachable from that
  network; unknown connect ids fail closed. Official and preview layouts drop
  classified accessible/EV stalls that miss those contacts; the shadow selector
  rejects unreachable classified modules and bonuses reachable ones
  (`weights.accessible_contact` / `weights.ev_contact`, default 100).
  Declared `min_width` is checked against `polyline_buffer` width;
  `max_slope` fail-closes because elevation is not modeled. Official/preview
  layouts can retarget same-family stalls on the contact band to classified
  accessible/EV types to meet quotas, dropping overlapping same-side neighbors
  when the classified stall is wider, or packing a contiguous contact run
  into two or more classified bays when the frontage fits. Remaining
  shortfall may be filled with perpendicular, parallel, or angled classified
  bays on empty contact-band pavement along an existing aisle; no new aisle
  is invented.
  Pedestrian-conflict operational proxy
  reports stall/aisle proximity and crossings of pedestrian or accessible
  routes; default risk weight is 0. Not ADA, apparatus, or electrical
  certification.
- Discrete candidate catalog for v0.4: official aisles are `base`, branch and
  connector skeletons are `variable`, greedy selector reports backend
  provenance. Optional `selector_backend=cpsat` (OR-Tools, `optimizer` extra)
  solves the same shadow catalog; missing/failed CP-SAT fail-closes to greedy.
  Default path does not import OR-Tools. Promoted official layouts rebuild
  aisle IDs from catalog `source_id` values and take branch/connector records
  from the selector, after the existing preview validation gates. Stall modules
  (`kind=stall_module`) are per-side strips on a parent aisle; greedy/CP-SAT
  may drop a strip, and preview/promoted stalls follow selected modules.
  When multiple stall types are declared, spine and branch sides get
  alternative family modules and the selector keeps at most one family per
  side. Same-side U, opposite-cross, and end-loop connectors also get
  perpendicular/parallel/angled alternatives. Default
  `stall_module_segment_stalls=4` splits a side into consecutive chunks so the
  selector can drop or retarget part of a strip; explicit `0` keeps the whole
  strip. Mixed-segment scoring uses optional `weights.stall_family` per
  family and an opt-in `weights.segment_family_mix` (or
  `prefer_uniform_segments`) so a mixed aisle-side can lose to a uniform
  family; default mix weight 0 keeps independent per-segment picks.
  Contract: `docs/v0_4_discrete_candidates.md`.
- Acute-angled reverse-in swept-path template (`reverse_in_angled_bicycle_v1`):
  one constant-radius reverse arc at the stall-to-aisle angle plus a straight
  reverse along the parallelogram axis. Obtuse approaches remain fail-closed
  when exact swept path is requested. Angled stalls without a swept-path
  request use a scaled-arc conservative reverse bound.
- Parallel reverse S-curve swept-path template
  (`parallel_reverse_s_curve_bicycle_v1`): two equal-radius opposite reverse
  arcs that restore the aisle heading. Parallel stalls without a swept-path
  request keep the analytic fit/reverse-length bound.
- T-end reverse-in swept-path template (`reverse_in_t_end_bicycle_v1`): a
  straight reverse along the bay axis from the convex court of the serving
  turnaround, parent aisle, and end bay. If that cannot be built, it falls
  back to the perpendicular-90 reverse-in on the same court. T-end without a
  swept-path request keeps the analytic reverse-in bound. When exact swept
  path is requested, main/branch aisle length reserves `swept_path_margin`
  beyond the end bay so the conservative envelope stays inside the site.
  Dogleg and multi-jog rear turnarounds also place these end bays, inset by
  the same reserve.
- Articulated design vehicles parse `configuration`, `hitch_offset`, and a
  nested `trailer` (length/width plus optional wheelbase/overhangs). Requested
  exact swept-path checks fail closed with
  `articulated_vehicle_template_not_supported` instead of running the rigid
  bicycle stall templates. Conservative analytic mode audits combination fit,
  steady-state trailer off-tracking versus aisle width, and a
  tractor-quarter-arc-plus-trailer reverse bound.
- Official connector-side stalls for active parallel and angled families on
  same-side U, opposite-cross, and end-loop connectors. T-end still records
  `connectors_not_supported_for_stall_family` and skips connector trials.
- Parallel stall generation on main and branch aisles, with a traffic-side
  rectangular access/turn proxy in addition to the optional exact S-curve.
- Example narrow strip site (`examples/parallel_strip_site.json`) that exercises
  parallel modules where dual-side 90-degree stalls do not fit.
- T-end stall generation as dead-end end-cap bays on main/branch turnarounds,
  optional `optimization.enable_t_end_caps` for other families, front-access
  maneuver proxy, and conservative vehicle analytic checks.
- Example t-end site (`examples/t_end_site.json`).
- Opposite-side short cross junctions for nearly aligned left/right branches
  (`optimization.enable_opposite_connectors`, selected only when score improves).
- Opposite-side outer end-loop connectors (C-shaped rails + end cross) via
  `optimization.enable_opposite_end_loops`.
- Optional main-aisle lateral-offset candidates with entrance throat reconnection
  (`main_aisle_lateral_offsets` / `enable_main_aisle_lateral_offsets`).
- Example multi-branch loop site (`examples/opposite_loop_site.json`).
- Example end-loop site with obstacle (`examples/end_loop_site.json`).
- Optional scoring bonus for connector loops via `optimization.prefer_loops` or
  `optimization.weights.connector_loop`.
- Optional obstacle-clearance preference via `prefer_obstacle_clearance` or
  `weights.obstacle_clearance` (mean layout-to-obstacle distance, capped).
- One-way fixed aisle classes (including narrow `single_vehicle` one-way) in the
  template generator, with per-aisle `directionality`, direction-aware traffic
  edges, and optional reverse-egress arcs
  (`constraints.circulation.one_way_allows_reverse_egress`, default true).
- Example one-way strip site (`examples/one_way_strip_site.json`).
- Minimal dual-entrance template: optional `A-EXIT` corridor from the main
  turnaround to a distinct far-end exit-capable entrance
  (`optimization.enable_dual_entrance`, default true).
- Example dual-entrance site (`examples/dual_entrance_site.json`); enables
  strict one-way without reverse egress when entry and exit gates are separate.
- Obstacle-aware layout: auto main-aisle lateral offset candidates when hard
  obstacles exist (up to ±2 aisle widths), plus branch selection clearance
  bonus (`auto_lateral_offsets_for_obstacles`,
  `auto_obstacle_clearance_for_branches`).
- Example obstacle offset site (`examples/obstacle_offset_site.json`).
- Main-aisle dogleg bypass around mid-site obstacles: centerline front spine,
  lateral jog (`A-JOG`), offset rear main (`A-MAIN-REAR`), and terminal
  turnaround (`generation_mode=phase1_main_aisle_dogleg`). Enabled via
  `optimization.enable_main_aisle_dogleg`, or auto when obstacles exist and
  `prefer_obstacle_clearance` is set; optional `dogleg_offsets` candidate list.
- Dogleg multi-spine branch attach: score-positive perpendicular branches on the
  centerline front spine (`A-MAIN`) and offset rear spine (`A-MAIN-REAR`) share
  one `max_branches` budget; connectors run per parent spine in its geometry
  frame.
- Dogleg dual-entrance: `A-EXIT` attaches from the rear turnaround using
  turnaround-relative lateral checks and optional L-elbow corridors when a
  straight exit path is blocked (`exit_lateral_budget` optional override).
- Passing-bay synthesis: side-pocket bays along main/dogleg spines via
  `enable_passing_bay_synthesis` (or auto when narrow two-way +
  `operational_min_passing_bays`); written as `role=passing_bay` aisles and
  `site_features` for Phase 5Q. Unlocks narrow two-way (`single_vehicle` +
  `two_way`) generation. Passing bays are non-circulation graph shoulders.
- Multi-jog main aisle (`generation_mode=phase1_main_aisle_multi_jog`): chain
  two or more lateral jogs around staggered obstacles when a single dogleg rear
  spine is blocked; `max_dogleg_jogs` caps chain length (default 3).
- Multi-jog multi-spine branches: all main segments share one `max_branches`
  budget via `_with_multi_spine_branches` (also used by single-dogleg front/rear);
  dual-entrance `A-EXIT` attaches from the multi-jog turnaround.
- Adaptive dogleg offsets: when enabled (default with obstacles + dogleg /
  `prefer_obstacle_clearance`), merge obstacle-envelope clearances into
  `dogleg_offsets` trials (`enable_adaptive_dogleg_offsets`).
- Role-layered DXF/SVG export: dedicated layers/colors for main, jog, branch,
  connector, turnaround, exit, and passing_bay aisles; aisle XDATA + labels.
- Examples catalog (`docs/examples_catalog.md`).
- Branch clip diagnostics on candidates: `clear_length`, `open_boundary_length`,
  `clip_amount`, `clipped_by_exclusion`, optional `prefer_side_hint`.
- Soft branch-side preference from clip diagnostics
  (`branch_clear_length_bonus`, `branch_clip_penalty`,
  `branch_clipped_side_penalty`) during greedy selection.
- Candidate network preview base layer includes jog/exit/passing_bay selected
  aisles alongside main/turnaround (branch/connector remain shadow-selected).
- Clearance score knobs: `obstacle_clearance_weight`, `obstacle_clearance_cap`
  (with existing `weights.obstacle_clearance` / `prefer_obstacle_clearance`).
- v0.3 release checklist (`docs/v0_3_release_checklist.md`).
- Example dogleg obstacle site (`examples/dogleg_obstacle_site.json`).
- Example dogleg dual-entrance site (`examples/dogleg_dual_entrance_site.json`).
- Example dogleg one-way dual-entrance site
  (`examples/dogleg_one_way_dual_entrance_site.json`).
- Example narrow two-way + passing bays (`examples/passing_bay_narrow_site.json`).
- Example multi-jog staggered obstacles (`examples/multi_jog_obstacle_site.json`).
- Example multi-jog dual entrance (`examples/multi_jog_dual_entrance_site.json`).
- Example adaptive dogleg site (`examples/adaptive_dogleg_site.json`).
- Example multi-jog one-way dual entrance
  (`examples/multi_jog_one_way_dual_entrance_site.json`).
- MIT licensing and package metadata.
- A machine-readable input schema and synthetic, representative
  real-site-shaped regression fixtures.
- A release contract separating advisory, project-policy, jurisdictional,
  proxy, and unsupported rule results.
- Audited turning-radius reference conversion, deterministic low-speed bicycle
  kinematics, and supported perpendicular-90, acute-angled reverse-in,
  parallel reverse S-curve, and T-end reverse-in swept-path templates.
- Scoped hard site exclusions, required-route definition checks, and explicit
  accessible/EV quota validation.
- A versioned `engineering_validation` decision that binds vehicle, site,
  authority, quota, official-object, and failure evidence to the exported
  layout.

### Changed

- Runtime field-support now reports dogleg, multi-jog, passing-bay synthesis,
  and opt-in narrow two-way as available/active instead of leaving those
  generation modes as future.
- Requested vehicle checks now fail closed when required geometry or a
  supported template is missing; reverse-distance-only policies expose their
  turning-radius prerequisite.
- Candidate previews and promoted official layouts are revalidated through the
  same vehicle/site gates.
- DXF XDATA, SVG data attributes, and JSON preserve canonical stall-type
  identity.

### Fixed

- Final empty layouts retain the best vehicle rejection evidence instead of
  replacing it with a misleading zero-check pass.
- Hard obstacle, reserved-area, feature, route, and quota failures now block
  transactional output publication.

### Release gate

- Vehicle turn/swept-path and reverse-distance checks must fail closed when
  explicitly requested.
- Active obstacle, reservation, route, feature, and quota constraints must
  validate the exact official layout.
- The report and exporters must preserve rule/object provenance and the
  existing transactional no-output-on-invalid behavior.
- Lint, branch coverage, package build, schema validation, installed-wheel
  smoke tests, and representative fixture invariants must pass.

### Boundary

0.3.0 validates only its documented templates and caller-declared project
policies. It does not certify statutory compliance, accessibility, fire access,
traffic capacity, construction readiness, or physical-world vehicle access.

## [0.2.0] - 2026-07-14

### Added

- Python 3.10/3.12 CI coverage for lint, branch-covered tests, package builds,
  and installed-wheel CLI execution.
- Detailed candidate preview, promotion, operational-quality, and report
  provenance retained through official output generation.

### Changed

- Candidate promotion rebuilds official IDs, revalidates geometry, maneuver,
  graph, and operational decisions, and compares the promoted score.
- Runtime dependencies are limited to geometry/CAD requirements; OR-Tools is an
  optional future optimizer extra.

### Fixed

- Final validity now rejects empty, maneuver-invalid, graph-invalid, or
  operationally hard-rejected layouts before export.
- Declared aisle and entrance relationships require geometric contact before
  becoming traffic-graph edges.
- DXF, SVG, and JSON outputs use transactional group-write/rollback behavior.
- CLI failures return non-zero, SVG text is XML-escaped, and excessive setbacks
  no longer silently disable themselves.

### Boundary

0.2.0 is a trustworthy template-planner closure, not a swept-path, statutory
compliance, traffic-simulation, or construction-design release.

[0.3.0]: https://github.com/helenananaa/OpenParkCAD/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/helenananaa/OpenParkCAD/releases/tag/v0.2.0
