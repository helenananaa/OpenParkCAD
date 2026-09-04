# CAD import contract (cad-import-mapping-1)

First-edition DXF import converts a support-range drawing into existing
SiteSpec JSON. It does not solve. Source file bytes are never modified.

## Supported source

- Modelspace only.
- One closed straight LWPOLYLINE or 2D POLYLINE site boundary.
- Obstacle polygons of the same type on mapped obstacle layers.
- Entrances as directed LINE entities on the mapped entrance layer: start point
  is the entrance centre; the line direction is the inward heading. Line length
  is not the entrance width.
- Units: metres or millimetres. Mapping `source_units` is compared with the DXF
  INSUNITS declaration.

## Mapping and defaults

Layer roles come only from an explicit mapping file (`cad-import-mapping-1`).
Vehicle, stall, quota, and solve policy come from a project defaults JSON
whose sizes are already in metres and are not scaled again.

Entrance id, width (metres), and enter/exit flags are recorded per DXF handle
in `entrance_entities`. Handles must match generated fixture entities.

## Transform

```
local = scale * source - origin_m
source = (local + origin_m) / scale
```

`origin_m` is the axis-aligned minimum corner of the imported boundary in
metres. First edition does not rotate, mirror, or assign an EPSG. Numeric
round-trip target is `1e-6` m.

## Rejected (not silently fixed)

ARC, SPLINE, bulge, INSERT/BLOCK, non-planar/non-zero elevation, open or
self-intersecting boundaries, multiple outer boundaries, and holes. Unit
missing/conflict, unknown entrance handle, and an entrance heading that cannot
be uniquely confirmed as inward.

Non-role layers may be listed as ignored. A critical-layer unsupported entity
prevents emitting a complete valid site.
