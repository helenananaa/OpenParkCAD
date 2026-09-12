# v0.5 执行计划：RoadSkeleton 路网骨架与 parallel-ladder 平行多通道

状态：**待实施的执行文档，不是当前能力声明。** 本文中的新模块、字段、测试、报告和命令，只有在对应 N 步完成并通过该步验收后才能视为可用。当前已实现能力以 [当前能力矩阵](current_status.md)、运行时报告和实际代码为准。

编写日期：2026-09-04。编写时本地分支为 `codex/v0.3-vehicle-validity`，HEAD 为 `9f33503`，分支领先远端 7 个提交，且存在既有未提交修改。当前本地树的 Ruff 检查通过，448 项测试通过，分支覆盖率 83.52%；这些数字只是本次编写前的本地快照，不是后续必须硬编码的测试数量，也不是远端 CI 或发布状态。

本计划承接 [v0.4 多主路执行计划](v0_4_multi_spine_execution_plan.md)、[后续五阶段执行计划](next_stages_execution_plan.md) 的 K1—K4，以及 [算法设计讨论](algorithm_design_discussion.md) 的“骨架 → 停车模块 → 选择 → 整体复核”结构。

## 1. 本轮目标

本轮只交付一个清晰能力增量：

> 将现有 straight / offset / dogleg / multi-jog 等主路候选适配到统一的 `RoadSkeleton` 中间层，并新增可显式启用的 `parallel_ladder` 平行多通道路网族，使支持范围内的宽矩形、L 形或分翼场地能够比较多条平行停车通道及一端/两端横向连接方案。

完成后，系统应具备以下行为：

1. 默认配置仍运行当前 legacy 流程，正式结果不因安装了新代码而变化。
2. 当前 multi-spine 流程可以通过 legacy adapter 消费统一骨架，但其原有候选身份、结果和验证语义保持兼容。
3. 用户显式开启 `parallel_ladder` 后，系统生成多套彼此隔离的完整路网骨架。
4. 每套骨架分别放置停车模块、运行 greedy 或 CP-SAT、正式重建并执行全部适用检查。
5. 只有几何、交通图、车辆/停车动作、道路连续通行、场地/配额、工程和运营门槛均满足的候选，才有资格在现有晋升开关授权下替换正式结果。
6. 搜索失败必须区分“场地几何确实冲突”“本轮拓扑不支持”“预算未完成”和“程序错误”。
7. 报告保存每套路网的来源、身份、预筛、完整检查、耗时和未选原因。

### 1.1 本轮不做

以下内容明确不计入本轮完成条件：

- 任意自由曲线路网、通用 A* / Hybrid A* 场地级路网生成；
- 任意数量、任意角度的交叉口；
- 通用边界跟随环路、中央岛环路或迷宫式障碍绕行；
- 所有入口/出口组合的联合优化；
- 动态交通、多车冲突、排队和信号/优先权仿真；
- 一次 CP-SAT 同时决定连续中心线坐标、全部道路拓扑和所有车位；
- 将局部 CP-SAT 的 `optimal` / gap 解释为全场布局全局最优；
- 将合成案例通过解释为真实场地效果、规范符合或施工可用；
- 顺便重写当前 CAD 导入、项目 UI 或区域规则系统。

边界跟随环路和受限障碍路由可以作为 v0.5 后续独立拓扑迭代，但必须复用本轮骨架契约和验证链，不与本轮并行塞入同一个验收目标。

## 2. 为什么先做这一轮

当前生成器已经能生成多种主路形态，但大部分拓扑逻辑集中在 [phase1_candidates.py](../openparkcad/phase1_candidates.py)，并依赖某个入口坐标系和“主路/支路”局部约定。继续增加条件分支会导致：

- 不同拓扑难以共享稳定身份和报告；
- 交叉口、道路方向和车辆动作难以统一验证；
- 新拓扑容易绕开正式重建或复制其他候选的证据；
- 候选数量、预算和失败原因难以解释；
- 工程锁定无法稳定引用跨修订道路对象。

`parallel_ladder` 是第一个适合验证通用骨架层的拓扑：它比单主轴明显更接近常见地面停车场，又只需要受控的平行道路、横向连接路和有限 T 形连接，不必立刻解决任意路网规划。

## 3. 必须保持的可信性不变量

以下不变量在 N0 固定，并贯穿 N1—N9：

1. **默认不变**：没有显式开启新族时，输入、正式几何、有效性、评分和输出语义保持原状。
2. **候选隔离**：每个 skeleton 拥有自己的 site 上下文、道路、停车模块、冲突矩阵、求解器 provenance、车辆证据和诊断。
3. **图不是路径证明**：`traffic_graph` 只负责便宜的拓扑预筛；请求 `road_traversal` 时仍必须通过真实姿态和包络路线。
4. **正式对象重算**：候选成为正式结果后必须使用正式 ID 重建并重新执行适用检查，不能复制预览布尔值。
5. **失败关闭**：请求的硬检查出现 missing、unsupported、incomplete、timeout 或 failed 时，不得发布为通过。
6. **预算诚实**：预算结束的候选记录为未完成，不作为无解，也不能参与正式晋升。
7. **输出一致**：正式 DXF、SVG、JSON、review bundle 和 manifest 必须引用同一个正式布局身份。
8. **局部最优分离**：selector objective / gap、骨架预筛分和最终 `score_layout` 分开报告。
9. **来源可追溯**：每条道路和每个停车模块可以追溯到 skeleton、生成参数和父对象。
10. **可回退**：新骨架层适配和 `parallel_ladder` 族分别可关闭；关闭优化功能不得关闭用户请求的硬验证。

## 4. 代码落点

