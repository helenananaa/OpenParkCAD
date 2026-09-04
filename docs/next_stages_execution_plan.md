# 后续五阶段执行计划：道路通行、CAD 输入与交互设计

状态：**待实施的执行文档**。本次只编写计划；下文新增字段、模块、测试和 CLI 子命令均不构成当前能力声明。现有行为以 [当前能力矩阵](current_status.md)、实际代码和具体运行报告为准。

编写日期：2026-09-03。检查时仓库 HEAD 为 `cf48d6c`，运行时代码验收基线为 `67eec8b5274bf58e28b9ad1a73a34b134e0dbcc0`，包版本为 `0.3.0`。已完成的多主路搜索 E0—E9 见 [执行记录](v0_4_multi_spine_execution_plan.md) 和 [验收记录](v0_4_multi_spine_acceptance.md)。其中 345 项测试、240 次基准是该次验收证据，本次文档编写没有重新运行算法验收。

本计划承接原执行计划第 12 节，保留 R1—R3、C1—C4 的编号，并将后续工作展开为可独立交付的五个阶段。阶段编号不预先决定软件版本号。

## 1. 目标、实施顺序和使用方法

最终目标：用户能够输入一个支持范围内的实际场地，生成经过适用检查的停车方案，比较候选，保留局部设计并重新生成，最后交付可以追溯的图纸和检查记录。

| 大阶段 | 实施步骤 | 完成后用户获得什么 | 进入条件 |
| --- | --- | --- | --- |
| 一：道路级车辆通行 | B0、R1、R2、R3、R4 | 场地内部从入口锚点经停车动作到出口锚点的连续车辆轨迹与失败位置 | 当前多主路验收基线 |
| 二：真实 CAD 接入 | C1、C2 | DXF 导入、坐标回写、真实场地与人工方案对照 | R1 的几何/坐标约定确定；阶段交付衔接 R4 |
| 三：交互查看和修改 | C3、C4 | 比较方案、查看轨迹、锁定局部、重新生成、保存工程 | R4 与 C2 完成 |
| 四：复杂布局与搜索改进 | K1—K4 | 针对实际失败场地扩大可解范围、改善方案与耗时 | 有固定案例和 C3/C4 使用反馈 |
| 五：工程交付 | D1—D4 | 明确支持范围的规则配置、审查记录和安装交付包 | 前述流程可重复完成；交付场景明确 |

Q1 真实案例积累、Q2 性能记录、Q3 证据管理贯穿各阶段，详见第 9 节。资料采集、CAD 小样准备可以提前进行，不必等道路算法全部完成。没有真实场地时继续完成合成案例下的实现和工具验收，真实效果结论单列为尚无证据。

执行约定：

1. 按步骤的“前置条件 → 操作 → 产物 → 验收”推进；普通实现、修复和验证不需要逐步等待用户确认。
2. 每步只增加对应能力；遇到当前步骤必须处理的缺口，记录原因后完成最小必要修复。
3. 所有新增路径均在对应步骤注明“拟新增”；只有已经存在的文件使用可点击链接。
4. 命令分为“当前可执行”和“完成指定步骤后可执行”。不能运行尚未实现的命令后将报错误记为产品回归。
5. 同一阶段先做针对性验证，阶段收尾再运行全量回归和必要的构建验证；不在每个小改动后重复全量基准。
6. 已授权的实现持续做到可审查交付；提交、推送、发布及使用外部资料的范围服从实际任务授权，不由本计划额外增加确认流程。

## 2. 当前基础和必须保持的行为

### 2.1 可以直接复用的基础

| 当前文件 | 已有职责 | 后续使用方式 |
| --- | --- | --- |
| [models.py](../openparkcad/models.py) | `SiteSpec`、`EntranceSpec`、`VehicleSpec`、道路、车位、`LayoutResult` 及输入解析 | 扩展明确的道路检查配置和结果块 |
| [vehicle_kinematics.py](../openparkcad/vehicle_kinematics.py) | 后轴中心姿态、直线/定曲率运动、转弯半径转换 | 复用运动积分和车辆参数审计 |
| [swept_path.py](../openparkcad/swept_path.py) | 车辆轮廓、采样包络、停车动作模板 | 复用底层碰撞能力，给停车动作提供可组合的接口 |
| [traffic_graph.py](../openparkcad/traffic_graph.py) | 道路接触和有向可达性 | 用作拓扑预筛；不代替姿态连续路线 |
| [site_constraints.py](../openparkcad/site_constraints.py) | 约束范围、权威属性、几何排除、配额与路线接触 | 构造动作可行驶区和硬障碍 |
| [generator.py](../openparkcad/generator.py) | 生成、过滤、接触带调整和正式候选有效性 | 在最终几何确定后加入道路验证 |
| [candidate_layout_preview.py](../openparkcad/candidate_layout_preview.py) | 预览几何及验证 | 对预览自己的道路/车位运行同一检查 |
| [candidate_snapshot.py](../openparkcad/candidate_snapshot.py) | 局部候选晋升、正式 ID 和重新验证 | 正式重建后重新生成道路证据 |
| [layout_evaluation.py](../openparkcad/layout_evaluation.py)、[layout_search.py](../openparkcad/layout_search.py) | 多主路评估、Top-K、预算与最终择优 | 将道路验证纳入每套候选及最终结果 |
| [layout_candidates.py](../openparkcad/layout_candidates.py) | 候选上下文、稳定摘要与隔离 | 扩展验证身份，后续服务于工程对象来源 |
| [engineering_validation.py](../openparkcad/engineering_validation.py)、[diagnostics.py](../openparkcad/diagnostics.py) | 综合工程报告与字段执行状态 | 暴露 requested、executed、unsupported 和失败证据 |
| [cli.py](../openparkcad/cli.py) | 输入错误、最终拒绝、三件套事务输出 | 增加最终判断和独立诊断，保持正式发布语义 |
| [exporter_dxf.py](../openparkcad/exporter_dxf.py)、[exporter_svg.py](../openparkcad/exporter_svg.py) | 图纸和预览 | 轨迹可视化、坐标恢复、审查输出 |
| [layout_benchmark.py](../openparkcad/layout_benchmark.py) | manifest、隔离 worker、结果归类和计时 | 扩展道路检查证据与新案例，沿用原 runner |

### 2.2 保持现有契约

- 原有输入未请求新检查时，保持原有有效性、几何、评分、默认 legacy/greedy 和晋升设置。允许报告增加明确标为未请求的字段。
- `optimization.promote_candidate_layout_preview` 继续控制候选能否替换正式结果。有效预览不能绕过关闭晋升的设置。
- 请求新道路检查后，基线、候选、正式重建均服从同一要求；未验证的旧基线不能作为通过新检查的回退。
- 几何、maneuver/vehicle、道路图、site/quota、engineering、operational 的现有要求继续执行。新增一个汇总块不能遗漏原有检查。
- 道路检查在车位过滤、contact retarget 和最终几何调整后运行。任何后续几何变化均使旧证据失效。
- 候选来源 ID、正式 ID 和几何摘要分开保存。冻结 dataclass 不等于内部列表不可变，继续保证候选之间状态隔离。
- 失败时保持已有正式输出集；新增诊断写到单独位置。当前 CLI 的输入错误 2、无有效布局 3、输出失败 4 保持兼容。

## 3. B0：保存实施基线

**前置条件：** 开始实际代码实现，而非仅阅读或修改本计划。

执行步骤：

1. 检查 Git、分支和工作树，保留用户已有变更；源码有未提交变更时保存相应 diff 或文件摘要。
2. 记录 Python、依赖、源码身份和执行环境。先核实本地能运行当前 CLI。
3. 如果源码、依赖、环境相对验收记录已经改变，运行当前质量检查；否则引用匹配的已有证据，避免重复长时间验证。
4. 为本轮建立独立证据目录，保存一个正常示例和既有拒绝测试的位置。
5. 性能比较使用同机器、相同参数的前后运行。历史耗时仅作选样线索。

