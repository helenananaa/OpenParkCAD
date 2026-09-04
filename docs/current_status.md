# Current status

The multi-spine iteration passed its 2026-09-03 closure checks: full regression,
240 controlled benchmark runs, installed-wheel rollback checks, and both default
and optimizer CI paths. See the [acceptance record](v0_4_multi_spine_acceptance.md)
for the exact source commit, results, and remaining capability boundaries.

OpenParkCAD is an executable `0.3.0` algorithm prototype. Its historical phase
labels and package version are not capability claims: the trustworthy product
boundary is the set of checks reported as active for the exact official layout.
The generator remains limited to a small family of parking-network templates
that it exports for engineering inspection.

## Capability matrix

| Area | Current status | Boundary |
| --- | --- | --- |
| Input | Versioned JSON shape plus machine-readable Schema; boundary, obstacles/reservations, entrances, vehicle, stall/aisle definitions, scoped constraints, quotas, optimization | Schema acceptance is not enforcement; runtime diagnostics remain authoritative |
| Usable geometry | Polygonal site/setback, polygon obstacles, and supported polygon/circle/rectangle/polyline-buffer hard exclusions with clearance and scope. Opt-in `import-dxf` converts a support-range modelspace DXF (closed straight LWPOLYLINE/POLYLINE, directed LINE entrances, m/mm) into SiteSpec JSON without solving | No curved/bulge/INSERT import, holes, multi-boundary, or survey topology repair. No licensed real-site field-effect claim; synthetic round-trip only |
| Circulation generation | Straight entrance-connected main aisle for fixed two-way, one-way, or opt-in narrow two-way classes (optional lateral offsets or dogleg jog), end turnarounds with T-end bays on straight/dogleg/multi-jog rears, optional far-end exit corridor to a second exit-capable entrance, perpendicular branches, same-side U connectors, opposite-side short cross / outer end-loop connectors when score-positive, optional synthesized passing-bay side pockets | No arbitrary multi-entrance network synthesis or general junction synthesis |
| Dual entrances | Minimal entry→main→turnaround→exit attach when a distinct exit-capable entrance sits at the far end (`enable_dual_entrance`, default on); dogleg rear turnaround uses turnaround-relative lateral checks and optional L-elbow exit corridors around mid-site obstacles | Not multi-gate optimization, queueing, or coordinated multi-path routing |
| One-way aisles | Fixed one-way class generation; graph edges are one_way with optional reverse-egress for stall exit (default on); dogleg + dual entrance supports strict one-way without reverse egress | Not a full one-way network optimizer |
| Passing bays / narrow two-way | Opt-in `enable_passing_bay_synthesis` places side pockets and unlocks narrow two-way generation; Phase 5Q counts usable bays | Not deadlock simulation or priority/queue modeling |
| Scoring preferences | Stall/aisle/branch/dead-end/operational terms; optional `prefer_loops` and `prefer_obstacle_clearance` | Not a statutory or capacity simulator |
| Obstacle-aware layout | Lateral offset fan-out when `prefer_obstacle_clearance` / `auto_lateral_offsets_for_obstacles`; optional main-aisle dogleg (single jog) or multi-jog chain (`phase1_main_aisle_multi_jog`, `max_dogleg_jogs`) around staggered obstacles; adaptive dogleg offsets from obstacle envelopes; multi-spine branches under one budget; dual-entrance exit from rear turnaround (incl. strict one-way); branch picks lightly prefer higher clearance | Not general free-form path planning or dense maze routing |
| Parking generation | Supported 90-degree, angled, parallel, and t_end main/branch stalls (t_end as dead-end caps; optional caps on other families; exact swept path insets the far edge by `swept_path_margin`); official connector-side 90-degree, parallel, and angled stalls on same-side U, opposite-cross, and end-loop; catalog modules still offer family alternatives; mixed-segment scoring can weight families and penalize mixed aisle-sides | No general mixed per-stall module optimization or real-site-tuned mix objective |
| Candidate selection | Discrete catalog (`base` official aisles vs `variable` branch/connector skeletons and per-side stall modules), conflict snapshot, greedy shadow selection with solver provenance; optional CP-SAT backend | Default greedy; `selector_backend=cpsat` uses OR-Tools when the optimizer extra is installed and fail-closes to greedy otherwise; stall modules depend on parent aisles and default to 4-stall chunks (`0` = whole strip); promotion still opt-in |
| Multi-spine search | Optional `optimization.layout_search.mode=multi_spine` keeps complete straight/offset/dogleg/multi-jog contexts, evaluates Top-K after the legacy baseline, and may replace official O only when promotion is on and the candidate is fully rebuilt and checked | Default remains `legacy`; Top-K is a cost cap, not a global optimum; only generated templates in the current family are searched; local selector gap is not a site-global gap |
| Preview promotion | Full candidate preview, validation, score comparison, guarded replacement of official layout using catalog `source_id` aisle names | Explicit opt-in; disabled by default; generator branch turnarounds do not leak into the preview base |
| Geometry validation | Containment, scoped hard obstacle/reserved/feature/route conflicts, serving-aisle association | `authority=advisory` or advisory/soft/draw-only/future priority is intentionally non-blocking |
| Traffic graph | Geometric contact for declared links, aisle/stall reachability, entrance/exit path, dead-end turnaround checks; one-way edges + reverse-egress when configured | Static consistency proxy, not traffic simulation or proof of capacity |
| Maneuver validation | Legacy rectangular/L/angled/parallel/t_end proxies plus requested vehicle checks | Exact vehicle templates are perpendicular-90 reverse-in, acute-angled reverse-in, parallel reverse S-curve, and T-end straight reverse from the turnaround/parent court; hard vehicle requests fail closed for unsupported exact templates, including articulated vehicles |
| Road traversal | Opt-in `constraints.road_traversal` (`scope=site_interior`) requires a hard per-retained-stall interior journey: entrance-inner anchor → supported forward road templates → parking/exit motion → exit-inner anchor. Official publication needs `status=passed` and `valid=true` together | Not off-site access, dynamic traffic, simultaneous multi-vehicle, road reversing, or articulated swept templates. Exhausting the finite template set is `no_supported_route_found`, not a proof that no path exists |
| Vehicle validation | Audited outer-front-wheel/rear-axle radius handling, conservative analytic mode, optional exact constant-curvature bicycle path with conservative body envelope; articulated vehicles parse trailer/hitch fields and use combination-fit plus off-tracking analytic checks | No general path search, steering/tyre/dynamics model, exact articulated swept-path template, or independent swept-path certification |
| Quotas | Positive accessible/EV minimums counted on final stalls through explicit classifications or charging features | Project-policy counts only; no statutory ratio, dimensional accessibility, or equipment-placement certification |
| Route usability | Classified accessible stalls must geometrically reach a hard accessible route when `accessible_min` is positive; serving accessible-route pieces must form one contact network and reach declared `connects` destinations; polyline_buffer `min_width` is enforced and declared `max_slope` fail-closes (no elevation); at least one hard fire/access route must reach an entrance when `emergency_access_required` is set; classified EV stalls must reach a placed charging post when `ev_min` is positive and charger geometry exists. Official/preview layouts drop classified stalls that miss those contacts, and may retarget same-family stalls on the contact band to meet quotas, dropping overlapping same-side neighbors when the classified stall is wider or packing a contiguous contact run into two or more classified bays when the frontage fits, or placing perpendicular, parallel, or angled classified bays on empty contact-band pavement along an existing aisle; the selector prefers reachable classified modules | Not ADA certification, surveyed grade, fire-apparatus swept path, electrical equipment design, or a dedicated accessible-aisle synthesizer |
| Operational quality | Phase 5R junction, entrance, route, directionality, narrow-two-way, passing-bay, meeting, merge, and pedestrian-conflict proximity/crossing proxies | Geometry/graph proxies; no arrival, priority, queue, or pedestrian-flow simulation |
| Output | Role-layered DXF (main/jog/branch/exit/passing_bay/…), role-colored SVG with aisle labels, detailed JSON diagnostics, versioned combined engineering decision | No interactive CAD editing or construction-document workflow |

