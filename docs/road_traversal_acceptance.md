# Road traversal (stage 一) acceptance

Date: 2026-09-03. Algorithm version: `road-traversal-1`. Package remains `0.3.0`.

## What shipped

- Input: `constraints.road_traversal` with `enabled`, `scope=site_interior`,
  `time_budget_seconds`. Missing or `enabled=false` is `not_requested`.
- Official publication requires `status=passed` **and** `valid=true`.
  `failed` / `unsupported` / `incomplete` / input error / internal exception
  stay distinct. The last four cannot be published as passed.
- Preview, candidate isolation, official rebuild, and promotion re-run the
  check on the object being published.
- Supported road families: straight, entrance/exit throat, left/right 90°
  turns, single/double dogleg, U-turn, U-connector, exit turn.
- Supported parking families: perpendicular, angled, parallel, t_end via the
  existing stall templates. Other families are `unsupported`, never skipped-as-pass.
- CLI: optional `--diagnostics` writes independent rejection evidence; official
  DXF/SVG/report trio is unchanged on failure.

## Evidence

Targeted pytest modules:

- `tests/test_road_traversal_contract.py`
- `tests/test_road_traversal_envelope.py`
- `tests/test_road_transitions.py`
- `tests/test_parking_motion_adapter.py`
- `tests/test_road_traversal.py`
- `tests/test_road_traversal_integration.py`
- `tests/test_road_traversal_export.py`
- `tests/test_road_traversal_benchmark.py`

Hand-built through-site fixture (entry west, exit east, one 90° stall) is the
R-T02 pass pair. Generated examples without the switch keep legacy stall counts.

## Remaining limits

- First edition is finite templates on site interior only.
- Generated multi-aisle lots may return `failed`/`incomplete` when templates
  cannot join; that is not a claim that no path exists.
- Articulated vehicles, road reversing, and off-site approach are unsupported.
- A generated site JSON with the switch on is not automatically a pass case;
  the R-T02 pass pair is the hand-built through-site fixture.

## R4 close-out

- Targeted road tests: 49 passed.
- Unrequested `solve examples/phase0_site.json`: 83 stalls, `status=not_requested`.
- `benchmarks/road_traversal.json --profile all --subset smoke --repeats 1`:
  0 unexpected. `phase0-unrequested` multi-cpsat `actual_backend=cpsat` with
  no fallback.
- Independent wheel (`-I`, outside the repo): import under the venv prefix;
  unrequested solve 83 stalls / `not_requested`; road-enabled fixture writes
  diagnostics with `executed=true` and is not published as passed.