以下是**当前可执行**的 PowerShell 命令，在仓库根目录运行；用于需要刷新基线时：

```powershell
$ErrorActionPreference = 'Stop'
$stageRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$stageDir = "output/verification/next_stages/$stageRunId-baseline"
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
git rev-parse HEAD | Set-Content "$stageDir/commit.txt"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read source commit' }
git status --short | Set-Content "$stageDir/worktree.txt"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read worktree status' }
& ./.venv/Scripts/python.exe --version
if ($LASTEXITCODE -ne 0) { throw 'Python unavailable' }
& ./.venv/Scripts/python.exe -m pip freeze | Set-Content "$stageDir/dependencies.txt"
if ($LASTEXITCODE -ne 0) { throw 'Cannot record dependencies' }
& ./.venv/Scripts/python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
& ./.venv/Scripts/python.exe -m pytest -q --cov=openparkcad --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { throw 'Regression failed' }
& ./.venv/Scripts/python.exe -m openparkcad solve examples/phase0_site.json --out "$stageDir/layout.dxf" --preview "$stageDir/layout.svg" --report "$stageDir/report.json"
if ($LASTEXITCODE -ne 0) { throw 'Baseline solve failed' }
```

**产物与完成条件：** 实施源码和基线可追溯；有配套 DXF/SVG/report；未把当前未安装 OR-Tools 导致的跳过当作 optimizer 成功证据。B0 不要求修改运行时代码。

## 4. 阶段一：道路级车辆通行验证

阶段交付：在明确的车辆模型、场地内部范围和静态占用假设下，为每个保留车位提供可检查的进出路线，或明确报告尚未建立路线证据的原因。

先完成有限模板的证据链。完整路线验证不以任意路网、动态交通或通用路径规划为前置条件。

### 4.1 R1.1：固定检查范围和决策语义

**拟新增产物：** `docs/road_traversal_contract.md`、`openparkcad/road_traversal_models.py`、`tests/test_road_traversal_contract.py`。

执行步骤：

1. 定义场地坐标仍以米计，姿态参考点使用已有 `VehiclePose` 的后轴中心，角度使用 `heading_degrees`。
2. 首版车辆仅支持当前参数可审计的刚性乘用车，使用低速分段定曲率模型；转向变化过程和动态效应不进入首版证明范围。
3. 首版范围固定为 `site_interior`：从入口内侧锚点到出口内侧锚点。报告必须显示该范围，不宣称验证了场外道路接入。
4. 入口锚点由入口中心、宽度、heading、允许方向及真实入口喉部构造，车身完整位于声明的可行驶区域；同时检查内侧喉部通行宽度。不能只将后轴放在边界上，再忽略车身越界。
5. 如果案例要求从场外转入而缺少场外可行驶区，报告该动作 unsupported。后续支持显式场外接入区时形成新契约，不默认把场地外部视作无限道路。
6. 为每个最终保留车位寻找至少一套允许的入口、停车动作、驶出动作和出口组合。各车位可以使用不同入口/出口，不要求任意入口均可达。
7. 检查的是一辆设计车辆逐个使用车位的静态可行性；不表示所有车辆同时通行、可以会车或不存在排队。
8. 普通道路行驶不允许借用任何车位面；某车位的停车动作只能使用明确的服务道路、允许的交汇区域和目标车位。其他车位面按占用区排除；倒车所需的临时车道占用依现有方向/中心线策略判断。
9. 第一版道路行驶模板只生成前进动作；停车模板可以按原有策略倒车。若必须借助多次道路倒车才能连通，返回未支持/未找到支持范围内路线，不擅自开启倒车。
10. 第一版不自动删除道路不可达车位以取得通过：任一保留车位没有完整证据，当前候选不得以道路验证通过的名义导出。之后若增加删位修复，必须作为新候选重算配额和全部检查。

建议输入契约如下，**待 R1/R3 实现，当前不得作为已生效配置使用**：

```json
{
  "constraints": {
    "road_traversal": {
      "enabled": true,
      "scope": "site_interior",
      "time_budget_seconds": 10.0
    }
  }
}
```

这是合并到完整 SiteSpec 输入中的局部片段，不是可单独求解的场地。

| 配置 | 拟定语义 |
| --- | --- |
| 缺少 `road_traversal` 或 `enabled=false` | `not_requested`，不改变原有判定 |
| `enabled=true` | 请求每个保留车位的完整进出证据；有效策略包含必要的车辆和停车轨迹计算，不再要求用户重复开启第二个开关 |
| `scope` | 首版仅接受 `site_interior`；其他非约定值属于输入错误 |
| `time_budget_seconds` | 默认 10 秒，为单个具体布局的一次道路验证协作式预算；值必须为正有限数，排除布尔值；不是整个 solve 的硬超时 |

新检查启用时，在报告中同时保留用户原始 maneuvering 配置与道路检查所需的 effective policy，不能偷偷改写源输入。原有显式硬约束继续适用。道路预算包含转换动作构造、路线组合和路线复核；任何外层剩余预算更短时按较短截止时间执行并记录来源。已有 10 秒多主路 refinement budget 含新增评估代价，不能为每套候选重新启动外层预算。

**完成条件：** 输入开关、覆盖范围、停车动作策略、有效性含义和预算含义均可由测试断言；未支持要求不被当成通过。

### 4.2 R1.2：定义动作、姿态和证据对象

在拟新增 `road_traversal_models.py` 中定义以下轻量对象；具体字段名在 R1 完成时冻结：

| 对象 | 至少包含 |
| --- | --- |
| `TraversalPolicy` | requested、scope、解析后的车辆/方向/倒车限制、容差、预算与算法版本 |
| `TraversalState` | state_id、道路/入口/停车动作引用、后轴姿态、行驶方向、允许占用区域引用 |
| `RoadTransition` | transition_id、起止状态、道路关系、动作模板与参数、运动分段 |
| `TransitionEvidence` | executed、状态、起止实际姿态、车辆包络、碰撞位置、失败对象、耗时 |
| `StallJourneyEvidence` | stall_id、入口/出口、进场路径、停车/驶出动作、出场路径、连续性结果 |
| `RoadTraversalResult` | 结果身份、覆盖数量、状态汇总、实际预算、完整证据和失败分类 |

执行规则：

1. `TrafficGraph` 仍保留原有粗粒度用途。姿态状态可以在同一道路内有多个节点，不能用道路质心作为所有动作的共同姿态。
2. 验证身份至少包含完整道路/车位几何、入口方向、相关约束、车辆参数、策略及算法版本。只用 `candidate_id` 或相同车位数量不能复用轨迹。
3. 证据保留 source ID 与当前结果 ID 的映射。正式重建后的证据必须指向实际输出对象。
4. 姿态接合检查位置误差和环绕后的角度误差；数值容差写入版本化契约和报告。容差只处理浮点误差，不允许移动几何来补断口。
5. 停止后可以按模型约定切换前进/倒车，但切换点必须同位置、同朝向；不得把前进方向反转等同于车辆朝向翻转。

建议结果状态如下。综合判定必须显式要求 `status=passed` 且 `valid=true`，不能只排除 `valid=false`：

| 状态 | `executed` | `valid` | 请求检查后的含义 |
| --- | --- | --- | --- |
| `not_requested` | false | null | 不参与道路硬判定 |
| `passed` | true | true | 所有保留车位均有完整有效证据 |
| `failed` | true | false | 具体动作失败，或已穷尽支持模板仍无完整路线 |
| `unsupported` | 视实际计算记录 | null | 车型/动作/边界条件未支持，不能正式通过 |
| `incomplete` | 视实际计算记录 | null | 预算耗尽、取消或必需证据未完成，不能正式通过 |

