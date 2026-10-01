# v0.4 discrete candidates (greedy catalog + optional CP-SAT)

This is the acceptance contract for the v0.4 selector. It does **not** replace
the official template generator or change default layouts. The CP-SAT backend
swaps only the **shadow** selector.

## Release claim

> Official aisle/stall geometry is still produced by the Phase 1 template
> generator. Branch and connector **shadow** selection is a discrete catalog
> with conflicts, parent dependencies, and solver provenance. The default
> backend is greedy. `selector_backend=cpsat` uses OR-Tools CP-SAT when the
> `optimizer` extra is installed; otherwise it fail-closes to greedy.

The claim does not establish statutory compliance, that the shadow subset is
the exported layout, or that CP-SAT is the default.

When `optimization.layout_search.mode=multi_spine`, each retained spine runs
this same discrete model independently. A CP-SAT gap or greedy choice on one
spine is a **local** catalog result; it is not a proof of site-global
optimality across spines. Official replacement still requires
`promote_candidate_layout_preview` and a fully rebuilt, checked layout.

## Invariants

1. Every generated aisle is present as a `CandidateObject` (`kind=aisle`,
   `status=selected`) with `parent_ids` matching `parent_aisle_id` /
   `connected_to_entrance_id`.
2. Selector **variables** are branch/connector skeletons (`kind=aisle_skeleton`)
   and stall modules (`kind=stall_module`). A stall module is one
   `(served_by_aisle, aisle_side, stall_type[, segment])` strip. At most one
   stall family may occupy a `family_slot` (`parent|side` or
   `parent|side|segN`). Every official aisle (`kind=aisle`) is **base**.
   Individual stalls remain `derived`.
3. Heading/offset main skeletons (`role=main`, `kind=aisle_skeleton`) are
   `spine_attempt` records. They are not selector variables.
4. A connector variable depends on its `metadata.connects` branch source ids.
   A backend must not select a connector unless a selected branch exists for
   each endpoint source (`connector_dependency_not_selected` when rejected).
5. At most one skeleton is selected per branch `source_id`. Selected branch
   count is the number of distinct sources and must not exceed
   `optimization.max_branches`.
6. Geometry conflicts between **variable** skeletons are exclusive
   (`x_i + x_j <= 1`). Conflicts with official base aisles are not treated as
   hard blockers in this slice (a later optimizer regenerates stalls).
7. Both backends use the same shadow objective: stall-delta score, plus a
   `150` loop bonus per selected connector that has both endpoints.
8. Passing-bay aisles remain base, non-circulation graph shoulders.
9. Selection remains `status=shadow_only` until
   `promote_candidate_layout_preview` is set. Promotion rebuilds official
   aisle IDs from catalog `source_id` values (`A-MAIN`, `A-BRANCH-001`, …),
   not preview labels (`PN-AISLE-*`). Official branch/connector records after
   promotion come from the **selector** `selected_ids`, not the generator's
   greedy picks.
10. Network preview base aisles are spines (`main` / `jog` / `exit` /
    `passing_bay`) plus spine-owned turnarounds. Generator branch turnarounds
    must not leak into the base layer; dead-end caps are synthesized only for
    selected unconnected branches.
11. Promoted geometry is revalidated with the same vehicle/site/graph/
    operational gates as today's promotion. A lower score, failed validation,
    empty preview, or dead-end without turnaround still blocks replacement.
12. The default solve path must not import OR-Tools. Missing OR-Tools is not a
    hard solve failure.
13. A stall module may be selected only if its parent aisle is present: base
    spines are always present; branch/connector modules require that parent
    skeleton to be selected (`module_parent_not_selected` when rejected).
    Overlap between a module and aisle skeletons is not a strip-level
    exclusive (individual overlapping stalls are still dropped in preview).
    Preview/promoted stalls come only from selected modules when the catalog
    contains any `stall_module` objects. When `parking.stall_types` lists
    more than one family, spine and branch aisles get alternative family
    modules; greedy and CP-SAT keep at most one per `family_slot`
    (`parent|side` or `parent|side|segN`), rejecting extras as
    `module_family_slot_taken`. Same-side U, opposite-cross, and end-loop
    connectors also get perpendicular/parallel/angled alternatives.
    `optimization.stall_module_segment_stalls` (default 4) splits each side
    into consecutive stall chunks; explicit `0` keeps the whole strip.
    Family exclusivity is per segment (`parent|side|segN`).
14. Stall-module shadow scores are `stall_count * family_weight - 0.2 * area`.
    `family_weight` defaults to 100 and uses `optimization.weights.stall_family`
    (or `stall_count_<family>`) when set. Official layout `stall_value` uses
    the same family map against `weights.stall_count`. `weights.segment_family_mix`
    (default 0; `prefer_uniform_segments` sets -50) penalizes an aisle-side that
    mixes families. Greedy then compares mixed independent picks against the
    best uniform family on that side; CP-SAT adds a mix-side binary to the
    same objective. Mix weight 0 leaves independent per-segment selection
    unchanged.
15. When `accessible_min` or `ev_min` is positive and the matching contact
    geometry exists, classified stall modules that cannot reach it are
    rejected; reachable ones receive `weights.accessible_contact` /
    `weights.ev_contact` (default 100 per stall).

## Failure behavior

