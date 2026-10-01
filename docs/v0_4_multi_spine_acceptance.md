# v0.4 多主路搜索验收记录

日期：2026-09-03。验收源码：`67eec8b5274bf58e28b9ad1a73a34b134e0dbcc0`。包版本保持 `0.3.0`，本轮未创建版本 tag 或 Release。后续只修改验收文档的提交沿用此运行时代码证据。

**E0—E9 本轮收尾通过。** 默认仍为 legacy / greedy；multi_spine 是显式启用的模板范围内 Top-K 搜索，不代表全局最优或道路级连续车辆轨迹已实现。

## 修复内容

- 基线按道路几何等价匹配，入口或骨架不一致时不会冒用既有基线结果。
- multi_spine 的获胜布局保留实际 selection 和 solver provenance，导出不重新求解。候选成为正式结果后，同步晋升状态、正式验证、诊断及预览到正式道路/车位 ID 的映射。
- 原始候选快照保留求解时的来源 ID；正式几何使用 A-* / P-*。使用 candidate_layout_promotion 的 preview_to_official_aisle_ids、preview_to_official_stall_ids 和 candidate_to_official_aisle_ids 关联两者。
- 基线生成时保留完整候选上下文，消除第二遍枚举；基准 JSON / CSV 接通已有基线、额外优化和收集计时。重建计入额外优化，未单独测量的 rebuild_seconds 继续标明 not_available。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 本地完整回归 | 345 passed，0 skipped；472.00 秒 |
| 覆盖率（包含分支统计） | 83.68%，通过原有 80% 门槛 |
| Ruff | 通过 |
| GitHub Python 3.10 / 3.12 默认依赖 | 各 337 passed、8 个可选 optimizer 测试跳过；覆盖率均为 82.53% |
| GitHub Python 3.12 optimizer | 显式安装并导入 OR-Tools，63 passed、0 skipped |
| 独立 wheel | 默认依赖路径 83 个车位；optimizer 比较示例 67 个车位，报告与晋升映射一致 |
| 回退 | legacy 与关闭晋升两条路径均返回 65 个车位、6500 分，正式几何相同 |
| 完整基准 | 20 案例 × 4 变体 × 3 次 = 240；216 次有效布局，24 次预期拒绝，240/240 符合预期 |
| CP-SAT / 输入保护 | 120/120 请求实际执行 CP-SAT，无回退；原始输入 SHA-256 不变 |

远端证据：[push CI](https://github.com/helenananaa/OpenParkCAD/actions/runs/33736091918)、[同源码 PR CI](https://github.com/helenananaa/OpenParkCAD/actions/runs/33736095103)。默认依赖的 skip 是未安装可选 optimizer 的预期行为；optimizer job 不依赖跳过来通过。

## 效果与耗时

所有案例顺序执行。统一设置 selector_seed=17、selector_num_workers=1、selector_time_limit_seconds=2.0；multi_spine 使用 top_k=4、refinement_budget_seconds=10.0；每次子进程硬超时 180 秒。基准不与本次 pytest / wheel 求解并行。

下表每格为车位数量与三次总耗时的中位数；完整最小值 / 最大值、正式分数与结果分类见 [机器可读验收记录](verification/v0_4_20260903.json)。总耗时按基准计时口径统计至求解和最终检查完成，不包括三件套写出。

| 案例 | legacy-greedy | legacy-cpsat | multi-greedy | multi-cpsat |
| --- | --- | --- | --- | --- |
| adaptive-dogleg | 42；0.32s | 42；0.58s | 42；0.70s | 42；0.99s |
| dogleg-dual-entrance | 62；0.52s | 62；0.78s | 62；1.00s | 62；1.27s |
| dogleg-obstacle | 129；112.83s | 129；114.24s | 129；120.68s | 129；123.08s |
| dogleg-one-way-dual-entrance | 66；0.53s | 66；0.78s | 66；1.03s | 66；1.30s |
| dual-entrance | 30；0.14s | 30；0.41s | 30；0.14s | 30；0.43s |
| end-loop | 82；32.99s | 82；33.03s | 82；48.40s | 105；49.55s |
| irregular-courtyard | 30；9.34s | 30；6.95s | 30；4.97s | 30；4.25s |
| multi-jog-dual-entrance | 148；50.25s | 148；51.31s | 148；53.12s | 148；53.96s |
| multi-jog-obstacle | 56；0.53s | 56；0.77s | 56；0.76s | 56；1.03s |
| multi-jog-one-way-dual-entrance | 60；0.60s | 60；0.85s | 60；0.89s | 60；1.16s |
| multi-spine-comparison | 65；1.12s | 65；1.31s | 67；1.78s | 67；1.93s |
| obstacle-offset | 28；35.02s | 28；35.51s | 28；35.90s | 28；36.54s |
| offset-gate-quota | 预期拒绝；0.12s | 预期拒绝；0.39s | 预期拒绝；0.18s | 预期拒绝；0.47s |
| one-way-strip | 26；0.11s | 26；0.39s | 26；0.11s | 26；0.39s |
| opposite-loop | 132；18.18s | 132；7.66s | 132；12.89s | 132；15.38s |
| parallel-strip | 12；0.07s | 12；0.35s | 12；0.07s | 12；0.35s |
| passing-bay-narrow | 46；0.24s | 46；0.49s | 46；0.24s | 46；0.50s |
| phase0-site | 83；1.27s | 83；1.31s | 83；1.26s | 83；1.35s |
| t-end | 2；0.05s | 2；0.33s | 2；0.05s | 2；0.33s |
| tight-rear-court | 预期拒绝；0.08s | 预期拒绝；0.32s | 预期拒绝；0.08s | 预期拒绝；0.35s |

按同案例、同后端、同重复编号比较，共 120 对：9 对正式分数提高、99 对持平、12 对均按预期拒绝；0 对分数退化，0 对从有效变为无效。

结果同时包含改进和增加的求解代价，不据单个案例宣称普遍更优或固定加速比例。每组只有三次测量，报告中位数和范围，不报告具有统计意义的 P95。所有输入仍为合成案例，没有声称真人工方案对比或施工适用性验收。

## 重跑与追溯

```powershell
& ./.venv/Scripts/python.exe -m pytest -q --cov=openparkcad --cov-report=term
& ./.venv/Scripts/python.exe tools/benchmark_layouts.py --manifest benchmarks/layout_v0_4.json --profile all --subset full --repeats 3 --timeout-seconds 180 --out "output/benchmarks/v0_4/recheck"
```

每次重跑使用新的输出目录；不要覆盖本次证据。源码、依赖版本、wheel 文件校验值和汇总数据见机器可读记录。本地完整日志与每次运行的 JSON / SVG / DXF 位于 gitignored 目录：

`output/verification/v0_4/20260903-164520-closure/`

wheel：`openparkcad-0.3.0-py3-none-any.whl`；SHA-256：`4723de8df59d0689eb54912e128381b773125606141e1a3cef9deea57bbfe73c`。该 wheel 从 sdist 构建，独立虚拟环境安装后逐个校验包内 Python 源文件，与验收源码一致。

后续工作按执行计划第 12 节进入 R1—R3 道路级车辆通行与连续轨迹，再推进受限 DXF 输入和交互编辑。这些能力未计入本轮完成项。