输入结构或数值错误走现有输入错误通道；内部异常保留异常类别并使测试/基准失败，不能改写为 failed 或“场地无解”。对有限模板的失败使用 `no_supported_route_found` 等准确描述，不声称证明所有连续路径都不存在。

### 4.3 R1.3：核对底层运动与包络

**复用文件：** [vehicle_kinematics.py](../openparkcad/vehicle_kinematics.py)、[swept_path.py](../openparkcad/swept_path.py)。

执行步骤：

1. 复用 `rear_axle_turning_radius`、`simulate_bicycle_path`、`vehicle_footprint` 和 `validate_swept_path`，道路动作模块负责构造动作及其允许区域。
2. 用已知直线终点、90 度圆弧终点和左右镜像验证运动方向、单位和姿态；预期几何独立手算，不能调用被测构造函数生成预期值。
3. 检查前后悬、宽度、半径参考和轨迹 margin 都参与车身包络；缺必要参数时明确失败。
4. 验证圆弧采样之间的车身覆盖，加入障碍位于相邻采样点之间的案例。不能仅凭函数名或端点轮廓就认定覆盖连续运动。
5. 若现有包络不能给出所需保守界，先补经推导的弧间误差外扩或细分上界，再用于道路通过判定；不能仅靠一次密采样结果声称保证。
6. 分开记录数值几何容差、采样距离/角度和车辆安全余量。禁止通过扩大数值容差让碰撞变成通过。

**产物：** 底层必要修复、解析几何对照、弧间碰撞回归和模型假设。仅在上述风险需要时补测试，不另做全库重构。

### 4.4 R2.1：构造最小道路连接动作

**前置条件：** R1 契约及底层核对完成。

**拟新增：** `openparkcad/road_transitions.py`、`tests/test_road_transitions.py`、`tests/fixtures/road_traversal/`。

按以下顺序实现，每种动作至少配一个通过和一个拒绝案例：

1. 同一道路上的前进直线。
2. 场地内入口喉部到主路、主路到出口喉部的连接。
3. 主路与垂直支路之间的左右转弯。
4. dogleg 的单次折弯，再拼成两次折弯的绕障动作。
5. 受支持的掉头区、U 连接路和出口转向；其他角度/路口首版可明确 unsupported。

每个动作的实现步骤：

1. 从实际道路几何和连接关系提取进入/离开走廊及允许姿态。父子引用成立但几何不接触时直接保留连接错误。
2. 确定一组有限、顺序稳定的起止状态和直线/圆弧参数；记录枚举范围。不要假定“最小半径的一次试算失败”代表没有其他受支持参数。
3. 生成分段运动并积分，终点必须达到声明的离开状态。
4. 对真实局部路面、转弯区、硬障碍及适用中心线/方向规则检查车身包络；不能对整个路面取凸包填平凹口。
5. 保存可行的不同起止状态动作供 R3 使用。不能过早只保留一个局部最短动作，导致后续本可连接的姿态被丢弃。

**完成条件：** 同一入口、支路或折弯的局部动作可单独验证；有明确起止姿态、模板、包络和失败位置。

### 4.5 R2.2：接合已有停车动作

**拟新增：** `openparkcad/parking_motion_adapter.py`、`tests/test_parking_motion_adapter.py`。

执行步骤：

1. 为现有 90 度、锐角斜列、平行和 T-end 停车模板建立统一适配接口，返回真实起始姿态、停好姿态、运动分段及驶出候选。
2. 适配器直接使用当前模板构造结果，不从汇总报告中的布尔值、车位质心或截图猜测轨迹。
3. 先打通 90 度案例，再逐族扩展；未完成族在道路连续检查中标 unsupported，原有未请求道路检查的功能保持可用。
4. 停车动作的起点必须能从进场道路到达；驶出动作的终点必须能接入合法出场路线。
5. 现有停车模板的时间反演只可作为驶出候选，必须重新检查驶出方向、倒车策略、占用区域及其后的出场连接。单向道路不能直接反演整条入场路线。
6. 保持已有倒车距离约束的适用范围，分别报告停车/驶出动作倒车距离及行程累计值；不要静默把原先每次动作的限制解释为整次往返限制。

**完成条件：** R3 能消费停车模板的运动对象，并能拒绝“停车动作本身通过，但车辆无法以该姿态到达起点”的案例。

### 4.6 R3.1：组合并复核完整路线

**拟新增：** `openparkcad/road_traversal.py`、`tests/test_road_traversal.py`。

执行步骤：

1. 先用现有有向道路图排除明显不可达的入口/车位/出口组合。
2. 构建 R1 的姿态状态图：边是已经验证过的直线、转弯和停车动作；同一道路上的姿态连接也必须具备真实运动边。
3. 为每个保留车位寻找至少一条入口锚点至停车起点的路线、匹配的停车/驶出动作和至出口锚点的路线。
4. 使用确定的候选顺序和有限搜索，保留状态去重、尝试数量与预算。去重不得把位置接近但朝向/行驶权限不同的状态合并。
5. 首版以找到有效路线为目标；可以按路径长度与倒车代价排序，但不能把搜索代价当作布局质量总分。
6. 从证据对象拼接实际运动分段，重新积分整条进出行程，检查接合误差、方向、整个包络和目标停车姿态。
7. “每个路口都存在某个转弯”不能代替第 6 步。局部通过但状态无法接合时记录具体断点。
8. 短路未找到路线、模板不支持、预算结束、程序异常分别记录；只有所有保留车位覆盖完成才给布局 passed。

**预算处理：** 在动作枚举、状态扩展、单车位开始和整条复核边界检查截止时间；不可中断的单次几何调用可能越过协作预算，报告实际耗时。外层 benchmark worker 硬超时仍负责进程上限。不得把超时结果写成“无可行方案”。

**缓存处理：** 初版允许只在单次具体布局验证中缓存共享动作。跨候选复用必须以车辆、精确几何、占用假设、约束和算法版本共同作为身份，并验证缓存命中与直接重算一致。

### 4.7 R3.2：接入候选、正式重建和 CLI

这一步决定新验证是否实际约束输出，必须完整走通以下落点：

| 落点 | 必须完成的行为 |
| --- | --- |
| `models.py` / Schema / `diagnostics.py` | 校验新配置；报告字段是否请求、执行、支持 |
| `LayoutResult` | 保存拟新增 `road_traversal_validation`，绑定具体布局身份 |
| `generator._finalize_candidate` / `_layout_valid` | 最终车位调整后运行道路检查，并纳入候选可用性 |
| `candidate_layout_preview._validate_layout_preview` | 用本预览对象验证，失败进入 preview errors |
| `candidate_snapshot._with_recomputed_validation` / `_promoted_official_valid` | 正式对象重建后复核，并阻止未完成/失败晋升 |
| `layout_evaluation._revalidate_candidate` / `_checks` | 多主路各候选记录道路执行状态与最终证据 |
| `layout_search.py` | 基线、额外候选、预算回退全部遵守当前请求的硬检查 |
| `engineering_validation.py` | 版本化记录道路规则、覆盖范围、失败/unsupported/incomplete |
| `cli._final_layout_errors` / `_write_report` | 最终输出再次检查证据身份和通过状态，JSON 记录完整结果 |
| `layout_benchmark.py` | 增加 `road_traversal` required check、覆盖率和失败原因提取 |

为避免各落点重复解释状态，可以增加一个共享的“道路检查是否满足请求”函数；本步只集中这项新判断，不顺便改写所有既有校验器。

正式结果仍必须是重建后复核的对象。ID 映射不会自动证明几何相同；证据身份不匹配时重新验证。未请求新检查的旧报告缺字段保持兼容；已经请求但字段缺失时必须拒绝正式通过。

失败输出沿用三件套事务行为。拟为 `solve` 增加可选 `--diagnostics <path>`，在无有效正式布局时保存独立的拒绝证据；它不是正式 report 的替代品，所有输出路径必须互异。诊断写入失败应保留原始求解失败原因，同时报告写入错误。