| 文件 / 模块 | 当前职责 | 本轮落点 |
| --- | --- | --- |
| [models.py](../openparkcad/models.py) | SiteSpec、LayoutResult 和生成结果 | 只增加必要报告字段；避免把搜索内部对象塞入公共布局模型 |
| [phase1_candidates.py](../openparkcad/phase1_candidates.py) | 当前 straight/offset/dogleg/multi-jog、支路和连接路生成 | 保留原实现；由 adapter 提取 legacy skeleton，不在本轮继续堆 ladder 特例 |
| [layout_candidates.py](../openparkcad/layout_candidates.py) | 候选上下文、稳定 ID 和隔离 | 加入 `skeleton_id` / version；维持跨拓扑隔离 |
| [layout_search.py](../openparkcad/layout_search.py) | Top-K、多候选评估和正式晋升 | 消费不同 skeleton family；记录预筛、预算和族级统计 |
| [layout_evaluation.py](../openparkcad/layout_evaluation.py) | 候选完整评估 | 保证 ladder 与 legacy 使用相同正式检查链 |
| [traffic_graph.py](../openparkcad/traffic_graph.py) | 几何接触和有向可达性 | 支持 skeleton 转换后的有限 T 形/横向连接拓扑预筛 |
| [road_transitions.py](../openparkcad/road_transitions.py) | 道路局部动作 | 增加 ladder 所需的有限 T 形和端部转弯动作 |
| [road_traversal.py](../openparkcad/road_traversal.py) | 入口—停车—出口整体道路路线 | 对 ladder 的道路段和转弯动作建立姿态连续路线 |
| [candidate_catalog.py](../openparkcad/candidate_catalog.py) | 离散模块目录 | 表达 skeleton 道路依赖、停车侧和横向连接依赖 |
| [candidate_selector.py](../openparkcad/candidate_selector.py) | greedy 模块选择 | 在单个 skeleton 内选择兼容模块 |
| [candidate_cpsat.py](../openparkcad/candidate_cpsat.py) | 可选 CP-SAT 选择 | 只解决离散模块，不决定连续道路坐标 |
| [candidate_layout_preview.py](../openparkcad/candidate_layout_preview.py) | 预览构建和检查 | 从选定 skeleton + modules 构造本候选预览 |
| [candidate_snapshot.py](../openparkcad/candidate_snapshot.py) | 正式重建、晋升和证据 | 正式 ID 映射、重新验证和 provenance |
| [project_model.py](../openparkcad/project_model.py) | 工程版本和对象身份 | N8 后才接 skeleton 来源和锁定映射 |
| [review_bundle.py](../openparkcad/review_bundle.py) | 冻结候选几何 | 展示完整 ladder 候选及未选原因 |
| [cli.py](../openparkcad/cli.py) | 输入、拒绝发布和事务输出 | 接入新报告；不增加绕过现有 validity 的独立导出路径 |
| [schema](../schema/openparkcad-input.schema.json) | 输入 Schema | N8 冻结后加入显式配置 |

计划新增：

```text
openparkcad/
  road_skeleton.py
  road_skeleton_geometry.py
  road_skeleton_validation.py
  topology_generators/
    __init__.py
    legacy_adapter.py
    parallel_ladder.py

tests/
  test_road_skeleton.py
  test_road_skeleton_geometry.py
  test_road_skeleton_validation.py
  test_legacy_skeleton_adapter.py
  test_parallel_ladder.py
  test_parallel_ladder_integration.py
  test_parallel_ladder_road_traversal.py
  test_parallel_ladder_report.py

examples/
  parallel_ladder_rect_site.json
  parallel_ladder_l_site.json
```

目录和文件名是目标接口；在对应步骤创建前不能按现有模块使用。

## 5. 目标数据契约

### 5.1 RoadSkeleton

N2 计划在 `openparkcad/road_skeleton.py` 中定义轻量、不可变或深拷贝边界清楚的数据结构。建议最小形状：

```python
@dataclass(frozen=True)
class RoadNode:
    id: str
    kind: str  # entrance_port | junction | terminal | turnaround
    point: tuple[float, float]
    heading_degrees: float | None
    source_id: str | None


@dataclass(frozen=True)
class RoadSegment:
    id: str
    role: str  # spine | parking_aisle | cross_aisle | exit | turnaround
    start_node_id: str
    end_node_id: str
    centerline: tuple[tuple[float, float], ...]
    width: float
    directionality: str
    parking_sides: tuple[str, ...]
    source: Mapping[str, object]


@dataclass(frozen=True)
class RoadMovement:
    id: str
    from_segment_id: str
    to_segment_id: str
    via_node_id: str
    movement_kind: str
    allowed: bool


@dataclass(frozen=True)
class RoadSkeleton:
    version: str
    skeleton_id: str
    family: str
    nodes: tuple[RoadNode, ...]
    segments: tuple[RoadSegment, ...]
    movements: tuple[RoadMovement, ...]
    entrance_ids: tuple[str, ...]
    source: Mapping[str, object]
```

最终字段可在 N2 调整，但必须满足：

- 所有 ID 确定、与 Python `hash()` 无关；
- `skeleton_id` 包含规范化后的几何、宽度、方向、连接和入口身份；
- 生成参数只作为 provenance，不替代实际几何摘要；
- 道路多边形是 centerline + width 的派生产物，不允许报告中心线 A、验证多边形 B；
- 道路交叉不自动代表允许所有转向，必须由 `RoadMovement` 或等价结构声明；
- skeleton 不持有其他候选可变的列表或字典引用。

### 5.2 计划输入

N8 完成前，以下字段只是目标契约：

```json
{
  "optimization": {
    "layout_search": {
      "mode": "multi_spine",
      "top_k": 6,
      "refinement_budget_seconds": 20.0
    },
    "road_network": {
      "enabled": true,
      "families": ["legacy", "parallel_ladder"],
      "max_skeletons": 16,
      "dominant_axis_count": 2,
      "max_parallel_aisles": 6,
      "cross_aisle_policy": "both_ends",
      "allow_one_way_loop": false
    }
  }
}
```

建议语义：

