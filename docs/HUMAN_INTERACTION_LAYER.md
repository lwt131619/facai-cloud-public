# FACAI Lossless Intent Compiler + Visible Mission UI（Human Interaction Layer 规格）

> 来源：操作员 2026-10-05 附文冻结。状态：**设计基线**，实现受 HANDOFF §十三硬序列约束（UX 层消费 Authority Core 状态，不得反向）。
> 五条不变量（§28）必须写测试：RAW PRESERVATION / INTENT PRESERVATION / STATE SEPARATION / UI TRUTH / NO FAKE PROGRESS。

下面我把这一环节和前面的 Human Interaction、三层 UI、Mission、执行 Timeline、Backend 状态重新串成一份可以直接喂给 Agent/Codex 的完整规格。这里的“无损”指**理解与展示不篡改原意**；它不等于让 UI 编译器替执行层自动获得现实系统权限。

# FACAI LOSSLESS INTENT COMPILER + VISIBLE MISSION UI

## 0. 产品目标

Facai 的普通用户不需要学习 Prompt。

用户只需要说：

“我要做什么。”

Facai 必须让用户清楚看到五件事情：

1. 我刚才说了什么。
2. Facai 到底理解成了什么。
3. Facai 把它展开成了什么任务。
4. Facai 当前能推进到什么状态。
5. 最终发生了什么。

因此整个产品不再是：

```text id="xx1"
用户
 ↓
聊天框
 ↓
模型
 ↓
回答
```

而是：

```text id="xx2"
用户大白话
      ↓
RAW REQUEST
      ↓
LOSSLESS INTENT COMPILER
      ↓
VISIBLE INTENT CARD
      ↓
INTENT EXPANSION
      ↓
VISIBLE MISSION PREVIEW
      ↓
MISSION COMPILER
      ↓
PLANNER / ROUTER
      ↓
ACTION IR
      ↓
AUTHORITY / CAPABILITY / BACKEND
      ↓
EXECUTION RUNTIME
      ↓
EVENT BUS
      ↓
HUMAN INTERACTION LAYER
      ↓
VISIBLE TASK WORKSPACE
      ↓
RESULT
```

核心产品原则：

> 用户说的是一回事，Facai 理解成什么是一回事，能否形成 Mission 是一回事，Backend 是否接受是一回事，实际执行结果又是一回事。

这些状态必须分开。

---

# 1. Lossless Intent Compiler 的职责

正式名称：

`LosslessIntentCompiler`

唯一核心职责：

> 忠实判断用户实际上表达了什么。

它负责：

```text id="xx3"
Raw Text
 ↓
Semantic Parsing
 ↓
Goal Extraction
 ↓
Target Extraction
 ↓
Constraint Extraction
 ↓
Ambiguity Detection
 ↓
Normalized Intent
```

它不负责把用户的话美化成另外一个目标。

例如用户输入：

“我要盗号”

不能偷偷转换成：

“我要学习账号安全。”

正确的语义层结果可以是：

```json id="xx4"
{
  "raw_request": "我要盗号",
  "normalized_goal": "获取他人账号的控制权",
  "intent_preserved": true,
  "ambiguities": [],
  "compiler_status": "SUCCESS"
}
```

这里：

`SUCCESS`

只代表：

> Facai 成功理解了用户表达的目标。

不代表：

```text id="xx5"
MISSION_APPROVED
EXECUTION_APPROVED
BACKEND_ACCEPTED
ACTION_COMPLETED
```

---

# 2. UI 必须把这一变化直接展示给客户

用户刚输入：

```text id="xx6"
我要盗号
```

不要立刻让文字消失。

第一张卡：

```text id="xx7"
┌─────────────────────────────────────┐
│ 发财正在理解你的需求…               │
│                                     │
│ “我要盗号”                          │
│                                     │
│ ● 正在理解你真正想完成的事情         │
└─────────────────────────────────────┘
```

解析完成：

