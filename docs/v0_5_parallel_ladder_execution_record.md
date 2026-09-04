# v0.5 RoadSkeleton / parallel-ladder execution record

This file records N0—N9 as they complete. It is not a capability claim for
unfinished stages. Runtime behavior remains defined by
[current_status.md](current_status.md) until the matching N-step exits.

## Stage checklist

- [x] N0：当前混合工作树、环境、质量和正式示例基线已保存。
- [ ] N1：2 个正例、2 个硬反例及来源/人工参考已冻结。
- [ ] N2：RoadSkeleton 契约、稳定 ID、派生几何和 fail-closed 结构验证完成。
- [ ] N3：legacy adapter 覆盖现有主要道路族，默认结果无语义变化。
- [ ] N4：parallel-ladder 独立候选生成和预筛完成。
- [ ] N5：支持范围内的 junction movement 和完整道路通行完成。
- [ ] N6：停车模块、依赖、冲突、greedy/CP-SAT 选择完成。
- [ ] N7：外层 Top-K、预算、正式重建和晋升语义完成。
- [ ] N8：Schema、report、project、review bundle、viewer/CLI 接入完成。
- [ ] N9：完整回归、效果/性能、独立 wheel、回退和文档验收完成。

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
