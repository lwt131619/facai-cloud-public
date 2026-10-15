# ICS Command Center UI · 设计冻结稿 V1（2026-10-13）

操作员 2026-10-13 指令：Phase 1 = 把已存在的 Runtime 能力可视化、产品化，
让别人第一眼看到"这不是聊天机器人，而是一套任务操作系统"。本文是设计冻结
稿：冻结的是**信息架构、状态词汇、数据契约**；不冻结像素（理由见 §6 反驳 c）。

核心立场（操作员原话收录）：**不是复制一个产品，而是创造。**

## 一、冻结对象：三个视图

### 视图 1 · Mission Center（首页，不是聊天框）

```
+----------------------------------------------+
| 今天运行                                       |
|   任务A  ████████░░░░  4/5 阶段   执行中        |
|   任务B  ████████████  5/5 阶段   已完成        |
|   任务C  ██░░░░░░░░░░  1/5 阶段   等你一句话 ⚠  |
+----------------------------------------------+
| 实时事件流（全局，SSE 推送）                     |
|   10:31  MissionCreated      任务A             |
|   10:32  StepCompleted       规划方案           |
|   10:33  StepCompleted       执行工作           |
|   10:34  FEEDBACK_GATE_OPENED 任务C 等待裁决    |
+----------------------------------------------+
| 最新产出物 Artifacts                            |
|   报告 · 代码 · 文件 · 证据                      |
+----------------------------------------------+
```

- 数据源（全部已存在，零新后端）：任务行 = `GET /api/v1/conversion/console`
  （owner 级列表）；进度 = 各任务快照 `stages[].status`（DONE 数 / 总阶段数）；
  "等你一句话" = 快照 `open_gates` 非空；事件流 = `GET /api/v1/events/stream`
  （本轮新增，1 秒级 SSE，游标契约与单任务流一致）；Artifacts =
  `GET /api/v1/missions/{id}/proof` 的 artifacts 段。
- 顶部快速输入条保留（一句话开工），但**首屏是任务与事件，不是输入框**。

### 视图 2 · Timeline Graph（工作流页，差异化主战场）

```
User Intent（原话，不弱化）
     │
     ▼
Intent Compiler（大白话→专业提示词，显示 basis 要点）
     │
     ▼
Mission（五阶段 / 模型自拟 DAG）
     ├── Agent A（首选 + 理由：能力评分）
     ├── Agent B（后备）
     └── Tool C（经 broker 授权的调用点）
     │
     ▼
Verifier（验证阶段）
     │
     ▼
Artifact（产出物 + 证据链）
```

- 数据源：快照的 `stages[label].assignment`（首选/后备/理由）、
  `analysis.tool_families`、mission steps 的 `depends_on`（模型 DAG 有，
  默认五阶段为线性）。
- 渲染规则见 §6 反驳 b：**线性任务渲染为垂直流水线卡片（复用 conversion.html
  既有形态），DAG 任务才展开有向图**——否则默认视图永远是一条直线，
  "AI 到底怎么工作"的差异化落空。

### 视图 3 · 状态显示器原则（所有端只是状态显示器）

移动端：任务状态、通知、**审批**（反馈门一键裁决）、快速输入。语音入口
Phase 2+。桌面端：控制中心、详细流水、配置、调试。两端同一数据契约，
不同信息密度。

## 二、冻结对象：显示状态词汇（Runtime State Machine 的 UI 面）

| UI 状态 | 底层来源 | 备注 |
|---|---|---|
| MISSION_CREATED | 任务落库 | |
| PREPARED | 提示词已转换 + 计划已定 | 见反驳 b：COMPILED/PLANNED 合并 |
| RUNNING | 有阶段 RUNNING | |
| WAITING_INPUT | 快照 open_gates 非空 | **必须显式**，这是所有者裁决点 |
| VERIFYING | "验证结果"阶段 RUNNING | |
| COMPLETED | 全部 DONE | |
| FAILED / CANCELLED | 对应 stage/mission 状态 | |
| ARCHIVED | 交付完成超过归档期 | 列表端点过滤，不新起状态机 |

底层 mission/stage 状态机**不动**（1248 项测试钉死的执行引擎是核心资产）；
显示词汇在 UI 映射层收敛，多端用同一张映射表。

## 三、已就绪的地基（本轮实跑验证）

- `store.events_for_owner(owner, after)`：owner 级全局事件查询（events 表
  全局自增序号 JOIN missions，持久回放天然成立）。
- `facai/eventbus.py`：ICS 规范事件词汇（MissionCreated…DeploymentFinished
  + 进化生命周期），`publish()` 在写入端拦截拼错的事件名。
- `GET /api/v1/events`（回放）+ `GET /api/v1/events/stream`（SSE 订阅），
  owner 严格隔离，遗留事件类型原样流过。
- 测试：`tests/test_eventbus.py` 7 项（词汇校验、owner 隔离、游标契约、
  遗留类型流过、401 门）。

## 四、相位计划（按 2026-10-13 操作员重排）

