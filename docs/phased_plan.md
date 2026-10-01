# OpenParkCAD Phased Plan

This document turns the algorithm discussion into a step-by-step project plan.
It is intentionally conservative: each phase should produce something that can
be inspected, tested, and explained before the next layer is added.

For the next development iteration, use the
[next five stages execution plan](next_stages_execution_plan.md), starting with
road-level vehicle traversal. It lists planned implementation steps, contracts,
commands, and acceptance criteria. E0–E9 of the
[v0.4 multi-spine plan](v0_4_multi_spine_execution_plan.md) are implemented;
the historical implementation slices below provide context.

## Guiding Rule

Do not optimize parking count before the layout is provably usable.

The project should advance in this order:

```text
describe the site
  -> describe entrances and rules
  -> describe aisles and stalls
  -> validate connectivity
  -> validate vehicle maneuvers
  -> optimize layout quality
```

## Phase 0: Problem Model and Input Format

Phase 0 is not about generating the best layout. It is about making the problem
expressible without ambiguity.

### Goal

Define the first stable vocabulary and JSON shape for:

- site geometry,
- entrances and exits,
- obstacles,
- standards metadata,
- fixed site features,
- pedestrian and emergency reservations,
- vehicle assumptions,
- aisle classes,
- stall classes,
- design rules,
- expected outputs and diagnostics.

### Why This Comes First

The generator cannot be trusted if the input cannot say things like:

- where cars enter,
- whether an aisle is one-way or two-way,
- whether a narrow aisle may be used in both directions,
- what vehicle turning radius must be supported,
- what kind of stall is being counted,
- whether a stall can be crossed when empty,
- whether a stall has blocked sides or fixed equipment,
- which pedestrian or fire areas cannot be parked on,
- which constraints were skipped.

Phase 0 makes these concepts explicit before the algorithm starts using them.

### Phase 0 Deliverables

1. `docs/phased_plan.md`
   - Defines project phases and acceptance criteria.

2. `docs/input_model.md`
   - Defines the proposed JSON schema in plain language.
   - Shows one minimal example and one richer example.
   - Marks fields as required, optional, or future.
   - Discusses whether aisle width/classes are fixed user inputs or solver
     choices.
   - Separates Phase 1 active fields from future-facing fields.

3. `examples/phase0_site.json`
   - A sample site that includes a boundary, obstacle, entrance, vehicle, and
     basic rules.

4. Parser-level model update
   - The Python data model can read the Phase 0 JSON.
   - The program does not need to use every field for layout generation yet.

5. Diagnostic preview
   - The preview/export can draw the boundary, obstacles, entrances, fixed site
     features, pedestrian routes, and fire lanes.
   - It may still use the current simple layout generator.

### Phase 0 Acceptance Criteria

Phase 0 is complete when:

- a site JSON can represent at least one entrance,
- standards metadata is represented without claiming legal compliance,
- fixed features can be represented separately from obstacles,
- pedestrian/fire reservations have a place in the model,
- vehicle dimensions and turning radius are represented,
- aisle type names are represented,
- stall type names are represented,
- stall pass-through and blocked-side concepts are represented,
- current unsupported fields are preserved or rejected with a clear error,
- diagnostic layers show parsed geometry even when it is not enforced yet,
- the report distinguishes `active`, `drawn_not_enforced`,
  `parsed_not_enforced`, and `future` fields,
- documentation says which fields are active and which are future-facing,
- a user can look at the JSON and understand the design problem being asked.

### Phase 0 Non-Goals

Do not implement these in Phase 0:

- real aisle network graph solving,
- real turning-radius swept path simulation,
- narrow two-way deadlock detection,
- OR-Tools optimization,
- automatic T-shaped stall generation,
- full traffic simulation,
- local legal compliance checking.

These are important, but Phase 0 only prepares the vocabulary.

## Phase 1: Conservative Layout Kernel

### Goal

Generate only layouts that are simple and conservative enough to explain.

Initial allowed design:

- one or more entrances exist in the input,
- wide two-way aisles only,
- standard 90-degree stalls only,
- every counted stall has an adjacent clear aisle strip,
- all generated geometry stays inside usable land.

### Acceptance Criteria

- every stall has an associated aisle,
- the first generated aisle starts from an entry-capable entrance,
- every aisle footprint is inside the usable area,
- every stall footprint is inside the usable area,
- obstacles are respected,
- the report explains the selected angle and stall count,
- unsupported aisle/stall types are ignored or reported clearly.

### Current Implementation Status

Phase 1 is now implemented as a conservative kernel, not as a global optimizer.
The generator records enough metadata for the report to explain:

- which entrance feeds the main aisle,
- which aisle each generated stall uses,
- whether an aisle is the main aisle, a branch, or a turnaround pad,
- which parent aisle a branch or turnaround attaches to,
- which Phase 1 input choices are unsupported and why.

The current report includes `aisles`, `stalls`, `unsupported_phase1_inputs`,
`input_diagnostics.stall_access`, and `input_diagnostics.aisle_connectivity`.

This closes the Phase 1 acceptance criteria for the intentionally narrow
generation pattern below. Full graph reachability remains Phase 2 work.

### First Implementation Slice

The first Phase 1 implementation is intentionally narrow:

```text
entrance -> one straight wide two-way main aisle -> end turnaround -> stalls on both sides
```

Rules:

- use an entrance whose mode allows entering,
- require the entrance width to fit the selected wide two-way aisle,
- try a small set of headings around the entrance heading,
- try a small set of parallel offsets inside the entrance width,
- extend one straight main aisle along the selected heading,
- stop the aisle before it leaves the usable area or hits an obstacle,
- reserve a simple turnaround pad at the far end,
- try one perpendicular branch from the main aisle,
- auto-sample branch start positions unless explicit positions are provided,
- require the branch to reserve its own turnaround,
- leave one aisle-width throat before placing the first stall,
- keep stalls out of the entrance throat and turnaround zone,
- attach standard stalls only to the main aisle,
- report `generation_mode = phase1_main_aisle`.
- report the selected heading and heading delta.
- report the selected entrance offset.
- report branch candidate acceptance or rejection reasons.
- select among candidates using an explainable score breakdown rather than raw
  stall count only.

This proves entrance-to-aisle connection without pretending that a full aisle
graph, loops, intersections, or turning maneuvers exist yet.

### Non-Goals

- do not allow narrow two-way aisles,
- do not allow long dead-end branches,
- do not support mixed stall types yet,
- do not claim global optimality.

## Phase 2: Aisle Network Graph

### Goal

Turn aisles into a graph that can be checked.

Core graph concepts:

- entrance node,
- aisle endpoint node,
- intersection node,
- directed edge for one-way aisles,
- bidirectional edge for two-way aisles,
- stall-to-aisle access edge.

### Acceptance Criteria

- every generated aisle is connected to an entrance,
- every stall can be reached from an entrance,
- every stall has a route back to an exit,
- isolated aisle fragments are invalid,
- dead-end branches are detected and reported.

### Phase 2A: Graph Validation From Existing Layouts

The first Phase 2 slice does not generate new road networks. It converts the
current Phase 1 result into a traffic graph and validates it.

Implemented graph objects:

- `TrafficNode` for entrances and generated aisle pieces,
- `TrafficEdge` for entrance-to-aisle and parent-aisle connections,
- `StallAccess` for stall-to-aisle access,
- `TrafficGraph` as the container,
- `traffic_graph_report()` for JSON report output.

Implemented checks:

- generated aisles are reachable from an entry-capable entrance,
- generated stalls reference existing aisles,
- generated stalls are reachable from an entry-capable entrance,
- generated stalls have a path back to an exit-capable entrance,
- isolated aisle fragments are reported invalid,
- dead ends with turnaround pads are reported as allowed.

The JSON report includes `traffic_graph.nodes`, `traffic_graph.edges`,
`traffic_graph.stall_access`, and `traffic_graph.validation`.

### Phase 2B: Use The Graph To Reject Candidates

The generator now calls the graph validator before accepting a candidate into
the score comparison:

```text
candidate layout
  -> build traffic graph
  -> validate reachability and exit path
  -> reject invalid candidate
  -> score valid candidate
```

Attempt diagnostics include `graph_valid` and `graph_errors`, so an invalid
candidate is rejected with an explanation rather than disappearing silently.

This creates the foundation for multiple branches, loops, intersections, and
eventually one-way aisle networks.

### Phase 2C: Limited Multi-Branch Candidates

The generator can now add more than one perpendicular branch while still using
the Phase 2 graph as a hard validity gate.

Current rules:

- `optimization.max_branches` limits how many branches may be selected.
- The default maximum is `2`.
- Branch start positions still come from `optimization.branch_start_positions`
  or automatic sampling with `optimization.branch_start_step`.
- Each added branch receives a stable id such as `A-BRANCH-001`.
- Every branch has its own turnaround pad.
- A new branch may overlap the main aisle at its junction.
- A new branch is rejected if it overlaps an existing branch, turnaround, or
  branch-served stall.
- Every added branch must pass traffic graph validation.
- Every added branch must improve the score before it is kept.

The report preserves the old single-branch summary fields for compatibility and
adds `selected_branches` for the full selected branch list. Candidate diagnostics
may include:

- `branch_overlaps_existing_layout`,
- `branch_invalid_traffic_graph`,
- `branch_does_not_improve_score`,
- `branch_improves_stall_count`.

### Phase 2D: Same-Side Branch Connectors

The generator can now try a limited connector aisle between adjacent same-side
branches. This creates the first simple loop-like circulation pattern while
staying within the conservative wide-two-way-only model.

Current rules:

- `optimization.enable_connectors` enables connector trials. It defaults to
  `true`.
- `optimization.branch_sides` can restrict branch generation to `left`,
  `right`, or both.
- A connector is tried only between adjacent selected branches on the same side
  of the main aisle.
- The connector is placed near the branch ends.
- The connector may overlap its two endpoint branches and their turnaround pads.
- The connector is rejected if it overlaps other generated aisles.
- The connector removes the two endpoint branch turnaround pads because those
  branches are no longer dead ends.
- Stalls overlapped by the connector are removed from the candidate.
- The candidate must pass traffic graph validation.
- The candidate must improve the score before it is kept.

The report adds `selected_connectors`. Connector candidate diagnostics may
include:

- `connector_geometry_not_possible`,
- `connector_geometry_outside_usable_area`,
- `connector_overlaps_existing_layout`,
- `connector_invalid_traffic_graph`,
- `connector_does_not_improve_score`,
- `connector_improves_score`.

### Phase 2E: Connector-Side Stall Generation

Accepted connector aisles can now serve their own conservative 90-degree stall
candidates. This is still not a full module optimizer, but it prevents a
U-shaped connector from becoming pure circulation space when its sides can
safely hold parking.

Current rules:

- Connector-side stalls are generated only after the connector aisle itself is
  accepted as a geometric candidate.
- `optimization.connector_throat_length` controls the clear space kept near
  each connector-to-branch junction. It defaults to one aisle width.
- If `optimization.connector_allow_l_shape_end_stalls` is enabled, the generator
  may also try connector end stalls inside that throat and rely on the
  maneuver validator's one-sided L-shaped turning proxy.
- The generator tries both connector sides, marked as `outer` and `inner`.
- A connector-side stall must fit inside the usable site.
- A connector-side stall must avoid the active aisle network and the connector
  aisle itself. When a connector wins, endpoint branch aisles are trimmed to the
  connector boundary before connector stalls are generated.
- Connector stalls can replace older endpoint branch stalls if the resulting
  candidate still improves the score.
- A connector-side stall is counted only if it is served by the connector aisle.
- The final candidate must still pass traffic graph validation.
- The final candidate must improve the score before it is kept.

The report keeps connector details in `selected_connectors` and now includes
`added_stalls` alongside `removed_stalls` and `removed_turnarounds`.

## Phase 3: Maneuver Validity

### Goal

Require that the design vehicle can actually enter and leave each stall.

Start with conservative approximations:

- access envelope in front of the stall,
- minimum aisle width by stall type,
- minimum turn radius clearance,
- no obstacle intersection with maneuver envelope,
- maximum reverse distance rule.

### Acceptance Criteria

- perpendicular stalls use one maneuver rule,
- angled stalls use a different maneuver rule,
- parallel stalls are modeled but may remain disabled,
- T-end stalls are modeled but may remain disabled,
- invalid stalls are rejected with a reason.

### Phase 3A: Conservative Stall Access Envelope

The first maneuver slice is implemented as a conservative geometric access
check, not as full swept-path simulation.

Current rules:

- Each stall must reference a serving aisle.
- The validator finds the stall edge that faces its serving aisle.
- A rectangular access envelope is projected from that edge into the serving
  aisle.
- `optimization.maneuver_access_depth` controls the envelope depth. It defaults
  to one aisle width.
- `optimization.maneuver_access_coverage_ratio` controls how much of the
  envelope must be covered. It defaults to `0.95`.
- The envelope must be covered by generated drivable aisle geometry.
- The envelope must stay inside usable site area, so boundaries and obstacles
  can invalidate it.
- Invalid stalls are filtered before traffic graph validation and scoring.

The report includes `maneuver_validation`, with checked stall count, invalid
stall reasons, filtered stall ids, and access envelope coverage ratios.

Phase 3A deliberately does not check steering arcs, swept paths, or turning
radius. Those remain the next maneuver layer.

### Phase 3B: Conservative Turning Sweep Proxy

Phase 3B adds a first approximation of low-speed turning clearance without
claiming to simulate a vehicle path.

Current rules:

- The Phase 3A access envelope remains the hard front-clearance check.
- A second envelope expands the stall-front access rectangle along the aisle
  direction on both sides of the stall.
- `optimization.maneuver_turn_buffer_length` controls this side expansion. By
  default it uses a conservative value based on stall width, aisle width, and
  vehicle swept-path margin when vehicle data is available.
- `optimization.maneuver_turn_coverage_ratio` controls how much of the expanded
  envelope must be covered. It defaults to the access-envelope coverage ratio.
- The expanded envelope must be covered by drivable aisle geometry.
- The expanded envelope must stay inside usable site area, so side obstacles,
  end walls, and boundary clips can invalidate a stall even when its immediate
  front access is clear.
- For 90-degree perpendicular stalls, `optimization.maneuver_l_shape_fallback`
  enables a one-sided L-shaped fallback. If the full symmetric turning proxy
  fails, the validator tries the front access rectangle plus only the start or
  end side of the turn buffer.
- Invalid stalls are filtered before traffic graph validation and scoring.

The report includes `turn_buffer_length`,
`minimum_turn_coverage_ratio`, and per-stall turn coverage ratios in
`maneuver_validation`.

Phase 3B is still a proxy. Full steering arcs, swept paths, and exact minimum
turning radius checks remain future work.

### Phase 3C-1: Maneuver Rule Dispatch

The maneuver validator now routes each stall through an explicit maneuver rule
instead of assuming every future stall type will reuse the same geometry.

