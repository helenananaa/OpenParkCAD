# OpenParkCAD

OpenParkCAD is a Python-first experimental kernel for generating parking layouts
inside irregular land parcels. The package version is `0.3.0`; the
[current capability matrix](docs/current_status.md) and each generated report,
not the version number alone, define which checks are active.

This is a planning and algorithm-development tool. It is **not** a code-compliance
checker, a construction-design system, or a substitute for independent vehicle,
fire-access, accessibility, and local-authority review.

## Current capability

The solver can currently:

- read the documented JSON site model and separate active fields from parsed,
  future-facing fields;
- generate a straight entrance-connected main aisle (two-way, one-way, or
  opt-in narrow two-way with synthesized passing bays), end turnaround,
  optional far-end exit aisle for a second exit-capable entrance, perpendicular
  branches, same-side U connectors, opposite-side short cross junctions,
  opposite-side outer end-loops, main-aisle lateral-offset candidates
  (auto-enabled when hard obstacles are present), and optional main-aisle dogleg
  bypass (centerline front + one or more lateral jogs + adaptive offset spines,
  with score-positive branches under one budget, and dual entrance exit attach
  from the rear turnaround with optional L-elbow corridors, including strict
  one-way) around mid-site obstacles;
- generate 90-degree, angled, parallel, and t_end stalls on supported
  main/branch aisles (t_end as dead-end end-caps), plus optional t_end caps on
  other families via `optimization.enable_t_end_caps`, and conservative
  90-degree stalls on connectors;
- enforce hard scoped clearances for polygonal obstacles, reserved areas,
  supported site features, and pedestrian/fire/access-route geometry;
- validate generated aisle/stall reachability with a traffic graph, including
  direction-aware one-way edges and optional reverse-egress arcs;
- apply rectangular and optional L-shaped maneuver-clearance **proxies**, plus a
  traffic-side rectangular proxy for parallel stalls;
- when requested, resolve a design vehicle's turning radius to the rear-axle
  path and run conservative analytic checks for perpendicular-90, angled,
  parallel, and T-end stalls, or the supported perpendicular-90, acute-angled
  reverse-in, parallel reverse S-curve, and T-end reverse-in swept-path
  templates;
- when `constraints.road_traversal.enabled` is true, require a site-interior
  entrance-to-exit journey for every retained stall using finite forward road
  templates plus the existing parking motions (`status=passed` and `valid=true`
  together); missing, failed, unsupported, or incomplete evidence cannot be
  published as passed;
- enforce caller-declared accessible/EV minimum counts from explicit stall-type
  classifications;
- compare stall types and main/branch stall assignments with an explainable
  score, and report a discrete candidate catalog (official aisles vs
  greedy- or optional CP-SAT-selected branch/connector skeletons) with
  selector provenance;
- create a candidate snapshot, conflict matrix, heuristic shadow selection,
  validated preview layout, and guarded preview promotion;
- report Phase 5R operational-risk **proxies** for junctions, entrance throats,
  routes, directionality, narrow two-way aisles, passing bays, meeting gaps, and
  junction merges; and
- export layered DXF, SVG preview, and a detailed JSON report.

The phase label describes implementation history, not product readiness. See
[the current capability matrix](docs/current_status.md) for the precise trust
boundary, [the roadmap](docs/roadmap.md) for release priorities, the
[examples catalog](docs/examples_catalog.md) for feature demos, and the
[v0.3 release checklist](docs/v0_3_release_checklist.md) before tagging.

## Setup

OpenParkCAD requires Python 3.10 or newer. Conda is not required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Runtime installation does not require Matplotlib or OR-Tools. The default
selector is greedy and does not import OR-Tools. The optimizer extra enables
the optional `optimization.selector_backend=cpsat` shadow selector:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,optimizer]"
```

## Quick start

```powershell
.\.venv\Scripts\openparkcad.exe solve examples/phase0_site.json `
  --out output/layout.dxf `
  --preview output/layout.svg `
  --report output/report.json
```

The equivalent module entry point is `python -m openparkcad solve ...`.

Convert a support-range DXF without solving:

```powershell
.\.venv\Scripts\python.exe -m openparkcad import-dxf tests/fixtures/cad/rectangle_mm.dxf `
  --mapping tests/fixtures/cad/rectangle_mm.mapping.json `
  --defaults tests/fixtures/cad/project_defaults.json `
  --out site.json --diagnostics import.json
.\.venv\Scripts\python.exe -m openparkcad solve site.json `
  --out layout.dxf --preview layout.svg --report report.json --source-coordinates
```

See [the CAD import contract](docs/cad_import_contract.md). Source DXF bytes are never modified. `--source-coordinates` writes the official DXF in recorded source units.

Write a frozen comparison bundle and an offline viewer:

```powershell
.\.venv\Scripts\python.exe -m openparkcad solve examples/multi_spine_comparison_site.json `
  --out layout.dxf --preview layout.svg --report report.json --review-bundle review-bundle.json
.\.venv\Scripts\python.exe -m openparkcad view review-bundle.json --out review.html
```

The JSON report includes the selected layout and score, attempted candidates,
input diagnostics, traffic-graph validation, maneuver/vehicle validation,
`site_constraint_validation`, the versioned combined `engineering_validation`,
`candidate_snapshot`, `candidate_network_preview`, `candidate_layout_preview`,
`candidate_layout_promotion`, Phase 5R `operational_quality`, and
`layout_search` (`layout-search-1`). Default `layout_search.mode` is `legacy`.
Set `mode=multi_spine` to compare complete spine templates (Top-K limited);
official DXF/SVG still change only when `promote_candidate_layout_preview` is
true.

## Selection and promotion

The official layout starts from a conservative template:

```text
entry-capable entrance -> straight two-way main aisle -> end turnaround
                                  |
                                  +-> optional perpendicular branches
                                  +-> optional same-side U connector