| 字段 | 缺省值 | 语义 |
| --- | --- | --- |
| `road_network.enabled` | `false` | 是否启用新骨架生成层；关闭时保持当前行为 |
| `families` | `["legacy"]` | 允许进入外层搜索的骨架族；非法值报输入错误 |
| `max_skeletons` | `16` | 所有新族合计最多保留的骨架预筛数量，不含已经建立的有效 legacy 基线 |
| `dominant_axis_count` | `2` | 最多尝试的场地主方向数量；入口 heading 仍是独立候选来源 |
| `max_parallel_aisles` | `6` | 单个 ladder 候选最多停车通道数，不是至少生成数 |
| `cross_aisle_policy` | `entry_end` | `entry_end` / `both_ends`；其他策略留待后续 |
| `allow_one_way_loop` | `false` | 是否生成当前受支持的单向闭环变体；N5 完成前不得开放 |

布尔值不得当整数；数量必须为正整数；预算必须为有限正数。非法新字段不能静默回退到 legacy。现有其他 optimization 字段不因本轮顺便增加新门槛。

### 5.3 计划报告

顶层新增或在 `layout_search` 内加入 `road_network_search`，版本建议为 `road-network-search-1`：

```json
{
  "version": "road-network-search-1",
  "requested": true,
  "executed": true,
  "families": ["legacy", "parallel_ladder"],
  "counts": {
    "generated": 0,
    "deduplicated": 0,
    "prefilter_passed": 0,
    "retained": 0,
    "fully_evaluated": 0,
    "verified": 0,
    "incomplete": 0
  },
  "budget": {
    "configured_seconds": 20.0,
    "elapsed_seconds": 0.0,
    "exhausted": false
  },
  "skeletons": [],
  "failure_counts": {}
}
```

每个 skeleton 至少记录：

- `skeleton_id`、family、生成参数和父入口；
- 节点/道路段/允许 movement 数量；
- 预筛状态和失败原因；
- 派生道路几何摘要；
- selector requested/actual backend 和 provenance；
- 模块数、冲突数和选择数；
- 完整检查状态；
- 最终车位数、正式分数和耗时；
- 是否进入 Top-K、是否完成、是否晋升及未选原因。

## 6. 总实施顺序

严格按 N0 → N1 → … → N9 执行。每一步都有独立提交候选和退出条件；某步失败时停在该步修复，不通过放宽 hard gate 进入下一步。

| 步骤 | 内容 | 主要产物 | 退出条件 |
| --- | --- | --- | --- |
| N0 | 冻结当前基线 | 源码/环境/行为/性能证据 | 当前状态可重复，既有修改未丢失 |
| N1 | 冻结案例与失败分类 | 合成案例、真实案例协议、人工参考 | 选定一个具体 ladder 问题 |
| N2 | RoadSkeleton 契约和基础验证 | 数据模型、ID、序列化、几何派生 | 同骨架稳定、不同骨架不误合并 |
| N3 | legacy adapter | 现有模板到 skeleton 的无损适配 | 默认和现有 multi-spine 语义不变 |
| N4 | parallel-ladder 候选生成 | 独立拓扑生成器和预筛 | 矩形/L 形正例与硬反例正确 |
| N5 | 连接动作和道路连续通行 | T 形/端部 movement、整体路线 | 图可达但车辆转不过的案例被拒绝 |
| N6 | 停车模块与 selector | skeleton 内 catalog/greedy/CP-SAT | 模块依赖、冲突、配额和 provenance 正确 |
| N7 | 外层搜索和正式晋升 | Top-K、预算、完整重建、统一报告 | 有效改进可晋升，无效/未完成不可晋升 |
| N8 | Schema、工程和查看链 | 输入、project、review bundle、CLI | 保存/重开和输出身份一致 |
| N9 | 效果、性能和发布级验收 | 回归、基准、wheel、回退、文档 | 支持边界与证据一致 |

每一步完成后记录：改动摘要、命令、退出码、证据目录、失败项、是否满足退出条件。不得只写“测试通过”而没有命令和输出身份。

## 7. N0：冻结当前基线

### 目标

保存当前混合工作树的准确身份和可重放结果，避免后续将既有变化误归因于 RoadSkeleton。

### 步骤

1. 检查 Git 分支、HEAD、远端差异和工作树。
2. 不清理、不 reset、不覆盖当前未提交修改。
3. 保存 tracked diff、未跟踪文件清单、Python/依赖/OR-Tools 版本。
4. 运行 Ruff、完整测试和构建。
5. 分别运行 legacy 与 multi-spine 示例，保存 DXF/SVG/report。
6. 保存报告中的正式布局身份、车位数、分数、有效性、实际 selector backend 和耗时。
7. 计算证据文件 SHA-256 并写 manifest。

以下命令**当前即可执行**：

```powershell
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$n0Dir = "output/verification/v0_5/$runId-n0-baseline"
New-Item -ItemType Directory -Force -Path $n0Dir | Out-Null

git rev-parse HEAD | Set-Content "$n0Dir/commit.txt"
git status --short --branch | Set-Content "$n0Dir/worktree.txt"
git diff --binary | Set-Content "$n0Dir/tracked.patch"
git ls-files --others --exclude-standard | Set-Content "$n0Dir/untracked.txt"
& ./.venv/Scripts/python.exe --version | Set-Content "$n0Dir/python.txt"
& ./.venv/Scripts/python.exe -m pip freeze | Set-Content "$n0Dir/dependencies.txt"

& ./.venv/Scripts/python.exe -m ruff check . --no-cache 2>&1 |
  Tee-Object "$n0Dir/ruff.log"
if ($LASTEXITCODE -ne 0) { throw 'N0 Ruff failed' }

& ./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing 2>&1 |
  Tee-Object "$n0Dir/pytest.log"
if ($LASTEXITCODE -ne 0) { throw 'N0 pytest failed' }

& ./.venv/Scripts/python.exe -m build 2>&1 |
  Tee-Object "$n0Dir/build.log"
if ($LASTEXITCODE -ne 0) { throw 'N0 build failed' }

& ./.venv/Scripts/python.exe -m openparkcad solve examples/phase0_site.json `
  --out "$n0Dir/legacy.dxf" `
  --preview "$n0Dir/legacy.svg" `
  --report "$n0Dir/legacy.json"
if ($LASTEXITCODE -ne 0) { throw 'N0 legacy solve failed' }