Phase 1（现在）：Command Center UI 三视图。Phase 2：Device Identity +
扫码登录 + 设备授权（Owner Device Authorization Protocol：device_id /
public_key / trust_level / 签名授权）。Phase 3：Skill Evolution Pipeline
（Discovery→Manifest→Version→SBOM→Compat→Sandbox→Evidence→Owner Gate→
Registry Update；本轮已停工让位，`eventbus.py` 的进化词汇已预埋）。
Phase 4：External Integrations（`integration/bounty/{hackerone,bugcrowd,
vendor,custom}` 统一提交接口 `{platform,submission,status,reward,evidence,
timeline}`，不进核心，换平台不改 Runtime）。

## 五、验收清单（Phase 1 完成的定义）

1. 首页首屏无聊天框，显示任务行 + 实时事件流；断网重连事件流自动续传
   （`after=最后 sequence`）。
2. 任一反馈门打开，移动端 3 秒内出现可裁决入口，一键 resolve 后阶段续跑。
3. DAG 任务在 Timeline Graph 展开有向图；线性任务渲染垂直流水线卡片。
4. 三个视图只用本文列出的端点，无新增后端路由。
5. 全量测试保持全绿。

## 六、反驳（操作员明示邀请）

**a. "后端已经准备好"——同意，含一个修正。** 任务/事件/SSE/产物端点确实
齐了；唯一没做的是首页"今天运行"的聚合统计，但列表端点返回全量行、前端
可直接聚合，不构成新后端。同意 Phase 1 纯前端可完成。

**b. Timeline Graph 是差异化主战场，但默认视图会退化成直线。** 五阶段默认
线性，若一律画图，用户天天看到的是一条竖线，"AI 到底怎么工作"反而落空。
创造点必须押在：线性任务用已有的垂直流水线卡片（阶段 + 首选 Agent + 理由），
DAG 任务才展开有向图；真正独特的是把 broker 授权点画进图里——别家画
"模型回答"，我们画"谁在什么授权下执行了什么"，这才是任务操作系统的可解释性。

**c. 冻结信息架构，不冻结像素。** 第一版渲染真实数据后布局必动；若把像素
一并冻结，Phase 1 会返工。本文冻结：视图清单、状态词汇表、端点契约。

**d. COMPILED/PLANNED 两态对用户是噪音。** 两者间隔秒级且都无停顿语义；
有停顿语义的是 WAITING_INPUT（反馈门）。状态词汇表已合并为 PREPARED——
状态机可以细，UI 词汇必须粗，八个状态顶在首页就是状态噪音。

**e. Phase 3 保留一条零成本旁路。** pentest_intake 幂等 rerun 已存在且被
测试钉死：Phase 1 完成后配一个 systemd timer 做"每日重收录"，即获得能力
保鲜 80% 的收益，完整进化管道（SBOM/沙箱/Owner Gate）仍按相位等 Phase 3。
不占 Phase 1 一天工期。

**f. 关于"不是复制而是创造"的落点。** Mission Center 进度条 + 事件流是
基本功（似 Linear/Grafana 无妨）；**不可让渡的创造点**有二：Timeline Graph
直译 Mission→Agent→Broker→Verifier 执行模型（§6b），和"大白话原话与专业
提示词并排可见"的转换台——指令不弱化传达是别人没有的产品人格，UI 必须
把原话显示出来，不能只显示转换结果。

## 七、所有者主权验收条款（2026-10-13 操作员指令：不得弱化、不得偏离）

以 FACAI/owner 为最大指挥中心的方案是本平台的**最高设计约束**，任何相位
（含 Phase 1 UI）违反以下任一条即为阻断性缺陷：

1. **单根不变量**：一切授权签发的 issuer 只能是 `FACAI_OWNER`
   （`authority.py:37` 单根；`boundary.py:179/329` owner 专属放行与签发）。
   任何新模块不得引入第二权威根。
2. **裁决点显式**：每个 UI 视图必须把所有者裁决点（反馈门、隔离放行、
   远程授权、升级批准）渲染为显式入口；不存在"绕过所有者"的自动化路径。
   自动化只能到达门，不能替所有者过门。
3. **流向不变量**：一切流（事件、任务、流水线、升级）严格 owner 域隔离
   （`events_for_owner` / `current_owner()` 域过滤为本仓库唯一模式）；
   `below_local_report` 的"远程权限 < 本地所有者"必须恒 holds。
4. **改动即留痕**：对边界/闸门的任何修改（模式切换、规则增删、gate 开关）
   都是所有者动作且必须留痕（boundary journal / evolution journal /
   提交记录）。静默弱化 = 最严重事故等级。
5. **UI 不得稀释**：显示层合并状态、简化词汇可以，但不得弱化传达——
   操作员原话永远与专业版并排可见；裁决选项永远是大白话。

审计基线（2026-10-13，本提交前实测）：33 处 admin 守卫、92 处 owner 域
检查；我接手后的三次提交（7a1e718/7d88aea/62f5510）全部为 owner 域内
新增，零弱化。既有先例 `FACAI_GATES_OFF`（7ea3e38，2026-10-12 操作员
决定）同样遵循此模式：闸门的开与关都是所有者签发的动作、带提交留痕。

## 八、本稿状态

待操作员过目修订后冻结。冻结即开工 Phase 1（新 `static/command-center.html`
起步，三视图渐进交付，每视图合入即跑全量测试）。