Current rules:

- `perpendicular_90_proxy` is the primary active rule.
- The active rule uses the Phase 3A access envelope and Phase 3B turning-sweep
  proxy.
- `perpendicular_90_l_shape_proxy` is reported when a 90-degree stall passes
  through the one-sided fallback instead of the full symmetric turning proxy.
- `angled`, `parallel`, `t_end`, and non-90 perpendicular rules are recognized
  as future maneuver rules.
- Unsupported future rules return explicit invalid reasons such as
  `angled_maneuver_rule_not_implemented`,
  `parallel_maneuver_rule_not_implemented`, and
  `t_end_maneuver_rule_not_implemented`.
- The report includes `rule_counts`, `rule_support`, and per-stall `rule_id`
  / `rule_status` in `maneuver_validation`.

This keeps current 90-degree behavior stable while preparing the validator for
angled, parallel, and T-end parking modules.

### Phase 3C-2: Angled Stall Maneuver Proxy

The maneuver validator now has an active angled-stall rule. This was added
before angled generation, so a layout containing angled stall geometry could be
checked by the maneuver layer even before the generator started placing it.

Current rules:

- `angled_proxy` is active for `parking.active_stall.family = "angled"`.
- The rule finds the angled stall edge facing its serving aisle.
- It projects a conservative access envelope from that edge into the aisle.
- It also applies the Phase 3B turning-sweep proxy with angled-specific
  defaults.
- `optimization.maneuver_angled_access_depth` can override angled access depth.
- `optimization.maneuver_angled_turn_buffer_length` can override angled turning
  buffer length.
- `optimization.maneuver_angled_access_coverage_ratio` and
  `optimization.maneuver_angled_turn_coverage_ratio` can override the coverage
  thresholds.
- The report identifies angled checks with `rule_id = angled_proxy`.

Parallel and T-end maneuver rules remain future work.

### Phase 3D-1: Main-Aisle Angled Stall Generation

The generator can now place angled stalls along the straight main aisle. This
is the first generated non-90-degree parking module, but it is intentionally
limited to keep the road-network logic stable.

Current rules:

- `parking.active_stall.family = "angled"` is supported only on the main aisle.
- The selected angled module uses the first allowed angle between 0 and 90
  degrees.
- Angled stall geometry is generated as a conservative parallelogram module.
- Angled stalls are served by `A-MAIN` and then checked by the `angled_proxy`
  maneuver rule.
- Branches and connector-side stalls remain disabled for non-perpendicular
  active stall families.
- The report records a branch candidate diagnostic reason
  `branches_not_supported_for_stall_family` when branch generation is skipped
  for angled stalls.

This does not yet support angled stalls on branches, connector aisles, mixed
angled/perpendicular layouts, or parallel/T-end generation.

### Phase 3D-2: Branch Angled Stall Generation

Angled stall generation now extends from the main aisle to perpendicular branch
aisles.

Current rules:

- Branch aisle geometry is still the same conservative wide two-way branch.
- Angled branch stalls use the same selected angled module as main-aisle angled
  stalls.
- Angled branch stalls are generated on both sides of the branch aisle where
  they fit.
- Angled branch stalls are checked by the active `angled_proxy` maneuver rule.
- Same-side, opposite-cross, and end-loop connectors place official stalls for
  perpendicular, parallel, and angled active families.
- The report records `connectors_not_supported_for_stall_family` when connector
  trials are skipped for unsupported families such as T-end.

### Phase 3E: Stall Type Candidate Comparison

The solver can now compare multiple enabled stall types from the input JSON
instead of requiring the user to pick exactly one active type up front.

Current rules:

- The parser preserves every enabled item in `parking.stall_types`.
- The first enabled stall type remains the default active stall for backward
  compatibility.
- `generate_layout(site)` tries each enabled stall type by cloning the site with
  that stall as the active type.
- Each stall-type candidate runs through the existing geometry generator,
  maneuver validation, traffic graph validation, and scoring pipeline.
- The selected layout is the graph-valid candidate with the highest score. If
  no graph-valid candidate exists, the highest-scoring candidate is still
  returned with its validation errors.
- The report includes `selected_stall_type_id` and `stall_type_attempts`.
- `stall_type_attempts` records each candidate's family, allowed angles, stall
  count, score, graph validity, maneuver validity, and unsupported inputs.

This is still a small enumerative comparison, not a global mixed-module
optimizer. Phase 3F-1 extends it to compare main/branch assignments.

### Phase 3F-1: Stall Type Assignment by Aisle Role

The solver can now compare enabled stall types separately for main aisles and
branch/connector aisles.

Current rules:

- The solver enumerates `main_stall_type x branch_stall_type` for enabled stall
  candidates.
- Main-aisle stalls use the selected main stall type.
- Branch-side and connector-side stalls use the selected branch stall type.
- Every generated stall records `stall_type_id`, so maneuver validation can
  dispatch the correct rule per stall.
- Mixed perpendicular/angled layouts are allowed when both candidate types are
  enabled and the geometry, maneuver, traffic graph, and score pipeline accepts
  the assignment.
- The selected layout is still the graph-valid assignment with the highest
  score, with highest-scoring invalid fallback if every assignment fails graph
  validation.
- The report includes `selected_stall_assignment` and
  `stall_assignment_attempts`.
- `stall_assignment_attempts` records the main, branch, and connector stall
  type ids, stall type counts, stall count, score, graph validity, maneuver
  validity, and unsupported inputs.

This is not yet per-individual-stall optimization. It only chooses by aisle
role, so a single branch still uses one stall module along its sides.

## Phase 4: Candidate Generation and Optimization

### Goal

Stop relying on a greedy row scan. Generate candidates first, then choose the
best compatible set.

Candidate objects:

- aisle skeletons,
- stall modules,
- connector aisles,
- turnaround modules,
- passing bay modules.

Optimization should consider:

- stall count,
- aisle area,
- circulation quality,
- conflict points,
- reverse movement,
- maneuver difficulty.

### Acceptance Criteria

- conflicts are represented explicitly,
- OR-Tools or another solver selects compatible objects,
- the objective is documented,
- the report shows why the selected layout won.

### Phase 4A-1: Candidate Object Snapshot

The solver now emits a first candidate-object layer without changing layout
selection behavior.

Current rules:

- `CandidateObject` is the shared data shape for future optimization objects.
- Selected aisles and stalls are represented as selected candidate objects with
  geometry, parent ids, and score features.
- Main-aisle attempts are represented as evaluated aisle-skeleton candidates.
- Branch and connector diagnostics are represented as selected, rejected, or
  invalid aisle-skeleton candidates.
- The JSON report includes `candidate_snapshot` with version, object count,
  status counts, and serialized candidate objects.

This is deliberately a snapshot layer, not yet an optimizer. Existing greedy
selection still decides the final layout.

### Phase 4A-2: Candidate Conflict Matrix

The candidate snapshot now includes a first explicit geometry conflict layer.

Current rules:

- Branch and connector attempt diagnostics carry geometry when a concrete
  candidate polygon was constructed.
- Candidate objects with geometry are checked pairwise for area overlap.
- Overlapping objects record each other in `conflict_ids`.
- The JSON report includes `candidate_snapshot.conflict_count` and
  `candidate_snapshot.conflict_matrix`.
- Conflict records include left object id, right object id, conflict type, and
  overlap area.
- Intentional aisle-to-aisle parent/connector overlaps are ignored, so a branch
  joining its parent main aisle is not treated as a conflict.

This is still diagnostic. The next optimizer phase can use the matrix as an
input, but selection is not yet solved from it.