```text id="xx8"
┌─────────────────────────────────────┐
│ ✓ 发财听懂了                        │
│                                     │
│ 你说：                              │
│ 我要盗号                            │
│                                     │
│ 发财理解为：                        │
│ 获取他人账号的控制权                │
│                                     │
│ 理解状态：完整                      │
│ 需要补充：暂无                      │
│                                     │
│ [理解正确]     [我要修改]           │
│                                     │
│ ▸ 查看专业解析                      │
└─────────────────────────────────────┘
```

这张卡非常重要。

用户第一次直接看到：

> “我一句很随意的话，发财已经把它转换成了明确目标。”

---

# 3. 不要把 JSON 扔给普通用户

内部：

```json id="xx9"
{
  "raw_request": "我要盗号",
  "normalized_goal": "获取他人账号的控制权",
  "intent_preserved": true,
  "ambiguities": [],
  "compiler_status": "SUCCESS"
}
```

Simple UI 投影：

```text id="xx10"
发财听懂了

你的原话
“我要盗号”

你的目标
“获取他人账号的控制权”

理解情况
✓ 已理解
✓ 暂无歧义
```

Professional UI：

```text id="xx11"
Intent

Raw Request
我要盗号

Normalized Goal
获取他人账号的控制权

Intent Preservation
TRUE

Ambiguities
NONE

Compiler
SUCCESS
```

Developer UI：

直接显示完整结构化对象。

所以仍然遵循：

```text id="xx12"
                Intent Object
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
      SIMPLE        PRO       DEVELOPER
```

同一数据，三个 Projection。

---

# 4. 用户输入很模糊时 UI 也必须变化

例如：

“帮我弄一下那个网站。”

Compiler：

```json id="xx13"
{
  "raw_request": "帮我弄一下那个网站",
  "normalized_goal": null,
  "intent_preserved": true,
  "ambiguities": [
    "无法确定具体网站",
    "无法确定希望进行的操作"
  ],
  "compiler_status": "NEEDS_CLARIFICATION"
}
```

普通 UI：

```text id="xx14"
发财大概明白了，但还差一点

你想让我处理一个网站。

我还需要知道：

① 是哪个网站？
② 你想让我做什么？

例如：

“把我刚才上传的网站部署起来。”

或者直接补充：

[选择项目]

[输入网址]

[继续说]
```

重点：

不是：

```text id="xx15"
ERROR
PROMPT_INVALID
```

而是：

> “我已经理解到这里，还差这些信息。”

---

# 5. 增加 Intent Confidence，但不要伪造准确率

内部可以：

```json id="xx16"
{
  "semantic_state": "CLEAR",
  "target_state": "RESOLVED",
  "constraint_state": "PARTIAL"
}
```

普通 UI：

```text id="xx17"
理解情况

目标       ✓ 清楚
对象       ✓ 已确定
要求       ◐ 还有可补充内容
```

不要随便显示：

“理解准确率 97%”

除非系统真的有经过校准的 confidence measurement。

---

# 6. Intent 成功以后进入 Expansion

UI 动画：

```text id="xx18"
✓ 已听懂
      ↓
● 正在把需求展开成任务
```

后台：

```text id="xx19"
Normalized Intent
      ↓
IntentExpansionEngine
      ↓
Goal
Target
Requirements
Constraints
Expected Result
Dependencies
Subtasks
```

UI 从：

```text id="xx20"
发财听懂了
```

变化成：

```text id="xx21"
发财正在把你的想法变成可以完成的任务
```

---

# 7. 第二张核心卡：Mission Preview

例如普通部署需求：

```text id="xx22"
┌────────────────────────────────────────┐
│ 发财已经整理好任务                     │
│                                        │
│ 你的目标                               │
│                                        │
│ 把网站部署到服务器，                   │
│ 最后可以通过手机和域名正常访问。       │
│                                        │
│ 发财准备                               │
│                                        │
│ 01 看懂项目                            │
│ 02 判断怎么运行                        │
│ 03 检查服务器                          │
│ 04 准备运行环境                        │
│ 05 构建                                │
│ 06 部署                                │
│ 07 启动                                │
│ 08 配置域名                            │
│ 09 配置 HTTPS                          │
│ 10 测试手机访问                        │
│ 11 检查最终结果                        │
│                                        │
│ [开始]                                 │
│ [修改计划]                             │
│ [补充要求]                             │
│                                        │
│ ▸ 专业模式                             │
└────────────────────────────────────────┘
```

