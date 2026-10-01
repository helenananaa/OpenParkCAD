# v0.4 下一轮执行计划：方案基准、多主路候选与持续验证

状态：**E0—E9 已完成本轮验收**，见 [2026-09-03 验收记录](v0_4_multi_spine_acceptance.md)。默认求解仍为 legacy/greedy；`layout_search.mode=multi_spine` 为显式开关，Top-K 与当前模板族是能力边界。第 12 节道路轨迹及 CAD 工作仍为后续阶段。

后续实施使用 [后续五阶段执行计划](next_stages_execution_plan.md)，其中将第 12 节展开为道路通行、CAD 输入、交互修改、复杂布局和工程交付步骤；本文件保留多主路迭代的执行历史。

适用基线：2026-09-02 本地检查的 `2e2766ae81d975e042551a24c18c8a1a88056ca1`，包版本 `0.3.0`。该次本机检查为 Python 3.12.7、OR-Tools 9.15.6755，301 项测试通过，Ruff 通过；这是一次本地记录，不代表远端 CI 状态，也不是以后必须保持的测试数量。

本计划沿用 [分阶段计划](phased_plan.md) 的“目标 → 实现切片 → 验收”结构，并衔接 [v0.4 候选选择契约](v0_4_discrete_candidates.md)。历史阶段的限制以 [当前能力矩阵](current_status.md) 为准。实现完成后，运行报告仍是具体场地实际执行了哪些检查的依据。

## 1. 本轮交付目标

让同一场地的多个主路方案分别完成支路、连接路和车位模块选择，比较经过完整复核的最终方案，并用固定案例集记录质量与耗时。

本轮交付三个可以独立使用的成果：

1. **批量方案对比工具**：保存输入、参数、后端、正式结果、失败分类与耗时，使下一次修改可以和本次比较。
2. **多主路候选搜索**：在现有直线、偏移、dogleg、multi-jog 模板范围内，保留多个完整候选上下文，逐个优化并复核。
3. **持续验证**：CI 同时覆盖没有 OR-Tools 的默认路径和安装 OR-Tools 的成功路径，验证最终导出对象与报告一致。

本轮沿用现有车辆、几何、道路图、配额和运营检查。道路级转弯轨迹、CAD 导入及交互编辑的后续步骤列在第 12 节，分别形成后续交付，不计入本轮完成条件。

### 1.1 完成后的使用行为

- 原有输入继续得到原有模式的结果；默认 selector 仍为 greedy。
- 用户显式启用多主路搜索后，可以看到每个保留方案的身份、正式重建后的有效性、分数和未选原因。
- 未开启预览晋升时，多主路搜索只增加方案比较信息，正式 DXF/SVG 几何仍来自原有流程。
- 开启晋升时，只有完成正式重建与全部适用检查、且分数不低于有效基线的方案可以替换输出。
- 超时、没有改进或预览无效时，保留已验证的有效基线；没有任何有效正式结果时继续按现有 CLI 语义拒绝发布。

### 1.2 本轮不扩展的能力

不新增任意路网或迷宫式路径规划，不将所有主路几何塞进一个 CP-SAT 模型，不把局部求解器的最优状态解释为整个停车场的全局最优。本轮也不改变配额规则、车辆模型、默认评分权重或区域规范支持范围。

真实场地可以逐步补充。没有真实场地时，照常完成基准工具和算法实现，结论标明“合成案例”；不以等待资料为由暂停可以执行的步骤。

## 2. 开始前需要读懂的代码

| 文件 / 入口 | 当前职责 | 本轮落点 |
| --- | --- | --- |
| [generator.py](../openparkcad/generator.py) / `generate_layout` | 比较车位类型组合，先选出模板结果，再附加候选快照 | 保留 legacy 流程，引入多方案协调入口 |
| [phase1_candidates.py](../openparkcad/phase1_candidates.py) / `iter_phase1_candidates` | 枚举入口、方向、入口偏移和横向偏移；内部还会择优丢弃直线 / dogleg 方案 | 在局部择优前保留完整候选及其来源 |
| [candidate_snapshot.py](../openparkcad/candidate_snapshot.py) | 从尝试诊断构建目录，选择、预览、晋升、重建官方 ID | 候选上下文隔离；拆开评估与正式发布 |
| [candidate_selector.py](../openparkcad/candidate_selector.py)、[candidate_cpsat.py](../openparkcad/candidate_cpsat.py) | 在目录内选择支路、连接路与车位模块 | 每个主路上下文单独运行，保留真实后端信息 |
| [candidate_layout_preview.py](../openparkcad/candidate_layout_preview.py) | 构造预览、过滤车位、处理接触配额、评分 | 复用现有检查顺序，输出待重建方案 |
| [engineering_validation.py](../openparkcad/engineering_validation.py)、[traffic_graph.py](../openparkcad/traffic_graph.py) | 工程决策和道路图验证 | 对具体方案重算，明确预览 / 正式结果作用域 |
| [cli.py](../openparkcad/cli.py) | 最终拒绝判定、JSON 报告、三件套事务写入 | 接入新报告；不绕开现有事务导出 |
| [CI](../.github/workflows/ci.yml) | Python 3.10 / 3.12 默认依赖验证 | 增加 optimizer 安装路径 |

实现时必须处理的四个事实：

1. `generate_layout` 当前在调用 `attach_candidate_snapshot` 前已选择一个结果。只在快照后再跑一次 selector，不能找回此前丢弃的主路方案。
2. `_generate_for_entrance` 和 `_generate_dogleg_for_entrance` 内部也会择优。只保留最外层循环结果，仍会漏掉同一入口下已经生成过的不同骨架。
3. 当前 `_branch_and_connector_attempt_candidates` 会遍历 `layout.attempts`。新流程必须按候选身份限定目录来源，不能把全局尝试历史当作当前主路可使用的对象集合。
4. `SiteSpec` / `LayoutResult` 虽然是 frozen dataclass，内部仍有可变列表、字典；会车袋合成还会形成方案自己的 `site_features`。候选不能共享可被后续步骤改写的嵌套状态。

## 3. 实施顺序与检查点

按 E0 → E1 → … → E9 顺序执行。每步先完成对应产物和针对性验证，再进入下一步；普通步骤不需要逐次请求确认。

| 步骤 | 内容 | 可审查产物 | 完成条件 |
| --- | --- | --- | --- |
| E0 | 保存当前基线 | 环境、源码版本和现有示例结果 | 能重新运行并说明版本 |
| E1 | 批量基准工具 | manifest、runner、汇总及失败诊断 | 正常、拒绝、超时均有独立记录 |
| E2 | optimizer CI | 两类依赖环境的 CI 配置 | 实际执行 CP-SAT 成功测试 |
| E3 | 隔离候选上下文 | 数据结构、ID、评估接口 | 候选间数据及目录不串用 |
| E4 | 保留多种主路 | 模板候选枚举与完整对象 | 局部未胜出的骨架仍可被评估 |
| E5 | 保留列表与预算 | 确定的候选排序、Top-K、计时 | 基线保留，预算含义清楚 |
| E6 | 完整方案择优 | 各候选重建、复核与最终比较 | 出现可解释的跨主路改进 |
| E7 | 输入、报告和 CLI 接入 | Schema、诊断、报告与示例 | 开关和晋升语义可验证 |
| E8 | 回归及效果比较 | 测试矩阵、固定案例对比 | 有效性不弱化，收益和代价有记录 |
| E9 | 文档与交付收尾 | 能力说明、构建和 wheel 验证 | 安装后可执行，回退路径清楚 |