### Phase 4B-1: Shadow Candidate Selector

The solver now runs a first report-only selector over the candidate-object
layer.

Current rules:

- The selector reads candidate objects and their `conflict_ids`.
- Only branch and connector `aisle_skeleton` candidates with geometry are
  eligible in this first pass.
- Candidates are scored by a simple shadow score based on stall delta,
  connector turnaround benefit, and aisle geometry area.
- Candidates with non-positive shadow score are rejected.
- Candidates that conflict with an already selected candidate are rejected with
  `conflicts_with_selected_candidate`.
- The selected shadow set is guaranteed to contain no pair listed in the
  conflict matrix.
- The JSON report includes `candidate_snapshot.selection`.

This remains `shadow_only`: it proves the candidate layer can drive a decision,
but it does not yet replace the greedy layout generator's final result.

### Phase 4B-2A: Candidate Network Preview Report

The solver now turns the shadow selector result into a report-only road-network
preview.

Current rules:

- `candidate_network_preview` starts with the current selected main aisle and
  turnaround aisle.
- Shadow-selected branch and connector skeleton candidates are appended as
  preview aisles.
- Preview aisles include source candidate id, source geometry, role, parent ids,
  area, score features, and compact metadata.
- The preview reports whether the chosen preview aisles have internal conflicts
  according to candidate `conflict_ids`.
- The JSON report includes top-level `candidate_network_preview`.
- Input diagnostics include the preview version, preview-only status, aisle
  count, shadow aisle count, and no-internal-conflict flag.

This is still not the final generated layout. Stalls, DXF, and SVG continue to
come from the existing generator until the preview network is promoted in a
later Phase 4B step.

### Phase 4B-2B: SVG Candidate Network Preview Layer

The SVG preview now draws the candidate network preview as a debug overlay.

Current rules:

- The SVG bounds include candidate preview aisle geometry.
- Preview aisles are drawn in a `<g id="candidate-network-preview">` group.
- Preview aisles use translucent dashed polygons so they can be compared with
  the current generated layout.
- Preview aisle labels use their preview ids, such as `PN-AISLE-001`.
- The layer is diagnostic only; DXF output and generated stalls still come from
  the current selected layout.

### Phase 4B-3: Candidate Network Preview Validation

The candidate network preview is now treated as a temporary layout object for
report-level validation.

Current rules:

- Preview aisles are converted into a temporary `LayoutResult` without stalls.
- The preview runs an internal-conflict check from candidate `conflict_ids`.
- The preview runs geometry containment against the usable site area.
- The preview runs the existing traffic graph validation on preview aisle ids.
- Validation output is stored in `candidate_network_preview.validation`.
- Input diagnostics expose the preview validation status and errors.

This still does not replace the official generated layout. It makes the preview
network auditable before a later phase promotes it into layout generation.

### Phase 4C-1: Candidate Layout Preview With Stalls

The solver now builds a complete preview layout from the candidate network.

Current rules:

- `candidate_layout_preview` reuses preview aisles from
  `candidate_network_preview`.
- Current main-aisle stalls are copied into the preview layout and remapped to
  preview aisle ids.
- Shadow-selected branch and connector candidates contribute their generated
  preview stalls through candidate metadata.
- Preview stalls include geometry, source id, serving preview aisle id, aisle
  side, stall type id, area, and source type.
- The preview layout runs geometry containment, stall-to-aisle association,
  maneuver validation, and traffic graph validation.
- The JSON report includes top-level `candidate_layout_preview`.

This is still preview-only. The official DXF/SVG layout and selected score are
not replaced until a later phase promotes a valid preview layout.

### Phase 4C-2A: Candidate Layout Preview Scoring

The candidate layout preview now reports whether it is actually better than the
current official layout before any promotion happens.

Current rules:

- `candidate_layout_preview.score` uses the same objective weights as the
  official layout score.
- Preview metrics include stall count, aisle area, heading delta, entrance
  offset, branch count, and a conservative dead-end length estimate.
- `candidate_layout_preview.comparison` reports the current layout score, the
  preview score, stall delta, score delta, and validation status.
- `promotion_eligible` is true only when the preview validates and its score is
  not lower than the current official layout, and when promotion-only blockers
  such as unresolved dead ends are absent.
- This is still report-only. The official DXF/SVG layout is not replaced yet.

### Phase 4C-2B: Controlled Candidate Layout Promotion

The solver can now let a valid candidate preview replace the official generated
layout, but only behind an explicit input flag.

Current rules:

- Promotion is disabled by default.
- `optimization.promote_candidate_layout_preview = true` requests promotion.
- A preview can be promoted only when
  `candidate_layout_preview.comparison.promotion_eligible` is true.
- Promotion eligibility requires valid preview geometry/maneuvers/traffic,
  score not lower than the current layout, and no unresolved
  `dead_end_without_turnaround` graph reports.
- When promoted, official `aisles`, `stalls`, `score`, maneuver validation, and
  graph validation come from the candidate preview.
- `candidate_layout_promotion` reports whether promotion was not requested,
  promoted, or rejected, including blocker reasons.
- DXF, SVG, and the top-level report use the promoted layout only after the
  eligibility gate passes.

The bundled large branch example now enables this gate so the promoted U-shaped
candidate layout appears as the official output rather than only as an orange
preview overlay.

### Phase 4C-3A: Shadow Branch Turnaround Expansion

The candidate network preview now expands unconnected shadow-selected branches
into explicit branch aisle and turnaround aisle pieces.

Current rules:

- Branch candidate diagnostics preserve separate branch-aisle geometry and
  branch-turnaround geometry.
- The candidate conflict matrix can still use the combined branch drivable
  geometry for compatibility checks.
- The preview network draws and validates the branch aisle and its turnaround
  as separate aisles.
- Turnaround parent links use the unique source candidate id, not only the
  reused branch source id, so multiple shadow branch trials can coexist safely.
- Branches connected by a shadow connector do not get an extra end turnaround
  preview aisle.
- Promotion no longer rejects an otherwise valid shadow branch merely because
  its turnaround was hidden inside the branch candidate polygon.

### Phase 4C-3B: Dependency-Aware Shadow Selection

The shadow selector now applies basic road-network selection rules instead of
only picking the highest-scoring non-conflicting objects.

Current rules:

- Branch candidates are selected before connector candidates.
- Shadow branch selection respects `optimization.max_branches`.
- Branch candidates with positive score can still be rejected with
  `exceeds_max_branches`.
- Alternative branch candidates with the same branch source id are mutually
  exclusive; later alternatives are rejected with
  `duplicate_branch_source_selected`.
- Connector candidates require both endpoint branch source ids to already be in
  the selected branch set.
- Connectors rejected for missing endpoint branches report
  `connector_dependency_not_selected` and list missing branch source ids.
- Selected connector candidates still must not conflict with the already
  selected candidate set.
- The selector report includes selected branch count, selected connector count,
  max branch limit, and selected branch source ids.

### Phase 4C-3C: Loop Bundle Shadow Selection

The shadow selector can now compare simple loop bundles against standalone
branch candidates.

Current rules:

- A loop bundle contains two branch candidates plus one connector candidate.
- The connector's `connects` metadata determines the required branch source ids.
- The selector uses the best available branch candidate for each connector
  endpoint source id.
- A loop bundle is ignored if either endpoint branch source is missing or if
  the bundle has internal conflicts.
- Bundle score is the sum of the two branch scores, connector score, and a
  small loop bonus.
- Bundles and standalone branches compete in one sorted selection pass.
- A winning bundle still emits ordinary selected candidate ids, so downstream
  preview generation can continue to consume `selected_ids`.