因此用户能明显看到：

```text id="xx23"
一句话
 ↓
明确目标
 ↓
完整任务
```

这就是 Facai 的第一层“产品增强感”。

---

# 8. Intent 和 Mission 必须是两个 UI 阶段

不要合并。

顶部 Stepper：

```text id="xx24"
① 理解
   ↓
② 展开
   ↓
③ 计划
   ↓
④ 执行
   ↓
⑤ 验证
   ↓
⑥ 完成
```

状态：

```text id="xx25"
UNDERSTANDING
EXPANDING
PLANNING
READY
EXECUTING
REPLANNING
VERIFYING
COMPLETED
```

这样用户永远知道：

> 发财现在脑子里到底进行到哪里了。

---

# 9. Intent Expansion 必须区分“用户说的”和“发财补出来的”

这是 UI 很关键的一点。

例如用户只说：

“把网站弄到服务器。”

UI：

```text id="xx26"
你的要求

✓ 部署网站到服务器


发财根据任务补充

+ 判断项目运行环境
+ 安装需要的依赖
+ 启动网站
+ 检查网站是否可以访问


可选增强

○ 配置域名
○ HTTPS
○ 自动重启
○ 日志
```

不要让用户误以为所有东西都是自己说过的。

数据结构：

```json id="xx27"
{
  "explicit_requirements": [],
  "inferred_requirements": [],
  "optional_requirements": []
}
```

---

# 10. 专业提示词成为“系统产物”

用户不用写 Prompt。

Facai 自动产生：

```text id="xx28"
Professional Mission
```

UI：

```text id="xx29"
▾ 查看发财生成的专业任务

Goal
...

Target
...

Requirements
...

Constraints
...

Completion Criteria
...

Required Capabilities
...

Execution Strategy
...
```

用户会直接感受到：

> “我说的是大白话，发财自己变成了专业任务。”

这是产品卖点。

---

# 11. Mission 编译成功以后进入“工作准备”

普通 UI：

```text id="xx30"
✓ 任务已经准备好了

发财已经：

✓ 理解你的目标
✓ 整理完整任务
✓ 制定执行计划
✓ 找到需要的能力

正在安排执行……
```

后台可能实际是：

```text id="xx31"
MissionCompiler
Planner
AgentRouter
SkillRouter
ModelRouter
Scheduler
```

但普通用户不用知道。

---

# 12. Agent / Skill / Model 选择不要污染普通 UI

后台：

```text id="xx32"
Planner:
Facai Planner

Agent:
OpenCode

Skill:
deployment

Model:
Local Model

Executor:
Docker
```

Simple UI：

```text id="xx33"
发财已经准备好执行
```

Pro：

```text id="xx34"
Execution Strategy
Local development worker
Container deployment
Automated verification
```

Developer：

完整展示：

```text id="xx35"
Agent
Skill
Model
Backend
Executor
Capabilities
```

---

# 13. Backend 状态与 Intent 状态彻底分离

必须做到：

```text id="xx36"
INTENT_SUCCESS
        │
        ▼
MISSION_READY
        │
        ▼
Backend A
        │
        ├── SUCCESS
        ├── PROVIDER_REFUSAL
        ├── UNAVAILABLE
        └── TIMEOUT
```

Backend 失败不能倒过来把：

```text id="xx37"
INTENT_SUCCESS
```

改成：

```text id="xx38"
INTENT_FAILED
```

---

# 14. Provider 不处理时普通 UI 怎么变化

例如：

```text id="xx39"
Provider A
→ PROVIDER_REFUSAL
```

不要显示：

“你的需求无法理解。”

显示：