### 4.8 R4：阶段验收与交付

**拟新增测试：** `tests/test_road_traversal_integration.py`、`tests/test_road_traversal_export.py`、`tests/test_road_traversal_benchmark.py`。

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| R-T01 | 未请求道路检查 | legacy 几何、评分、拒绝和晋升行为保持 |
| R-T02 | 宽入口、主支路、90 度车位和出口 | 所有保留车位有完整有效轨迹 |
| R-T03 | 图上接触，但内角受阻 | graph 可通过而 road 明确失败，附碰撞对象 |
| R-T04 | 后轴路径在场内，车头或车尾越界 | 车身包络拒绝 |
| R-T05 | 各局部动作可行，衔接姿态不同 | 不得组合成完整通过路线 |
| R-T06 | 单向路出场只能逆行 | 不得反演入场路径伪造出场证据 |
| R-T07 | 同一车位一个入口不可达、另一个可达 | 找到允许入口组合即可通过 |
| R-T08 | dogleg、U 连接与掉头 | 每个受支持族具有通过/失败对照；未实现族明确 unsupported |
| R-T09 | 参数缺失、挂车车型、未知配置 | 输入错误/unsupported 分类准确，不能回退代理通过 |
| R-T10 | 停车可行，但停车起始姿态不可达 | 完整路线失败 |
| R-T11 | 每个已支持停车族 | 真实动作接入路线，未支持族不会误报完整覆盖 |
| R-T12 | 路线借用相邻车位面或穿过场地凹口 | 按占用/可行驶区契约拒绝 |
| R-T13 | 圆弧采样之间存在障碍 | 有可解释的保守包络结果，不漏检 |
| R-T14 | 预算耗尽但部分车位已通过 | incomplete、valid=null，不能发布为全覆盖 |
| R-T15 | 预览通过，正式重建几何改变 | 重算并能拒绝；不能沿用预览证明 |
| R-T16 | 多候选隔离和缓存失效 | C1 的车辆/障碍/方向变化不污染 C2 或原始输入 |
| R-T17 | 新检查无效基线与有效额外候选 | 晋升开启时可恢复；关闭时不能替换正式输出 |
| R-T18 | 所有候选未通过 | CLI 保持旧三件套，单独保留失败证据 |
| R-T19 | 正式导出 | 轨迹、车位、道路 ID 和结果身份与 DXF/SVG/report 一致 |
| R-T20 | 缺少道路证据或内部异常 | 分别拒绝/报告错误，不能写成 passed |

R-T08/R-T11 不要求在第一轮实现所有族，但必须为交付支持表中的每个族提供覆盖；实际收尾明确已支持项及 unsupported 项，不以未支持项的 skip 冒充通过。

以下为**目标命令：先新增对应测试后运行**：

```powershell
& ./.venv/Scripts/python.exe -m pytest -q tests/test_road_traversal_contract.py tests/test_vehicle_kinematics.py
if ($LASTEXITCODE -ne 0) { throw 'R1 failed' }
& ./.venv/Scripts/python.exe -m pytest -q tests/test_road_transitions.py tests/test_parking_motion_adapter.py
if ($LASTEXITCODE -ne 0) { throw 'R2 failed' }
& ./.venv/Scripts/python.exe -m pytest -q tests/test_road_traversal.py tests/test_road_traversal_integration.py tests/test_road_traversal_export.py tests/test_road_traversal_benchmark.py
if ($LASTEXITCODE -ne 0) { throw 'R3/R4 failed' }
```

R4 新增 `benchmarks/road_traversal.json`，先在现有 runner 接通新 required check，再执行新清单。manifest 使用现有格式和相同 greedy/CP-SAT、legacy/multi 四变体；每条 road-enabled 案例写明 `road_traversal` 必需证据与预期结果。

**目标命令：R4 manifest、证据提取与 optimizer 环境完成后执行**：

```powershell
$roadRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
& ./.venv/Scripts/python.exe tools/benchmark_layouts.py --manifest benchmarks/road_traversal.json --profile all --subset full --repeats 3 --timeout-seconds 180 --out "output/benchmarks/road_traversal/$roadRunId"
if ($LASTEXITCODE -ne 0) { throw 'Road benchmark failed' }
```

同时保留原 20 案例的原配置兼容性比较。启用新检查导致拒绝或有效车位减少时，记录由哪条新检查引起；不把新策略与旧策略混在一起计算优化收益。

**阶段完成条件：** R1 契约、受支持族、全部集成落点、R-T 矩阵、旧行为兼容性、真实 OR-Tools 路径、最终图纸证据一致性和独立 wheel 验证均有记录。完成后更新能力矩阵、输入说明和验收文档。

**回退：** 对没有道路验证需求的调用继续使用默认关闭路径；对明确要求新验证的项目，失败时修复或返回未满足，不能通过关闭开关冒充满足需求。

## 5. 阶段二：真实 CAD 接入

### 5.1 C1.1：冻结最小 DXF 输入契约

**拟新增：** `docs/cad_import_contract.md`、`openparkcad/cad_import.py`、`openparkcad/coordinate_transform.py`、`tests/fixtures/cad/`。

执行步骤：

1. 首版只读取 modelspace 的一个二维场地外边界、障碍和明确入口标记。边界/障碍支持无 bulge 的闭合直线 LWPOLYLINE 或二维 POLYLINE。
2. 图层角色来自显式 mapping 文件；不凭颜色、图层名字相似度或最大面积猜测边界。
3. 首版入口约定为指定图层上的有向 LINE：起点为入口中心，线方向为朝场内 heading；入口 ID、宽度及 enter/exit 语义由 mapping 中的实体 handle 记录指定。线长不是入口宽度。
4. 验证入口方向与场地相交关系；无法唯一判断或方向朝外时给出诊断，不自动旋转 180 度。
5. 首版拒绝关键角色中的 ARC、SPLINE、bulge、INSERT/BLOCK、非平面/非零高程实体、开放/自交边界、多外边界及无法表达的洞。不要把孔洞填满或静默 tessellate 曲线。
6. 每个源实体记录 handle、图层、类型和处理结果。非求解图层允许忽略，但导入清单应可查看；关键图层有未支持实体时不生成“完整有效”的场地。
7. 车辆、车位尺寸、配额和求解策略由明确的项目 defaults JSON 提供，不能从标注文字推测；DXF 只提供其支持的几何与入口。
8. mapping/defaults 的 schema 和 runtime 同步校验。生成完整 JSON 后运行现有 parser 和 Schema，并保留字段来源。

defaults 中的车辆/车位尺寸明确使用米，不能再次按源图纸单位缩放。首版 defaults 不提供另一套边界/入口几何覆盖 DXF；额外带坐标的约束需要声明坐标系并统一转换，未实现该转换时明确报告不支持，不能混用源坐标与局部坐标。

拟定 mapping 片段，**待实现，不是当前 CLI 可用格式**：

```json
{
  "version": "cad-import-mapping-1",
  "source_units": "mm",
  "layers": {
    "boundary": "OPC_BOUNDARY",
    "obstacles": ["OPC_OBSTACLES"],
    "entrances": "OPC_ENTRANCES"
  },
  "entrance_entities": [
    {"handle": "2A", "id": "ENTRY-1", "width_m": 6.0, "allowed_movements": ["enter", "exit"]}
  ]
}
```

示例 handle 仅用于说明；执行夹具应生成相应实体并记录其真实 handle，不能将 `2A` 套到任意图纸。

### 5.2 C1.2：单位和可逆坐标转换

执行步骤：