& ./.venv/Scripts/python.exe -m openparkcad solve examples/multi_spine_comparison_site.json `
  --out "$n0Dir/multi.dxf" `
  --preview "$n0Dir/multi.svg" `
  --report "$n0Dir/multi.json" `
  --review-bundle "$n0Dir/multi-review.json"
if ($LASTEXITCODE -ne 0) { throw 'N0 multi-spine solve failed' }

Get-ChildItem -LiteralPath $n0Dir -File |
  Get-FileHash -Algorithm SHA256 |
  Select-Object Path, Hash |
  ConvertTo-Json -Depth 3 |
  Set-Content "$n0Dir/sha256.json"
```

### 退出条件

- Git/环境/依赖/差异均被保存；
- Ruff、测试、build 和两个 solve 均有明确结果；
- 生成物彼此使用独立文件名，没有覆盖既有 output；
- 若任何项失败，先记录为基线缺口，不进入 N2 修改架构。

### 回退

N0 不修改运行时代码。若需要清理输出，先核对绝对路径在 `output/verification/v0_5/<run-id>` 内，再按工作区归档策略处理，不编写宽范围递归删除命令。

## 8. N1：冻结场地和失败分类

### 目标

证明下一轮是在解决一个真实可描述的问题，而不是为了抽象而抽象。

### 步骤

1. 建立一个宽矩形场地：人工参考为 3 条平行停车通道 + 两端横向连接。
2. 建立一个 L 形场地：人工参考至少使用两个翼区，当前单主路明显浪费其中一个区域。
3. 建立一个窄场地反例：几何上放不下两条通道，不允许通过缩窄硬要求伪造 ladder。
4. 建立一个图接触但车辆无法完成 T 形转弯的反例。
5. 若已有获授权真实 CAD，按 [真实案例协议](real_site_case_protocol.md) 记录；没有则将案例明确标为 synthetic。
6. 对每例保存当前 legacy/multi-spine 结果、人工参考、失败分类和预期改进，不只保存成功截图。
7. 冻结开发集和保留评估集；同一批案例不得既调参数又宣称泛化改善。

计划新增：

```text
examples/parallel_ladder_rect_site.json
examples/parallel_ladder_l_site.json
tests/fixtures/v0_5/parallel_ladder_tight_reject.json
tests/fixtures/v0_5/parallel_ladder_turn_reject.json
docs/topology_iteration_parallel_ladder.md
```

`topology_iteration_parallel_ladder.md` 至少写明：来源类型、当前失败、人工解决、硬约束、预期输出、资源预算、非目标和退出条件。

### 退出条件

- 至少 2 个正例、2 个硬反例；
- 每个案例的“为什么当前不够”和“什么才算解决”可被测试化；
- 真实与合成来源不混称；
- 未将“增加固定数量车位”作为脱离场地的统一 KPI。

## 9. N2：RoadSkeleton 契约和基础验证

### 目标

建立与具体生成族无关、可序列化、可验证、可派生道路几何的骨架中间层。

### 实施步骤

1. 在 `road_skeleton.py` 定义 node、segment、movement、skeleton 和版本常量。
2. 定义规范化序列化：字段顺序稳定、浮点规范明确、集合排序确定。
3. 由完整规范化 payload 生成 `skeleton_id`，并在摘要碰撞时比较完整 payload。
4. 实现结构验证：
   - ID 唯一；
   - segment 引用存在的起止节点；
   - movement 引用存在的 segment/node；
   - 宽度有限且为正；
   - centerline 至少两个不同点；
   - directionality 是支持枚举；
   - entrance port 对应真实 SiteSpec entrance。
5. 在 `road_skeleton_geometry.py` 由 centerline + width 构造道路多边形；记录 join/cap 策略和版本。
6. 在 `road_skeleton_validation.py` 检查：
   - 道路处于可行驶区域；
   - 非声明连接的道路不得靠意外重叠建立边；
   - 声明连接处存在真实接触；
   - 入口喉部连接真实；
   - 道路自交、零长度、过窄、孤立分量和非法 movement 可定位。
7. 加入深拷贝/不可变性测试，证明修改候选 C1 不影响 C2 或 SiteSpec。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_road_skeleton.py `
  tests/test_road_skeleton_geometry.py -q
```

### 必须覆盖

- 相同输入跨进程得到相同 `skeleton_id`；
- 只改变宽度、方向或一个 movement 会改变 ID；
- 只改变字典插入顺序不会改变 ID；
- 两条道路几何相交但未声明 movement 时不自动允许转向；
- 声明连接但几何不接触时 fail-closed；
- centerline 派生多边形不能填平凹形不可行驶区域；
- 非法浮点、空线和重复 ID 被拒绝并带诊断。

### 退出条件

- 数据契约和算法版本固定为首版；
- 单独模块不依赖 `phase1_candidates` 内部私有坐标约定；
- 结构/几何错误都有稳定错误码；
- 尚未接入 generator，不改变任何正式结果。

### 回退

删除新模块和新测试即可；当前生成器没有调用它们。

## 10. N3：legacy skeleton adapter

### 目标

证明统一骨架层可以无损表达当前正式道路，而不是先重写现有算法再调到“差不多”。

### 实施步骤

1. 在 `topology_generators/legacy_adapter.py` 将当前 LayoutResult 道路转换为 RoadSkeleton。
2. 映射 main、jog、branch、connector、exit、passing_bay 和 turnaround 角色。
3. 根据当前显式关系建立节点和 movement；不得仅凭多边形相交猜全部连接。
4. 保存 legacy 对象 ID 到 skeleton segment ID 的双向映射。
5. 从 skeleton 重新派生道路几何，和原 LayoutResult 比较语义几何：
   - 面积/对称差在冻结容差内；
   - 角色、宽度、方向和连接一致；
   - 入口接触和 traffic graph 结果一致。
6. 第一阶段只 shadow 生成 skeleton 和报告，不改变正式布局。
7. shadow 对比稳定后，允许 multi-spine 候选携带 skeleton context，但正式几何仍由当前路径构建。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_legacy_skeleton_adapter.py `
  tests/test_layout_search.py `
  tests/test_layout_search_integration.py `
  tests/test_road_traversal_integration.py -q