每一步完成时记录：改变了什么、执行命令、结果位置、未完成项。实现记录可以放到 `output/verification/v0_4/<run-id>/`；需要长期维护的说明和小型样例进入 Git。

## 4. 先固定输入、结果和比较语义

本节记录 **E3—E7 已实现的契约**。下面保留逐步实施和验证顺序；实际支持仍以 runtime diagnostics 为准，未知 optimization 字段被 parser 接受不等于已执行。

### 4.1 新增输入

已实现 `optimization.layout_search`，沿用现有 backend 和晋升开关：

```json
{
  "optimization": {
    "selector_backend": "cpsat",
    "selector_seed": 17,
    "selector_time_limit_seconds": 2.0,
    "promote_candidate_layout_preview": false,
    "layout_search": {
      "mode": "multi_spine",
      "top_k": 4,
      "refinement_budget_seconds": 10.0
    }
  }
}
```

| 字段 | 缺省值 | 精确定义 |
| --- | --- | --- |
| `layout_search.mode` | `legacy` | `legacy` 保留当前流程；`multi_spine` 开启本计划的多主路比较 |
| `layout_search.top_k` | `4` | 最多深入评估的候选上下文数，包含旧流程获胜的原始上下文；其已有结果直接复用 |
| `layout_search.refinement_budget_seconds` | `10.0` | 基线建立完成后，额外候选优化和复核的协作式预算；不是整个 solve 的硬超时 |
| `selector_backend` | 现有 `greedy` | 在每个候选内部使用的 selector；缺 OR-Tools 时沿用现有回退语义 |
| `promote_candidate_layout_preview` | 现有 `false` | 唯一授权候选替换正式输出的输入开关 |

`mode` 只接受约定枚举；`top_k` 必须为正整数且不能接受布尔值；预算必须为正的有限数。非法的新配置明确报输入错误，不默默按旧模式运行。不要为现有其他 optimization 字段顺便增加新的拒绝规则。

`top_k=1` 是兼容性检查：复用旧流程上下文，正式输出应与相同输入、后端、晋升设置下的 legacy 结果一致。这里的一致指几何、车位身份、分数和有效性，不要求 DXF 时间戳等非语义元数据逐字节一致。

### 4.2 基线与待选方案

- **基线 B**：相同输入保留原有后端、评分、约束和晋升设置，只将搜索模式视为 legacy 后得到的完整正式结果。B 可能已经经历原有局部预览晋升。
- **候选 C**：一个完整主路上下文，以及在该上下文中选择、构建并复核后的方案。可以有多个 C。
- **最好预览 P**：各候选中已完成正式几何重建和适用检查的最好方案，但尚未写入正式输出。
- **正式结果 O**：最终交给 CLI 的唯一布局。只有 O 的对象进入正式 DXF/SVG 和顶层报告。

| 基线 | 晋升开关 | 搜索结果 | 正式结果 |
| --- | --- | --- | --- |
| 有效 | 关闭 | 任意 | B，额外方案写入新比较报告 |
| 有效 | 开启 | 有效候选分数更高 | 候选通过正式对象复核后成为 O |
| 有效 | 开启 | 无有效改进、预算结束或重建失败 | B |
| 无效 | 关闭 | 存在有效预览 | 正式求解仍无效；预览可由基准诊断保存，CLI 不发布正式三件套 |
| 无效 | 开启 | 存在有效、可晋升候选 | 该候选成为 O；报告记录“恢复可行解”，分数差为 null |
| 无效 | 任意 | 没有有效且获准晋升的候选 | 保留失败证据，沿用拒绝发布语义 |

最终按现有 `score_layout` 对**重建后的具体布局**比较。CP-SAT objective、预览分数、模板生成前估算不能替代最终分数。有效基线与候选分数相同时保留基线，减少无收益的图纸变化。

### 4.3 候选身份与隔离

在 `openparkcad/layout_candidates.py` 定义两个轻量数据结构：

- `LayoutCandidateContext`：`candidate_id`、`spine_id`、候选自己的 `SiteSpec`、模板 `LayoutResult`、本候选的支路诊断、来源参数与预筛结果。
- `LayoutCandidateEvaluation`：候选 ID、实际 selector 后端和 provenance、预览结果、重建后的布局、各检查结果、最终分数、耗时与失败分类。

`spine_id` 描述道路骨架：实际入口/出口、主路/jog/exit/会车袋及所属掉头区几何、方向和连接关系。`candidate_id` 再加入主/支路车位类型组合等会改变候选模块的上下文。相同骨架下不同车位组合允许分别比较。

实现规则：

1. 用确定的规范化序列化及摘要建立 ID，禁止使用 Python 进程随机化的 `hash()`。
2. 入口、heading、entrance offset 三元组不足以区分候选；必须区分 lateral offset 和实际 dogleg / multi-jog 几何。
3. 摘要和去重不得改变验证所用坐标。若摘要采用坐标舍入，碰撞时仍比较完整上下文，不合并有语义差异的布局。
4. 跨方案证据使用 `(candidate_id, local_object_id)`。每个正式方案内部仍可使用 `A-MAIN`、`A-BRANCH-*`、`P-*`。
5. 本候选的模块、支路诊断、合成 feature 和冲突矩阵只能来自本上下文。
6. 在构建/评估边界拷贝会被修改的列表和字典；测试应验证修改 C1 不影响 C2、B 或原始输入。

### 4.4 不改变的有效性规则

正式结果始终需要非空车位和道路，以及现有几何、道路图、maneuver/vehicle、site/quota、engineering、operational 判定。当前 `engineering_validation` 并不代替所有其他判定，不能只检查这一个布尔值。

继续按现有顺序处理车位过滤、接触带 retarget、必要的车辆复检、接触过滤及最终验证。预览和正式重建分别验证，不能把其他候选的通过报告拷贝过来。

遇到无效方案时保留失败种类；遇到内部异常时记录异常并让测试/基准标为错误，不用“没有可行方案”掩盖程序错误。OR-Tools 的已定义不可用/无解/异常回退继续沿用现有契约。

## 5. E0：保存基线

### 目标

在改动算法前保存一份可复查的源码、环境和默认行为记录。本次计划已有的 301 项通过记录可以引用；若开始实施时源码、依赖或环境已经变化，则重新检查。

### 执行步骤

