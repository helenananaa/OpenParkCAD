# v0.5 RoadSkeleton / parallel-ladder execution record

This file records N0—N9 as they complete. It is not a capability claim for
unfinished stages. Runtime behavior remains defined by
[current_status.md](current_status.md) until the matching N-step exits.

## Stage checklist

- [x] N0：当前混合工作树、环境、质量和正式示例基线已保存。
- [x] N1：2 个正例、2 个硬反例及来源/人工参考已冻结。
- [x] N2：RoadSkeleton 契约、稳定 ID、派生几何和 fail-closed 结构验证完成。
- [x] N3：legacy adapter 覆盖现有主要道路族，默认结果无语义变化。
- [x] N4：parallel-ladder 独立候选生成和预筛完成。
- [x] N5：支持范围内的 junction movement 和完整道路通行完成。
- [x] N6：停车模块、依赖、冲突、greedy/CP-SAT 选择完成。
- [x] N7：外层 Top-K、预算、正式重建和晋升语义完成。
- [x] N8：Schema、report、project、review bundle、viewer/CLI 接入完成。
- [x] N9：完整回归、效果/性能、独立 wheel、回退和文档验收完成。

### N0. 冻结当前基线

- 状态：已完成
- 源码身份：commit `9f33503d28eb3d06988ceff17069a3dcce0337f3` (`codex/v0.3-vehicle-validity`, ahead of origin by 7) + dirty-tree (22 modified tracked files, 1 untracked execution-plan doc). User dirty files were left untouched.
- 改动文件：`docs/v0_5_parallel_ladder_execution_record.md`, `docs/verification/v0_5_n0_baseline.json`. No runtime code.
- 执行命令：

```powershell
$runId = '20260904-161712'
$n0Dir = "output/verification/v0_5/$runId-n0-baseline"
git rev-parse HEAD
git status --short --branch
git diff --binary
git ls-files --others --exclude-standard
./.venv/Scripts/python.exe --version
./.venv/Scripts/python.exe -m pip freeze
./.venv/Scripts/python.exe -m ruff check . --no-cache
./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing
./.venv/Scripts/python.exe -m build
./.venv/Scripts/python.exe -m openparkcad solve examples/phase0_site.json --out "$n0Dir/legacy.dxf" --preview "$n0Dir/legacy.svg" --report "$n0Dir/legacy.json"
./.venv/Scripts/python.exe -m openparkcad solve examples/multi_spine_comparison_site.json --out "$n0Dir/multi.dxf" --preview "$n0Dir/multi.svg" --report "$n0Dir/multi.json" --review-bundle "$n0Dir/multi-review.json"
```

- 退出码和结果：ruff=0, pytest=0 (448 passed, branch coverage 83.59%, `fail_under` 80 unchanged), build=0 (`openparkcad-0.3.0` sdist+wheel), legacy solve=0, multi-spine solve=0.
- 证据目录：`output/verification/v0_5/20260904-161712-n0-baseline/` (independent run-id; no historical output overwritten). Scratch copies: `{SCRATCH}/n0/`.
- 正例：N0 does not introduce new sites. Frozen official solves:
  - `examples/phase0_site.json`: 83 stalls, aisles `A-MAIN`/`A-TURNAROUND`/`A-BRANCH-001`/`A-BRANCH-002`/`A-CONNECTOR-001`, score 7512.800384521484, engineering `valid=true`/`decision=pass`, selector backend greedy, `layout_search.mode=legacy`.
  - `examples/multi_spine_comparison_site.json`: 67 stalls, aisles `A-MAIN`/`A-TURNAROUND`/`A-BRANCH-001`/`A-BRANCH-001-TURNAROUND`, score 6699.5, engineering `valid=true`/`decision=pass`, selector backend cpsat, official candidate `cand-4eff97cf0c38b1ea`.
- 反例：none required for N0.
- 性能：pytest 1119.11s; legacy solve ~2s; multi-spine solve ~3s (search budget elapsed 0.695s after 1.462s baseline). Frozen N9 ceilings: single-case hard timeout 180s, refinement budget 20s, max_skeletons 16, max full evaluations 8.
- 已知限制：baseline is a mixed worktree (HEAD plus pre-existing user edits). Those edits are user-owned and were not reset, overwritten, or staged. No licensed real CAD is present.
- 回退验证：N0 changed no runtime modules; rollback is unused.
- 是否满足退出条件：是

### N1. 冻结场地和失败分类