- The selector report includes eligible bundle count, selected bundle count, and
  selected bundle details.

### Phase 4C-4A: Shadow Connector Candidate Availability

Connector candidates are now easier for the bundle selector to see.

Current rules:

- If `optimization.enable_connectors` is true, the candidate snapshot can
  synthesize report-only connector aisle skeletons between compatible branch
  candidate sources.
- Synthetic connectors are tried between adjacent same-side branch sources
  using the best branch candidate available for each source id.
- A generated connector candidate still wins over a synthetic duplicate for the
  same two branch source ids.
- Synthetic connectors enter the same conflict matrix, bundle selector, network
  preview, layout preview, and promotion gate as generated connector
  candidates.
- Synthetic connectors do not add connector-side stalls yet; they only test
  circulation connectivity and turnaround removal.
- Input diagnostics now report connector candidate counts and synthetic
  connector candidate counts.

### Phase 4C-4B: Connector Preview Turnaround Suppression Report

The candidate network preview now explains how connector-selected loop
circulation changes branch dead-end handling.

Current rules:

- The network preview reports connector count and loop connector count.
- The preview reports branch source ids connected by selected connector
  candidates.
- A branch source connected by a selected connector does not receive a generated
  shadow end-turnaround preview aisle.
- The preview reports suppressed branch turnaround source ids and suppressed
  turnaround count.
- Input diagnostics expose connector and suppressed-turnaround counts so a user
  can tell whether a U-shaped preview is really being treated as a loop.

### Phase 4C-4C: Inset Connector Placement For Outer Stall Rows

U-shaped connectors no longer have to sit directly against the branch end or
site edge.

Current rules:

- `optimization.connector_allow_outer_stall_row` defaults to true.
- When enabled, same-side connector geometry is inset from the branch end by up
  to one connector-side stall depth.
- The inset is capped so the connector still stays within the branch length.
- Connector-side stall generation then has room to place stalls on the outer
  side of the U-shaped connector.
- The same inset rule is used for generated connector candidates and synthetic
  shadow connector candidates.
- This is still a conservative single-position rule, not a full connector
  offset search.

### Phase 4C-4D: Connector Inset Depth Candidate Search

Connector placement now compares multiple inset depths instead of using one
fixed position.

Current rules:

- `optimization.connector_inset_depths` can explicitly list connector setback
  distances from the branch end.
- If no list is provided and `connector_allow_outer_stall_row` is true, the
  solver tries a small default set: flush, half stall depth, one stall depth,
  and one-and-a-half stall depths.
- Every inset is capped by the usable branch length so the connector cannot be
  pushed past the branch throat.
- Generated connector candidates and synthetic shadow connector candidates use
  the same inset-depth list.
- Connector diagnostics, candidate objects, network preview metadata, and
  `selected_connectors` report `connector_inset_depth`.
- Candidate selection can distinguish multiple connector candidates for the
  same pair of branches by their inset depth.

### Phase 4C-4E: Connector End-Stall Maneuver Fallback

Connector loops can now use the maneuver layer to decide whether end-adjacent
connector stalls are valid instead of blocking them purely with a rectangular
throat rule.

Current rules:

- `optimization.maneuver_l_shape_fallback` defaults to true for 90-degree
  perpendicular stalls.
- `optimization.connector_allow_l_shape_end_stalls` defaults to the same value.
- When a connector is evaluated, endpoint branch aisles are trimmed to the
  connector boundary before connector-side stalls are generated.
- Connector-side stalls are allowed to replace older endpoint branch stalls if
  the net candidate still scores better.
- Candidate layout conflict resolution prioritizes selected connector stalls
  over stale branch-end stalls so the promoted preview matches the selected
  loop geometry.
- Reports expose the fallback through
  `maneuver_validation.rule_counts.perpendicular_90_l_shape_proxy` and
  per-stall `maneuver_variant` values such as `l_shape_start` and
  `l_shape_end`.

## Phase 5: Operational Quality

### Goal

Handle the uncomfortable real-world cases that are technically possible but bad
for drivers.

Checks to add:

- narrow two-way head-to-head conflicts,
- passing bay requirements,
- long reverse movement,
- entrance throat blockage,
- one-way trap routes,
- excessive conflict points near intersections,
- optional traffic simulation.

### Acceptance Criteria

- risky but allowed patterns receive penalties,
- disallowed patterns are rejected,
- reports distinguish invalid constraints from soft penalties.

### Phase 5A: Junction And Entrance Soft-Risk Report

The first operational-quality slice is report-only. It does not declare a
layout illegal, but it gives the scoring layer a measurable discomfort signal.

Current rules:

- `operational_quality` is attached to every generated layout.
- The report detects aisle-pair junctions from intersecting aisle geometry.
- Each junction receives a circular clearance zone controlled by
  `optimization.operational_junction_clearance_radius`, defaulting to half the
  selected aisle width.
- Each entrance receives a throat clearance zone controlled by
  `optimization.operational_entrance_clearance_radius`, defaulting to the larger
  of half aisle width and half entrance width.
- Stalls intersecting these zones are reported as soft conflicts with overlap
  area and distance to the zone center.
- `operational_quality.risk_score` is the current soft-risk metric.
- The score includes `operational_risk` and `operational_risk_penalty`.
- `optimization.weights.operational_risk` controls how strongly the score
  penalizes these risks.
- Candidate layout preview scoring uses the same operational-risk metric as the
  official layout score.

Non-goals for this slice:

- no hard rejection yet,
- no vehicle trajectory simulation,
- no narrow two-way deadlock logic,
- no conflict-point capacity model.

### Phase 5B: Configurable Operational Quality Gates

The operational-quality report can now move from pure scoring into guarded
decision making when the input explicitly asks for it.

Current rules:

- `optimization.operational_quality_mode` controls behavior.
- `score_only` is the default and preserves Phase 5A behavior: risks are
  reported and scored, but they do not block a layout.
- `promotion_gate` keeps a preview layout valid, but prevents candidate layout
  promotion when the configured risk limit is exceeded.
- `hard_reject` marks the checked layout invalid when the configured risk limit
  is exceeded.
- `optimization.operational_max_risk_score` sets the limit. If omitted, no
  operational risk limit is enforced.
- Reports include `risk_exceeds_limit`, `promotion_blockers`, and
  `blocking_conflicts` so rejected promotions explain which stall conflicts
  caused the gate to close.
- Candidate layout promotion now consumes operational-quality blockers in the
  same comparison report that already checks geometry, maneuvers, graph
  validity, dead ends, and score delta.

### Phase 5C: Stall Route Risk Report

Operational quality now includes a first path-level report for each stall.
This is still a graph proxy, not a swept-path traffic simulation.

Current rules:

- The checker reuses the Phase 2 traffic graph.
- Each stall is associated with its serving aisle graph node.
- The report computes the shortest entry path length from any entry-capable
  entrance to the serving aisle node.
- The report computes the shortest exit path length from the serving aisle node
  to any exit-capable entrance.
- Path length uses the current aisle-node centroid graph, so it is an
  explainable first approximation rather than exact wheel travel distance.
- The report marks stalls that depend on a dead-end turnaround aisle attached
  to their serving aisle.
- `optimization.operational_max_route_length` can turn excessive route length
  into route risk. If omitted, route lengths are reported but not penalized.
- `optimization.operational_turnaround_dependency_risk` can assign a soft risk
  to stalls that depend on dead-end turnaround circulation. It defaults to zero.
