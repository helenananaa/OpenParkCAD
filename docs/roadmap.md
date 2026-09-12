# Roadmap

The near-term objective is not to add more risk metrics. It is to make the
existing supported template generator predictable, fail-closed, testable, and
honest about what a “valid” result means.

## v0.2 closure baseline

The first reliability pass was pushed as the v0.2 template-planner baseline:

- final validity is checked before export and hard rejection is fail-closed;
- declared aisle and entrance graph links require plausible geometric contact;
- promoted candidates are rebuilt as official objects and the exact exported
  layout is revalidated and rescored;
- CLI errors, XML escaping, and transactional output-set writes are covered by
  end-to-end tests;
- runtime dependencies no longer include unused plotting/optimizer packages;
  and
- documentation plus Python 3.10/3.12 lint, coverage, build, and wheel-smoke CI
  define the engineering baseline.

That baseline is a **template planner**, not an automatic compliance or
construction-design system. Package licensing, Schema, version metadata, and
representative fixtures are completed in the v0.3 development line.

## v0.3: Vehicle and enforced site constraints

The current v0.3 implementation adds a narrow, fail-closed engineering layer:

- audited outer-front-wheel or rear-axle-center turning-radius input, explicit
  vehicle footprint geometry, and conservative analytic fit/reverse bounds;
- an optional perpendicular-90 reverse-in template with exact
  constant-curvature bicycle poses and conservative sampled swept envelopes;
- an optional acute-angled reverse-in template using the same bicycle integrator
  along the stall parallelogram axis;
- an optional parallel reverse S-curve of two equal-radius opposite reverse arcs;
- an optional T-end straight reverse from the turnaround/parent court, with
  perpendicular-90 reverse-in as fallback;
- fail-closed turning-radius, reverse-distance, centerline policy, drivable-area,
  boundary, and hard site-exclusion decisions for that template;
- hard obstacle/reserved/feature/pedestrian/fire/access-route scopes plus
  accessible/EV minimum quotas on the final layout;
- separate advisory/project-policy/jurisdictional authority and execution
  priority, with source/effective-date metadata required for jurisdictional
  declarations; and
- MIT licensing, a packaged JSON Schema, and synthetic representative
  real-site-shaped regression inputs.

Remaining validation/product work is intentionally not hidden inside the v0.3
claim:

- permissioned/anonymized survey cases with recorded human CAD comparisons and
  tolerances;
- **Done in this tree:** acute-angled reverse-in uses the same one-arc-plus-straight
  bicycle template as perpendicular-90; family `angled` is exact when
  `require_swept_path_check` is set and conservative-analytic otherwise.
- **Done in this tree:** parallel reverse parking uses a two-arc S-curve bicycle
  template (`parallel_reverse_s_curve_bicycle_v1`) when swept path is requested.
- **Done in this tree:** T-end end-bays reverse straight along the bay axis
  from the convex court of the turnaround, parent aisle, and bay
  (`reverse_in_t_end_bicycle_v1`), falling back to perpendicular-90 reverse-in
  on that court when a straight reverse cannot be built.
- **Done in this tree:** articulated vehicles parse `configuration` /
  `hitch_offset` / `trailer`; exact swept path fails closed; conservative
  analytic uses combination fit, trailer off-tracking, and a
  tractor-arc-plus-trailer reverse bound.
- remaining exact articulated/emergency-vehicle path templates or general
  path search for multi-point maneuvers;
- **Done in this tree:** official connector stalls follow the active parallel or
  angled family (same-side U, opposite-cross, and end-loop), not only 90-degree
  bays; T-end still skip connectors fail-closed.