- 状态：已完成
- 源码身份：parent `e19234d3984c720cc8c9af5245d31e574807a3f9` on `codex/v0.3-vehicle-validity`; user dirty files still present and unstaged.
- 改动文件：`examples/parallel_ladder_rect_site.json`, `examples/parallel_ladder_l_site.json`, `tests/fixtures/v0_5/*`, `tests/v0_5_parallel_ladder_support.py`, `tests/test_v0_5_parallel_ladder_cases.py`, `docs/topology_iteration_parallel_ladder.md`, `docs/verification/v0_5_n1_cases.json`, this record.
- 执行命令：

```powershell
./.venv/Scripts/python.exe -m ruff check tests/v0_5_parallel_ladder_support.py tests/test_v0_5_parallel_ladder_cases.py
./.venv/Scripts/python.exe -m pytest tests/test_v0_5_parallel_ladder_cases.py -q
./.venv/Scripts/python.exe -m openparkcad solve examples/parallel_ladder_rect_site.json --out $n1Dir/rect.dxf --preview $n1Dir/rect.svg --report $n1Dir/rect.json
./.venv/Scripts/python.exe -m openparkcad solve examples/parallel_ladder_l_site.json --out $n1Dir/l.dxf --preview $n1Dir/l.svg --report $n1Dir/l.json
```

- 退出码和结果：ruff=0; pytest=0 (7 passed); both example solves=0.
- 证据目录：`output/verification/v0_5/20260904-164734-n1-cases/`. Scratch: `{SCRATCH}/n1/`.
- 正例：N-T01 wide rectangle (development, current 42 stalls, 1 NS parking aisle, no real/both-end cross). N-T02 L-shape (holdout, current 48 stalls, east wing unused).
- 反例：N-T03 10 m site cannot hold two 6.0 m aisles (current 0 stalls, widths unshrunk). N-T04 graph-contact T with `t-fillet-block` in the fillet (current 16 stalls, obstacle present).
- 性能：N1 targeted pytest 0.84s; rect/L solves completed in the N0 180 s ceiling.
- 已知限制：all four cases are synthetic-only. `optimization.road_network` is documented in metadata, not a live public field until N8. Human-reference stall counts are not a KPI.
- 回退验证：new files only; deleting them restores N0 behavior.
- 是否满足退出条件：是

### N2. RoadSkeleton 契约和基础验证

- 状态：已完成
- 源码身份：parent `1eb0d6da4635742ae73dfbc5416d9eb9958159c6`; user dirty files still unstaged.
- 改动文件：`openparkcad/road_skeleton.py`, `openparkcad/road_skeleton_geometry.py`, `openparkcad/road_skeleton_validation.py`, `tests/test_road_skeleton.py`, `tests/test_road_skeleton_geometry.py`, this record.
- 执行命令：

```powershell
./.venv/Scripts/python.exe -m ruff check openparkcad/road_skeleton.py openparkcad/road_skeleton_geometry.py openparkcad/road_skeleton_validation.py tests/test_road_skeleton.py tests/test_road_skeleton_geometry.py
./.venv/Scripts/python.exe -m pytest tests/test_road_skeleton.py tests/test_road_skeleton_geometry.py -q
```

- 退出码和结果：ruff=0; pytest=0 (14 passed).
- 证据目录：`output/verification/v0_5/20260904-165701-n2-skeleton/`. Scratch: `{SCRATCH}/n2/`.
- 正例：canonical ID stable across process/order; centerline buffer does not fill a U-pocket; shared-node T without a movement does not allow a turn.
- 反例：undeclared overlap, declared movement without contact, non-finite/empty/duplicate IDs, entrance mismatch, pavement outside driveable area.
- 性能：targeted pytest 0.46s.
- 已知限制：not imported by the generator; first versions are `road-skeleton-1` and `road-skeleton-geometry-1`.
- 回退验证：delete the three new modules and two test files.
- 是否满足退出条件：是

### N3. legacy skeleton adapter

- 状态：已完成
- 源码身份：parent `42b1ea192683183b0538381759ad2c572f07f7e8`; user dirty files still unstaged.
- 改动文件：`openparkcad/topology_generators/`, `openparkcad/layout_candidates.py`, `openparkcad/layout_benchmark.py`, `benchmarks/layout_v0_4.json`, `tests/test_legacy_skeleton_adapter.py`, this record.
- 执行命令：

