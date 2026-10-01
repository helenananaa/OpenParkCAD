# CAD import (stage 二) acceptance

Date: 2026-09-03. Import version: `cad-import-1`. Transform: `cad-transform-1`.

## What shipped

- `import-dxf` converts a support-range modelspace DXF plus mapping and project
  defaults into SiteSpec JSON. It does not solve.
- Mapping layers are explicit. Entrances are directed LINE entities keyed by
  real DXF handles. Vehicle/stall sizes in defaults stay in metres.
- Units are metres or millimetres. `local = scale * source - origin_m` with
  origin at the boundary AABB minimum corner. Round-trip target is `1e-6` m.
- Unsupported ARC/SPLINE/bulge/INSERT, open/self-intersecting boundaries, unit
  conflict, and unknown handles fail closed with entity diagnostics.
- Source DXF SHA-256 is unchanged. `--source-coordinates` restores official DXF
  to source units. SVG stays in local metres and records source handles.

## Evidence

- `tests/test_coordinate_transform.py`, `tests/test_cad_import.py`,
  `tests/test_cad_import_cli.py`, `tests/test_cad_roundtrip.py` (C-T01–C-T09).
- Two-shot CLI: import `tests/fixtures/cad/rectangle_mm.dxf` then solve produced
  14 stalls; source DXF hash unchanged.

## Remaining limits

- No licensed real CAD is in the repository. This stage is synthetic
  round-trip only and is not a field-effect claim. See
  [the real-site case protocol](real_site_case_protocol.md).
- No holes, multi-boundary, curves, blocks, or units other than m/mm.