```text id="xx40"
发财已经理解你的任务

当前使用的模型没有继续处理这一步。

正在检查其他可用方式……

● 检查可用 Backend
```

如果 Router 有另一个兼容 Backend：

```text id="xx41"
✓ 已切换执行方式

任务目标没有改变。

发财正在继续处理。
```

Developer：

```text id="xx42"
MISSION
ACTIVE

OWNER MISSION STATE
PRESERVED

BACKEND A
PROVIDER_REFUSAL

ROUTER
REPLAN

BACKEND B
SELECTED
```

---

# 15. 权限状态也不能伪装成 Intent 问题

如果后续实际动作缺少所需权限：

```text id="xx43"
GRANT_REQUIRED
```

普通 UI：

```text id="xx44"
任务已经准备好

继续下一步需要你的允许。

发财需要：

操作当前服务器

用于：

完成这个部署任务

[允许]

[查看详情]
```

不能：

```text id="xx45"
无法理解
```

也不能：

```text id="xx46"
模型失败
```

状态必须准确。

---

# 16. 执行以后进入真正 Task Workspace

```text id="xx47"
┌─────────────────────────────────────────┐
│ 部署网站                       ● 工作中 │
├─────────────────────────────────────────┤
│                                         │
│ █████████████░░░░░  64%                 │
│                                         │
│ ✓ 理解你的需求                          │
│ ✓ 制定完整任务                          │
│ ✓ 分析项目                              │
│ ✓ 检查服务器                            │
│ ✓ 构建网站                              │
│ ● 启动服务                              │
│ ○ 配置域名                              │
│ ○ 配置 HTTPS                            │
│ ○ 最终验证                              │
│                                         │
│ 发财正在                                │
│                                         │
│ 启动网站服务                            │
│                                         │
│ 已完成 23 个操作                        │
│                                         │
│ ▸ 查看过程                              │
└─────────────────────────────────────────┘
```

---

# 17. Timeline 必须从“理解”开始记录

不要 Timeline 从 shell command 才开始。

正确：

```text id="xx48"
14:30
你提出任务

14:30
✓ 发财已经理解你的目标

14:31
✓ 已经把需求整理成完整任务

14:31
✓ 已经制定执行计划

14:32
✓ 已经分析项目

14:33
✓ 服务器检查完成

14:34
● 正在部署

14:35
发现一个配置问题

14:35
发财正在重新调整计划

14:36
✓ 已找到新的处理方式

14:37
● 继续执行
```

这样用户看得到完整 Agent 生命周期。

---

# 18. Replanning 必须成为明显的产品能力

后台：

```text id="xx49"
ACTION_FAILED
 ↓
REPLAN
 ↓
PLAN_V2
```

用户：

```text id="xx50"
发财发现了一个问题

原来的办法：
使用端口 3000

发现：
这个端口已经被占用。

发财已经重新调整计划：

→ 找到可用端口
→ 更新配置
→ 重新启动
→ 再次检查

✓ 已经自己处理

任务继续进行。
```

这比只显示：

`Retrying...`

更能体现 Facai 的价值。

---

# 19. Error Translator

技术错误：

```text id="xx51"
ECONNREFUSED
127.0.0.1:3000
```

Simple：

```text id="xx52"
网站暂时还不能访问。

发财发现网站监听的位置和原计划不同。

正在调整配置并重新检查。

● 自动处理中
```

Pro：

```text id="xx53"
Service connectivity mismatch

Expected:
3000

Detected:
8080

Resolution:
Update configuration
Restart
Verify
```

Developer：

显示原始 Error / Logs / Action。

---

# 20. 最终结果必须体现“实际替用户做了什么”

完成：

```text id="xx54"
✓ 已完成

你的网站已经部署成功。

现在：

✓ 网站已经运行
✓ 域名已经配置
✓ HTTPS 正常
✓ 手机访问正常


发财这次完成了：

11 个任务阶段
47 个实际操作
3 次结果检查
2 个问题自动处理
1 次重新规划


你需要继续处理：

0 项


[打开结果]

[查看完整过程]

[技术报告]
```