```

### 退出条件

- straight、offset、dogleg、multi-jog、through-corridor 至少各有一个适配案例；
- 默认未请求 skeleton 报告时，正式报告语义不变；
- `top_k=1` 与当前 multi-spine 正式结果一致；
- adapter 不能给原本无效的图补出虚假连接；
- 旧生成函数仍可独立回退。

## 11. N4：parallel-ladder 独立候选生成器

### 目标

在不接入正式搜索前，独立生成并预筛有限的平行多通道路网骨架。

### 候选算法

1. 构造可行驶区域：boundary/setback 减去 hard aisle-affecting exclusions。
2. 建立候选方向：
   - 入口 heading；
   - 最长可用边界段方向；
   - 可行驶区域最小旋转矩形主轴；
   - 按无向轴去重，最多保留 `dominant_axis_count`。
3. 在每个方向坐标系中建立横向连接路候选。
4. 按实际模块宽度生成平行停车通道偏移：道路宽度、两侧停车深度、必要间距必须来自 SiteSpec，不能写死。
5. 对每条停车通道求可用连续区间；低于最小通行/停车长度时不生成。
6. 生成受控拓扑变体：
   - `entry_end`：入口端一条横向连接路；远端按现有掉头能力处理；
   - `both_ends`：两端横向连接，形成有限 ladder 环行；
   - `one_way_loop`：只在 N5 有完整动作支持后开放。
7. 连接节点只允许端点或明确 T 形位置；本轮不允许任意斜交叉。
8. 对完整道路带做硬几何预筛，不用中心线点落在场地内代替车道宽度检查。
9. 对规范化 skeleton 去重，保留生成来源和被去重原因。
10. 使用便宜预筛分排序，但不把它当最终布局分数。

建议预筛分量：

- 可用停车带总长度；
- 连通分量和入口可达；
- 道路占地；
- 死路长度；
- T 形连接数量；
- 预估不能满足转弯半径的节点数；
- 障碍最小净距；
- 平行通道数量。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_parallel_ladder.py `
  tests/test_road_skeleton_validation.py -q
```

### 必须覆盖

- N-T01：宽矩形生成至少两条不同 parking aisle 和真实 cross aisle；
- N-T02：L 形场地至少有候选利用第二翼，但不要求本步已经放车位；
- N-T03：窄场地不缩窄硬道路宽度来凑候选；
- N-T04：障碍切断道路带时，该 segment 被拒绝或拆成明确不连通诊断；
- N-T05：入口喉部与横向道路不真实接触时拒绝；
- N-T06：相同输入候选顺序和 ID 稳定；
- N-T07：`max_skeletons` 生效且被截断数量可见；
- N-T08：预筛最高不等于正式获胜，字段命名不得误导为 final score。

### 退出条件

- 正例生成可查看的 skeleton JSON/SVG 调试产物；
- 硬反例不生成伪有效道路；
- 尚不进入正式 candidate catalog 和输出；
- 性能记录含 generated/deduplicated/prefiltered 数量。

## 12. N5：连接动作与道路连续通行

### 目标

让 ladder 不只是图上连通，而是支持范围内的车辆可以从入口经过横向连接路进入停车通道、停车并驶出。

### 实施步骤

1. 冻结本轮允许的连接几何：正交 T 形、正交端部转弯；斜交和任意角度标记 unsupported。
2. 为每个 junction 建立进入/驶出姿态和允许 movement。
3. 在 `road_transitions.py` 生成直行、左转、右转候选动作；由实际车辆转弯半径和道路边界检查包络。
4. 连接动作必须检查局部真实路面联合，而不是对全部道路取凸包。
5. 将 movement 边接入 `road_traversal` 姿态状态图。
6. 将停车 aisle 上的道路姿态与现有 perpendicular/angled/parallel/T-end parking motion adapter 对接。
7. 对每个保留车位构造入口→道路→停车→驶出→出口路线。
8. 记录失败 junction、from/to segment、车辆姿态、碰撞对象和 unsupported reason。
9. 对 one-way ladder 检查方向闭环、禁止逆行和可退出性。
10. 超时/预算结束标记 incomplete；不能退化为 graph pass。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_parallel_ladder_road_traversal.py `
  tests/test_road_transitions.py `
  tests/test_road_traversal.py `
  tests/test_road_traversal_contract.py -q
```

### 必须覆盖

- N-T09：图上 T 形接触，但车辆转弯包络撞障碍，road traversal 必须失败；
- N-T10：两端连接的双向 ladder 每个保留车位都有进出路线；
- N-T11：单向闭环方向正确时通过，断开或需要逆行时失败；
- N-T12：一个 junction unsupported 时，不得只验证其他 junction 后报告全场 passed；
- N-T13：道路请求关闭时保持当前 validity；开启后要求 `status=passed` 且 `valid=true`；
- N-T14：路径证据身份包含 skeleton、正式道路/车位几何、车辆、策略和算法版本。

### 退出条件

- 至少双向 entry-end 和 both-ends 两个变体完成路线验证；
- one-way 未完成时保持配置不可用或明确 unsupported，不能部分开放；
- traffic graph pass / road traversal fail 的反例可重复；
- 正式路线不复用其他 skeleton 的缓存证据。

## 13. N6：停车模块和 skeleton 内 selector

### 目标

在每个 ladder skeleton 内生成并选择兼容停车模块，不把道路拓扑发明问题塞给 CP-SAT。

### 实施步骤

1. 每条 parking aisle 声明允许停车侧；cross aisle 默认不得自动铺停车位。
2. 复用现有 `place_main_family_stalls` 或提取公共停车带接口，避免复制各车位族几何。
3. 按 `(skeleton_id, segment_id, side, interval, stall_family)` 建立稳定 module ID。
4. 建立依赖：停车模块依赖父道路；端部连接路/掉头区可能禁止相邻停车模块。
5. 建立冲突：
   - 车位/道路/障碍几何冲突；
   - 相邻 aisle 背靠背停车带重叠；
   - junction clearance 区域冲突；
   - 特殊车位/通道/充电设备冲突。
6. greedy 与 CP-SAT 消费同一 catalog 和同一硬冲突。
7. selector 只选择离散道路/停车模块；道路中心线坐标已经由 skeleton 固定。
8. 完成选择后构建实际预览，并运行接触配额、车辆、图和道路检查。
9. 报告 requested backend、actual backend、fallback、objective、bound、gap、seed、workers 和 time limit。
10. CP-SAT 不可用/异常继续按当前定义回退；硬验证失败不能回退为“选择成功”。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_parallel_ladder_integration.py `
  tests/test_candidate_selector_closure.py `
  tests/test_candidate_cpsat.py `
  tests/test_discrete_candidates.py `
  tests/test_stall_modules.py -q
```