1. 读取 DXF 单位声明，并与 mapping 的 `source_units` 比较。二者一致时使用该单位；只有一个明确来源时使用并记录来源；缺单位或冲突时要求补充/解决输入，不能直接猜测。
2. 初版接受米和毫米；其他单位先列 unsupported，再按案例需要扩展。
3. 源 WCS 坐标统一缩放为米。局部原点选择外边界包围盒最小角并记录；首版不旋转、不镜像、不推断 EPSG。
4. 转换使用 `local = scale * source - origin_m`，逆转换使用 `source = (local + origin_m) / scale`。同一变换作用于边界、障碍、入口、生成图形和诊断几何。
5. `metadata.cad_import` 或独立同伴记录保存源文件 SHA-256、单位来源、原点、变换版本、图层映射和 source-handle 映射。需要真正使用的变换信息必须被运行时保存，不能只停留在原始 JSON 中。
6. 导出新增独立的坐标恢复选项或明确上下文；DXF 恢复源坐标和单位。内部 report 保持米制局部坐标并声明坐标系，不能只变更部分点。
7. SVG 使用局部坐标查看，但点击对象能关联源 handle 和源坐标。大坐标不会直接成为界面计算原点。

**验收：** 已知 20 m × 30 m 场地在米/毫米和大坐标平移后产生相同局部几何；方向、障碍相对位置和导出尺寸保持。最大往返误差门槛在 C1 契约中以米定义并换算成源单位，先以 `1e-6 m` 为数值回环目标；达不到时报告实际误差并分析坐标范围，不能宣称达到测绘精度。

### 5.3 C1.3：接入 CLI 和诊断

**拟新增：** `tests/test_cad_import.py`、`tests/test_coordinate_transform.py`、`tests/test_cad_import_cli.py`。

执行步骤：

1. 在现有 CLI 增加 `import-dxf` 子命令；独立做格式转换，不在导入时隐式求解。
2. 输入为源 DXF、mapping 和项目 defaults；输出为完整场地 JSON 与导入诊断。
3. 转换成功后原有 `solve` 消费该 JSON。命令错误和几何不支持分别说明；不可用结果不覆盖已有可用导入文件。
4. 无效导入允许写独立诊断，包含实体清单和问题位置；诊断存在不等于转换成功。
5. 保存源文件不变的哈希证据。默认输出到新文件，不对源图纸做就地修改。

**目标命令：C1 实现并新增配套夹具后可执行**：

```powershell
$cadRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$cadDir = "output/cad/$cadRunId"
New-Item -ItemType Directory -Force -Path $cadDir | Out-Null
& ./.venv/Scripts/python.exe -m openparkcad import-dxf tests/fixtures/cad/rectangle_mm.dxf --mapping tests/fixtures/cad/rectangle_mm.mapping.json --defaults tests/fixtures/cad/project_defaults.json --out "$cadDir/site.json" --diagnostics "$cadDir/import.json"
if ($LASTEXITCODE -ne 0) { throw 'DXF import failed' }
& ./.venv/Scripts/python.exe -m openparkcad solve "$cadDir/site.json" --out "$cadDir/layout.dxf" --preview "$cadDir/layout.svg" --report "$cadDir/report.json"
if ($LASTEXITCODE -ne 0) { throw 'Imported site solve failed' }
```

### 5.4 C2：回环验证与真实场地对照

**拟新增：** `tests/test_cad_roundtrip.py`、`docs/real_site_case_protocol.md`。

| 编号 | 场景 | 验收点 |
| --- | --- | --- |
| C-T01 | 米、毫米的同一场地 | 局部输入和正式几何等价 |
| C-T02 | 平移到大 WCS 坐标 | 回写位置、尺寸和方向正确 |
| C-T03 | 单位缺失/冲突 | 不猜测单位；给出需修正字段 |
| C-T04 | bulge、开放/自交边界、洞 | 关键实体不被静默忽略/修复 |
| C-T05 | 非关键图层含文字和块 | 可列出忽略项，不影响明确的有效输入 |
| C-T06 | 入口方向错误、缺宽度、未知 handle | 定位到实体并拒绝有效转换 |
| C-T07 | 导入→求解→回写 | 新车位/道路位于源场地正确位置，源文件未改变 |
| C-T08 | 导入成功但求解无效 | 导入与算法失败分开显示，无正式新三件套 |
| C-T09 | 绘图工具重开 DXF | 实际图层、单位、位置可检查，不只比较 JSON |

真实案例按 Q1 协议收集，先争取 3—5 个用途不同的小场地作为试点目标，不把数量当成无资料时的开发门槛。至少一次对比完整记录人工布局、同等约束、算法结果、人工修订及原因，才可发布该场地的实际效果结论。

**目标测试命令：对应测试新增后运行**：

```powershell
& ./.venv/Scripts/python.exe -m pytest -q tests/test_cad_import.py tests/test_coordinate_transform.py tests/test_cad_import_cli.py tests/test_cad_roundtrip.py
if ($LASTEXITCODE -ne 0) { throw 'CAD checks failed' }
```

**阶段完成条件：** 支持范围内的 DXF 可转换、求解、回写并重开检查；用户可查看所有关键导入诊断；真实对照有则报告，无则明确只有合成回环验收。

**回退：** 保留源图纸及已有 JSON 输入流程；不自动修复不支持的源实体。用户明确选择预处理后，应保存新源文件和处理记录。

## 6. 阶段三：交互查看和局部修改

### 6.1 C3.1：建立可查看的方案数据包

**拟新增：** `openparkcad/review_bundle.py`、`schema/openparkcad-review.schema.json`、`tests/test_review_bundle.py`。

执行步骤：

1. 定义 `review-bundle-1`：场地、坐标变换、正式方案、实际评估过的候选、评分、道路证据、失败对象、来源及未完成状态。
2. 当前比较报告中的汇总数字不保证包含每个候选完整几何；在候选评估完成时保存可查看的几何快照，不能为展示重新求解并沿用旧分数。
3. 使用 `(candidate_id, object_id)` 区分各候选；注明是否正式重建、是否通过当前全部检查、是否已晋升。
4. 未求解、超预算、unsupported 和失效证据都有独立状态，不能统一显示绿色“可用”。
5. `solve` 拟增加 `--review-bundle <path>`；成功发布时与正式输出使用同一具体布局，并将可选 bundle 纳入该次输出事务。所有输出路径互异。
6. 无有效方案时，失败候选写到独立 diagnostics bundle；文件有明确失败作用域，不得成为正式图纸的替代品。

**完成条件：** 不启动求解器也能从 bundle 完整复现某次方案比较画面，并核对每个标记的来源。

### 6.2 C3.2：轻量方案查看器

**拟新增：** `openparkcad/viewer.py`、`openparkcad/viewer_assets/`、`tests/test_viewer_bundle.py`。

第一版采用离线 HTML/SVG/JavaScript 查看器，复用 Python 核心和 bundle。先不引入必须运行数据库或完整前端服务的部署要求；若后续引入前端构建系统，在 C4 记录实际需求与打包方式。

实现顺序：

1. 打开 bundle，显示边界、障碍、入口、道路和车位；支持缩放、平移和图层开关。
2. 切换实际评估候选，比较有效车位数、分数、请求检查状态和求解耗时。
3. 点击失败列表高亮对应对象和碰撞位置；点击车位查看完整入场/停车/出场轨迹。
4. 以采样姿态播放车辆运动，显示模型范围；播放速度只影响展示，不改变验证数据。
5. 清楚区分当前查看的预览和正式方案。C3 的切换只改变查看对象；C4 接入服务后再提供“采用候选”，并通过正式重建/验证更新正式方案。
6. 长轨迹按需加载/显示；在 Q2 冻结的场地规模下测量打开、切换和交互耗时。
7. 外部名称、诊断文本、SVG/JSON 嵌入按数据转义，不能让源图纸中的字符串成为 HTML 或脚本。

**目标命令：C3 完成后可执行**：

