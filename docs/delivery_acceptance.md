# Delivery scene (stage 五) acceptance

Date: 2026-09-03. Profile: `private_surface_lot_v1`. Manifest: `delivery-manifest-1`.

## Scene

Private surface lot, rigid passenger car, JSON or support-range DXF, engineering
review drawings. Synthetic trial only; no licensed real site.

## What shipped

- Rule profile lists only software-executed checks and names unsupported
  statutory/slope/fire-apparatus/permit items.
- Delivery manifest traces input digest, package version, rule profile,
  official layout identity, and output SHA-256 hashes.
- Human review cannot be auto-marked approved.
- Unsupported project versions raise rather than guess-migrate.
- Trial: import DXF → solve → review bundle → viewer → delivery manifest
  produced 14 stalls, `human_review.status=not_reviewed`,
  `synthetic_trial=true`, `field_effect_claimed=false`.

## Remaining limits

- Not a jurisdiction certification or construction-document package.
- Synthetic trial only: no licensed real-site CAD, no actual jurisdiction rule
  source, no field project, and no human-review sign-off loop.

## D3 wheel (2026-09-04)

Fresh wheel `openparkcad-0.3.0-py3-none-any.whl` SHA-256
`9F10A778EF7B5600B4EB53324FD94F3819289039E799948F66D91701EFD3B91C`
installed with `-I` outside the source tree at
`E:\CacheVault\Temp\openparkcad-d3-f10db37363ef46e786a77630a62faf37`.
Imported package path is under that venv prefix.

Default extra ran `import-dxf` → `solve --review-bundle --delivery-manifest
--source-coordinates` → `view` on `tests/fixtures/cad/rectangle_mm.dxf`:
14 stalls, `human_review.status=not_reviewed`, `synthetic_trial=true`,
`field_effect_claimed=false`. Unsupported project version
`openparkcad-project-0` raises rather than guess-migrate.

Optimizer extra installed OR-Tools `9.15.6755` and solved
`examples/phase0_site.json` (83 stalls).