1. 检查 Git 状态，保留既有未提交工作，不进行清理或重置。
2. 记录 commit、Python 和依赖版本、运行机器与本地时间；性能比较另记当时有无明显并行负载。
   若源码有未提交变更，另存相关源码差异及文件摘要，不能仅凭相同 commit 将两次运行视为同一实现。
3. 运行当前质量命令和一个正式示例，输出放在独立目录。
4. 保存既有 fail-closed、默认不开启晋升及事务输出测试的位置，后续作为兼容性断言。

以下是**当前即可执行**的 PowerShell 命令，在仓库根目录运行：

```powershell
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$baselineDir = "output/verification/v0_4/$runId-baseline"
New-Item -ItemType Directory -Force -Path $baselineDir | Out-Null
git rev-parse HEAD | Set-Content "$baselineDir/commit.txt"
git status --short | Set-Content "$baselineDir/worktree.txt"
& ./.venv/Scripts/python.exe --version
& ./.venv/Scripts/python.exe -m pip freeze | Set-Content "$baselineDir/dependencies.txt"
& ./.venv/Scripts/python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
& ./.venv/Scripts/python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
& ./.venv/Scripts/python.exe -m openparkcad solve examples/phase0_site.json --out "$baselineDir/layout.dxf" --preview "$baselineDir/layout.svg" --report "$baselineDir/report.json"
if ($LASTEXITCODE -ne 0) { throw 'Baseline solve failed' }
```

### 验收与回退

- 能找到本次源码身份、依赖记录和配套三件套。
- 示例的正式对象与 engineering / graph 结果一致。
- 本步不改变运行逻辑，无代码回退项。

## 6. E1—E2：建立比较工具和 optimizer CI

### E1.1 建立案例清单

**实现文件**：`benchmarks/layout_v0_4.json`。

执行步骤：

1. 显式列出当前 `examples/` 的 16 个示例和 `tests/fixtures/v0_3/` 的 3 个场地，不使用会悄悄扩大集合的递归 glob。
2. 为每个案例记录 `case_id`、相对路径、覆盖标签、来源类型、预期判定及必须执行的检查。
3. 将既有测试里的语义断言作为预期来源，不把第一次运行的车位数直接固化成正确答案。
4. 给日常 smoke 标注一个小子集：基础、绕障、严格单向双出口、车辆拒绝、配额场地。完整集用于 E8。

manifest 中至少有以下概念；实际字段在 E1 固定并由 runner 验证：

```json
{
  "version": "layout-benchmark-manifest-1",
  "cases": [
    {
      "case_id": "irregular-courtyard",
      "path": "tests/fixtures/v0_3/irregular_courtyard_pass.json",
      "provenance": "synthetic",
      "tags": ["irregular", "vehicle", "site_constraints"],
      "expectation": "valid_with_required_checks"
    },
    {
      "case_id": "tight-rear-court",
      "path": "tests/fixtures/v0_3/tight_rear_court_reject.json",
      "provenance": "synthetic",
      "tags": ["vehicle_rejection"],
      "expectation": "reject_vehicle_policy"
    },
    {
      "case_id": "offset-gate-quota",
      "path": "tests/fixtures/v0_3/offset_gate_quota_reject.json",
      "provenance": "synthetic",
      "tags": ["quota", "contact"],
      "expectation": "valid_only_if_quotas_and_contacts_satisfied"
    }
  ]
}
```

上面仅展示三行，当前清单完整列出 19 个原始输入和 1 个跨主路收益示例。配额场地的本质断言是配额与接触规则成立；若新搜索真实找到满足全部规则的方案，允许从拒绝变成有效，记录原因并核验，不能为了维持旧结果而强制拒绝。

### E1.2 实现 runner 和单案例 worker

**实现文件**：`tools/benchmark_layouts.py`、`tools/benchmark_case.py`、`tests/test_layout_benchmark.py`。

执行步骤：

1. 父进程读取 manifest；每个案例、变体、重复次数使用独立子进程顺序运行，采用当前 `sys.executable`。
2. worker 深拷贝输入，只修改变体明确列出的 optimization 字段；保存原始文件 SHA-256、实际输入副本与实际输入摘要。
3. 用 Python API 取得 `LayoutResult`，提取失败证据。不要依赖失败 CLI 生成正式报告：当前 CLI 会在正式写入前拒绝无效布局。
4. 有效结果复用现有最终判断和事务导出路径；无效结果只写基准诊断 `result.json`，明确 `result_scope=benchmark_diagnostic`，不伪装成正式三件套。
5. 父进程施加独立的每次运行硬超时，保存 stdout/stderr、退出状态和已完成证据。超时只终止该次 worker，不影响其他案例。
6. 用 `valid`、`invalid`、`input_error`、`exception`、`timeout` 区分结局；预期拒绝是成功覆盖一个拒绝场景，程序异常不是预期拒绝。
7. 输出所有案例的结果后再汇总。预期不符、异常或超时使 runner 返回非零；符合预期的无效场地不使整套验证失败。

运行产物约定：

```text
output/benchmarks/v0_4/<run-id>/
  run.json                         # commit、环境、参数、manifest 摘要
  summary.csv                      # 每案例 / 变体 / 重复的结果
  comparison.md                    # 有效性、质量、耗时和变化说明
  cases/<case-id>/<variant>/<repeat>/
    effective-input.json
    result.json                    # 有效和无效运行都保留
    stdout.txt
    stderr.txt
    layout.dxf                     # 以下三件仅在最终有效时生成
    layout.svg
    report.json
```

必须记录的指标：

| 类别 | 字段 / 内容 |
| --- | --- |
| 身份 | commit、dirty 状态、输入摘要、案例来源、变体、重复编号 |
| 参数 | 实际约束、评分配置、晋升设置、seed、后端时间限制、worker 配置 |
| 后端 | requested / actual backend、fallback reason、求解 status / objective / bound / gap |
| 最终结果 | 是否有效、有效车位总数与分类、道路面积、最终 score、正式布局语义摘要 |
| 检查 | graph、maneuver、vehicle、site/quota、engineering、operational 的执行与失败信息 |
| 代价 | 总耗时；E5 接入后增加基线、额外优化、重建验证等阶段耗时 |
| 搜索 | 生成/去重/保留/评估数量、预算是否耗尽、获胜 candidate_id |

没有数据的字段写 null 或明确 `not_available`，不能写 0 或 pass。声明启用车辆检查但实际没有执行时，仍按现有契约判定，不能只依据空列表断言通过。

### E1.3 固定比较方式

初始实现 `--profile legacy`，运行 legacy-greedy 与 legacy-cpsat；E7 完成后加入 `--profile multi` 和 `--profile all`。

质量对比变体统一显式开启 `promote_candidate_layout_preview=true`，从而比较能实际导出的结果。另有兼容性测试保留每个输入原本的晋升设置；两类结果不能混写。