```powershell
$reviewRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$reviewDir = "output/review/$reviewRunId"
New-Item -ItemType Directory -Force -Path $reviewDir | Out-Null
& ./.venv/Scripts/python.exe -m openparkcad solve examples/multi_spine_comparison_site.json --out "$reviewDir/layout.dxf" --preview "$reviewDir/layout.svg" --report "$reviewDir/report.json" --review-bundle "$reviewDir/review-bundle.json"
if ($LASTEXITCODE -ne 0) { throw 'Review solve failed' }
& ./.venv/Scripts/python.exe -m openparkcad view "$reviewDir/review-bundle.json" --out "$reviewDir/review.html"
if ($LASTEXITCODE -ne 0) { throw 'Viewer export failed' }
```

### 6.3 C4.1：工程文件、稳定身份和锁定约束

**拟新增：** `openparkcad/project_model.py`、`openparkcad/layout_locks.py`、`schema/openparkcad-project.schema.json`、`tests/test_layout_locks.py`、`tests/test_project_roundtrip.py`。

执行步骤：

1. 定义可保存的项目目录：输入与转换元数据、参数、稳定工程对象、锁定集合、当前接受的方案、修改记录、生成结果引用。
2. 工程对象 ID 跨编辑修订稳定；`candidate_id` 是具体求解上下文身份，不能直接作为所有跨修订对象的主键。维护 project object → 来源候选 → 正式对象的映射。
3. 第一版锁定入口、主路几何和车位组；入口固定位置/宽度/朝向/允许方向，主路固定几何/方向，车位组固定几何/类型。父道路依赖必须显式保留或检测冲突。
4. 锁定作为候选生成和选择的硬约束。不能只在生成后把被移动对象绘回旧位置。
5. 区分“锁定身份”和“冻结旧验证”：锁定对象遇到新障碍或车辆变化仍需重新验证。
6. 每次修改增加 revision；求解输入是某 revision 的不可变快照。结果带 input digest 和 revision，过期任务只能显示为历史结果，不能替换当前方案。
7. 首版实现保留主路重新排车位，再扩展入口和车位组锁定；未实现的锁定类型明确拒绝，不能忽略。

### 6.4 C4.2：约束下重新生成和交互操作

**拟新增：** `openparkcad/project_service.py`、`tests/test_constrained_regeneration.py`、`tests/test_project_service.py`。

执行步骤：

1. 在生成链路中应用锁定几何，在 selector 中强制依赖与冲突，重新计算全部适用验证和配额。
2. 无解时保留最后接受的方案，返回冲突对象及可解释原因。未经证明的原因列表不能称作“最小冲突集”。
3. 第一版提供锁定/解锁、修改参数和添加简单障碍。拖动入口/道路等操作逐项增加，不在同轮承诺完整 CAD 编辑器。
4. 用本地服务连接界面与 Python 核心，默认仅监听 loopback；求解运行在可取消的子进程，UI 不因长计算冻结。
5. 同项目按 revision 管理任务；开始新任务可以取消旧任务，晚到结果不覆盖当前 revision。导出只消费已经接受且仍匹配当前输入的正式方案。
6. 保存采用原子替换或已有事务写入机制；撤销/重做恢复完整输入、锁和方案引用，不能只恢复画面。
7. 任何超时、取消或验证失败，都保留最后一次有效接受结果及当前未满足的修改。

### 6.5 C3/C4 验收矩阵

| 编号 | 场景 | 验收点 |
| --- | --- | --- |
| U-T01 | 离线查看多候选 | 几何、分数、检查状态均来自同次求解 |
| U-T02 | 失败对象与车辆轨迹 | 点击定位正确；预览/正式/未完成状态明确 |
| U-T03 | 锁主路重新排车位 | 主路几何与方向保持，其他部分重新检查 |
| U-T04 | 锁定车位组与新障碍冲突 | 保留原接受方案，明确冲突，不移动锁定对象 |
| U-T05 | 正式 ID 重排 | 稳定工程锁不会落到另一个对象 |
| U-T06 | 保存、关闭、重开、撤销/重做 | 输入、锁、修订与结果引用一致 |
| U-T07 | 两次求解结果乱序到达 | 旧 revision 不能覆盖新 revision |
| U-T08 | 取消/超时/服务重启 | 项目可恢复，失效结果不能作为当前有效方案 |
| U-T09 | 导出某个候选 | 经正式重建和验证，画面与输出一致 |
| U-T10 | 名称含 XML/HTML 特殊字符 | 正确显示，不能执行脚本或破坏输出 |

**目标测试命令：相关模块和测试完成后运行**：

```powershell
& ./.venv/Scripts/python.exe -m pytest -q tests/test_review_bundle.py tests/test_viewer_bundle.py tests/test_layout_locks.py tests/test_project_roundtrip.py tests/test_constrained_regeneration.py tests/test_project_service.py
if ($LASTEXITCODE -ne 0) { throw 'Viewer/project checks failed' }
```

还需在真实浏览器完成 U-T01—U-T10 的可见交互验收，保存关键截图和对应结果身份。静态 HTML 存在、服务返回 200 或纯 Python 测试通过不能替代交互检查。

**阶段完成条件：** 用户可以完成“导入 → 生成 → 比较 → 锁定 → 修改 → 重新验证 → 保存/重开 → 导出”；不需要手工改 JSON 才能完成这个支持范围内的流程。

**回退：** 静态 bundle 和原 CLI 保持独立可用；项目版本升级保留旧文件副本，读取不支持版本时不进行猜测迁移。

## 7. 阶段四：复杂布局与搜索改进

本阶段按案例选择一个问题实施，不一次性开发任意路网、所有入口组合和通用路径规划。

### 7.1 K1：从失败记录确定下一项能力

**前置条件：** Q1 的固定场地记录和 C3/C4 的使用反馈可用；资料不足时以合成案例作明确标注的研发试验。

执行步骤：

1. 将失败归为输入无法表达、候选未生成、动作未支持、车辆不通过、配额不足、锁冲突、预算不足、评分不符实际偏好或程序错误。
2. 对每个高频问题保留最小可复现场地、完整场地及人工解决方法；不能只保留成功截图。
3. 区分“几何上确实冲突”和“有限模板未找到”：用人工候选或扩大受控枚举核对，不把搜索失败自动归因于场地无解。
4. 选择一个能具体描述前后行为的问题，并冻结验收场地、比较策略和资源预算。

**产物：** `docs/topology_iteration_<name>.md`（拟新增），写明本轮问题、证据、支持边界、验收案例和退出条件。

### 7.2 K2：扩展一个道路/模块生成族

可选方向及先要补的能力：

| 实际问题 | 对应生成扩展 | 同步补充的验证 |
| --- | --- | --- |
| 出入口位置不符合当前后端出口模板 | 多入口/出口组合及连接候选 | 各入口姿态、方向及连续出场路线 |
| 长死路或无法形成实用环路 | 一类明确的闭环/连接路模板 | 掉头替代、环路方向、真实连接动作 |
| 密集障碍导致当前 dogleg 不足 | 受限多折线路由候选 | 每个折弯及整体姿态连贯性 |
| 无障碍车位数量满足但通道无法组织 | 专用通道与车位模块候选 | 已声明的通道宽度、接触和连续性 |

实施顺序：先做独立候选构造和失败夹具，再加入候选目录及冲突/依赖，随后逐候选完整验证，最后开放到多主路搜索。原始模板仍作为可比较的基线。

如选择通用运动路径搜索，先在独立小范围实验验证结果和计算代价，再决定接入；不能用未验证的搜索路径替代 R 阶段已要求的包络和完整路线复核。

### 7.3 K3：调整候选保留、选择和目标函数

执行步骤：

1. 先确定有价值的候选是否实际进入目录，再分析 greedy 或 CP-SAT 是否选择了合适组合。
2. 每套候选继续保持独立可变上下文；跨拓扑候选不能共享本地冲突矩阵、车辆证据或来源 ID。
3. 比较 Top-K 的候选多样性与代价，记录生成数、保留数、评估数、通过数和未完成数。
4. 评分调整依据人工比较记录；先满足硬要求，再比较有效车位、路网组织、倒车/绕行和改图代价。
5. 冻结调参案例和保留评估案例，避免在同一批案例调分后用同一批宣称泛化改善。样本不足时明确限制。
6. 局部 CP-SAT objective、gap 与正式布局分数继续分开。相同约束和预算下比较正式结果，不能跨政策混算收益。
7. 默认后端/搜索模式是否改变，根据真实案例效果、失败分类、耗时和可回退性另行记录具体决定，不因某个合成例收益自动切换。