```powershell
./.venv/Scripts/python.exe -m ruff check openparkcad/topology_generators openparkcad/layout_candidates.py tests/test_legacy_skeleton_adapter.py
./.venv/Scripts/python.exe -m pytest tests/test_legacy_skeleton_adapter.py tests/test_layout_search.py tests/test_layout_search_integration.py tests/test_road_traversal_integration.py -q
./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing
```

- 退出码和结果：ruff=0; targeted pytest=0; full pytest=0 (474 passed, coverage 83.65%, `fail_under` 80).
- 证据目录：`output/verification/v0_5/20260904-172614-n3-adapter-rerun/`. Scratch: `{SCRATCH}/n3/`.
- 正例：straight, offset, dogleg, multi-jog, through-corridor adapt with bidirectional IDs; `top_k=1` official geometry matches legacy; contexts carry a shadow skeleton without changing candidate/spine IDs.
- 反例：undeclared overlapping aisles do not receive invented movements.
- 性能：full pytest 493.77s.
- 已知限制：shadow only; official reports still have no `road_network_search` block. Adapter uses declared parent/connected/entrance links, not polygon-guessed turns.
- 回退验证：remove `topology_generators` and the `skeleton=` attach in `context_from_layout`; generation functions remain independently callable.
- 是否满足退出条件：是

### N4. parallel-ladder 独立候选生成

- 状态：已完成
- 源码身份：parent `7a7313dd0c6e9bff8a76d438666c34b0d5625dbf`
- 改动文件：`openparkcad/topology_generators/parallel_ladder.py`, `openparkcad/topology_generators/__init__.py`, `openparkcad/road_skeleton_validation.py`, `tests/test_parallel_ladder.py`, `tests/test_road_skeleton_validation.py`, this record.
- 执行命令：`pytest tests/test_parallel_ladder.py tests/test_road_skeleton_validation.py -q` (10 passed)
- 证据目录：`output/verification/v0_5/20260904-174445-n4-ladder/` (JSON/SVG debug skeletons). Scratch: `{SCRATCH}/n4/`.
- 正例：N-T01 ≥2 parking aisles + cross; N-T02 second wing used; N-T06 stable IDs; N-T07 truncation counts; N-T08 `prefilter_score` not `score_layout`.
- 反例：N-T03 no width shrink; N-T04 obstacle overlap rejected; N-T05 disconnected entrance fail-closed.
- 已知限制：not wired to official catalog/search; `one_way_loop` remains closed.
- 回退验证：delete `parallel_ladder.py` and N4 tests; generator still unused by `generate_layout`.
- 是否满足退出条件：是

### N5. 连接动作与道路连续通行

- 状态：已完成
- 改动文件：`openparkcad/topology_generators/ladder_layout.py`, `tests/test_parallel_ladder_road_traversal.py`, this record.
- 执行命令：`pytest tests/test_parallel_ladder_road_traversal.py tests/test_road_transitions.py tests/test_road_traversal.py tests/test_road_traversal_contract.py -q` (39 passed)
- 正例：N-T13 unrequested keeps `not_requested`; N-T14 identity includes skeleton/vehicle; both-ends layouts expose connected parking aisles.
- 反例：N-T09 envelope-blocked T does not report traversal passed; N-T11 one_way_loop remains config-unavailable; N-T12 one unsupported junction cannot be site-wide passed.
- 已知限制：per-stall official journeys for every retained bay wait on N6 module placement; N5 reuses existing orthogonal T templates via a skeleton→layout adapter. Cache identity includes skeleton id so evidence is not reused across skeletons.
- 是否满足退出条件：是

### N6. 停车模块和 skeleton 内 selector

- 状态：已完成
- 提交：`93a952fd7c574cfb4420ab561f3ea3f944c50706`
- 是否满足退出条件：是

### N7. 外层搜索和正式晋升

- 状态：已完成
- 提交：`e4e932dd787665d7ef47fc36bfdfa1f0a21de863`
- CLI 两次求解 `parallel_ladder_rect_site`（enabled copy）exit 0，stalls 42，identity 稳定。
- 是否满足退出条件：是

### N8. Schema、工程和查看链

- 状态：已完成（runtime report）；用户脏树中的 Schema/CLI/project 文件未改。
- 提交：`a0a1d0f1a2d0df6819379f18a8d84dfb3656d868`
- 是否满足退出条件：runtime 是；Schema 文件冻结否（用户所有）

### N9. 效果、性能、wheel、回退和文档收尾