## Decision semantics

Candidate layout promotion is controlled by
`optimization.promote_candidate_layout_preview`. The default is `false`; the
bundled example sets it to `true` to exercise the promotion path. Without the
flag, the candidate layout remains a report/debug preview and cannot replace the
official output geometry.

Operational quality has three modes:

| Mode | Effect |
| --- | --- |
| `score_only` | Adds risks to the report/score and keeps the layout eligible |
| `promotion_gate` | Blocks preview promotion when risk exceeds the configured limit |
| `hard_reject` | Makes an over-limit solve invalid and prevents official artifact publication |

All three modes operate on Phase 5R proxies. `hard_reject` strengthens software
decision semantics; it does not turn those proxies into regulatory validation.

Vehicle and site policies are independent hard gates. When a turning-radius,
swept-path, or reverse-distance check is requested, missing vehicle inputs,
unsupported maneuver templates, or a failed check invalidate that candidate.
Hard site definitions, exact-layout conflicts, required routes, and positive
accessible/EV quotas are also fail-closed. Candidate promotion and final CLI
publication use the same vehicle/site gates; no official output set is replaced
after an invalid decision.

The JSON `engineering_validation` block is the combined v0.3 decision surface.
It records contract/algorithm versions, exact-layout object IDs, active,
advisory, unsupported, and failed rules, authority metadata, per-stall vehicle
evidence, site conflicts, and quota counts. Nested evidence remains available
for audit; this summary does not weaken any hard gate.