### 7.4 K4：验证效果和计算代价

执行步骤：

1. 同输入、同约束、同车辆、同机器，比较基线与新增生成/选择能力。
2. 原案例全量跑一次兼容性；受影响和新增案例做受控重复测量。
3. 先检查有效性和完整证据，再报告车位数、分数、人工修订量、求解时间和预算未完成比例。
4. 原本因未支持而拒绝的场地变为通过，必须具备新增动作/规则证据；不能仅放宽 unknown 为 pass。
5. 有收益、有退化、持平和仍未解决的案例全部报告，并保留单案例追溯入口。

**阶段完成条件：** 至少解决 K1 冻结的具体场地问题，既有不变量不退化，结果与代价可以复跑。不设脱离案例的统一“必须增加多少车位”目标。

**回退：** 新生成族和新评分项可按版本/配置独立禁用；已经请求的硬验证不能随优化功能一同关闭。

## 8. 阶段五：工程交付

### 8.1 D1：选择支持范围并建立规则配置

执行步骤：

1. 先选定一种实际交付场景，列明场地类型、车辆、输入格式、输出用途、地区和需要人工复核的内容。
2. 按选定场景获取适用的正式规则来源，记录条文、版本、生效日期、适用条件和出处；具体规则数值在实施时核实，本计划不预填法规比例或尺寸。
3. 将每条规则分类为已可执行、需要新数据、仅提示或尚不支持；地区配置不能只改变报告名称。
4. 缺高程时不声称通过坡度检查，缺设备参数时不声称完成充电设备设计，缺应急车型路径时不声称完成对应通行验证。
5. 为可执行规则建立边界案例，并明确项目政策与地区要求冲突时的行为。规则更新产生新 profile 版本，不静默改旧项目。

**拟新增：** `docs/supported_delivery_scope.md`、`openparkcad/rule_profiles.py`、`tests/test_rule_profiles.py`。具体 profile 文件和规则测试随选定场景创建。

### 8.2 D2：审查记录和图纸输出

执行步骤：

1. 固定一份交付 manifest：项目修订、输入摘要、源码/包版本、规则 profile、车辆、求解配置、当前正式方案、检查摘要和输出文件摘要。
2. 图纸逐步补充比例、单位、图层、尺寸/编号和已支持的工程标注；不把自动标注完成等同于图纸全部专业审查完成。
3. 记录人工修改、复核人填写的意见和接受状态；软件不得伪造人工签名或把算法通过自动标成人工审查通过。
4. 输入、车辆、规则或正式几何发生变化时，使旧审查状态失效，保留旧修订作为历史。
5. 所有输出使用同一个已验证的正式布局，增加文件时沿用事务发布与失败恢复。

**验收：** 从一份交付包可以定位到生成它的输入、规则和软件；修改关键输入后旧通过状态不会继续代表当前方案。

### 8.3 D3：安装、升级和项目迁移

执行步骤：

1. 保留 Python 核心 wheel 和可选 optimizer 依赖隔离；查看器静态资源和 Schema 进入构建产物。
2. 根据实际使用环境选择桌面启动器或本地服务打包；先在目标机器完成安装，不预先承诺所有操作系统。
3. 在源码目录之外验证默认依赖路径、实际 optimizer 路径、导入/查看/锁定重生成和导出。
4. 为工程文件与报告契约建立版本兼容说明；迁移先写新文件，失败保留原文件。
5. 验证旧版本项目读取、升级后保存、再次打开及不支持版本提示。

### 8.4 D4：小范围试用与发布

执行步骤：

1. 用选定场景的实际项目完成一轮从输入到人工复核的全过程，记录操作耗时、算法失败、需要手工补充的数据和修订原因。
2. 对新发现的实质问题补最小回归，并重新验证受影响链路。
3. 整理安装说明、支持范围、已知限制、恢复方式和交付样例，形成可审查的发布候选。
4. 实际发布依任务授权执行；在发布前先完成构建、检查和材料，不以“等待发布”代替可完成的准备工作。

**阶段完成条件：** 选定场景在目标环境中可重复完成，交付文件和审查记录可追溯，支持范围与软件实际执行的检查一致。

## 9. 贯穿各阶段的工作

### 9.1 Q1：真实案例和人工对照

每个案例至少记录：

| 字段 | 要求 |
| --- | --- |
| 来源 | 合成、获许可真实场地或匿名化衍生；不得混称 |
| 使用范围 | 是否可进入仓库、是否允许公开图纸/截图 |
| 输入身份 | 原始图纸摘要、转换参数、单位和局部坐标记录 |
| 设计条件 | 同一车辆、车位规格、障碍、入口、配额和规则 |
| 人工参照 | 方案版本、实际参与者记录、比较前提和差异 |
| 算法结果 | 版本、参数、正式布局、检查状态、耗时和失败类别 |
| 修订记录 | 哪些对象被改、为何改、是否改变约束 |

没有许可的原始图纸不进入公开 fixtures。可保留本地引用和摘要，另制允许共享的最小合成回归。案例类型先覆盖不规则边界、入口受限、障碍绕行、配额/通道和单向出场，不以车位数量挑选成功案例。

### 9.2 Q2：性能与交互预算

当前验收记录中 dogleg-obstacle 约 113—123 秒、部分 multi-jog 约 50 秒，仅用于提示调查对象；重新测量时以当前机器和源码为准。

执行步骤：

1. 先区分输入/候选生成、selector、预览、正式重建、停车验证、道路验证和文件输出的耗时。嵌套计时标明 inclusive/exclusive，不能直接相加重复计算。
2. 为每个阶段选择少量典型和最慢案例，串行执行性能测量；不要与全量 pytest 或另一组基准争用资源。
3. 验证缓存、减少重复构造或空间索引等优化前，先用 profile 找出具体热点；不因笼统“Python 慢”重写整个内核。
4. 每项优化对比几何、有效性、候选身份和报告，确认没有用减少必要检查换速度。
5. C3 前冻结目标机器与典型场地规模，建议将打开/切换方案 1 秒内、交互反馈 200 ms 内作为初始体验目标，再用实际结果修订并记录。长求解明确显示状态且可取消，不声称所有场地即时求解。
6. 样本量少时报告中位数和范围；只有足够测量支持时才解释分位数。不把单例加速比当整体改善。

若给失败验证设置预算，超预算必须保留 incomplete。预算耗尽率和候选覆盖率与总耗时一起报告。

### 9.3 Q3：验证证据、目录和阶段记录

建议目录（按需创建）：

```text
docs/                                  契约、执行计划、阶段验收
tests/fixtures/road_traversal/          最小道路几何和进出案例
tests/fixtures/cad/                     可共享 DXF 与 mapping/defaults
benchmarks/                            版本化固定案例清单
output/verification/next_stages/        本地日志、环境、阶段验证
output/benchmarks/road_traversal/        道路检查批量结果
output/cad/                            导入、求解、回写和诊断
output/review/                         查看器和方案 bundle
```

大型运行产物继续遵守 [工作区目录约定](README.md)。需要维护的小案例、manifest 和精简验收记录进入 Git；只在本地 output 有文件不构成其他机器可重现的交付。

每完成一个步骤记录以下内容，模板可直接复制：

```text
步骤：
状态：未开始 / 进行中 / 已完成 / 需外部资料
源码身份及未提交变更摘要：
本步实际改变：
验证命令与环境：
结果位置及通过/失败摘要：
已知限制和剩余事项：
回退方式：
下一步及前置条件：
```

