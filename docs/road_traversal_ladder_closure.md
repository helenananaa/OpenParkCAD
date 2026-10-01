# Parallel-ladder journey closure (2026-09-08)

Follow-up: the 2026-09-12 full regression passes **552 tests** with **84.07%**
combined coverage, and a new isolated-wheel run reproduces 20/20 journeys.
This supersedes the earlier incomplete full-suite verification described below,
without expanding the supported fixture boundary. See
[the integration/qualification record](v0_5_closeout_20260912.md).

This is a bounded synthetic acceptance slice, not completion of the v0.5 N9
matrix, field validation, or a general vehicle-path planner.

## Supported positive case

`tests/fixtures/v0_5/parallel_ladder_traversable_site.json` produces an official
`parallel_ladder` layout with two parking aisles and 20 retained stalls. Every
stall passes entrance → forward road motions → reverse parking → forward exit
parking motion → exit, including final pose continuity and swept-envelope
rechecks. CLI publication passes the graph, maneuver, site, and engineering
checks and writes DXF, SVG, report, review bundle, and delivery manifest.

The case uses **3.5 × 5.5 m bays**, 6 m aisles, and the unchanged N-T01 passenger
car (4.8 × 1.9 m, 2.8 m wheelbase, 5.5 m outer-front-wheel turning radius,
0.3 m swept margin). The original 2.5 × 5 m N-T01 fixture is unchanged. Dense
neighboring bays can still block the finite parking template; this case is not
a density improvement claim. No failed stalls are silently ignored by the road
validator, and the positive test checks the entire retained layout.

## Implementation

- Orthogonal connections can use a forward straight/arc/straight with analytically
  solved tangent lengths. Radius and collision checks are unchanged.
- Arc and dogleg endpoints must meet the same tolerance as journey rechecks;
  a centimetre-scale gap cannot become a graph edge.
- The pose graph includes minimum-radius tangent stations at orthogonal road
  intersections, and filters physically invalid road stations. Ladder turns use
  those stations and adjacent straight edges instead of enumerating redundant
  long approach/departure edges.
- Only when road traversal is requested, ladder generation reserves an entry
  approach using vehicle radius and front/rear reach, and keeps parking modules
  away from junctions. The inset cross aisle connects through the actual throat;
  it no longer declares a physically nonexistent direct entrance connection.
- Requested report and identity include `template_revision=orthogonal-tangents-2`.
  Default/unrequested report identities and generation geometry remain on their
  previous path. Time-budget exhaustion remains `incomplete`.

## Reproduction

```powershell
.venv/Scripts/python.exe -m pytest tests/test_ladder_journey_closure.py tests/test_road_transitions.py tests/test_parallel_ladder_road_traversal.py -q
.venv/Scripts/python.exe -m openparkcad solve tests/fixtures/v0_5/parallel_ladder_traversable_site.json --out output/traversal-closure/layout.dxf --preview output/traversal-closure/layout.svg --report output/traversal-closure/official-report.json --review-bundle output/traversal-closure/review.json --delivery-manifest output/traversal-closure/manifest.json
```

The local CLI run validated 20/20 journeys in 17.81 seconds of road validation
(60-second configured budget). This is one run, not a p95 or whole-solve latency
claim. Report and review identities match; output hashes are checked against the
manifest. Summary evidence is in `verification/ladder_journey_closure_20260908.json`.

Remaining work: dense-bay parking motions, more junction and entrance layouts,
the final-revision full matrix, main Schema/project integration, real survey
comparisons, and interactive editing. The existing N9 matrix remains historical.

## Final local verification

Ruff and sdist/wheel builds pass. Full regression: 528 passed and one example-
manifest failure, fixed by putting the new case in the v0.5 fixture directory
instead of extending the frozen example corpus. Final targeted rerun: 4 passed
(including the corrected manifest check, relocated positive case, revision
identity, and dense-neighbor rejection). Together these verify 530 distinct
tests; branch-inclusive coverage is 83.88% against an 80% threshold.

A fresh venv outside the repository installed the wheel, then ran `python -I`
from that external directory: official 20/20 journeys passed and the layout
identity matched the source run. Existing user changes are included in this
working-tree build; no commit, push, tag, or release was performed.