同一案例各变体保留同样的车位、车辆、硬约束、评分权重及 selector 时间设置，只更改已声明的后端和搜索模式。不要给缺少车辆参数的示例强行开启 exact；需要检查模式对比时建立单独的、有完整参数的案例组。

比较规则：

- 有效 → 有效：比较最终分数、车位数、道路面积和耗时；车位少但分数高的方案需要说明取舍。
- 无效 → 有效：记录“恢复可行解”，不从无效布局的分数计算提升百分比。
- 有效 → 无效：记录退化并追查；保留有效基线的路径原则上不应出现此情况。
- 无效 → 无效：比较拒绝类别和实际检查覆盖，不比较零车位的“收益”。
- requested=cpsat 但 actual=greedy：作为回退证据保留，不能计入 CP-SAT 成功运行样本。
- 不将不同场地、不同权重下的原始 score 直接相加后宣布总收益。

E1 的测试只需要用小型案例验证 runner 的协议：有效、预期拒绝、异常、超时、输入不被修改、后端回退标记。性能比较不放进普通单元测试。

**E1 完成标准**：一次命令产出可浏览的全套结果；无效案例仍有可定位的诊断；缺依赖和程序错误不会被当作正常有效结果。

### E2.1 增加 optimizer CI 路径

修改 `.github/workflows/ci.yml`，保留当前 Python 3.10 / 3.12 的 `[dev]` 路径及已有覆盖率、build、installed-wheel smoke。

新增一个 Python 3.12 optimizer job：

1. 安装 `.[dev,optimizer]`，打印 OR-Tools 版本并显式执行 import，安装或 import 失败直接让该 job 失败。
2. 运行 `test_candidate_cpsat.py`、`test_selection_rebuild.py`、`test_stall_modules.py`、`test_mixed_segment_scoring.py`，以及后续新增的多主路集成测试。
3. 成功路径必须断言 actual backend 为 cpsat、fallback reason 为空；不能只断言“得到一个有效布局”。
4. 保留缺 OR-Tools 的模拟测试，并在默认依赖 job 验证默认求解不会导入 OR-Tools。

可比较实验使用可选 `optimization.selector_num_workers`：不提供时保留当前 OR-Tools 行为；基准配置和小型确定性测试显式设为 1，并记录实际值。仅设置 seed 不能承诺有时间限制的求解在所有机器上都产生相同结果。

该参数的 selector 传递、CP-SAT 设置、局部解析和 provenance 在 E2 一起实现；E7 再将说明与完整搜索 Schema 汇合。E1 的旧版本测量尚无该参数时记录 `not_available`，不得假称已使用单 worker，也不与 E2 之后的受控性能数据混为同一批。

**E2 完成标准**：两个依赖路径都有证据；optimizer job 的成功测试实际执行，没有因缺少 OR-Tools 而 skip。CI 不运行完整、重复三次的性能基准。

**回退**：benchmark 工具独立于默认求解；CI 改动可独立撤回。不要通过删除成功路径断言或把失败改成 skip 来维持绿色状态。

## 7. E3—E6：实现多主路候选搜索

### E3.1 引入上下文类型与隔离测试

按 4.3 节建立候选类型与 ID。先新增 `tests/test_layout_candidate_context.py`，覆盖：

- 相同入口/heading、不同 lateral offset 有不同身份。
- 相同入口参数、不同 dogleg / multi-jog 几何有不同身份。
- 相同骨架、不同车位类型组合有不同 candidate_id。
- C1 的支路、会车袋 feature 或晋升编号变化不改变 C2 和原始输入。
- C1、C2 都有 `A-MAIN` / `A-BRANCH-001` 时，其目录、父关系及冲突仍独立。

这里需要真实小型几何和对象关系断言；仅比较两个 hash 字符串不足以证明目录隔离。

### E3.2 拆开候选评估与发布

**实现模块**：`openparkcad/layout_evaluation.py`、`openparkcad/layout_search.py`。

执行步骤：

1. 将“构建目录 → selector → 预览 → 正式对象重建 → 检查和评分”整理成可被协调器调用的评估接口。
2. 评估接口返回数据，不写正式文件，不修改全局基线，不通过递归调用 `generate_layout` 再次启动搜索。
3. 允许内部评估在晋升开关关闭时构建一份待选正式几何对象；这只是在内存中检查可发布性，不等于获准发布。
4. 为候选报告传入清楚的 scope。只有选中的正式结果使用 `official_layout`；内部候选证据明确标明候选 ID 和候选作用域。
5. 保留旧入口包装，使 legacy 模式仍按原来的顺序、评分和平局规则执行。

针对性回归先运行现有 promotion、selection rebuild、validation closure、contact retarget 和 passing bay 测试。此时新增搜索模式尚不接管默认入口。

### E4.1 暴露已生成但尚未获胜的主路

修改 `phase1_candidates.py`，在以下局部择优发生之前获得完整上下文：

1. 一个入口、heading、entrance offset、lateral offset 下的直线候选。
2. 同一组合下已经构建的各 dogleg 偏移候选。
3. 当前 multi-jog 规划器实际构建的候选。
4. 不同 main / branch 车位类型组合下的上述候选。

首版可以使用显式 collector 或新枚举接口，legacy 包装仍取原来相同的结果。不要从 `AngleAttempt` 的计数和诊断重新拼装主路；该结构缺少完整骨架和候选自己的 site 状态。

当前 multi-jog 内部仍存在逐段贪心决策，本轮保留它实际构建的路线，不枚举所有横移路径。报告用“已生成候选范围”描述覆盖，不能声称遍历了所有可行骨架。

拆分 `phase1_candidates.py` 时只迁移本步触及的职责，例如骨架枚举与来源记录；模块名由实际依赖决定。保留兼容包装，避免一次移动车位放置、所有绕障、连接路和会车袋逻辑。

### E4.2 候选可评估性

先排除无道路几何、非法输入、缺少硬检查必需参数、确定违反硬道路排除的候选，并记录拒绝原因。

车位数量不足、车位被过滤或配额未满足，不一定证明这套骨架不能通过模块重选修复。只要有完整且结构可用的骨架上下文，允许保留为 `requires_layout_rebuild`；仍必须经过最终全部检查才能获胜。本轮不保证恢复这些案例的可行解。

若某模板内部尚不能返回可用上下文，报告 `not_collected` 与原因，不把遗失的几何计为“已搜索且无解”。

**E4 完成标准**：同一场地至少能取得两套实际不同、各自完整的骨架；旧入口仍保持 legacy 结果；目录没有跨上下文对象。

### E5.1 建立确定的保留列表

执行顺序：