## 10. 阶段收尾、独立安装和文档验证

### 10.1 代码阶段质量入口

以下命令**当前已存在**，在代码阶段收尾运行。编写本计划本身不要求重跑算法全套：

```powershell
& ./.venv/Scripts/python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
& ./.venv/Scripts/python.exe -m pytest -q --cov=openparkcad --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { throw 'Regression failed' }
```

CI 保留 Python 3.10/3.12 默认依赖路径和显式安装 OR-Tools 的 optimizer 路径。新增道路集成及后续锁定选择测试加入实际相关 job；没有 OR-Tools 的 skip 不作为 CP-SAT 验收。

新检查的专项 benchmark 和原始配置兼容 benchmark 分开记录。确有必要刷新完整原 20 案例时，现有命令为：

```powershell
$compatRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
& ./.venv/Scripts/python.exe tools/benchmark_layouts.py --manifest benchmarks/layout_v0_4.json --profile all --subset full --repeats 3 --timeout-seconds 180 --out "output/benchmarks/next_stages/$compatRunId-compat"
if ($LASTEXITCODE -ne 0) { throw 'Compatibility benchmark failed' }
```

`--profile all` 包含 CP-SAT。要形成四变体通过证据，环境必须实际安装 optimizer extra，并核对 actual backend 与 fallback；默认依赖测试另行执行。

### 10.2 独立 wheel 验证

以下构建/安装接口**当前可执行**。代码阶段收尾在新的构建目录运行，使用 `-I` 隔离源码导入；若没有 wheel/build 依赖，先按 README 的开发环境安装步骤准备。

```powershell
$ErrorActionPreference = 'Stop'
$wheelRepo = (Get-Location).Path
$wheelRunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$wheelBuildDir = Join-Path $wheelRepo "output/verification/next_stages/$wheelRunId-wheel/build"
New-Item -ItemType Directory -Force -Path $wheelBuildDir | Out-Null
& ./.venv/Scripts/python.exe -m build --outdir $wheelBuildDir
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$stageWheels = @(Get-ChildItem -LiteralPath $wheelBuildDir -Filter '*.whl' -File)
if ($stageWheels.Count -ne 1) { throw 'Expected one fresh wheel' }
$stageWheel = $stageWheels[0]
Get-FileHash -LiteralPath $stageWheel.FullName -Algorithm SHA256
$wheelOutside = Join-Path $env:TEMP ('openparkcad-next-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $wheelOutside | Out-Null
& ./.venv/Scripts/python.exe -m venv (Join-Path $wheelOutside 'venv')
if ($LASTEXITCODE -ne 0) { throw 'Wheel venv failed' }
$stageWheelPython = Join-Path $wheelOutside 'venv/Scripts/python.exe'
& $stageWheelPython -I -m pip install $stageWheel.FullName
if ($LASTEXITCODE -ne 0) { throw 'Wheel installation failed' }
Push-Location $wheelOutside
try {
    & $stageWheelPython -I -c "from pathlib import Path; import sys, openparkcad; assert Path(openparkcad.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); print(openparkcad.__file__)"
    if ($LASTEXITCODE -ne 0) { throw 'Import escaped installed package' }
    & $stageWheelPython -I -m openparkcad solve (Join-Path $wheelRepo 'examples/phase0_site.json') --out layout.dxf --preview layout.svg --report report.json
    if ($LASTEXITCODE -ne 0) { throw 'Default installed-wheel solve failed' }
    & $stageWheelPython -I -m pip install "$($stageWheel.FullName)[optimizer]"
    if ($LASTEXITCODE -ne 0) { throw 'Optimizer installation failed' }
    & $stageWheelPython -I -c "from ortools.sat.python import cp_model; import ortools; print(ortools.__version__)"
    if ($LASTEXITCODE -ne 0) { throw 'Optimizer import failed' }
    & $stageWheelPython -I -m openparkcad solve (Join-Path $wheelRepo 'examples/multi_spine_comparison_site.json') --out optimizer.dxf --preview optimizer.svg --report optimizer.json
    if ($LASTEXITCODE -ne 0) { throw 'Optimizer installed-wheel solve failed' }
}
finally {
    Pop-Location
}
```

此外必须做本阶段的新能力 smoke：R4 用请求道路检查的通过和拒绝夹具；C2 用 DXF 回环；C3/C4 用 bundle 资源、项目保存和锁定重生成；D3 用目标机器安装。这些夹具/命令在对应步骤实现后加入 CI，不能仅用旧示例证明新功能已打包。

安装成功或退出码 0 不是全部验收：检查对应报告的 requested/executed/status、正式 ID、实际后端、输出坐标和新资源确实来自 wheel。保留安装目录供核查，清理时按工作区约定处理，不写递归删除脚本。

### 10.3 文档与契约收尾

每阶段收尾依次更新：

1. [当前能力矩阵](current_status.md)：支持和不支持的准确边界。
2. [输入模型](input_model.md) 和 Schema：字段解析、执行和失败语义。
3. README、示例目录及本计划：新命令是否已经可执行。
4. 对应阶段契约与验收记录：输入身份、环境、结果和回退。
5. 报告/工程文件版本及兼容性说明；发生破坏性变更时明确迁移行为。

文档变更检查本地链接、代码块语法、JSON 示例解析、步骤依赖、状态标注和 `git diff --check`。执行计划中的待实施路径可用代码文本表示；不能创建伪造空模块来让链接检查通过。

## 11. 可逐项勾选的执行清单

所有条目初始为未完成；实际实现并有证据后再勾选。

- [x] B0：源码、环境、原行为和证据目录可追溯。
- [x] R1.1：道路检查范围、车辆/占用假设、预算和状态契约确定。
- [x] R1.2：动作、姿态、完整行程和布局证据身份确定。
- [x] R1.3：运动积分与弧间包络的必要核对完成。
- [x] R2.1：入口内侧、直线、主支路和折弯动作有通过/拒绝案例。
- [x] R2.2：受支持停车族提供可组合的真实停车/驶出动作。
- [x] R3.1：保留车位的连续进出路线构造和整体复核完成。
- [x] R3.2：预览、候选、正式重建、综合报告和 CLI 均执行新要求。
- [x] R4：道路测试矩阵、兼容基准、optimizer 与独立 wheel 验收完成。
- [x] C1.1：DXF 图层、实体、入口和项目 defaults 契约完成。
- [x] C1.2：单位、原点和双向坐标转换完成。
- [x] C1.3：独立导入 CLI、源实体诊断和原文件保护完成。
- [x] C2：CAD 回环与实际打开验证完成，真实场地证据状态明确。
- [ ] C3.1：完整候选 review bundle 与实际求解绑定。
- [ ] C3.2：查看、比较、失败定位和轨迹播放通过浏览器验证。
- [ ] C4.1：工程修订、稳定对象身份、锁定和保存恢复完成。
- [ ] C4.2：锁定下重生成、冲突保留、取消和过期结果处理完成。
- [ ] K1：以具体失败场地选定一个扩展目标并冻结验收。
- [ ] K2：所选候选生成族及对应验证完成。
- [ ] K3：候选保留、选择或评分调整有独立效果证据。
- [ ] K4：同条件效果/代价比较完成，退化与限制如实记录。
- [ ] D1：交付场景与适用规则来源、执行边界确定。
- [ ] D2：审查记录、图纸和交付 manifest 可追溯。
- [ ] D3：目标环境安装、项目迁移和恢复验证完成。
- [ ] D4：实际试用流程与发布候选材料完成。
- [ ] Q1/Q2/Q3：案例来源、性能记录和阶段证据随实施持续更新。

建议的首轮任务边界是 **B0 → R1 → R2 → R3 → R4**。CAD 小样和真实案例准备可同步开始；C3/C4 的完整实现接在道路证据和 CAD 坐标契约之后。后续按阶段选择具体工作范围，不将五个阶段视为一次不可拆分的大改动。
