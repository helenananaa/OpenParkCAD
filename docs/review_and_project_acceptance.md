# Review bundle, viewer, and project locks (stage 三) acceptance

Date: 2026-09-03. Bundle version: `review-bundle-1`. Project version: `openparkcad-project-1`.

## What shipped

- `solve --review-bundle` writes a frozen comparison packet from the official
  layout: site, CAD transform if present, official geometry, evaluated candidate
  snapshots (scores from that solve, not re-solved), road evidence, journeys,
  and distinct official/preview/failed/unsupported/over_budget/not_solved/stale
  statuses.
- `view` writes an offline HTML/SVG/JS page with pan/zoom, layer toggles,
  candidate switching, failure highlighting, and stall-journey playback. Bundle
  text is JSON-escaped and not executed as script.
- Project revisions, stable project object ids, and hard locks on entrance,
  main-aisle geometry, and stall groups. Constrained regenerate keeps the last
  accepted layout on conflict, cancel, timeout, or stale/out-of-order results.
  Export rebuilds and revalidates the accepted layout. Unsupported project
  versions are not guess-migrated.

## Evidence

- Unit tests U-T01–U-T10 in `tests/test_review_bundle.py`,
  `tests/test_viewer_bundle.py`, `tests/test_layout_locks.py`,
  `tests/test_project_service.py`, `tests/test_project_roundtrip.py`,
  `tests/test_constrained_regeneration.py`.
- CLI: `solve examples/multi_spine_comparison_site.json --review-bundle` (67
  stalls) then `view` wrote `review.html` + `review.js` + `review.css`.

## Remaining limits

- First-edition locks reject non-matching generated geometry rather than
  synthesising a full CAD editor.
- Browser interaction coverage is unit/static HTML plus a headless screenshot
  attempt; Playwright may lack installed browsers in this environment.