1. 收集候选来源和轻量摘要，对完全相同的上下文去重。
2. 有旧流程获胜原始上下文时，将它固定放在保留列表首位，复用已有评估。
3. 对剩余候选按模板初始最终分数降序排序，平局按 candidate_id 排序；尚不可行的候选列在可行候选之后，并记录其状态。
4. 先从尚未覆盖的 `(入口, 实际出口, straight/dogleg/multi_jog 家族)` 中依上述顺序各取一个，再按原排序补满。
5. 截取到 Top-K；K 小于可用家族数时，如实报告未保留的候选，不声称所有家族得到深入优化。
6. 保存生成数量、去重数量、保留数量、各候选的保留/排除理由。轻量尝试历史与实际可求解目录分开存放。

Top-K 是计算成本控制，不保证找到所有上下文中的最好解。增大 K 的实验要单独记录质量与耗时。第一版先测量候选对象内存，尽早释放不再需要的完整结果，只保留摘要；不在缺少测量时引入持久化缓存或分布式求解。

B 连同它原来的选择证据作为只读保底保存。旧流程可能保有聚合的 attempts 历史，这部分只能展示为 legacy 来源，不能拿来构建其他新候选的目录。若重新评估旧骨架，也必须从它自己的完整上下文开始。

### E5.2 预算和确定性

- 额外预算从基线 B 建立完成后开始。基线生成、原有局部选择、最终文件写入的耗时独立记录。
- 新候选开始前检查剩余预算；CP-SAT 每次实际限制为现有 selector 限制与剩余预算中的较小值。
- 几何检查使用现有同步函数，首版不承诺能在任意函数执行中打断；记录实际超出预算的时间和原因。
- 一个候选必须完成重建与检查才能参选。预算到期时未完成验证的对象不进入正式结果集合。
- 没有时间进行下一个候选时返回当前最好有效结果，报告 `budget_exhausted`；它与无可行方案是不同状态。
- `top_k=1` 直接复用 legacy 结果，不额外进行会改变正式结果的重选，适用于基线有效和无效两种情况。
- 基准父进程的硬超时包含整个 worker；协调器的额外预算不能冒充端到端硬时限。

测试用可控时钟和小型评估替身验证边界，不依赖“恰好运行 10 秒”的脆弱断言。候选枚举、ID 和并列排序应确定；有墙钟预算的 CP-SAT 结果只承诺报告可追溯，不承诺跨机器完全相同。

### E6.1 每套骨架独立完成评估

对每个保留上下文 C：

1. 构建只属于 C 的目录、模块、依赖与冲突。
2. 执行请求的 greedy / CP-SAT，并保存该次真实的 selected_ids 和 provenance。
3. 构建预览，按既有顺序处理过滤、retarget 和再次检查。
4. 将预览重建成正式 ID 的候选对象，重新验证全部适用约束并调用正式评分。
5. 保存模板原始有效结果作为该上下文内部的备选；局部 selector 导致无效或降分时，仍可使用该原始有效结果。
6. 记录候选内部采用了 template 还是 selector 结果，及对应 ID 映射。

不要在记录获胜 provenance 时重新运行 selector。可以重建目录来核对正式对象，但第二次求解可能产生另一组选择，不能用它覆盖实际产生获胜几何的选择记录。

### E6.2 全局协调器择优

1. 从有效 B 和已完成完整复核的 C 中选择正式 score 最高者；B 无效时不拿它的分数作为晋升下限。
2. 对仍有候选时按 4.2 节处理晋升开关；不开启时只更新新比较报告。
3. 候选分数与 B 相同则保留 B；其他候选并列时按稳定 candidate_id 决定。
4. 非有限分数、缺失验证块或缺少正式对象视为评估错误，不能参加排序。
5. 确定 O 后构建与它一致的正式快照、对象 ID、图及验证证据，再交给原 CLI 的最后检查与事务写入。

**E6 必须有的收益证明**：新增一个小型合成场地，旧流程按模板初始分数选择 A，而 B 在完成模块选择和正式复核后分数更高。测试证明 B 被选中、全部检查通过且确实导出 B 的几何。协调器可另用替身测试排序逻辑，但至少一个端到端收益案例必须使用真实几何和真实评估链。

**回退**：将 `layout_search.mode` 设为 `legacy` 即回到旧流程。若无效改进或超时发生，不更改输入约束或降低检查强度；保留 B 和失败证据。算法内部异常需要修复，不允许用通用异常捕获静默维持“通过”。

## 8. E7：接入输入、报告与正式输出

### E7.1 输入与诊断

修改 `models.py`、`schema/openparkcad-input.schema.json`、`diagnostics.py` 和 `docs/input_model.md`：

1. 对 `layout_search` 和 `selector_num_workers` 增加有类型的解析及局部校验，模型默认值与 Schema 默认说明一致。
2. `field_support` 明确区分未请求、可用、实际执行和执行失败；不能仅凭输入存在就标 active。
3. 报告 requested / effective backend、搜索模式和 worker 数；有效配置中保留用户声明及本次实际采用值。
4. 原始输入与各候选的合成 site_features 分开追踪；输出采用获胜候选自己的 features，不能把所有候选生成的会车袋并在一起。

新增 `examples/multi_spine_comparison_site.json` 作为 E6 真实几何收益案例的易运行版本，显式设置 multi_spine、cpsat 和 `promote_candidate_layout_preview=true`，用于覆盖完整交付链。使用现有完整 JSON 输入形状，不把第 4 节的参数片段当作可独立求解文件。

### E7.2 新增比较报告

已新增独立版本块 `layout_search.version = "layout-search-1"`。顶层报告契约能兼容增加字段时沿用原版本；若改变原字段含义或删除字段，必须同时更新契约、测试和使用方。本轮优先采用增加字段的方式，不提前改包版本。

新块至少包含：

| 字段 | 语义 |
| --- | --- |
| `mode` / `status` | 实际模式；`not_requested`、`completed` 或 `budget_exhausted` |
| `baseline` | 基线身份、有效性、正式分数、车位数、已有局部晋升情况 |
| `counts` | 已生成、可收集、去重、保留、已评估、已验证、未评估数量 |
| `budget` | 配置预算、实际耗时、是否耗尽、未完成阶段 |
| `candidates` | 各候选来源、保留原因、选择结果、最终有效性和最终分数 |
| `best_preview_candidate_id` | 最好且完整复核的待选方案；没有时为 null |
| `official_candidate_id` | 本次正式结果来源；无有效正式结果时为 null |
| `publication` | 晋升是否请求、是否替换、保留基线/拒绝的理由 |
| `quality_delta` | 有效基线和有效正式结果之间的差；恢复可行解时单独标记 |

每个被深入评估的候选记录：

- `candidate_id`、`spine_id`、入口/出口、模板家族、方向、入口及横向偏移、车位类型组合。
- `requested_backend`、`actual_backend`、`fallback_reason`、seed、实际 workers、时间限制、status、objective、bound、gap。
- 初始分数、预览分数、正式重建后分数，以及最终采用 template / selector 的原因。
- 各验证结果、失败类别、最终车位分类数量、ID 映射与阶段耗时。
- 被 Top-K 排除、预算未评估、重建失败、较低分、同分保留基线等明确理由。