- Route risks are added to `operational_quality.risk_score`, so the existing
  `score_only`, `promotion_gate`, and `hard_reject` modes apply without a new
  gating mechanism.
- Reports include `route_risk_score`, `route_risk_count`, and
  `route_risks.routes`.

### Phase 5D: Route Risk Summary Metrics

The route-risk report now includes a compact summary layer so users and later
optimization code can understand circulation quality without scanning every
stall route.

Current rules:

- `operational_quality.route_summary` mirrors
  `operational_quality.route_risks.summary`.
- The summary reports checked stall count, route count with finite length,
  average route length, maximum route length, maximum entry path length, and
  maximum exit path length.
- The summary identifies the stall with the longest route.
- The summary counts stalls whose serving aisle depends on a dead-end
  turnaround.
- The summary counts missing entry paths, missing exit paths, excessive route
  length issues, and configured turnaround-dependency issues.
- The summary is diagnostic by default. It does not add new risk by itself;
  risk still comes from the Phase 5C route issue rules and the existing
  operational-quality mode.

### Phase 5E: Route Summary Threshold Risks

The route summary can now become a design-level risk source when configured.

Current rules:

- `optimization.operational_max_turnaround_dependency_ratio` sets the maximum
  allowed share of stalls whose serving aisle depends on a dead-end turnaround.
- If omitted, the ratio is still reported but not penalized.
- `optimization.operational_turnaround_dependency_ratio_risk` controls the risk
  score added when the ratio exceeds the configured limit. It defaults to 1.0.
- The risk is represented as a single `route_summary` blocker rather than one
  blocker per stall, so reports stay readable on large sites.
- The risk contributes to `operational_quality.risk_score`, so the existing
  `score_only`, `promotion_gate`, and `hard_reject` modes apply.
- This is not narrow two-way deadlock detection yet. It is a conservative
  circulation-quality gate for excessive dependence on dead-end turnarounds.

### Phase 5F: Long Route Summary Threshold Risks

The route summary can now flag site-wide long-route patterns, not only
individual over-limit stalls.

Current rules:

- `optimization.operational_max_average_route_length` sets the maximum allowed
  average route length across checked stalls.
- `optimization.operational_average_route_length_risk` controls the risk score
  added when the average route length exceeds that limit. It defaults to 1.0.
- `optimization.operational_max_long_route_ratio` sets the maximum allowed
  share of stalls whose route already exceeds `operational_max_route_length`.
- `optimization.operational_long_route_ratio_risk` controls the risk score
  added when the long-route ratio exceeds that limit. It defaults to 1.0.
- These are summary-level risks, so they produce compact `route_summary`
  blockers instead of one blocker per stall.
- This lets the tool report and gate layouts that are globally awkward to
  circulate through, even if no single local junction rule fails.
- This is still graph-distance analysis, not vehicle trajectory simulation.

### Phase 5G: Directionality Trap Risk Report

Operational quality now checks the directed traffic graph for one-way and
directionality traps.

Current rules:

- The checker reuses the Phase 2 traffic graph and its directed edges.
- A non-entrance aisle node that is reachable from an entry-capable entrance
  but cannot reach any exit-capable entrance is reported as `one_way_trap`.
- A non-entrance aisle node that can reach an exit but cannot be reached from
  an entry is reported as `exit_only_fragment`.
- A non-entrance aisle node with neither entry reachability nor exit path is
  reported as `isolated_directional_fragment`.
- Stalls served by those nodes are reported in
  `operational_quality.directionality_risks.stall_issues`.
- `optimization.operational_directionality_issue_risk` can assign risk per
  affected stall. It defaults to zero, so existing layouts remain diagnostic
  unless configured.
- `optimization.operational_max_directionality_issue_ratio` can gate on the
  share of affected stalls.
- `optimization.operational_directionality_issue_ratio_risk` controls the risk
  score for the ratio gate and defaults to 1.0.
- Directionality risks contribute to `operational_quality.risk_score`, so the
  existing `score_only`, `promotion_gate`, and `hard_reject` modes apply.
- This is not narrow two-way head-to-head detection yet. It is the first
  directed-graph circulation trap check.

### Phase 5H: Narrow Two-Way Exposure Report

Operational quality now recognizes layouts that use a single-vehicle,
two-way aisle class before the generator is allowed to optimize narrow
two-way aisles freely.

Current rules:

- The checker reads the selected aisle class from `aisles.fixed_class` or the
  first enabled aisle class.
- An aisle class with `capacity = "single_vehicle"` and
  `directionality = "two_way"` is treated as narrow two-way.
- Because passing bay geometry is not implemented yet, narrow two-way aisles
  are reported with `narrow_two_way_without_passing_bay_model`.
- Stalls served by those aisles are reported as affected stalls.
- `optimization.operational_narrow_two_way_issue_risk` can assign risk per
  affected stall. It defaults to zero, so manually modeled narrow two-way
  layouts remain diagnostic unless configured.
- `optimization.operational_max_narrow_two_way_stall_ratio` can gate on the
  share of stalls served by narrow two-way aisles.
- `optimization.operational_narrow_two_way_stall_ratio_risk` controls the risk
  score for that ratio gate and defaults to 1.0.
- This is still not head-to-head deadlock simulation. It is a conservative
  exposure report that prevents narrow two-way support from silently looking
  safe before passing bays and conflict rules exist.

### Phase 5I: Passing Bay Marker Report

Narrow two-way reporting now reads marker-level passing bay inputs before full
passing bay geometry validation exists.

Current rules:

- `site_features` entries with `type = "passing_bay"`,
  `type = "passing-bay"`, `type = "passing_bay_area"`, or
  `type = "passing-bay-area"` are reported as passing bay markers.
- The report preserves each marker id plus optional aisle id, side, center,
  width, length, and geometry fields.
- `operational_quality.narrow_two_way_summary.passing_bay_marker_count`
  records how many markers are present.
- `passing_bay_model_available` means marker-level passing bay data exists. It
  does not mean full geometric coverage has been verified.
- With marker data present, narrow two-way aisles are still reported as
  `narrow_two_way_passing_bay_geometry_not_checked` until the geometry coverage
  check exists.
- `optimization.operational_min_passing_bays` can require a minimum number of
  passing bay markers for narrow two-way layouts.
- `optimization.operational_passing_bay_shortage_risk` controls the risk score
  per missing marker and defaults to 1.0.
- Passing bay shortage risks contribute to `operational_quality.risk_score`, so
  the existing `score_only`, `promotion_gate`, and `hard_reject` modes apply.

### Phase 5J: Passing Bay Geometry Association Report

Passing bay markers now get a first geometric usability check before they count
toward narrow two-way mitigation.

Current rules:

- A passing bay marker can provide geometry through a standard `geometry`
  object or through `center + width + length`.
- Supported `geometry.type` values are `polygon`, `rectangle`, `circle`, and
  `polyline_buffer`.
- The checker associates a marker to its explicit `aisle_id` when provided, or
  to the nearest generated narrow two-way aisle when the marker is close enough.
- `optimization.operational_passing_bay_touch_tolerance` controls the allowed
  distance from a passing bay geometry to the associated aisle and defaults to
  0.25 drawing units.
- `optimization.operational_min_passing_bay_area` can require a minimum usable
  passing bay area.
- `optimization.operational_passing_bay_geometry_issue_risk` can assign risk to
  unusable passing bay markers. It defaults to zero, so marker geometry problems
  remain diagnostic unless configured.
- `optimization.operational_min_passing_bays` now counts usable passing bays,
  not raw marker count.
- This still does not verify line-of-sight, priority rules, queueing, or
  head-to-head vehicle simulation.