### 必须覆盖

- 父道路未选时其停车模块不能被选；
- junction clearance 与停车模块冲突时两者不能同时选；
- 相邻平行 aisle 的背靠背停车带不重叠；
- greedy/CP-SAT 均保持硬约束，结果差异可解释；
- CP-SAT optimal 只描述当前 skeleton catalog；
- 特殊车位配额只按最终保留且实际可达车位计数。

### 退出条件

- 每个 skeleton 有独立 catalog、冲突矩阵和 provenance；
- 至少 90 度车位族完成端到端；其他族未验证前明确 unsupported；
- 正式车位数来自重建布局，不来自模块估算数。

## 14. N7：外层搜索、预算和正式晋升

### 目标

将 legacy 和 ladder skeleton 放入同一外层搜索，完成 Top-K、预算、完整复核和正式结果选择。

### 实施步骤

1. 始终先建立当前 legacy 正式基线 B。
2. 收集 legacy adapter 和启用的新 skeleton family。
3. 用实际 skeleton payload 去重；跨 family 几何相同仍只保留一个，但来源列表完整。
4. 有效 legacy 基线不占用新 skeleton 的 `max_skeletons`，并必须进入最终比较。
5. 按确定规则保留 Top-K：至少包含 B、每个启用 family 的一个代表、其余按预筛排序。
6. 在 B 完成后开始 refinement budget；每个候选记录启动、完成和未完成阶段。
7. 对每个 retained skeleton 单独生成 catalog、运行 selector、构建预览、正式重建和完整验证。
8. 最终只比较完整验证后的正式分数；相同分数保留 B，减少无收益变化。
9. 晋升继续由现有 `promote_candidate_layout_preview` 授权。
10. B 无效时，只有显式授权且完整有效的新候选可以恢复正式输出。
11. 内部异常记录为 error 并使对应测试/基准失败，不能混为 no candidate。

### 目标命令

以下命令在 N8 输入和示例完成后才可执行：

```powershell
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$n7Dir = "output/verification/v0_5/$runId-n7-search"
New-Item -ItemType Directory -Force -Path $n7Dir | Out-Null

& ./.venv/Scripts/python.exe -m openparkcad solve `
  examples/parallel_ladder_rect_site.json `
  --out "$n7Dir/layout.dxf" `
  --preview "$n7Dir/layout.svg" `
  --report "$n7Dir/report.json" `
  --review-bundle "$n7Dir/review-bundle.json" `
  --diagnostics "$n7Dir/diagnostics.json"
if ($LASTEXITCODE -ne 0) { throw 'N7 ladder solve failed' }
```

### 必须覆盖

- N-T15：新族关闭时与当前结果一致；
- N-T16：新族开启、晋升关闭时只增加候选证据，正式布局保持 B；
- N-T17：完整有效且更高分的 ladder 在晋升开启时成为 O；
- N-T18：预筛更高但正式无效的 ladder 不能晋升；
- N-T19：预算耗尽保留有效 B，未完成候选不进入比较；
- N-T20：B 无效、新候选有效、晋升关闭时仍拒绝正式发布；
- N-T21：B 无效、新候选有效、晋升开启时可以恢复正式结果并记录原因；
- N-T22：输出 DXF/SVG/report/review bundle 的 official ID 一致。

### 退出条件

- 至少一个真实几何端到端案例展示 ladder 相对 legacy 的具体收益；
- 收益可以是解决原本 unsupported、减少人工修订或提高有效正式分数，不强制固定车位增量；
- 所有晋升路径都经过正式重建和全部适用检查；
- 外层 Top-K 与内层 CP-SAT provenance 分离。

## 15. N8：Schema、工程、review bundle 和 CLI

### 目标

将已验证契约接入公开输入、工程保存恢复和审查链，不提前暴露半实现字段。

### 实施步骤

1. 冻结 `optimization.road_network` 实际字段和默认值。
2. 更新 JSON Schema、输入文档和错误消息；未知 family 和非法数值明确拒绝。
3. 在 report 中加入 versioned road network search block。
4. review bundle 保存每个实际评估 skeleton 的道路/车位几何、有效性和未选原因，不为展示重新求解。
5. project 保存：
   - 输入配置；
   - skeleton/version；
   - project object → skeleton source → official object 映射；
   - 锁定对象和过期状态。
6. 锁定道路时，重新生成不得暗中移动该道路；新障碍冲突时保留原修订并报告冲突。
7. viewer 至少显示 family、skeleton ID、道路角色、方向、候选状态和失败 junction。
8. CLI 不新增跳过 validation 的“直接导出 skeleton”正式路径；调试导出必须明显标注 debug/non-official。
9. delivery manifest 继续标记 human review，不得自动批准。

### 针对性测试

```powershell
& ./.venv/Scripts/python.exe -m pytest `
  tests/test_parallel_ladder_report.py `
  tests/test_project_roundtrip.py `
  tests/test_project_service.py `
  tests/test_review_bundle.py `
  tests/test_viewer_bundle.py `
  tests/test_cli_and_exporters.py -q
```

### 退出条件