主报告保存候选摘要和最好预览所需的可检查几何；完整候选证据可由基准工具保存到各方案自己的目录。不要把每个候选的全部快照重复嵌入所有报告，造成输出体积随候选数的多层增长。

### E7.3 对齐正式结果

逐项核对：

1. 顶层 aisles、stalls、score、selected assignment、图与验证全部描述 O。
2. O 的道路父关系、连接路端点、车位 serving aisle 都指向 O 内部真实存在的对象。
3. 既有 `candidate_snapshot`、预览及局部晋升报告仍围绕 O 的局部选择链；跨主路的替换原因放到新 `layout_search.publication`，不混淆两次选择。
4. 已实际用于获胜方案的 selected_ids 和 provenance 原样保留，并能映射到正式对象。
5. `engineering_validation.result_scope` 在最终报告中为 `official_layout`；内部候选证据不假称正式结果。
6. DXF 与 SVG 使用 O 的实际几何、role、车位类型和 ID。仅比较截图相似不能证明一致。

验证现有事务写入：最后一步检查失败或写入失败时，不替换原三件套；基准诊断另存到独立位置。不要为保存失败信息而让失败 CLI 输出看似正式的有效报告。

## 9. E8：正确性回归与效果验收

### 9.1 必须覆盖的测试矩阵

测试按责任分文件，包括 `test_layout_candidate_context.py`、`test_layout_search.py`、`test_layout_search_integration.py`、`test_layout_search_report.py`；文件名可随实现组织调整，但以下行为必须有证据。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| T01 | 无新参数 / 显式 legacy | 正式几何、分数、原有有效性语义保持 |
| T02 | multi_spine，Top-K=1 | 复用 legacy 正式结果，不意外改变晋升行为 |
| T03 | 多骨架，关闭晋升 | 新报告可比较候选，正式对象仍为 B |
| T04 | 原模板第二名优化后胜出 | 使用真实几何链，最终 O 确实来自该候选 |
| T05 | 最优 shadow objective 的方案最终无效或降分 | 用正式复核与分数择优，保留可用基线 |
| T06 | 已有基线局部晋升 | 比较基准包含旧晋升收益，不退回未晋升模板 |
| T07 | 不同候选复用相同局部道路 ID | 不发生父关系、冲突、模块和 feature 串用 |
| T08 | 相同入口参数但不同横移 / 绕障几何 | 身份不同，两者能独立进入候选集 |
| T09 | 预算开始前 / 中途耗尽 | 无未验证对象获胜，保留有效 B，状态明确 |
| T10 | OR-Tools 不存在、异常、无解或未获得解 | 按既有契约回退，actual backend 和原因准确 |
| T11 | OR-Tools 真正成功 | 实际运行 cpsat；结果经过几何、配额、车辆和最终评分复核 |
| T12 | 基线无效，候选可恢复 | 晋升开启才可发布，关闭时仍不发布；无效基线不参与分数差 |
| T13 | 全部无效 | CLI 非零退出，保留失败类别，旧文件不变 |
| T14 | 配额 / 接触、严格单向双出口、会车袋 | 沿用原约束，获胜方案 own-site 状态完整 |
| T15 | 最终对象和报告 | 官方 ID、快照、provenance、DXF/SVG 和报告一致 |
| T16 | 输出写入故障 | 三件套不部分替换 |
| T17 | 非法新配置 | 清楚报错，无未知开关假执行或非法值静默变默认 |
| T18 | 基准有效、预期拒绝、异常、超时、回退 | 汇总分类正确，输入不被修改，缺失值不伪造成零 |

T04 至少同时有一个 greedy 集成证明；CP-SAT 实际成功路径由 optimizer job 覆盖。复杂时间行为可以用可控替身测试，但几何收益、正式对象重建和输出一致性不能全部用 mock 代替。

沿用现有代表性车辆拒绝测试。新的路径只能在真的满足已请求车辆检查时接受案例，不能通过关闭 exact、放宽半径、删除不方便的障碍或降低配额获得改进。

以下是各步骤对应的针对性验证命令，测试文件均已实现；文件改名时同步这些入口。

```powershell
# E2：已有选择链的真实 optimizer 路径
& ./.venv/Scripts/python.exe -m pytest -q -rs tests/test_candidate_cpsat.py tests/test_selection_rebuild.py tests/test_stall_modules.py tests/test_mixed_segment_scoring.py
if ($LASTEXITCODE -ne 0) { throw 'E2 validation failed' }

# E3—E4：上下文、枚举提取和原有晋升行为
& ./.venv/Scripts/python.exe -m pytest -q tests/test_layout_candidate_context.py tests/test_candidate_promotion_consistency.py tests/test_main_aisle_dogleg.py tests/test_passing_bay_synthesis.py
if ($LASTEXITCODE -ne 0) { throw 'E3-E4 validation failed' }

# E5—E6：协调、预算及真实几何收益
& ./.venv/Scripts/python.exe -m pytest -q tests/test_layout_search.py tests/test_layout_search_integration.py
if ($LASTEXITCODE -ne 0) { throw 'E5-E6 validation failed' }

# E7：最终报告、输出与原有拒绝语义
& ./.venv/Scripts/python.exe -m pytest -q tests/test_layout_search_report.py tests/test_cli_and_exporters.py tests/test_validation_closure.py tests/test_v0_3_integration.py
if ($LASTEXITCODE -ne 0) { throw 'E7 validation failed' }
```

E1 单独执行 `python -m pytest -q tests/test_layout_benchmark.py`；在本地使用同一 `.venv` 解释器。每步只运行其相关测试；E8 / E9 再执行完整回归，不要求每修改一处都重复整套慢案例。

### 9.2 效果比较顺序

1. 先运行 smoke 子集一次，确认所有变体正确执行、报告没有缺字段或冒名后端。
2. 再运行完整 20 案例（19 个原始案例加跨主路收益示例），先看判定变化和失败分类，不急于计算平均收益。
3. 对需要性能结论的案例固定参数重复 3 次，顺序运行，记录中位数和范围；只有 3 次时不报告有统计意义的 P95。
4. 对变化案例保留 baseline / candidate 的 SVG、正式报告和解释；指出是哪套骨架、哪些车位或道路改变了结果。
5. 对新增的 E6 收益场地给出具体有效分数差、车位差和耗时，证明功能确实跨越了旧流程的早期选择限制。
6. 若有退化，区分更严格且合理的检查发现问题、实现回归和预算限制；本轮没有授权放松检查来掩盖退化。

性能指标采用实际测量，不预设无依据的“至少提升 10%”或固定秒数合格线。现有公开示例若没有平均收益，也应如实报告；只要跨骨架能力有端到端证据、默认行为兼容且代价可观察，仍可以交付实验功能，不宣称普遍更优。