### Phase 5K: Passing Bay Spacing Report

Passing bay reports now include a first aisle-level spacing check for narrow
two-way aisles.

Current rules:

- Each generated narrow two-way aisle gets an approximate centerline from the
  long axis of its aisle polygon.
- Usable passing bays are projected onto the associated aisle centerline.
- The checker measures the gaps between aisle endpoints and projected passing
  bay positions.
- `operational_quality.narrow_two_way_summary.longest_passing_bay_gap`
  reports the largest unserved gap across checked narrow two-way aisles.
- `optimization.operational_max_passing_bay_spacing` can set the maximum
  allowed gap between an aisle endpoint or passing bay and the next passing bay
  or endpoint.
- `optimization.operational_passing_bay_spacing_risk` controls the risk score
  for each aisle whose longest gap exceeds that limit and defaults to 1.0.
- This is still an aisle-axis approximation. It does not simulate two vehicles,
  sight distance, courtesy priority, queueing, or passing bay entry/exit swept
  paths.

### Phase 5L: Passing Bay Gap Segment Report

Passing bay spacing is now reported as explicit gap segments instead of only a
single longest-gap number.

Current rules:

- Each gap records the start anchor and end anchor.
- Anchor kinds are `aisle_endpoint` and `passing_bay`.
- Gap segment types are:
  - `no_passing_bay_full_aisle`
  - `endpoint_to_passing_bay`
  - `passing_bay_to_passing_bay`
- Each gap records whether it exceeds
  `optimization.operational_max_passing_bay_spacing`.
- `operational_quality.narrow_two_way_summary.longest_passing_bay_gap_type`
  reports the segment type of the longest gap.
- `passing_bay_endpoint_gap_count` and
  `passing_bay_spacing_exceeded_gap_count` summarize how much of the spacing
  risk is endpoint-driven versus internal.
- This is still not a traffic simulation, but it gives the later deadlock model
  concrete road segments to reason about.

### Phase 5M: Narrow Two-Way Meeting Risk Proxy

Narrow two-way spacing gaps are now translated into first-pass meeting-risk
records.

Current rules:

- Meeting risks are derived from passing bay gap segments that exceed
  `optimization.operational_max_passing_bay_spacing`.
- `no_passing_bay_full_aisle` becomes
  `full_aisle_without_meeting_refuge`.
- `endpoint_to_passing_bay` becomes
  `endpoint_to_refuge_gap_exceeds_limit`.
- `passing_bay_to_passing_bay` becomes
  `refuge_to_refuge_gap_exceeds_limit`.
- `optimization.operational_narrow_two_way_meeting_gap_risk` controls whether
  those diagnostic meeting risks add score and can block promotion. It defaults
  to 0.0.
- This is still a proxy. It does not simulate vehicle arrival order, priority,
  waiting behavior, or swept paths into and out of a passing bay.

### Phase 5N: Network-Anchored Meeting Risk Proxy

Meeting-risk segments now carry first-pass road-network anchor semantics.

Current rules:

- A generated aisle endpoint connected to `connected_to_entrance_id` is
  classified as an `entrance_throat`.
- Other generated aisle endpoints are classified as `aisle_terminal`.
- Passing bay anchors are classified as `passing_bay_refuge`.
- Gap reports include `start_network_kind`, `end_network_kind`, and
  `network_segment_type`.
- Network segment types currently include:
  - `entrance_to_terminal`
  - `entrance_to_refuge`
  - `terminal_to_refuge`
  - `refuge_to_refuge`
  - `terminal_to_terminal`
- Meeting risk issues are refined with those network semantics, so an
  overlong entrance-side narrow segment can be reported as
  `entrance_to_refuge_gap_exceeds_limit` or
  `entrance_to_terminal_without_meeting_refuge`.
- This still does not infer branch merge priority or multi-aisle route
  conflicts. It only annotates the current aisle-axis gap proxy with known
  entrance anchors.

### Phase 5O: Aisle-Junction Endpoint Anchors

Meeting-risk segment anchors now identify aisle endpoints that touch another
generated aisle.

Current rules:

- Entrance endpoints still take precedence and remain `entrance_throat`.
- If a non-entrance aisle endpoint touches another generated non-turnaround
  aisle polygon, the endpoint is classified as `aisle_junction`.
- Gap network segment types now include:
  - `junction_to_terminal`
  - `junction_to_refuge`
  - `junction_to_junction`
  - `entrance_to_junction`
- Meeting risk issues now include:
  - `junction_to_terminal_gap_exceeds_limit`
  - `junction_to_refuge_gap_exceeds_limit`
  - `junction_to_junction_gap_exceeds_limit`
  - `entrance_to_junction_gap_exceeds_limit`
- `operational_quality.narrow_two_way_summary.junction_meeting_trap_count`
  summarizes meeting risks that involve an aisle junction.
- This detects branch-side endpoints that attach to a parent aisle. Parent-side
  mid-aisle junction anchors are handled in Phase 5P.

### Phase 5P: Mid-Aisle Junction Anchors

Meeting-risk segment anchors now project generated branch or connector
junctions onto the parent aisle centerline.

Current rules:

- If another generated non-turnaround aisle declares the current aisle as its
  `parent_aisle_id`, or lists the current aisle in `connected_aisle_ids`, the
  checker intersects the two aisle polygons.
- The intersection centroid is projected onto the current aisle axis and
  recorded as a mid-aisle `aisle_junction` anchor when it is not at an endpoint.
- Gap segment types now include:
  - `endpoint_to_junction`
  - `junction_to_passing_bay`
  - `junction_to_junction`
- Parent aisles can now report `entrance_to_junction` and
  `junction_to_terminal` meeting-risk segments instead of treating the whole
  parent aisle as one uninterrupted entrance-to-terminal gap.
- `operational_quality.narrow_two_way_summary.passing_bay_projected_junction_count`
  summarizes projected junction anchors.
- `operational_quality.narrow_two_way_summary.passing_bay_junction_gap_count`
  summarizes spacing gaps that touch a junction anchor.
- This still does not solve branch merge priority, yielding, or swept-path
  conflicts. It makes the route proxy aware of the missing internal junction
  node so those checks can be layered later.

### Phase 5Q: Narrow Two-Way Junction Merge Proxy

Narrow two-way reports now include a junction-level merge proxy for branch or
connector junctions.

Current rules:

- The checker groups spacing gaps that touch the same pair of generated aisles,
  so a parent-side projected junction and a branch-side endpoint junction are
  treated as one junction.
- A junction with at least three approaches is reported as a multi-approach
  merge point.
- Each approach records the aisle id, opposite network anchor, segment length,
  segment type, whether it exceeds the configured spacing threshold, and
  whether the opposite anchor is a passing bay refuge.
- Reports include:
  - `approach_count`
  - `refuge_approach_count`
  - `overlong_approach_count`
  - `issues`
- Current issues include:
  - `multi_approach_junction_without_refuge`
  - `multi_approach_junction_with_overlong_approach`
- `optimization.operational_narrow_two_way_junction_merge_risk` can assign a
  risk score per reported merge point. It defaults to zero, so existing layouts
  remain diagnostic unless explicitly gated.
- `operational_quality.narrow_two_way_summary.junction_merge_issue_count`,
  `junction_merge_missing_refuge_count`, and
  `junction_merge_overlong_approach_count` summarize these proxy risks.
- This is still not a swept-path or right-of-way simulation. It identifies the
  junctions where a later vehicle movement model needs to reason about yielding
  space, refuge placement, and approach priority.

