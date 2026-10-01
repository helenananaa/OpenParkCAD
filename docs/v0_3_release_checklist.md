# v0.3 release checklist

Use this before tagging `0.3.0` on the development branch.

## Trust boundary (must stay honest)

- Package version `0.3.0` is not a statutory/compliance claim.
- Runtime reports (`engineering_validation`, graph, maneuver, site constraints)
  define which checks actually ran on the official layout.
- Capability matrix: [current_status.md](current_status.md).
- Examples: [examples_catalog.md](examples_catalog.md).

## Local verification

```powershell
.\.venv\Scripts\python.exe -m ruff check openparkcad tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m build
```

Optional feature smoke (representative demos):

```powershell
$demos = @(
  "examples/phase0_site.json",
  "examples/dogleg_obstacle_site.json",
  "examples/multi_jog_obstacle_site.json",
  "examples/adaptive_dogleg_site.json",
  "examples/passing_bay_narrow_site.json",
  "examples/dual_entrance_site.json",
  "examples/one_way_strip_site.json",
  "examples/multi_jog_one_way_dual_entrance_site.json"
)
foreach ($demo in $demos) {
  $name = [IO.Path]::GetFileNameWithoutExtension($demo)
  .\.venv\Scripts\python.exe -m openparkcad solve $demo `
    --out "output/smoke/$name/layout.dxf" `
    --preview "output/smoke/$name/layout.svg" `
    --report "output/smoke/$name/report.json"
}
```

Each demo writes a separate output set under `output/smoke/`. Older local
builds and drawings can be retained using the [workspace archive convention](README.md).

## Regression expectations

| Area | Expect |
| --- | --- |
| Lint | `ruff check` clean |
| Tests | Full `pytest` green on Python 3.10+ |
| Exporters | DXF role layers (`AISLES_JOG`, …) present when roles exist |
| Dogleg | Centerline obstacle recovers with dogleg or adaptive offsets |
| Multi-jog | Staggered obstacles reach deep rear spine |
| Dual entrance | `A-EXIT` when far exit-capable gate exists |
| One-way strict | No reverse-egress edges when disabled + dual gates |
| Passing bays | Narrow two-way + synthesis yields usable bay markers |

## Score knobs (opt-in)

| Key | Effect |
| --- | --- |
| `prefer_obstacle_clearance` | Enables clearance bonus (default weight 10 unless overridden) |
| `obstacle_clearance_weight` | Intensity for that default bonus |
| `weights.obstacle_clearance` | Explicit score weight (overrides default intensity) |
| `obstacle_clearance_cap` | Cap metres for clearance metric (default 12) |
| `prefer_loops` / `weights.connector_loop` | Connector loop bonus |

Defaults keep promotion baselines stable when these flags are off.

## Docs to skim before tag

- [CHANGELOG.md](../CHANGELOG.md) Unreleased → 0.3.0 date
- [current_status.md](current_status.md) capability matrix
- [input_model.md](input_model.md) active field table
- [roadmap.md](roadmap.md) remaining non-claims
- [examples_catalog.md](examples_catalog.md)

## Explicit non-goals for the 0.3 claim

- General multi-gate / maze path planning
- Exact articulated/emergency-vehicle swept-path templates
- Deadlock simulation for narrow two-way
- Statutory code profiles or construction drawings