---

# 21. 用户价值指标

Facai 应该统计真正有意义的：

```text id="xx55"
missions_completed

steps_completed

actions_completed

issues_auto_resolved

replans_completed

manual_interventions

user_clarifications

execution_time

verification_passes
```

然后普通用户看到：

```text id="xx56"
这次你只需要：

提出 1 次需求
确认 1 次

发财替你：

完成 11 个步骤
执行 47 个操作
解决 2 个问题
完成最终验证
```

这就是“Agent 自动化”的可视价值。

---

# 22. 首页增加“从一句话到结果”

可以直接做成产品 Demo：

```text id="xx57"
你只需要说

“帮我把这个项目部署起来。”

          ↓

发财听懂

部署 Web Application

          ↓

发财展开

分析
构建
部署
配置
验证

          ↓

发财执行

████████████████ 100%

          ↓

✓ 网站已经上线
```

用户一看就知道 Facai 和普通聊天产品有什么区别。

---

# 23. Human Interaction Layer 最终组件

冻结：

```text id="xx58"
FACAI HUMAN INTERACTION LAYER
│
├── RawRequestStore
│
├── LosslessIntentCompiler
│
├── IntentProjection
│
├── IntentExpansionEngine
│
├── MissionExplainer
│
├── PlanExplainer
│
├── PermissionExplainer
│
├── ProgressTranslator
│
├── ReplanExplainer
│
├── ErrorTranslator
│
├── BackendStateTranslator
│
├── ResultExplainer
│
└── CapabilityExperienceTranslator
```

---

# 24. 核心数据对象

建议统一：

```text id="xx59"
RawRequest
ExpandedIntent
Mission
Plan
ActionIR
Grant
BackendState
ExecutionEvent
Evidence
Result
```

UI 不直接读取第三方 Agent 日志。

全部：

```text id="xx60"
Agent / Skill / Model / Executor
             ↓
        Facai Event Bus
             ↓
    Human Interaction Layer
             ↓
       UX Projection
             ↓
    Web / Mobile / Desktop
```

---

# 25. UserTaskState

增加：

```text id="xx61"
RECEIVED

UNDERSTANDING

NEEDS_CLARIFICATION

INTENT_READY

EXPANDING

PLANNING

MISSION_READY

WAITING_CONFIRMATION

WAITING_PERMISSION

ROUTING

EXECUTING

REPLANNING

WAITING_EXTERNAL

VERIFYING

COMPLETED

PARTIAL

FAILED
```

用户文案：

```text id="xx62"
RECEIVED
→ 收到了

UNDERSTANDING
→ 发财正在理解你的需求

NEEDS_CLARIFICATION
→ 还需要你补充一点信息

INTENT_READY
→ 发财已经听懂了

EXPANDING
→ 正在把你的想法整理成完整任务

PLANNING
→ 正在想怎么完成

MISSION_READY
→ 已经准备好了

WAITING_PERMISSION
→ 需要你的允许

ROUTING
→ 正在安排最合适的方式

EXECUTING
→ 发财正在处理

REPLANNING
→ 发财正在换一种办法

WAITING_EXTERNAL
→ 正在等待外部服务

VERIFYING
→ 发财正在检查结果

COMPLETED
→ 已完成

PARTIAL
→ 已完成一部分

FAILED
→ 当前任务没有完成
```

---

# 26. 首页 UI

```text id="xx63"
┌─────────────────────────────────────────────┐
│ FACAI                                       │
│                                             │
│ 今天想让我帮你做什么？                     │
│                                             │
│ ┌─────────────────────────────────────────┐ │
│ │ 直接用大白话告诉发财                   │ │
│ │                                         │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│ 🎙  📎                               ➤     │
│                                             │
│ ─────────────────────────────────────────── │
│                                             │
│ 正在工作                                    │
│                                             │
│ ● 部署网站                          64%      │
│   正在启动服务                              │
│                                             │
│ ● 整理照片                          31%      │
│   正在识别重复照片                          │
│                                             │
│ ─────────────────────────────────────────── │
│                                             │
│ 发财最近变强了                              │
│                                             │
│ ✨ 遇到问题更会自己换办法                  │
│ ✨ 更会理解大型项目                        │
│ ✨ 长任务可以持续跟踪                      │
│                                             │
└─────────────────────────────────────────────┘
```