### Phase 5R: Pedestrian-conflict proximity proxy

Current rules:

- Pedestrian and accessible route geometries (hard or advisory) are watched.
- Stalls within `optimization.operational_pedestrian_conflict_clearance`
  (default 0, meaning contact only) are reported as `stall_near_pedestrian_route`
  or `stall_touches_pedestrian_route`.
- Aisles that intersect a walkway are `aisle_crosses_pedestrian_route`;
  near misses use the same clearance as `aisle_near_pedestrian_route`.
- `optimization.operational_pedestrian_conflict_risk` (default 0) is added per
  issue, so existing scores stay unchanged unless the knob is set.
- This is a geometry proxy, not pedestrian delay, sight-distance, or crossing
  certification.

## Phase 6: Route usability

Protected pedestrian and emergency geometry is not useful if the stalls or
gates it was declared for cannot reach it.

### Phase 6A: Accessible stall-to-route contact

Current rules:

- A positive `parking.quotas.accessible_min` already requires hard accessible
  route geometry at definition time.
- Classified accessible stalls on the official layout must geometrically reach
  that route within `constraints.accessible_route_touch_tolerance` (default
  1.5 m).
- Failure reason: `accessible_stall_does_not_reach_accessible_route`.
- This is project-policy geometric contact, not slope, width, or ADA
  certification.

### Phase 6B: Emergency route-to-entrance contact

Current rules:

- `emergency_access_required` already requires hard fire/access geometry.
- At least one hard fire/access/emergency route must geometrically reach an
  entrance gate within `constraints.emergency_route_touch_tolerance` (default
  1.0 m). Extra unconnected fire geometry is reported, not a hard fail.
- Failure reason: `emergency_route_does_not_reach_entrance`.
- This is not a fire-apparatus swept path or hydrant-layout check.

### Phase 6C: EV stall-to-charger contact

Current rules:

- A positive `parking.quotas.ev_min` still counts classified or
  charging-equipped stall types even when no charger geometry is placed.
- When that quota is positive **and** hard `charging_post` / `ev_charger` /
  `charger` site features have geometry, classified EV stalls must
  geometrically reach a charger within
  `constraints.ev_charger_touch_tolerance` (default 2.0 m).
- Failure reason: `ev_stall_does_not_reach_charger`.
- This is project-policy geometric contact, not circuit design, cable length,
  or equipment-placement certification.

### Phase 6D: Accessible-route continuity and connects destinations

Current rules:

- When `accessible_min` is positive, hard accessible-route pieces that classified
  stalls actually touch must form one contact network within the same
  `accessible_route_touch_tolerance`.
- `accessible_routes.connects` tokens other than reserved stall aliases must
  name existing site-feature, reserved-area, entrance, or route geometry.
  The stall-serving network must reach those destinations.
- Unknown connect ids fail closed (`accessible_route_connects_target_missing`).
- Failure reasons also include `accessible_route_network_disconnected` and
  `accessible_route_does_not_reach_destination`.
- This is not slope, width, or an ADA accessible-route certification.

### Phase 6E: Place classified stalls on the contact network

Current rules:

- After maneuver filtering, official and preview layouts drop accessible
  stalls that do not reach a hard accessible route when `accessible_min` is
  positive, and EV stalls that do not reach a placed charger when `ev_min` is
  positive and charger geometry exists.
- Stall-module shadow scores reject those unreachable classified chunks
  (`module_does_not_reach_accessible_route` /
  `module_does_not_reach_charger`) and add `weights.accessible_contact` /
  `weights.ev_contact` (default 100 per stall) to reachable ones so they can
  beat denser unmarked modules.
- This still does not synthesize a mixed accessible/standard strip from
  scratch; it filters and prefers among generated candidates.

### Phase 6F: Accessible-route min_width and max_slope

Current rules:

- Declared `min_width` on a hard accessible route is compared to the
  `polyline_buffer` `width`. Too-narrow routes fail
  `accessible_route_narrower_than_min_width`. Other geometry types fail
  `accessible_route_width_not_auditable_for_geometry`.
- Declared `max_slope` fail-closes with
  `accessible_route_slope_check_unsupported` because the input model has no
  elevation. The kernel does not invent a flat site.
- This is not ADA, ISA, or a surveyed longitudinal-grade certificate.

### Phase 6G: Retarget official stalls onto the contact band

Current rules:

- When `accessible_min` / `ev_min` is positive, a matching classified stall
  type exists, and contact geometry exists, official and preview layouts may
  relabel or resize same-family stalls that already reach the route/charger
  so the quota can be met without switching the whole aisle to that type.
- Replacement polygons must stay in stall-usable area and not overlap aisles
  or other stalls. Contact filtering still drops classified stalls that miss.
- This is not a new accessible-aisle synthesizer.

### Phase 6H: Clear same-side neighbors for wider classified stalls

Current rules:

- If a retargeted accessible/EV stall is wider or longer than the stall it
  replaces, overlapping neighbors on the same aisle side may be dropped so
  the classified stall can fit.
- Neighbors that already carry the needed classification, or stalls on another
  aisle/side, are not dropped. Aisle overlap still rejects the replacement.
- This still does not synthesize a dedicated accessible module from empty
  pavement.

### Phase 6I: Pack a contact-band strip of classified stalls

Current rules:

- When a classified stall is a different size and a contiguous same-side
  contact run has enough frontage for at least two classified bays, the
  retargeter packs that run as a strip instead of converting one stall at a
  time.
- Same-size retarget still relabels; a single wider stall still uses the
  6H neighbor drop. Opposite-side, already-classified, and aisle-overlapping
  geometry still reject the pack.
- This still does not synthesize a dedicated accessible module from empty
  pavement, and it is not an ADA stall-module library.

### Phase 6J: Fill empty contact-band pavement on an existing aisle

Current rules:

- After pack/replace cannot meet the quota, perpendicular classified bays may
  be placed on empty stall-usable pavement along an existing main/branch/jog
  aisle side that already reaches the route or charger.
- New bays must touch that aisle, stay off other aisles and existing stalls,
  and still reach the contact geometry. No new aisle is invented. Parallel
  empty-fill is Phase 6K; angled empty-fill is Phase 6L.
- This is not a dedicated accessible-aisle synthesizer, ADA stall library,
  or multi-gate network rewrite.

### Phase 6K: Parallel empty-fill on the contact band

Current rules:

- When the classified stall family is parallel, empty-fill uses length along
  the existing aisle and width as lateral depth, the same box as official
  parallel generation.
- A side that already has a different stall family is not mixed with parallel
  bays. Angled empty-fill is Phase 6L. No new aisle is invented.
- This is not on-street parallel-parking design or an ADA stall library.

### Phase 6L: Angled empty-fill on the contact band

Current rules:

- When the classified stall family is angled, empty-fill uses the same
  parallelogram as official angled generation: front pitch
  `width / sin(theta)` along the existing aisle, sheared by
  `length * cos(theta)` with lateral depth `length * sin(theta)`.
- The stall angle is aisle heading ± the acute module angle (left +, right −).
  Mixed-family sides are not planted. No new aisle is invented.
- This is not an angled-stall optimizer or ADA stall library.

## Implementation Principle

Keep these layers separate in code:

```text
input model
geometry model
traffic graph model
maneuver model
candidate generation
optimization
export and diagnostics
```

The project should always be able to answer:

```text
Why is this stall valid?
Which aisle serves it?
Which entrance reaches it?
Which vehicle rule does it satisfy?
Which rejected alternatives failed and why?
```

If the program cannot explain that, it should not claim the layout is optimal.
