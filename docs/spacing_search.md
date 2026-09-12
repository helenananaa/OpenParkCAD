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

Final full regression, installed-wheel and paired corpus results are pending.
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