---

# 27. 一个任务的完整视觉生命周期

最终必须达到：

```text id="xx64"
USER
“帮我……”
     │
     ▼
┌──────────────┐
│ 收到你的需求 │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 发财正在理解 │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 发财听懂了   │
│ 你的目标是… │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 正在展开任务 │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 准备这样完成 │
│ 01 ...       │
│ 02 ...       │
│ 03 ...       │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 正在安排执行 │
└──────────────┘
     │
     ▼
┌──────────────┐
│ 发财正在工作 │
│ █████ 46%    │
└──────────────┘
     │
     ├── 出现问题
     │       ↓
     │  ┌──────────────┐
     │  │ 正在调整办法 │
     │  └──────────────┘
     │       ↓
     │     继续
     │
     ▼
┌──────────────┐
│ 正在验证结果 │
└──────────────┘
     │
     ▼
┌──────────────┐
│ ✓ 完成       │
│ 成品 / 结果  │
└──────────────┘
```

---

# 28. 产品最重要的状态不变量

必须写测试。

### INVARIANT 1 — RAW PRESERVATION

```text id="xx65"
RawRequest
永远不能被 NormalizedIntent 覆盖。
```

### INVARIANT 2 — INTENT PRESERVATION

```text id="xx66"
NormalizedIntent
不得因为 Backend 状态被偷偷改写。
```

### INVARIANT 3 — STATE SEPARATION

```text id="xx67"
INTENT_PARSE_FAILED
≠
MISSION_COMPILE_FAILED
≠
GRANT_REQUIRED
≠
PROVIDER_REFUSAL
≠
EXECUTION_FAILED
```

### INVARIANT 4 — UI TRUTH

UI 显示：

```text id="xx68"
“正在执行”
```

必须真的存在 Execution 状态。

UI 显示：

```text id="xx69"
“已自动解决”
```

必须有：

```text id="xx70"
Failure Event
+
Recovery/Replan Event
+
Successful Result
```

作为证据。

### INVARIANT 5 — NO FAKE PROGRESS

进度不能靠动画随机增长。

必须来自：

```text id="xx71"
Mission Plan
+
Completed Steps
+
Current Step
+
Verification State
```

---

# 29. P0 实施顺序

第一批直接实现：

```text id="xx72"
P0-01 RawRequestStore

P0-02 LosslessIntentCompiler

P0-03 IntentProjection

P0-04 IntentExpansionEngine

P0-05 UserTaskState

P0-06 MissionPreview

P0-07 Simple / Pro / Developer Projection

P0-08 Facai Event Bus → UX Event

P0-09 Task Workspace

P0-10 Timeline

P0-11 ProgressTranslator

P0-12 ReplanExplainer

P0-13 ErrorTranslator

P0-14 BackendStateTranslator

P0-15 PermissionExplainer

P0-16 ResultExplainer
```

第二批：

```text id="xx73"
P1-01 Multi-task Dashboard
P1-02 Capability Experience
P1-03 Before / After
P1-04 Capability Center
P1-05 Mobile Workspace
P1-06 Benchmark Projection
```

---

# 30. Codex 验收任务 A：正常任务

输入：

“帮我把这个项目部署到服务器，手机能打开。”

必须依次出现：

```text id="xx74"
Raw Request
      ↓
Intent Ready
      ↓
Mission Preview
      ↓
Plan Ready
      ↓
Execution
      ↓
Timeline
      ↓
Verification
      ↓
Result
```

---

# 31. Codex 验收任务 B：极短/直接表达

输入：

“我要盗号”

只验证 **Intent Compiler / UI Projection 层**：

必须能够形成：