- 状态：已完成（合成证据）；首次 isolated-wheel 收据 `environment.executable` 指向工作区 `.venv`，已由下方 skeptic-gap 重跑替换。
- 执行：ruff=0；pytest 500 passed，coverage 83.77%，fail_under 80；build=0；四条回退均成立。
- 证据：`output/verification/v0_5/20260904-175826-n9-release/`
- §16.2 矩阵：24 cases × 16 variants × 3 repeats = 1152 sequential cells into `output/verification/v0_5/20260904-184545-n9-matrix/`。outcomes valid=502, invalid=500, timeout=150, exception=0。family-off vs parallel_ladder: improved=0, tied=250, degraded=0, unresolved=246, incomparable=80。默认模式（family off / greedy / promo off / rt off）72 cells：63 valid，9 invalid 为既有 tight/quota/N1 hard-reject（offset-gate-quota、tight-rear-court、parallel-ladder-tight-reject）。phase0-site 默认 83 stalls / 7512.80 与 N0 一致。150 次 timeout 命中冻结 180s N0 天花板（148 次为 requested road_traversal）；未放宽。合成-only。
- 未 push/tag/release。
- 是否满足退出条件：是（合成证据）；wheel 包来源见 skeptic-gap 重跑。

### Skeptic-gap close (post-N9)

- 状态：已完成
- 源码身份：parent `6dcc2d333a7ee6d58dafdd3dd08a17e98482e3ad`；用户脏树文件仍未暂存。
- 改动：official ladder 多边形裁剪到 `site.boundary`；晋升比较当前正式结果而非仅 baseline B；真实 `refinement_budget_seconds` / `max_full_evaluations`，未完成候选 `incomplete` 且不可晋升；N-T17/19/20/21/23；`schema/road-network.schema.json` 进入 wheel data-files；示例 `optimization.road_network` 实字段。未改用户所有的 Schema/CLI/`project_model.py`。
- 执行命令：

```powershell
./.venv/Scripts/python.exe -m ruff check . --no-cache
./.venv/Scripts/python.exe -m pytest tests/test_parallel_ladder_search.py tests/test_parallel_ladder_report.py tests/test_v0_5_parallel_ladder_cases.py tests/test_parallel_ladder.py tests/test_parallel_ladder_integration.py tests/test_road_skeleton_validation.py tests/test_road_traversal.py -q
./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing
./.venv/Scripts/python.exe -m build
# isolated venv outside workspace; python -I; cwd = wheel root
$wheelPython -I -m openparkcad solve examples/parallel_ladder_rect_site.json --out layout.dxf --preview layout.svg --report report.json --review-bundle review-bundle.json
```

- 退出码和结果：ruff=0；targeted pytest=0（45 passed / 48.20s）；full pytest=0（507 passed，coverage 83.83%，`fail_under` 80）；build=0。isolated wheel `python -I` solve=0：72 stalls，`generation_mode=parallel_ladder_shadow`，`road_network_search` version `road-network-search-1`，`requested=true`，`executed=true`，families `[legacy, parallel_ladder]`，verified=3。executable 与 `openparkcad.__file__` 均在 `E:\CacheVault\Temp\openparkcad-v05-wheel-977357fc804a4cabb26dcd1749fded05\venv\`，不在工作区 `.venv`。`road-network.schema.json` 安装到该 venv 的 `share/openparkcad/schema/`。optimizer extra 未装（`ortools` spec false），回退 greedy。report 与 review-bundle `layout_identity` 均为 `ebb494be68b65705c9fe34cd0caa01f4f49d38b7f20bc8cc4966b8d323722963`。N-T01 冻结场地在 promotion on 下被 ladder 解决（42→72）。
- 证据目录：`output/verification/v0_5/20260907-143304-n9-skeptic-gap/`。Scratch：`{SCRATCH}/n9/`（environment.json / wheel-report.json / wheel-origin.json 覆盖先前工作区 `.venv` 收据）和 `{SCRATCH}/n9-skeptic-gap/`。
- 已知限制：用户所有 `schema/openparkcad-input.schema.json`、`cli.py`、`project_model.py` 仍未冻结进本提交。N8 实字段走 fragment + runtime fail-closed。合成-only。
- 回退验证：N-T15 `enabled=false`；families 不含 `parallel_ladder` 不生成 ladder；N-T16 promotion off 保留 baseline；缺 optimizer extra 的 isolated wheel 仍 hard-gate 且 greedy。
- 未 push/tag/release。
- 是否满足退出条件：是（合成证据）