- **Done in this tree:** accessible stall-to-route geometric reachability when
  `accessible_min` is positive; fire/access route-to-entrance contact when
  `emergency_access_required` is set; EV stall-to-charger contact when
  `ev_min` is positive and charging posts are placed; accessible-route
  piece continuity and `connects` destination contact; official/preview
  contact filters and selector bonuses for reachable classified modules;
  polyline `min_width` checks and fail-closed `max_slope` (no elevation);
  official retarget of same-family stalls on the contact band to meet
  accessible/EV quotas, including same-side neighbor drops, contiguous
  contact-band strip packing, and perpendicular/parallel/angled empty-pavement
  fill on an existing aisle;
  pedestrian-conflict operational proxy (default risk 0).
  ADA graphs and electrical/equipment design remain later.
- remaining statutory route profiles and charger circuit/layout certification; and
- sourced regional rule implementations, review/sign-off, and supported release
  operations.

The release claim and fail-closed behavior are defined in the
[v0.3 vehicle and enforced-constraint contract](v0.3_vehicle_and_constraints.md).
Representative fixtures are useful regression inputs, but are not field
validation or real customer sites.

## v0.4: Global candidate optimization

E0–E9 of the
[benchmark and multi-spine execution plan](v0_4_multi_spine_execution_plan.md)
are implemented in this tree: a 20-case comparison corpus, optimizer CI,
isolated per-spine evaluation, Top-K search, and official-layout verification.
Section 12 (road-level turning and CAD import) remains later work. Default
solve is still legacy/greedy; multi-spine is opt-in and Top-K limited.

Replace heuristic-only selection without losing the current explainable
baseline. The first slice is a catalog, not a new generator:

- **Done in this tree:** official aisles vs branch/connector skeletons are
  classified (`base` / `variable` / `spine_attempt` / `derived`); greedy is
  the default selector; optional `selector_backend=cpsat` runs OR-Tools on
  the same variables with seed/time-limit provenance and fail-closes to greedy
  when OR-Tools is missing; opt-in promotion rebuilds official aisle IDs from
  catalog `source_id` and records selector-chosen branches/connectors after
  the existing preview gates. Contract:
  [v0_4_discrete_candidates.md](v0_4_discrete_candidates.md).
- **Done in this tree:** per-side stall modules are discrete variables that
  depend on the parent aisle; preview/promotion stalls follow the selected
  modules; multiple stall types compete per `(aisle, side)` on spines,
  branches, and all current connector patterns; default
  `stall_module_segment_stalls=4` (explicit `0` keeps a whole strip).
- **Done in this tree:** mixed-segment scoring: optional per-family stall
  weights and an opt-in mix penalty (`weights.segment_family_mix` /
  `prefer_uniform_segments`); default mix weight 0 keeps independent
  per-segment family picks. Not a real-site-tuned objective.
- do not change the default backend until a real-site corpus exists.

The `optimizer` installation extra is reserved for the CP-SAT backend; the
current runtime does not import OR-Tools.

## Later product integration

The [next five stages execution plan](next_stages_execution_plan.md) expands
the remaining work into road-level vehicle traversal, CAD import, interactive
viewing and constrained regeneration, case-driven layout extensions, and
scoped engineering delivery. It specifies dependencies, proposed contracts,
implementation locations, acceptance cases, and rollback. These are planned
capabilities; start with B0 and R1–R4. Real-site collection and performance
measurement continue alongside implementation.

Only after the earlier release gates are met:

- multiple coordinated entrances/exits, richer one-way/narrow aisle strategies
  beyond synthesized passing bays, denser multi-jog / maze-style obstacle
  routing, arbitrary junctions, and general loops;
- DXF/site-survey import and coordinate-system handling;
- interactive editing with constraint-aware regeneration;
- versioned regional rule profiles with traceable sources and effective dates;
  and
- review/sign-off workflows, audit trails, packaging, and supported releases.

## Prioritization rule

For every new feature, prefer this order:

1. define the enforceable invariant and failure behavior;
2. add a minimal fixture that fails without the feature;
3. implement and revalidate the exact exported layout;
4. expose diagnostics and provenance; and
5. only then expand candidate generation or scoring.

This keeps “more layouts” from outrunning “layouts we can trust within the
documented boundary.”