| Case | Result |
| --- | --- |
| Default / `selector_backend=greedy` | Greedy catalog selection; `backend=greedy` |
| `selector_backend=cpsat` and OR-Tools present | CP-SAT on the same variables/constraints; `backend=cpsat` |
| `selector_backend=cpsat` and OR-Tools missing | Greedy; `requested_backend=cpsat`, `backend_fallback_reason=cpsat_backend_unavailable` |
| CP-SAT import/solve exception | Greedy; `cpsat_backend_failed` |
| CP-SAT infeasible / unknown | Greedy; `cpsat_backend_infeasible` or `cpsat_backend_no_solution` |
| Invalid backend string | Greedy; `unknown_selector_backend` |
| Connector missing an endpoint source | Not selected; `connector_dependency_not_selected` |
| Official layout invalid | Unchanged fail-closed publication rules |

Time limit: `optimization.selector_time_limit_seconds` (default `2`). A
feasible non-optimal CP-SAT result is accepted (`solver_provenance.status=feasible`)
and is **not** a fallback. Seed: `optimization.selector_seed` (optional int).

## Authority

Selector backend and catalog scoring are `advisory` unless a later contract
promotes a chosen subset to official geometry. Project-policy still lives in
vehicle/site/quota/graph gates on the **exported** layout.

## Catalog classes

| Class | Objects | Selector role |
| --- | --- | --- |
| `base` | Official aisles (`kind=aisle`) | Pre-selected; `base_selected_ids` |
| `variable` | `aisle_skeleton` branch/connector and `stall_module` strips | Greedy or CP-SAT; `selected_ids` |
| `spine_attempt` | Main-aisle heading/offset trials | Recorded; not variables |
| `derived` | Official stalls | Not selector variables; rebuilt from selected modules |

## CP-SAT model (this slice)

Binary `x_i` for each eligible variable (skeletons and stall modules).

- At most one branch skeleton per `source_id`
- `sum(branch x_i) <= max_branches`
- `x_connector <= sum(x_branch for endpoint source)` for each of two endpoints
- `x_module <= x_parent` when the parent is a variable skeleton; base-spine
  modules have no parent inequality
- `sum(x_module for family_slot) <= 1` so one stall family occupies a
  side (or a segment of that side)
- `x_i + x_j <= 1` for each variable–variable geometry conflict
- Maximize `sum(score_i * x_i) + 150 * sum(x_connector) + mix_weight * y_mixed_side`
  using integer millipercent scaling (`round(score * 1000)`). `y_mixed_side`
  is 1 when two or more stall families are selected on one aisle-side.

OR-Tools is imported only inside the CP-SAT function.

## Report provenance

`candidate_selection` must include:

- `version` (`phase4d-11` for this slice)
- `backend` (`greedy` or `cpsat`)
- `requested_backend`
- `backend_fallback_reason` (string or `null`)
- `base_roles`, `variable_roles`
- `base_selected_ids` / `base_selected_count`
- `solver_provenance`: `backend`, `seed`, `time_limit_seconds`,
  `objective_bound`, `gap`, `status` (`optimal_greedy` / `optimal` / `feasible`)
- existing fields (`selected_ids`, bundles, rejected reasons)

`field_support`:

- `optimization.discrete_candidate_catalog` — `active` when objects exist
- `optimization.selector_backend` — `active` when a backend ran
- `optimization.selector_backend_cpsat` — `active` when `backend=cpsat`;
  `active_failed` when CP-SAT was requested and fell back; otherwise `available`
- `optimization.stall_module_segment_stalls` — `active` when the effective
  chunk size is greater than 0 (default 4); `available` when explicit `0`
  keeps whole-strip modules
- `optimization.mixed_segment_scoring` — `active` when multiple stall families
  are declared, family weights are set, or a mix weight is requested; otherwise
  `available`

## Promotion rebuild

When `optimization.promote_candidate_layout_preview` is true and the preview
passes the existing gates:

1. Assemble preview aisles from spine base + selector `selected_ids`.
2. Place preview stalls (main-aisle survivors + skeleton `generated_stalls`).
3. Revalidate the preview (containment, graph, maneuver, site, engineering,
   operational quality).
4. If eligible, rewrite aisle IDs to catalog `source_id`, remap parents /
   connector endpoints / stall `served_by_aisle_id`, renumber stalls `P-###`.
5. Revalidate that official layout. Report
   `candidate_layout_promotion.official_id_scheme = catalog_source_id` and
   copy pre-promotion `backend` / `selected_ids` into the promotion block.

Preview SVG overlays may still use `PN-AISLE-*` / `PL-STALL-*`. Official
DXF/SVG after promotion must not.

## Non-claims

- No OR-Tools import on the default greedy path.
- Without the promotion flag, shadow selection is not the official layout.
- CP-SAT is not default and is not benchmarked as globally better on real sites.
- Stalls stay derived from the selected road network, not independent variables.

## Fixture

`tests/test_discrete_candidates.py`, `tests/test_candidate_cpsat.py`,
`tests/test_selection_rebuild.py`, `tests/test_stall_modules.py`, and
`tests/test_mixed_segment_scoring.py`.
CP-SAT success tests skip when OR-Tools is not installed
(`pip install -e ".[dev,optimizer]"`).
