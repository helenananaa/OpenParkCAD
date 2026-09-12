# Bounded occupied-stall conflict repair

Status: first implementation; frozen synthetic corpus qualification is in
progress. No human CAD reference is available. This does not establish a
site-global optimum, a density improvement, or general vehicle-path planning.

## Supported computation

For a generated `parallel_ladder`, the engine diagnoses parking-motion envelope
collisions against the exact other occupied stall polygons. It records concrete
blocking stall IDs separately from hard obstacles, learns pairwise conflicts,
selects a compatible subset, and reruns diagnosis plus complete journey and
engineering checks. CP-SAT maximizes retained stall count in that learned graph;
greedy uses a deterministic low-degree heuristic. Solver provenance and bounds
refer only to these fixed-template conflicts.

The loop does not shrink vehicles, reduce margins, change road or stall geometry,
ignore occupied neighbors, or turn a failed check into a pass. Every removed
stall is listed with retained conflicting objects. The caller's minimum count,
locks, quotas and all other active hard checks still apply. Static-obstacle,
unsupported-motion and remaining full-journey failures are not automatically
"fixed" by deleting arbitrary failed stalls.

The initial development rectangle keeps the original 2.5 × 5 m bays. All 30
generated stalls fail their parking motions when neighbors are occupied, while
all 30 pass in isolation. The learned graph has 45 conflicts; its CP-SAT optimum
retains 12. A source run verifies all 12 complete journeys. This trades 18 stalls
for access; it does not claim a capacity gain over a valid 30-stall design.

## Opt-in input

```json
{
  "constraints": {
    "road_traversal": {"enabled": true, "scope": "site_interior", "time_budget_seconds": 60}
  },
  "optimization": {
    "selector_backend": "cpsat",
    "promote_candidate_layout_preview": true,
    "road_network": {
      "enabled": true,
      "families": ["legacy", "parallel_ladder"],
      "refinement_budget_seconds": 60,
      "repair": {
        "enabled": true,
        "max_rounds": 3,
        "time_budget_seconds": 60,
        "min_retained_stalls": 10
      }
    }
  }
}
```

Repair defaults to disabled. Defaults when enabled: three diagnosis rounds,
30 seconds, and at least one retained stall. Explicit nulls, unknown options and
invalid numeric types are rejected. Repair requires requested interior road
traversal and a supported rigid vehicle. Its effective deadline is the earlier
of its own budget and the outer search deadline; final road checking also obeys
the road-validation budget. A late or incomplete result cannot be promoted.
These are cooperative in-process budgets, not preemption of arbitrary geometry
operations. The corpus runner also imposes a worker-process hard timeout.

Repair records live under each `layout_search.road_network_search.skeletons[]`
row, alongside original module-selector provenance and final geometry. The
repair graph optimizer has its own provenance; it does not replace the module
selection history. Project and review snapshots retain the search records.

## Reproduction and evidence

```powershell
.venv/Scripts/python.exe -m pytest tests/test_ladder_repair.py tests/test_ladder_journey_closure.py tests/test_road_network_project.py -q
.venv/Scripts/python.exe -m openparkcad solve tests/fixtures/dense_repair/rectangle.json --out output/dense-repair/layout.dxf --preview output/dense-repair/layout.svg --report output/dense-repair/report.json --review-bundle output/dense-repair/review.json --delivery-manifest output/dense-repair/manifest.json
# Requires a committed, clean source tree and a fresh output directory:
.venv/Scripts/python.exe tools/benchmark_dense_repair.py --out output/dense-repair-corpus
```

The frozen corpus contains a development rectangle, deeper rectangle, L-shaped
case and a negative retention-floor case, all synthetic. Each is evaluated with
repair off/on three times, with the same fixed ladder generation and 60-second
validation budget. The fixture manifest includes source hashes. "Accepted
stalls" means the count of a layout that passes all checks; a rejected layout
can still contain drawn stalls. Per-stall road coverage is reported separately.
These comparisons do not replace a whole-site search benchmark.

Tests cover occupied-neighbor attribution, static obstacles, incompatible locks,
retention floor, expired time/round budgets, missing optimizer fallback, invalid
input, default-off behavior, and complete retained-stall journey validation.

The pre-existing v0.5 1152-cell matrix remains pinned to detached `9cca1da` and
its installed wheel. It is separate evidence and cannot qualify this new repair
implementation. Current runs share the host; timings are descriptive rather
than isolated-host latency claims.

## Next boundaries

This first loop only changes which fixed-geometry stalls are kept. Repositioning
roads or stalls, using alternative parking motions to avoid dropping stalls,
repairing road-junction failures, and testing against human layouts remain later
work. A learned conflict is evidence for the tested motion, not proof that every
possible motion between those stalls must fail.