基准工具的四个变体统一覆盖 `selector_seed=17`、`selector_num_workers=1` 和 `selector_time_limit_seconds=2.0`，并保存实际后端的参数。该覆盖只作用于基准的 effective-input，不修改原始案例，也不改变普通 CLI 的默认值。`result.json.cost` 与 CSV 保存总耗时、基线、额外优化和收集耗时；重建已包含在额外优化内，尚无独立计时的 `rebuild_seconds` 明确为 `not_available`。

当基准证明重复构建可用区域、重复 vehicle 检查或 snapshot 成为热点时，再做一项有前后数据的局部优化。先限定在一次 solve 内缓存不可变派生值，缓存键包含实际候选 site、车辆和硬约束；不能跨候选直接复用通过结论。

### 9.3 真实案例的渐进补充

可取得真实场地时，为其记录：来源和可使用范围、单位/坐标、边界/入口/障碍处理、设计车辆、约束与人工方案版本、比较人员、日期及几何容差。

逐个添加匿名化案例；不将有来源限制的原始测绘文件放入公共 fixture。人工比较至少记录有效车位、道路占地、需要人工调整的位置和失败原因。合成案例、真实输入、完成人工比较三种状态分别记录，缺人工比较时不写“人工验证通过”。

## 10. E9：文档、构建和交付

### 执行步骤

1. 更新 `docs/current_status.md`，只将已通过验收的多主路能力写为 active，继续说明 Top-K 和模板范围。
2. 更新 `docs/v0_4_discrete_candidates.md`，说明每个主路内部仍是独立的局部离散模型，局部 gap 不代表全局 gap。
3. 更新输入文档、示例目录、README、roadmap 和 CHANGELOG；将本文件各步骤标记为完成，并链接执行证据。
4. 运行一次完整 lint / branch coverage。沿用当前覆盖率要求，不通过降低阈值完成交付。
5. 构建 wheel / sdist，在独立验证环境安装 wheel，离开源码目录后用完整示例运行 CLI，核对新搜索报告和正式对象。
6. 验证两条回退路径：改成 legacy；保持 multi_spine 但关闭晋升。两者分别验证正式行为，而不仅仅是配置可以解析。

文档实现不自动等于发布 `0.4.0`。包版本、tag 和发布动作按实际交付请求处理；本轮执行证据使用源码 commit 和报告算法版本识别能力。

### 命令入口

以下基准接口均已实现；`all` 执行四种变体。命令和参数需要与工具帮助保持一致。

```powershell
# E1 完成后：建立旧流程对比
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
& ./.venv/Scripts/python.exe tools/benchmark_layouts.py --manifest benchmarks/layout_v0_4.json --profile legacy --subset smoke --repeats 1 --timeout-seconds 120 --out "output/benchmarks/v0_4/$runId-legacy"
if ($LASTEXITCODE -ne 0) { throw 'Legacy benchmark needs inspection' }

# E7 完成后：四个变体的完整比较
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
& ./.venv/Scripts/python.exe tools/benchmark_layouts.py --manifest benchmarks/layout_v0_4.json --profile all --subset full --repeats 3 --timeout-seconds 180 --out "output/benchmarks/v0_4/$runId-all"
if ($LASTEXITCODE -ne 0) { throw 'Benchmark needs inspection' }

# E9：保留现有质量与构建入口
& ./.venv/Scripts/python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
& ./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { throw 'Coverage or tests failed' }
& ./.venv/Scripts/python.exe -m build
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
```

`--profile all` 固定为 legacy-greedy、legacy-cpsat、multi-greedy、multi-cpsat。`smoke` 对应 manifest 标记的小集合，`full` 对应全部清单。每次 120 秒是可调的基准硬超时，用来保护批量执行；超时必须出现在结果里，不是修改算法有效性规则的依据。

本轮完整对比需要安装 `.[dev,optimizer]`。工具即使记录了缺 OR-Tools 的回退，也必须将需要 cpsat 的性能比较标为 incomplete 并返回非零，不能把两条实际 greedy 曲线当作双后端对比成功。

### 独立 wheel smoke

以下命令属于 **E9**。沿用 [CI](../.github/workflows/ci.yml) 的“安装 wheel 后离开源码目录运行”原则，使用独立目录构建与安装，避免误取 `dist/` 中历史 wheel。现有 `.venv` 保留。

```powershell
$planRepoRoot = (Get-Location).Path
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$wheelCheckDir = Join-Path $planRepoRoot "output/verification/v0_4/$runId-wheel"
New-Item -ItemType Directory -Force -Path $wheelCheckDir | Out-Null
& ./.venv/Scripts/python.exe -m build --outdir "$wheelCheckDir/dist"
if ($LASTEXITCODE -ne 0) { throw 'Isolated build failed' }
$builtWheels = @(Get-ChildItem -LiteralPath "$wheelCheckDir/dist" -Filter '*.whl')
if ($builtWheels.Count -ne 1) { throw 'Expected exactly one newly built wheel' }
& ./.venv/Scripts/python.exe -m venv "$wheelCheckDir/venv"
if ($LASTEXITCODE -ne 0) { throw 'Verification environment creation failed' }
$wheelCheckPython = Join-Path $wheelCheckDir 'venv/Scripts/python.exe'
& $wheelCheckPython -m pip install "$($builtWheels[0].FullName)[optimizer]"
if ($LASTEXITCODE -ne 0) { throw 'Wheel installation failed' }
& $wheelCheckPython -m pip freeze | Set-Content "$wheelCheckDir/dependencies.txt"
Get-FileHash -LiteralPath $builtWheels[0].FullName -Algorithm SHA256 | Format-List | Out-File "$wheelCheckDir/wheel-sha256.txt"
$wheelRunDir = Join-Path $wheelCheckDir 'run'
New-Item -ItemType Directory -Force -Path $wheelRunDir | Out-Null
Push-Location $wheelRunDir
try {
    & $wheelCheckPython -c "from pathlib import Path; import sys, openparkcad; assert Path(openparkcad.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); print(openparkcad.__file__)"
    if ($LASTEXITCODE -ne 0) { throw 'Package was not loaded from the wheel environment' }
    & $wheelCheckPython -m openparkcad solve "$planRepoRoot/examples/multi_spine_comparison_site.json" --out layout.dxf --preview layout.svg --report report.json
    if ($LASTEXITCODE -ne 0) { throw 'Installed wheel solve failed' }
    $wheelReport = Get-Content -LiteralPath 'report.json' -Raw | ConvertFrom-Json
    if ($wheelReport.layout_search.mode -ne 'multi_spine') { throw 'Search mode was not exercised' }
    if ($wheelReport.engineering_validation.valid -ne $true) { throw 'Engineering validation failed' }
    if ($wheelReport.engineering_validation.result_scope -ne 'official_layout') { throw 'Wrong result scope' }
    $cpsatEvaluations = @($wheelReport.layout_search.candidates | Where-Object { $_.actual_backend -eq 'cpsat' })
    if ($cpsatEvaluations.Count -eq 0) { throw 'CP-SAT success path was not exercised' }
    foreach ($artifact in @('layout.dxf', 'layout.svg', 'report.json')) {
        if ((Get-Item -LiteralPath $artifact).Length -le 0) { throw "Empty artifact: $artifact" }
    }
}
finally {
    Pop-Location
}
```