`active_exact` means that constant-curvature bicycle poses are integrated
exactly for a supported reverse-in template (perpendicular-90, acute-angled,
parallel S-curve, or T-end straight reverse from the turnaround/parent court).
Collision checking deliberately uses a conservative envelope between sampled
body poses. Articulated vehicles have no exact template; a requested swept-path
check fails closed. `active_conservative` uses radius conversion, vehicle/stall
fit, and a reverse-distance upper bound; for articulated vehicles it also
applies combination length/width fit and steady-state trailer off-tracking
versus aisle width. It does not prove a spatial path or centerline crossing.

## Reliability closure in the current tree

The v0.2 closure and current v0.3 development tree close the highest-risk
software gaps that were present in the original prototype:

- declared parent/connector/entrance relationships no longer create usable graph
  edges when the corresponding generated geometry is physically disconnected;
- an operational `hard_reject`, invalid graph, invalid maneuver/vehicle/site
  result, unmet quota, or empty layout is rejected before any official artifact
  is published;
- DXF, SVG, and JSON are committed as one output set, with existing artifacts
  preserved if writing fails;
- promoted candidates are rebuilt with official IDs, revalidated, rescored, and
  reflected consistently in snapshots and provenance;
- user-controlled SVG text is XML-escaped; and
- lint, branch coverage, package build, and installed-wheel execution are
  represented in the Python 3.10/3.12 CI baseline.

The v0.3 branch adds:

- audited turning-radius reference conversion and fail-closed vehicle inputs;
- optional perpendicular-90, acute-angled, parallel S-curve, and T-end
  low-speed bicycle/template validation with conservative swept-envelope
  collision checks;
- scoped hard exclusions, rule authority/priority reporting, required-route
  definition checks, and accessible/EV quota enforcement; and
- MIT licensing, a packaged JSON Schema, and synthetic representative
  real-site-shaped fixtures.

These changes expand engineering checks inside a narrow documented template.
They do not establish statutory compliance or field-validated vehicle access.

## Release posture

The project is suitable for:

- algorithm experiments and regression fixtures;
- explainable comparison of supported template layouts;
- project-policy screening with explicitly declared vehicle and exclusion data;
- DXF/SVG review by a knowledgeable human; and
- developing stronger geometry, vehicle, and optimization layers.

It is not suitable for unattended production design, permit/code claims,
construction output, or safety-critical acceptance. Those claims remain blocked
by general vehicle-path coverage, permissioned real-site/human-CAD comparison,
regional-rule, and product-integration work in the [roadmap](roadmap.md).

## Verification baseline

The repository baseline is intentionally reproducible rather than tied to a
stale test-count claim:

```powershell
ruff check .
python -m pytest --cov=openparkcad --cov-report=term-missing
python -m build
```

CI executes the same lint/test/build path and runs the installed wheel against
the bundled example on Python 3.10 and 3.12. Tagging guidance and smoke demos
are listed in [v0_3_release_checklist.md](v0_3_release_checklist.md).

## v0.3 acceptance boundary

The vehicle-validity work is governed by the
[v0.3 vehicle and enforced-constraint contract](v0.3_vehicle_and_constraints.md).
The implemented code covers the contract's narrow vehicle and project-policy
core, but a constraint is active only when the report says so and records the
result for the exact official layout. Accepted or drawn input vocabulary is not
evidence of enforcement.

The representative inputs under `tests/fixtures/v0_3/` are synthetic regression
assets shaped like common real sites. They are not customer surveys or completed
field validation, and they do not replace the human CAD comparison gate.