- 默认值向后兼容；非法配置 fail-closed；
- 保存/重开后 skeleton 和正式对象身份不漂移；
- review bundle 与当次实际评估结果一致；
- viewer 浏览器交互验证另有真实记录，静态 HTML 测试不冒充浏览器验收。

## 16. N9：效果、性能、wheel、回退和文档收尾

### 目标

证明这一轮既扩大了能力，又没有破坏当前可信边界，并形成可安装、可回退的验收证据。

### 16.1 完整质量验证

```powershell
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$n9Dir = "output/verification/v0_5/$runId-n9-release"
New-Item -ItemType Directory -Force -Path $n9Dir | Out-Null

& ./.venv/Scripts/python.exe -m ruff check . --no-cache 2>&1 |
  Tee-Object "$n9Dir/ruff.log"
if ($LASTEXITCODE -ne 0) { throw 'N9 Ruff failed' }

& ./.venv/Scripts/python.exe -m pytest --cov=openparkcad --cov-report=term-missing 2>&1 |
  Tee-Object "$n9Dir/pytest.log"
if ($LASTEXITCODE -ne 0) { throw 'N9 pytest failed' }

& ./.venv/Scripts/python.exe -m build 2>&1 |
  Tee-Object "$n9Dir/build.log"
if ($LASTEXITCODE -ne 0) { throw 'N9 build failed' }
```

不得通过降低现有覆盖率门槛或删除高风险测试完成收尾。测试总数可以自然变化；验收看具体契约覆盖，而不是必须等于 448。

### 16.2 基准矩阵

对以下组合顺序运行，避免并发争抢影响比较：

| 维度 | 取值 |
| --- | --- |
| 案例 | v0.4 既有 corpus + N1 正反例 + 获授权真实案例（若有） |
| 新族 | off / `parallel_ladder` |
| selector | greedy / cpsat |
| promotion | off / on |
| road traversal | unrequested / requested |
| 重复 | 正常案例至少 3 次；长耗时案例根据预先记录的资源预算执行 |

每例保存：

- 输入、代码/dirty-tree 身份和依赖；
- generated/deduplicated/retained/evaluated/verified/incomplete；
- 预筛时间、selector 时间、正式复核时间、总时间；
- 正式有效性、车位数、分数、道路占地、死路/环路指标；
- 人工参考差异、需要修订对象和原因；
- 收益、退化、持平和仍未解决分类。

性能门槛在 N0/N1 数据后冻结，不能在看到结果后临时放宽。至少设置：单例硬超时、refinement budget、最大 skeleton 数和最大完整评估数。

### 16.3 独立 wheel 验证

以下是目标流程；执行时使用独立临时目录和独立 Python 环境，不能因源码工作区在 `PYTHONPATH` 中而误用本地包：