常规 `python -m build` 与上述独立构建属于同一个构建检查点，选择独立构建即可，不必为满足清单机械地执行两次。CI 的默认依赖 smoke 继续负责未安装 optimizer 的普通路径。

## 11. 可逐项勾选的交付清单

以下清单以 [本轮验收记录](v0_4_multi_spine_acceptance.md) 和 [机器可读统计](verification/v0_4_20260903.json) 为当前结论。旧版本历史日志继续保留，不代替本轮验证。

- [x] E0：已有基线可复查，未覆盖原有工作与输出。证据：scratch `e0-baseline/`（commit `2e2766ae81d975e042551a24c18c8a1a88056ca1`，301 passed，`examples/phase0_site.json` 三件套 stall_count=83，`engineering_validation.result_scope=official_layout`）；仓库内 gitignored 副本 `output/verification/v0_4/20260902-214041-baseline/`。本步无运行时逻辑改动。
- [x] E1：20 个明确输入进入 manifest；smoke/full 集合固定，runner 保存成功、拒绝、异常、超时及原始输入哈希。真实基准参数和后端记录见验收统计。
- [x] E2：远端 Python 3.10 / 3.12 默认依赖路径均通过（337 passed、8 个可选测试跳过）；optimizer job 显式导入 OR-Tools，63 passed、0 skipped。[源码 67eec8b 的 CI](https://github.com/helenananaa/OpenParkCAD/actions/runs/33736091918)。
- [x] E3：候选身份、数据隔离和无发布副作用的评估接口完成。证据：`openparkcad/layout_candidates.py`、`openparkcad/layout_evaluation.py`、`tests/test_layout_candidate_context.py`；scratch `e3e4-pytest.log`。
- [x] E4：直线、偏移和已生成的绕障候选在局部择优前保留完整上下文。证据：`collect_phase1_candidate_contexts` / `collect_layout_candidate_contexts`；同一场地可取得多套完整骨架，legacy `generate_layout` 包装保持原结果。scratch `e3e4-pytest.log`。
- [x] E5：Top-K、基线保留、计时和预算状态可解释；基线仅按真实骨架几何等价匹配。基线生成时收集上下文，复用路径由集成回归与完整基准验证。
- [x] E6：同一合成场地经过完整几何计算与优化后可由另一骨架胜出；比较示例 65→67 车位，三次 end-loop CP-SAT 对比为 82→105 车位。实际选择与 solver provenance 随结果保存。
- [x] E7：新开关、Schema、field_support、比较报告和正式输出一致；已补齐晋升状态、正式验证及预览到正式道路/车位 ID 的映射，导出不再次求解。
- [x] E8：完整回归 345 passed，覆盖率 83.68%；20 案例 × 4 变体 × 3 次共 240 次基准全部符合预期，120/120 CP-SAT 请求实际执行，无回退或源输入变更。收益、持平、拒绝和耗时范围见验收记录。
- [x] E9：wheel / sdist 构建成功；独立环境先验证默认依赖路径，再验证 optimizer 晋升和完整报告。legacy / 关闭晋升两条回退均得到 65 个车位且正式几何相同；包内 Python 源文件哈希与验收源码一致。

建议实施提交边界与 E 步骤对应：基准工具、CI、候选隔离、枚举提取、搜索协调、报告集成、验证收尾。每个边界可独立审查，不把纯代码移动和算法行为变化混成一个无法核对的大改动。是否创建提交、推送或发布由实际任务要求决定。

## 12. 本轮之后的执行顺序

这些步骤承接本轮结果，具有独立验收条件，不要求为完成 E0—E9 一并实现。

下面保留原有顺序概述；详细契约、代码落点、命令、验收和回退见 [后续五阶段执行计划](next_stages_execution_plan.md)。

### R1—R3：道路级车辆通行验证

**R1：定义与构造最小转弯对象。** 从入口转入、主支路交叉、dogleg 折弯各取一个小场地。对象明确进入/离开姿态、所在道路、设计车辆、允许倒车、可行驶区域和障碍；复用 `vehicle_kinematics.py` / `swept_path.py` 的底层几何与包络计算，不直接把停车模板当作道路转弯模板。

**R2：验证单个连接动作。** 先覆盖受支持的刚性乘用车低速转弯，增加足够宽可通过、内角受阻、外轮廓越界、反向通行和缺参数案例。报告模型、采样/包络容差、路径和失败位置；未实现的转弯类型明确 unsupported。

**R3：验证连续路线。** 将连接动作与入口、车位停车动作及出口路线衔接，检查相邻动作位置和朝向连续性。各路口分别有可行转弯，不自动证明它们能组成同一条完整路线。支持的路线需要一条可检查的连续证据，并接入候选评估和最终输出。

交付条件：至少有一个图上连通但车辆实际转不过的案例被明确识别，以及一个连续路线通过的对照；现有场地未请求新检查时保持原语义。新检查的请求参数与失败策略在 R1 的独立契约中定义。

### C1—C3：最小 CAD 输入与方案查看

**C1：受限 DXF 输入。** 先支持明确图层上的直线闭合多段线边界和障碍、显式入口标记。定义图层映射、单位换算、局部原点和回写坐标转换；弧线、bulge、未闭合/自交边界等首版不支持的数据给出具体诊断，不静默改变几何。导入结果仍落到现有 JSON 模型。

**C2：导入回环检查。** 用已知尺寸的 DXF 验证单位、方向、原点偏移和边界/障碍关系，导出后恢复源坐标。增加大坐标、毫米输入和缺单位声明案例；单位未知时要求显式指定，不能推测后直接计算车位。

**C3：轻量方案查看。** 先提供场地、候选切换、有效车位/分数对比、失败对象高亮及导出。复用本轮 candidate_id 和报告证据，使用户能够从画面定位到检查结果。

交付条件：一份支持范围内的 DXF 可以转换、求解、比较并回写正确坐标；用户能看到导入诊断及未支持实体。界面不能把未验证候选显示为正式可用方案。

### C4：锁定局部并重新生成

先定义可锁对象与冲突行为：主路、入口或车位组使用稳定来源 ID 和几何约束；重新生成时把锁定对象作为明确约束，不能在编号重排后锁到另一个对象。

用一个保留主路重新排车位的案例，以及一个锁定对象与新障碍冲突的案例验证。无解时保留原方案并指出冲突；不暗中移动锁定对象。完成这些行为后再扩展交互编辑种类。

### 后续拓扑和领域能力

使用基准中的实际失败类别决定下一次扩展：多出入口协调、复杂环路、更多绕障候选、专用无障碍通道、应急车辆、坡度或区域规则。每次选一个能复现的场地问题，先定义可执行检查，再增加相应生成能力，并纳入同一套案例对比。