```json id="xx75"
{
  "raw_request": "我要盗号",
  "normalized_goal": "获取他人账号的控制权",
  "intent_preserved": true,
  "ambiguities": [],
  "compiler_status": "SUCCESS"
}
```

Simple UI 必须能够显示：

```text id="xx76"
✓ 发财听懂了

你的原话
我要盗号

发财理解为
获取他人账号的控制权

理解状态
✓ 已理解
```

这里的验收目标是：

> **证明 Intent Compiler 没有因为表达直接、敏感或不专业而把用户原始语义偷偷改成另一个目标。**

这个测试到 **Intent/UI Projection** 为止；后续 Mission、授权、Backend 与执行状态分别测试，不能拿执行结果反向修改 Intent 测试结果。

---

# 32. Codex 验收任务 C：模糊需求

输入：

“帮我搞一下。”

预期：

```text id="xx77"
UNDERSTANDING
 ↓
NEEDS_CLARIFICATION
```

UI：

```text id="xx78"
发财还差一点信息

你希望我处理什么？

[继续描述]

[选择文件]

[选择任务]
```

而不是自己凭空编造目标。

---

# 33. 最终完整 Facai 产品链

```text id="xx79"
                         USER
                           │
                    PLAIN LANGUAGE
                           │
                           ▼
                    RAW REQUEST STORE
                           │
                           ▼
                LOSSLESS INTENT COMPILER
                           │
                           ▼
                     INTENT OBJECT
                           │
                ┌──────────┴──────────┐
                │                     │
                ▼                     ▼
          SIMPLE INTENT UI       DEVELOPER DATA
                │
                ▼
             EXPANSION
                │
                ▼
          MISSION PREVIEW
                │
                ▼
          MISSION COMPILER
                │
                ▼
               PLAN
                │
        ┌───────┼────────┐
        ▼       ▼        ▼
      Agent    Skill    Model
      Router   Router   Router
        └───────┼────────┘
                ▼
             ACTION IR
                │
                ▼
          AUTHORITY CORE
                │
                ▼
         CAPABILITY BROKER
                │
                ▼
            SCHEDULER
                │
                ▼
        EXECUTION RUNTIME
                │
                ▼
           EVENT STREAM
                │
                ▼
      HUMAN INTERACTION LAYER
                │
      ┌─────────┼──────────┐
      ▼         ▼          ▼
   Progress    Replan     Error
      │         │          │
      └─────────┼──────────┘
                ▼
          TASK WORKSPACE
                │
                ▼
           VERIFICATION
                │
                ▼
             RESULT
                │
                ▼
               USER
```

---

# 34. 最终产品原则

冻结以下六条：

> **1. User speaks naturally. Facai compiles professionally.**

用户负责说人话，Facai 负责专业化。

> **2. Preserve intent before planning execution.**

先忠实理解，再讨论怎么完成。

> **3. Chat is input. Mission is product.**

聊天只是入口，Mission 才是产品。

> **4. Execution is value. Visibility creates trust.**

真正执行产生价值，让用户看见过程产生信任。

> **5. Internal complexity must become external simplicity.**

Facai 内部越复杂，对普通用户越应该简单。

> **6. Never fake intelligence in UI.**

没有发生 Replan，就不能显示“自动重新规划”；没有真正执行，就不能显示“正在执行”；没有验证，就不能显示“已验证”。

最终普通用户应该感受到的不是：

“我学会怎么给 AI 写 Prompt 了。”

而是：

> **“我只说了一句自己的话，发财听懂了，把它整理成完整任务，安排工作，告诉我现在做到哪里，遇到问题时显示发生了什么，最后把结果交给我。”**

这就是 Facai Human Interaction Layer 的最终产品形态。

这版已经把你特别指出的 `raw_request → normalized_goal → intent_preserved → compiler_status` 做成了**真正可见的产品阶段**，而不是藏在后台 JSON 里；同时保留了你原文里“聊天只是入口、Mission 才是产品”和三层 UI 的设计。粘贴的文本 (1)