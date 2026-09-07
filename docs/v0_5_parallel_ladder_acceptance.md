# v0.5 parallel-ladder acceptance (synthetic)

Date: 2026-09-04. This records N0–N8 runtime behavior. It is not a licensed
real-site, code-compliance, or constructability claim.

## Enabled behavior

With `optimization.road_network.enabled=true` and
`families=["legacy","parallel_ladder"]`:

- the current official baseline B is still built first;
- `parallel_ladder` skeletons are generated and prefiltered independently;
- 90° modules are selected inside each skeleton;
- promotion still requires `optimization.promote_candidate_layout_preview`.

Default inputs (no `road_network` block) keep the N0 official path.

## Explicitly unsupported

- `allow_one_way_loop`
- `boundary_loop`, `obstacle_route`, `multi_entrance_network`
- treating selector `optimal`/gap as site-global
- public Schema enum freeze in `schema/openparkcad-input.schema.json` (that file
  is user-owned and was not modified this round; runtime still fail-closes
  unknown families)

## Evidence

See `docs/v0_5_parallel_ladder_execution_record.md`,
`docs/verification/v0_5_20260904.json`, and the §16.2 matrix receipt
`docs/verification/v0_5_n9_matrix.json` (1152 sequential synthetic cells;
timeouts hit the frozen 180s N0 ceiling and were not relaxed).