```powershell
$wheelRoot = Join-Path $env:TEMP ("openparkcad-v05-wheel-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $wheelRoot | Out-Null

& python -m venv (Join-Path $wheelRoot 'venv')
$wheelPython = Join-Path $wheelRoot 'venv/Scripts/python.exe'
$wheel = Get-ChildItem -LiteralPath dist -Filter '*.whl' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $wheel) { throw 'No wheel found' }

& $wheelPython -m pip install $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw 'Wheel install failed' }

Push-Location $wheelRoot
try {
  & $wheelPython -I -m openparkcad solve `
    (Join-Path 'H:/program/OpenParkCAD' 'examples/parallel_ladder_rect_site.json') `
    --out layout.dxf `
    --preview layout.svg `
    --report report.json `
    --review-bundle review-bundle.json
  if ($LASTEXITCODE -ne 0) { throw 'Installed wheel solve failed' }
} finally {
  Pop-Location
}
```

wheel 验收必须解析 report，确认：

- package/version 和新 schema 资源来自 wheel；
- road network `requested/executed` 为真；
- family 实际包含 `parallel_ladder`；
- actual selector backend 符合安装依赖；
- 正式对象通过全部适用检查；
- DXF/SVG/report/review bundle 引用同一 official ID。

### 16.4 回退验证

至少验证四条回退：

1. `road_network.enabled=false`：回到当前 legacy/multi-spine 行为。
2. `families=["legacy"]`：保持骨架报告但不生成 ladder。
3. ladder 开启但 promotion 关闭：保留比较证据，不替换正式结果。
4. optimizer extra 缺失：按当前定义回退 greedy，报告真实 backend；不得关闭道路或工程 hard gate。

### 16.5 文档收尾

更新：

- `docs/current_status.md`：只写验收通过的 ladder 范围；
- `docs/input_model.md` 和 Schema：实际字段与非法输入；
- `docs/examples_catalog.md`：新示例和预期行为；
- `docs/roadmap.md`：本轮完成和后续 boundary-loop/obstacle-route；
- `README.md` / `docs/README.md`：入口链接；
- `CHANGELOG.md`：行为、默认值和回退；
- 新增 `docs/v0_5_parallel_ladder_acceptance.md`；
- 新增机器可读验收摘要 `docs/verification/v0_5_<date>.json`。

### 退出条件

- 完整 lint/test/build/wheel 通过；
- 默认路径、显式 ladder、晋升开关和缺 optimizer 回退均有证据；
- 既有 corpus 没有未解释退化；
- 至少一个冻结场地问题被解决；
- 没有真实案例时，文档明确写 synthetic-only；
- 当前能力矩阵和实际 report 语义一致；
- 发布/提交/push 仅在任务明确授权后执行。

## 17. 验收场景总表

| ID | 场景 | 预期 |
| --- | --- | --- |
| N-T01 | 宽矩形，双向，两端横向连接 | 生成至少两条停车通道和真实连接 |
| N-T02 | L 形，第二翼可利用 | 至少一个 ladder 候选进入完整评估 |
| N-T03 | 场地过窄 | 不缩窄硬宽度，不伪造候选 |
| N-T04 | 障碍切断停车通道 | 拒绝或明确不连通，不跨障碍连边 |
| N-T05 | 入口喉部未接触 | skeleton 预筛失败 |
| N-T06 | 相同输入重复运行 | skeleton ID、顺序和去重稳定 |
| N-T07 | 候选超过上限 | 截断数量、排序和原因可见 |
| N-T08 | 预筛高、正式分低 | 不把预筛分当正式分 |
| N-T09 | 图连通、转弯撞障碍 | graph 可通过，road traversal 必须失败 |
| N-T10 | 双向 both-ends | 每个保留车位有完整进出路线 |
| N-T11 | 单向闭环 | 正向可达；逆行或断环失败 |
| N-T12 | 一个 junction unsupported | 全场不得报告通过 |
| N-T13 | road traversal off/on | off 保持旧语义；on 要求 passed + valid |
| N-T14 | 路线缓存身份变化 | 几何/车辆/策略变化使证据失效 |
| N-T15 | 新族关闭 | 正式结果与当前基线一致 |
| N-T16 | 新族开、promotion 关 | 只新增比较证据，不替换 B |
| N-T17 | ladder 完整有效且更优 | promotion 开启后正式晋升 |
| N-T18 | ladder 正式无效 | 无论预筛多高都不晋升 |
| N-T19 | refinement budget 耗尽 | 保留有效 B；未完成不参与比较 |
| N-T20 | B 无效、ladder 有效、promotion 关 | 仍拒绝正式发布 |
| N-T21 | B 无效、ladder 有效、promotion 开 | 可恢复正式解并记录原因 |
| N-T22 | 多输出产物 | official ID、几何和有效性一致 |
| N-T23 | 保存、锁定、重开 | skeleton 来源和对象身份不漂移 |
| N-T24 | 既有 v0.3/v0.4 corpus | 默认模式不退化，显式变化有解释 |

## 18. 失败分类

实现和基准统一使用稳定失败分类，建议至少包括：

| 分类 | 含义 | 是否可晋升 |
| --- | --- | --- |
| `input_invalid` | 新配置或输入结构非法 | 否 |
| `unsupported_topology` | 超出本轮正交 ladder 范围 | 否 |
| `no_geometric_corridor` | 完整道路带无法放入可行驶区域 | 否 |
| `entrance_not_connected` | 入口姿态/喉部不能真实接入 | 否 |
| `junction_movement_unsupported` | 缺少请求的局部转弯动作 | 否 |
| `road_traversal_failed` | 路线存在但包络或方向失败 | 否 |
| `module_conflict` | 无法选择满足硬要求的模块组合 | 否 |
| `quota_failed` | 最终特殊车位/路线配额不满足 | 否 |
| `budget_exhausted` | 候选未完成完整评估 | 否 |
| `candidate_invalid` | 完整工程检查失败 | 否 |
| `valid_not_better` | 完整有效但不优于基线 | 否，保留作比较 |
| `internal_error` | 程序异常或证据不一致 | 否，并使验证任务失败 |

“没有找到支持的候选”不等于“数学上无解”。报告必须保留这个边界。

## 19. 每步提交建议

以下只是建议切片，不授权自动提交或 push：

1. N0/N1：基线协议和冻结案例。
2. N2：RoadSkeleton 数据契约、ID、几何和验证。
3. N3：legacy adapter 与无变化证明。
4. N4：parallel-ladder 独立生成和预筛。
5. N5：junction movement 与 road traversal。
6. N6：停车模块、greedy/CP-SAT。
7. N7：Top-K、预算、正式重建和晋升。
8. N8：Schema、project、review bundle、CLI 和文档。
9. N9：基准、wheel、验收记录和能力矩阵。

每次准备提交前运行该步针对性测试；改变正式生成/验证链的提交还要运行完整测试。不要将大批 output 运行产物直接提交；长期证据只保留小型机器可读摘要、必要 fixture 和验收说明。

## 20. 执行记录模板

每完成一步，在验收记录中追加：

```markdown
### N?. <名称>

- 状态：未开始 / 进行中 / 已完成 / 需外部资料
- 源码身份：commit + dirty-tree digest
- 改动文件：
- 执行命令：
- 退出码和结果：
- 证据目录：
- 正例：
- 反例：
- 性能：
- 已知限制：
- 回退验证：
- 是否满足退出条件：是 / 否
```

若状态不是“已完成”，后续步骤不得将该能力当作既有前提。

## 21. 最终完成清单

- [ ] N0：当前混合工作树、环境、质量和正式示例基线已保存。
- [ ] N1：2 个正例、2 个硬反例及来源/人工参考已冻结。
- [ ] N2：RoadSkeleton 契约、稳定 ID、派生几何和 fail-closed 结构验证完成。
- [ ] N3：legacy adapter 覆盖现有主要道路族，默认结果无语义变化。
- [ ] N4：parallel-ladder 独立候选生成和预筛完成。
- [ ] N5：支持范围内的 junction movement 和完整道路通行完成。
- [ ] N6：停车模块、依赖、冲突、greedy/CP-SAT 选择完成。
- [ ] N7：外层 Top-K、预算、正式重建和晋升语义完成。
- [ ] N8：Schema、report、project、review bundle、viewer/CLI 接入完成。
- [ ] N9：完整回归、效果/性能、独立 wheel、回退和文档验收完成。
- [ ] 所有正式输出均引用同一 official layout，且适用 hard gate 全部通过。
- [ ] local selector optimal/gap 未被宣传为全场全局最优。
- [ ] 没有真实人工对照时，所有效果结论明确标记 synthetic-only。
- [ ] 未经明确授权，没有执行 commit、push、tag 或发布。

## 22. 本轮之后

只有 N0—N9 闭合并获得案例证据后，才从失败频率选择下一项，而不是预先承诺全部实现：

1. `boundary_loop`：凹多边形、中央岛或建筑周边的边界跟随环路；
2. `obstacle_route`：基于可见图/栅格的受限多折线路径候选，再做曲率和完整包络复核；
3. `multi_entrance_network`：多个入口/出口组合及方向策略；
4. 更多 junction movement 和局部运动路径搜索；
5. 真实场地人工对照驱动的评分和默认策略决策。

后续每个族继续遵循：先冻结失败场地和不变量，再生成候选，随后接入完整验证，最后才接入搜索和正式晋升。
