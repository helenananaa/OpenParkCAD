# Examples catalog

Executable JSON demos under `examples/`. From the repository root, choose a
demo name from the table and keep its DXF, preview, and report together:

```powershell
$name = "phase0_site"
.\.venv\Scripts\python.exe -m openparkcad solve "examples/$name.json" `
  --out "output/examples/$name/layout.dxf" `
  --preview "output/examples/$name/layout.svg" `
  --report "output/examples/$name/report.json"
```

`--out` is a DXF file path. Set all three options to give each demo its own
output set. See the [workspace guide](README.md) for local archive conventions.

| File | What it exercises |
| --- | --- |
| `phase0_site.json` | Baseline bundled site; promotion path |
| `parallel_strip_site.json` | Parallel stall family on a narrow strip |
| `t_end_site.json` | T-end cap bays on dead-end turnarounds |
| `opposite_loop_site.json` | Opposite-side connectors / loops |
| `end_loop_site.json` | Outer end-loop with obstacle |
| `one_way_strip_site.json` | Fixed one-way aisle class |
| `dual_entrance_site.json` | Entry-only + far exit corridor (`A-EXIT`) |
| `obstacle_offset_site.json` | Lateral main-aisle offsets for clearance |
| `dogleg_obstacle_site.json` | Single dogleg + multi-spine branches |
| `dogleg_dual_entrance_site.json` | Dogleg + dual entrance L-elbow exit |
| `dogleg_one_way_dual_entrance_site.json` | Dogleg one-way strict (no reverse egress) |
| `multi_jog_obstacle_site.json` | Chained multi-jog around staggered blocks |
| `multi_jog_dual_entrance_site.json` | Multi-jog + exit + multi-spine branches |
| `multi_jog_one_way_dual_entrance_site.json` | Multi-jog strict one-way dual entrance |
| `adaptive_dogleg_site.json` | Adaptive offsets past a wide center obstacle |
| `passing_bay_narrow_site.json` | Narrow two-way + synthesized passing bays |
| `multi_spine_comparison_site.json` | Two laterals + perp/angled families; multi_spine module rebuild promotes a higher official score |
| `parallel_ladder_rect_site.json` | Synthetic wide rectangle with live `optimization.road_network` (N-T01); promotion on |
| `parallel_ladder_l_site.json` | Synthetic L-shape frozen for opt-in `parallel_ladder` (N-T02 holdout) |

DXF layers for circulation (v0.3 exporters):

| Layer | Role |
| --- | --- |
| `AISLES_MAIN` | Main spine segments |
| `AISLES_JOG` | Dogleg / multi-jog lateral connectors |
| `AISLES_BRANCH` | Perpendicular branches |
| `AISLES_CONNECTOR` | Same-side / opposite connectors |
| `AISLES_TURNAROUND` | End turnarounds |
| `AISLES_EXIT` | Dual-entrance exit corridor |
| `AISLES_PASSING_BAY` | Synthesized or declared passing bays |
| `AISLES` | Fallback unknown roles |

SVG previews color-code the same roles and label aisle IDs.
