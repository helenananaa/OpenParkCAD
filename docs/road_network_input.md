# optimization.road_network (v0.5 live field)

This is the runtime input for opt-in `parallel_ladder`. It is consumed from
`SiteSpec.optimization["road_network"]` by `generate_layout`. Illegal values
fail-closed (`ValueError`); they do not silently fall back to legacy.

The user-owned `schema/openparkcad-input.schema.json` was not edited this round.
The machine-readable fragment is `schema/road-network.schema.json`.

## Shape

```json
"optimization": {
  "road_network": {
    "enabled": true,
    "families": ["legacy", "parallel_ladder"],
    "max_skeletons": 16,
    "dominant_axis_count": 2,
    "max_parallel_aisles": 6,
    "max_full_evaluations": 8,
    "refinement_budget_seconds": 20.0,
    "cross_aisle_policy": "both_ends",
    "allow_one_way_loop": false
  }
}
```

- Default (field absent or `enabled: false`): N0 official path; no `road_network_search` block.
- `families` must be strings from `{legacy, parallel_ladder}`. Unknown names fail-closed.
- Integers (`max_skeletons`, `dominant_axis_count`, `max_parallel_aisles`, `max_full_evaluations`) reject booleans, floats, strings, and values `< 1`.
- `refinement_budget_seconds` must be a finite number `> 0`. `NaN` / `Inf` fail-closed.
- `allow_one_way_loop: true` remains unsupported; keep `false`.
- `cross_aisle_policy` is `entry_end` or `both_ends`.
- Official replacement still requires `promote_candidate_layout_preview`. Promoted official `generation_mode` is `parallel_ladder`.
- Unfinished/budget-exhausted skeletons are `incomplete`, not infeasible, and cannot promote.

Shipped example: `examples/parallel_ladder_rect_site.json`.
