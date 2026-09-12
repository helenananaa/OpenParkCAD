# Ladder spacing search

This opt-in step changes road centerline spacing and repacks perpendicular stalls
at their original dimensions. It then runs occupied-stall repair and all final
vehicle/road/site/engineering checks. It extends the earlier fixed-geometry
[delete-only repair](dense_repair.md); it is not arbitrary road-network design.

## Candidate construction

The original shortlisted ladder is always first. Alternatives add an
`inter_aisle_gap_m` to `aisle_width + 2 * stall_depth`, and a `stall_gap_m` between
adjacent stalls in a row. Road widths, stall dimensions, vehicles, margins,
quotas and locks are unchanged. Changed roads are rebuilt and revalidated;
geometry that no longer fits the site is rejected.

The default candidate gaps are zero and `2 * swept_path_margin + 0.05` metres.
These are proposals to test, not assumed clearances or statutory rules. With the
current passenger car's 0.3 m margin, the nonzero proposal is 0.65 m. The combined
gap is tried before single-gap alternatives. Fixed custom arrays are supported.

Road skeleton IDs continue to identify road geometry. A separate spacing
candidate ID distinguishes different stall arrangements on identical roads.
Reports and review/project snapshots preserve that distinction; unchanged locked
roads retain their project-object identity even when other roads are adjusted.

By default the search can stop after the first fully validated score improvement.
Remaining candidates are explicitly not evaluated; they are not failures or
passes. Set `stop_after_improvement` false to continue within the outer count and
time budgets. This is a best-found search, not a global-optimum certificate.

## Input

```json
{
  "constraints": {
    "road_traversal": {"enabled": true, "time_budget_seconds": 120}
  },
  "optimization": {
    "selector_backend": "cpsat",
    "promote_candidate_layout_preview": true,
    "road_network": {
      "enabled": true,
      "families": ["legacy", "parallel_ladder"],
      "max_full_evaluations": 4,
      "refinement_budget_seconds": 240,
      "repair": {"enabled": true, "time_budget_seconds": 120, "min_retained_stalls": 10},
      "spacing_search": {
        "enabled": true,
        "inter_aisle_gaps_m": [0, 0.65],
        "stall_gaps_m": [0, 0.65],
        "max_variants": 4,
        "stop_after_improvement": true
      }
    }
  }
}
```

Spacing is disabled by default. When enabled, `max_variants` defaults to four per
shortlisted base. Gap arrays accept one to eight finite, nonnegative numbers;
zero is included automatically to retain the original arrangement. Nulls,
booleans used as numbers, negative/nonfinite gaps and unknown options are rejected.
The feature does not enlarge the caller's budgets automatically. A small outer
budget may retain an earlier valid result while new candidates remain incomplete.

## Verification protocol

The source smoke result on the rectangle is 12 valid stalls for delete-only
repair and 24/24 complete journeys after spacing. All bays remain 2.5 × 5 m.
The module/source-targeted checks pass. A rotation/translation check also exposed
and repaired an existing axis-normalization bug: an explicit west-facing 180°
entrance was previously folded into east-facing 0°. The actual inward bearing is
now retained; the rotated site passes all 24 journeys.

Full regression and the paired synthetic corpus are complete; results follow.
Frozen cases are in `tests/fixtures/spacing_search/manifest.json`: rectangle,
deeper rectangle, L-shaped site, and minimum-retention rejection. Each runs
delete-only and spacing search three times. Both modes receive the same
240-second outer and 120-second road/repair budgets, with a 300-second worker
hard timeout. These are this study's resource limits; they do not replace or
claim to pass the old N9 matrix's 180-second ceiling. Host timings are descriptive
because other work may be active. No human CAD reference is available.

```powershell
.venv/Scripts/python.exe -m pytest tests/test_ladder_spacing.py -q
.venv/Scripts/python.exe -m openparkcad solve tests/fixtures/spacing_search/rectangle.json --out output/spacing/layout.dxf --preview output/spacing/layout.svg --report output/spacing/report.json --review-bundle output/spacing/review.json --delivery-manifest output/spacing/manifest.json
# Frozen clean source and a fresh output directory:
.venv/Scripts/python.exe tools/benchmark_spacing_search.py --out output/spacing-corpus
```

The old N9 matrix and earlier delete-only corpus remain attached to their own
commits and installed environments. Neither qualifies this implementation.
Alternative parking motions, arbitrary geometry optimization, traffic dynamics,
surveyed sites and human-design comparisons remain outside this step.

## 2026-09-12 qualification

Computation commit: `78ef0ce`; corpus runner: `aab9a8a`; viewer evidence-scoping
fix: `2c7a9ea`. Full regression: **592 passed in 1402.44 seconds**, combined
coverage **84.25%**, unchanged minimum **80%**. Ruff and package builds pass.
Targeted source tests covered candidate identity, locks, reopens, promotion off,
expired budgets, infeasible gaps, invalid input and complete rotated-site journeys.

| Fixed synthetic case | Delete-only repair | Spacing search | Repetition |
| --- | ---: | ---: | --- |
| Rectangle | 12/12 journeys | 24/24 journeys | 3/3 stable |
| Deeper rectangle | 20/20 journeys | 40/40 journeys | 3/3 stable |
| L-shaped site | 27/27 journeys | 56/56 journeys | 3/3 stable |
| Rectangle requiring at least 30 retained stalls | Rejected | Rejected | 3/3 |

The final run contains **24 cells, zero worker errors and zero worker timeouts**.
Each mode/case's repeated layout identity is stable. Median search times for
delete-only / spacing were 15.38 / 40.37 s, 26.00 / 73.40 s, and 49.26 / 131.32 s
for the three positive cases. This improvement costs more computation; timings
are shared-host observations, not isolated performance guarantees. All modes
used the same study budgets described above. No dimensions or vehicle margins
were reduced, and these counts are not real-site capacity claims.

The initial runner stopped after a baseline solve because optional classification
metadata was missing. The runner was fixed and all 24 cells were rerun in a new
`corpus-final` directory; the incomplete initial directory is excluded.

Browser verification exposed another issue: previews and unassessed candidates
could display the official candidate's trajectories. Trajectories and failure
details now remain with their owner, playback is disabled without corresponding
trajectory data, and switching candidates stops an active animation. Unassessed
stall counts are displayed as not evaluated rather than zero. Real browser checks
observed 24 official trajectories and zero foreign trajectories on previews or
unassessed candidates, with no console errors.

The final wheel was installed outside the repository with `pip --target`; its
Python modules match the tested computation code and its rendered viewer uses the
fixed packaged assets. The solve CLI produced a consistent 24-stall DXF/SVG/report/
review/manifest set. Core and browser verification of the installed package are
recorded in `installed-final-wheel.json` and `browser-check.json`.

Evidence directory: `output/verification/spacing-search-20260912/`.
The authoritative paired results are `corpus-final/summary.json` and its cell
records. The earlier `corpus/` and invalid exploratory probe are not acceptance
evidence. A compact checked-in receipt is
[spacing_search_20260912.json](verification/spacing_search_20260912.json).
