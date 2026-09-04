# Topology iteration: through-corridor family

Frozen case (synthetic, not a licensed site): `examples/dual_entrance_site.json`
with `constraints.road_traversal.enabled=true`.

## Problem

Without the new family, enabling road traversal rejects every generated
candidate (finite templates cannot join the turnaround/exit package), so the
solver publishes an empty layout (`no_layout_to_validate`). A hand-built
straight entry-to-exit corridor with 90° stalls does produce complete journeys.

## Family

`optimization.enable_through_corridor=true` adds a straight spine from the
enter-capable entrance to a distinct exit-capable entrance, places the active
90° stall family on that spine, and runs the same finalize/road checks.

Baseline templates remain available. The default backend and search mode are
unchanged.

## Exit

The frozen dual-entrance site with the family enabled yields a valid official
layout with road `status=passed`, `valid=true`, and `stall_coverage==stall_count`.
Same-input comparison reports stall counts and road status with the family off
versus on. The family does not publish an invalid layout merely because it has
more stalls than the rejected baseline.