```

Heading/entrance-offset candidates and supported stall assignments are scored.
Branch and connector candidates are selected with deterministic heuristics, not
global mathematical optimization.

The candidate layout is always reported as a preview. Replacing the official
DXF/SVG/report layout is opt-in and defaults to disabled:

```json
{
  "optimization": {
    "promote_candidate_layout_preview": true
  }
}
```

Promotion occurs only when the preview passes its configured geometry,
maneuver/vehicle, site-constraint/quota, graph, and operational gates and is not
worse under the current score.
The bundled example deliberately enables promotion so the preview path remains
exercised; other inputs must opt in explicitly.

## Validation modes and trust boundary

Interior road-traversal checks are opt-in under `constraints.road_traversal`:

```json
{
  "constraints": {
    "road_traversal": {
      "enabled": true,
      "scope": "site_interior",
      "time_budget_seconds": 10.0
    }
  }
}
```

Unrequested solves keep the previous validity and official trio. `solve` accepts
optional `--diagnostics` to write independent rejection evidence when no official
layout is published. See [the road-traversal contract](docs/road_traversal_contract.md).

Vehicle-level checks are opt-in under `constraints.maneuvering`:

- `require_turning_radius_check: true` with swept path disabled uses
  `active_conservative` mode. It audits the input-radius reference, converts an
  outer-front-wheel radius to a rear-axle-center radius, checks vehicle/stall
  fit, and applies a conservative reverse-distance upper bound.
- `require_swept_path_check: true` uses `active_exact` mode for supported
  perpendicular-90 reverse-in, acute-angled reverse-in, parallel reverse
  S-curve, and T-end reverse-in stalls. It integrates a constant-curvature
  low-speed bicycle path exactly, then checks a conservative sampled body
  envelope against the drivable area, site boundary, centerline policy, and
  hard exclusions.

Both modes require auditable vehicle geometry. In particular,
`outer_front_wheel` radius input requires wheelbase and track width. A requested
check fails closed when required parameters are missing, the stall/template is
unsupported, or the check fails; the older proxy cannot silently turn that into
a pass. `active_exact` names the path integration mode, not physical-world
exactness: the current templates are a reverse constant-radius arc (90 degrees
or the acute stall angle) plus a straight, a straight reverse from the T-end
court, or two opposite reverse arcs for parallel parking, with exit represented
as its time reverse. It does not
model steering transients, tyres, dynamics, or driver variation.

Articulated vehicles (`configuration: "articulated"` or a nested `trailer`)
never use those bicycle stall templates. A requested swept-path check fails
closed. Conservative analytic mode then audits combination fit, trailer
off-tracking versus aisle width, and a tractor-arc-plus-trailer reverse bound.

Hard geometry uses declared `affects`, `priority`, and `authority` semantics.
Supported obstacle/reserved/feature/route geometry can block stalls, aisles,
and/or swept paths. Advisory, soft, draw-only, and future declarations remain
non-blocking. Accessible/EV quotas are project minimums counted on the final
layout; they are not built-in statutory ratios or accessibility certification.

Operational-quality behavior remains separately configurable.

`optimization.operational_quality_mode` supports:

- `score_only` (default): report and score Phase 5Q risks without rejecting the
  official layout;
- `promotion_gate`: keep the risks soft for the current layout but block preview
  promotion when the configured risk limit is exceeded; and
- `hard_reject`: treat a configured operational-risk limit violation as an
  invalid solve and do not publish official output artifacts.

Set `optimization.operational_max_risk_score` to establish the limit used by the
gate/reject modes.

Operational checks do not simulate traffic. Declared aisle links must make
geometric contact before they become graph edges, but the traffic graph is still a static
consistency and reachability check, and Phase 5Q uses graph/geometry indicators.
A reported pass therefore means “passes the active checks for the supported
template and declared project inputs,” not “every real vehicle can traverse the
design” or “the design complies with a code.”

## Input model

Use [examples/phase0_site.json](examples/phase0_site.json) as the executable
example and [docs/input_model.md](docs/input_model.md) as the field reference.
All currently supported dimensions are interpreted as metres.

The model intentionally accepts some fields ahead of enforcement so reports can
expose unsupported requirements instead of silently discarding them. Hard
pedestrian/fire geometry follows its declared exclusion scopes. Requested route
checks also audit supported contact, connectivity, and accessible polyline-width
rules; declared slope requirements fail closed without elevation data. These
checks do not establish emergency-apparatus access or regulatory compliance.

## Not implemented

The current release does not provide:

- arbitrary road-network synthesis, general intersections, multiple coordinated
  entrances/exits, or a general loop optimizer;
- general vehicle-path search, multi-point maneuvers, steering transients,
  tyre/dynamic behavior, exact articulated swept paths, or emergency-vehicle
  models;
- dynamic traffic, arrival/priority simulation, or capacity/queue analysis;
- general pedestrian/fire-route planning, statutory accessible-stall dimensional
  certification, EV equipment design, slope/drainage analysis, or built-in
  local-code profiles;
- global layout optimization beyond the discrete shadow candidate catalog;
- DXF/site-survey import, interactive editing, or a graphical application; or
- certification that generated drawings are construction-ready or compliant.

Connector-side T-end stalls and non-90-degree perpendicular stalls remain
outside the active generation path. Supported parallel and T-end stalls can
use the requested vehicle templates described above.

## Development

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest --cov=openparkcad --cov-report=term-missing
.\.venv\Scripts\python.exe -m build
```

CI runs lint, tests with branch coverage, and an installed-wheel CLI smoke test
on Python 3.10 and 3.12.

## Design documents

- [Documentation index and workspace layout](docs/README.md)
- [Changelog](CHANGELOG.md)
- [Current status and capability matrix](docs/current_status.md)
- [Roadmap](docs/roadmap.md)
- [Next five stages execution plan](docs/next_stages_execution_plan.md) (planned: road traversal, CAD input, interactive design, broader layouts, delivery)
- [v0.4 benchmark and multi-spine execution plan](docs/v0_4_multi_spine_execution_plan.md) (E0–E9 implemented; §12 later)
- [v0.4 multi-spine acceptance results](docs/v0_4_multi_spine_acceptance.md) (full regression, 240 benchmark runs, installed wheel, CI)
- [Input model](docs/input_model.md)
- [v0.3 vehicle and enforced-constraint contract](docs/v0.3_vehicle_and_constraints.md)
- [Algorithm design discussion](docs/algorithm_design_discussion.md)
- [Detailed phased implementation history](docs/phased_plan.md)

## License

OpenParkCAD is available under the [MIT License](LICENSE).
