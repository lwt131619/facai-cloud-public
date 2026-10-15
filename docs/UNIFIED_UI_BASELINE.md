# FACAI Desktop + Mobile Unified UI（统一 UI 基线）

> 来源：操作员 2026-10-05 附文冻结。状态：**UI 开发正式基线**。五条冻结原则：One Mission Every Screen Every Device；Desktop=总控台 Mobile=遥控器；UI 反映真实执行不制造执行感；Simple outside Powerful inside；电脑下任务→手机批准→任意设备收成品。
> 核心验收：电脑创建 Mission → 手机实时看到同一 Mission → 手机操作后电脑实时同步 → Result/Artifact 两端一致。状态协议唯一（17 态，见 facai/intent.py UserTaskState），禁止 Desktop/Mobile/后台三套乱命名。

我建议下一阶段直接冻结成下面这个结构。

# FACAI DESKTOP + MOBILE UNIFIED UI

## 1. 总原则

Facai 不开发“两套产品”。

开发：

```text
ONE FACAI PRODUCT
        │
        ├── Desktop Projection
        └── Mobile Projection
```

共用：

```text
Mission
Intent
Plan
Action
TaskState
Timeline
Grant
BackendState
Evidence
Result
```

不同的只是 UI Projection。

---

# 2. 总架构

```text
                     FACAI CORE
                         │
                  Mission State
                         │
                   Facai Event Bus
                         │
                UX Projection Layer
                         │
             ┌───────────┴───────────┐
             │                       │
             ▼                       ▼
       DESKTOP UI                MOBILE UI
             │                       │
       Full Workspace            Action View
```

禁止：

```text
Desktop 自己维护任务状态
Mobile 自己维护另一套任务状态
```

否则迟早不同步。

---

# 3. Desktop 定位

电脑端是：

> **FACAI COMMAND CENTER**

适合：

```text
复杂任务
多任务并行
开发
文件
Agent 管理
Skill 管理
模型管理
服务器
详细 Timeline
Professional Mode
Developer Mode
```

布局：

```text
┌────────────────────────────────────────────────────────────┐
│ FACAI                                   ● Online    Owner   │
├───────────┬────────────────────────────────────┬───────────┤
│           │                                    │           │
│ 首页      │                                    │ 发财状态  │
│ 任务      │                                    │           │
│           │         TASK WORKSPACE             │ 当前任务  │
│ 发财能力  │                                    │           │
│ 文件      │                                    │ Worker    │
│ 自动化    │                                    │           │
│           │                                    │ Model     │
│ 高级      │                                    │           │
│ 设置      │                                    │ Runtime   │
│           │                                    │           │
├───────────┴────────────────────────────────────┴───────────┤
│ 输入：直接告诉发财你想做什么……                   🎙 📎 ➤ │
└────────────────────────────────────────────────────────────┘
```

---

# 4. Desktop 首页

首页第一屏：

```text
┌──────────────────────────────────────────────────────┐
│                                                      │
│                  今天想让我做什么？                  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ 直接用大白话告诉发财                          │  │
│  │                                                │  │
│  └────────────────────────────────────────────────┘  │
│                                        🎙  📎  ➤    │
│                                                      │
├──────────────────────────────────────────────────────┤
│ 正在工作                                             │
│                                                      │
│ ● 部署网站                67%    正在启动服务        │
│ ● 开发 App                42%    正在修复登录模块    │
│ ● 整理资料                28%    正在分析文件        │
│                                                      │
├──────────────────────────────────────────────────────┤
│ 最近完成                         发财最近变强了       │
│ ✓ 网站部署                       ✨ 自动重新规划      │
│ ✓ 报告生成                       ✨ 项目理解增强      │
└──────────────────────────────────────────────────────┘
```

用户第一眼看到：

**输入 + 正在干什么 + 干完什么 + 最近增强什么。**

---

# 5. Desktop Intent UI

用户输入：

> 我要做一个自己的购物网站。

输入框立即转换为：

```text
┌──────────────────────────────────────────────────────┐
│ ✓ 发财听懂了                                        │
│                                                      │
│ 你说                                                │
│ “我要做一个自己的购物网站。”                       │
│                                                      │
│ 发财理解                                            │
│ 创建一个可以实际使用的个人电商网站。                │
│                                                      │
│ 理解情况                                            │
│ ✓ 目标清楚                                          │
│ ◐ 部分细节可以稍后决定                              │
│                                                      │
│ [继续] [修改理解]                                   │
│                                                      │
│ ▸ 专业解析                                          │
└──────────────────────────────────────────────────────┘
```

这一步必须成为 UI 正式状态：

```text
INTENT_READY
```

而不是聊天消息。

---

# 6. Desktop Mission Expansion

点击继续：

```text
发财正在把你的想法整理成完整任务……

██████████
```

然后：

```text
┌──────────────────────────────────────────────────────┐
│ 发财准备这样完成                                    │
│                                                      │
│ 目标                                                 │
│ 创建并上线一个购物网站                              │
│                                                      │
│ 01 确定网站结构                                     │
│ 02 创建前端                                         │
│ 03 创建后端                                         │
│ 04 商品系统                                         │
│ 05 用户系统                                         │
│ 06 管理页面                                         │
│ 07 测试                                             │
│ 08 部署                                             │
│ 09 域名                                             │
│ 10 最终检查                                         │
│                                                      │
│ 发财自动补充                                        │
│ + 数据存储                                          │
│ + 错误处理                                          │
│ + 手机适配                                          │
│                                                      │
│ [开始工作]                                          │
│ [修改计划]                                          │
│ [补充要求]                                          │
│                                                      │
│ ▸ Professional Mission                              │
└──────────────────────────────────────────────────────┘
```

---

# 7. Desktop Task Workspace

进入执行：

```text
┌────────────────────────────────────────────────────────────┐
│ ← 任务              开发购物网站             ● EXECUTING │
├──────────────────────────────────────┬─────────────────────┤
│                                      │                     │
│ 总体进度                             │ 当前                 │
│ █████████████░░░  61%                │                     │
│                                      │ 正在创建商品 API     │
│ ✓ 理解需求                           │                     │
│ ✓ 制定方案                           │ Worker              │
│ ✓ 创建项目                           │ 2 active            │
│ ✓ 前端基础                           │                     │
│ ● 后端 API                           │ Model               │
│ ○ 用户系统                           │ Local               │
│ ○ 测试                               │                     │
│ ○ 部署                               │ Runtime             │
│                                      │ Docker              │
│                                      │                     │
├──────────────────────────────────────┴─────────────────────┤
│ TASK TIMELINE                                              │
│                                                            │
│ 15:21 ✓ 已理解需求                                        │
│ 15:22 ✓ 已完成任务规划                                    │
│ 15:24 ✓ 项目已经创建                                      │
│ 15:31 ✓ 前端基础完成                                      │
│ 15:34 ● 正在创建商品 API                                  │
└────────────────────────────────────────────────────────────┘
```

---

# 8. Desktop 三层视图

右上角：

```text
[简单] [专业] [开发者]
```

Simple：

```text
目标
进度
问题
结果
```

Professional：

```text
Mission
Requirements
Plan
Capabilities
Execution Strategy
```

Developer：

```text
Intent IR
Mission IR
Action IR
Agent
Skill
Model
Backend
Grant
Executor
Evidence
Proof
Logs
```

切换视图不能创建新任务。

它只是改变同一 Mission 的 Projection。

---

# 9. Desktop 多任务中心

这是电脑端非常重要的页面：

```text
FACAI TASK CENTER

ACTIVE  4     WAITING  1     COMPLETED  18

────────────────────────────────────────────────

● 开发购物网站
  ███████████░░░ 61%
  正在创建商品 API

● 部署服务器
  ██████████████░ 82%
  正在配置 HTTPS

● 整理照片
  ███████░░░░░░░ 43%
  正在识别重复照片

◐ 数据分析
  等待文件

────────────────────────────────────────────────

Workers

Worker-01   开发购物网站
Worker-02   开发购物网站
Worker-03   部署服务器
Worker-04   整理照片
```

普通模式不需要显示 Worker。

Developer Mode 才展开 Worker 分配。

---

# 10. Mobile 定位完全不同

手机不是缩小版 Desktop。

手机定位：

> **FACAI REMOTE CONTROL**

主要做：

```text
下任务
看进度
补充要求
授权
处理异常
查看结果
打开成品
```

不要在手机首页塞：

```text
Capability Graph
完整 Logs
Agent Registry
复杂 Scheduler
大量配置
```

---

# 11. Mobile 首页

```text
┌────────────────────────┐
│ 发财               ●   │
│                        │
│ 今天想让我做什么？     │
│                        │
│ ┌────────────────────┐ │
│ │ 直接告诉发财…      │ │
│ │                    │ │
│ └────────────────────┘ │
│                        │
│ 🎙   📎           ➤   │
│                        │
├────────────────────────┤
│ 正在工作               │
│                        │
│ ● 购物网站        61%  │
│   创建商品 API         │
│                        │
│ ● 部署服务器      82%  │
│   配置 HTTPS           │
│                        │
├────────────────────────┤
│ 最近完成               │
│                        │
│ ✓ 整理照片             │
│ ✓ 数据报告             │
│                        │
└────────────────────────┘
```

---

# 12. Mobile Intent Card

输入：

> 做一个购物网站。

界面直接变：

```text
┌────────────────────────┐
│ ✓ 发财听懂了           │
│                        │
│ 你想                   │
│                        │
│ 创建一个自己的         │
│ 购物网站。             │
│                        │
│ ✓ 目标清楚             │
│                        │
│ [继续]                 │
│                        │
│ 修改理解               │
└────────────────────────┘
```

手机不要塞完整 JSON。

---

# 13. Mobile Mission Preview

```text
┌────────────────────────┐
│ 发财准备这样做         │
│                        │
│ ✓ 设计网站             │
│ ✓ 创建前端             │
│ ✓ 创建后端             │
│ ✓ 商品系统             │
│ ✓ 用户系统             │
│ ✓ 测试                 │
│ ✓ 部署上线             │
│                        │
│ 预计结果               │
│                        │
│ 一个可以实际打开       │
│ 使用的购物网站         │
│                        │
│ [开始工作]             │
│                        │
│ 修改计划               │
└────────────────────────┘
```

---

# 14. Mobile 执行页

```text
┌────────────────────────┐
│ ← 购物网站        ●    │
│                        │
│ 61%                    │
│ ███████████░░░         │
│                        │
│ 发财正在               │
│ 创建商品 API           │
│                        │
│ ✓ 理解需求             │
│ ✓ 创建项目             │
│ ✓ 前端基础             │
│ ● 后端 API             │
│ ○ 用户系统             │
│ ○ 测试                 │
│ ○ 上线                 │
│                        │
│ 已完成 34 个操作       │
│                        │
│ [查看过程]             │
└────────────────────────┘
```

---

# 15. Mobile Timeline

用户点“查看过程”：

```text
任务过程

15:21
✓ 发财已经理解你的需求

15:22
✓ 已经制定完整计划

15:24
✓ 项目已经创建

15:31
✓ 前端基础完成

15:33
发现一个代码问题

15:34
发财已经调整办法

15:36
✓ 问题已经解决

15:37
● 继续开发
```

这非常适合用户在外面用手机看 Facai 在家里/云端工作。

---

# 16. Mobile 授权

如果需要用户决定：

手机直接 Push / 红点：

```text
发财需要你的确认
```

进入：

```text
┌────────────────────────┐
│ 需要你的允许           │
│                        │
│ 为了继续部署网站，     │
│ 发财需要操作服务器。   │
│                        │
│ 用于                   │
│ 当前部署任务           │
│                        │
│ 范围                   │
│ 本次任务               │
│                        │
│ [允许]                 │
│                        │
│ [不允许]               │
│                        │
│ 查看技术详情           │
└────────────────────────┘
```

这应该是手机端最重要的交互之一。

---

# 17. Mobile Replan

后台发生：

```text
ACTION_FAILED
→ REPLAN
```

手机：

```text
发财换了一个办法

原来的部署方式遇到问题。

发财发现：
服务器端口已经被占用。

已经调整：
✓ 更换可用端口
✓ 更新配置
✓ 重新启动

任务正在继续。

[知道了]
```

普通人完全能理解。

---

# 18. Mobile Result

完成时 Push：

```text
✓ 发财已经完成“部署网站”
```

打开：

```text
┌────────────────────────┐
│ ✓ 完成                 │
│                        │
│ 网站已经上线           │
│                        │
│ ✓ 服务正常             │
│ ✓ 域名正常             │
│ ✓ HTTPS 正常           │
│ ✓ 手机访问正常         │
│                        │
│ 发财完成               │
│ 18 个步骤              │
│ 52 个操作              │
│ 2 个问题自动解决       │
│                        │
│ 你还需要               │
│ 0 项                   │
│                        │
│ [打开网站]             │
│                        │
│ 查看完整报告           │
└────────────────────────┘
```

---

# 19. Desktop ↔ Mobile 实时同步

这是 P0，不是后期功能。

例如用户电脑下任务：

```text
Desktop
“帮我部署网站”
```

然后离开电脑。

手机：

```text
发财

部署网站
████████░░ 46%

正在构建项目
```

手机批准一个操作：

```text
ALLOW
```

电脑端立即：

```text
✓ Owner 已确认
继续执行
```

数据流：

```text
Desktop
   │
   ▼
Facai Server
   │
 Mission/Event Store
   │
   ├───────────────┐
   ▼               ▼
Desktop WS      Mobile WS
```

WebSocket/SSE 只是传输实现。

真正 Source of Truth：

```text
Mission State
+
Event Store
```

---

# 20. 跨设备继续任务

用户电脑：

```text
开发 App
53%
```

打开手机：

```text
开发 App
53%
```

不是新 Conversation。

必须还是：

```text
Mission M-8821
```

用户手机补一句：

> 登录页面再加一个忘记密码。

产生：

```text
Mission Amendment
```

而不是重新创建完全无关 Mission。

电脑马上显示：

```text
用户新增要求

+ 忘记密码功能

发财正在更新任务计划……
```

---

# 21. Desktop 与 Mobile 统一 Design System

建立：

```text
FACAI DESIGN SYSTEM
│
├── Typography
├── Spacing
├── Radius
├── Elevation
├── Motion
├── Icons
├── Status
├── Cards
├── Timeline
├── Progress
├── Input
├── Permission
├── Result
└── Task Components
```

不要 Web 写一套组件，Mobile 又重新设计。

核心组件：

```text
IntentCard

MissionCard

TaskCard

ProgressCard

TimelineItem

ReplanCard

ErrorCard

PermissionCard

ResultCard

CapabilityUpdateCard
```

Desktop：

宽版组件。

Mobile：

Stacked / Bottom Sheet 版本。

---

# 22. 状态视觉语言统一

全平台只认：

```text
RECEIVED
UNDERSTANDING
NEEDS_CLARIFICATION
INTENT_READY
EXPANDING
PLANNING
MISSION_READY
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

不能 Desktop：

```text
PROCESSING
```

Mobile：

```text
RUNNING
```

后台：

```text
ACTIVE
```

三套乱命名。

统一一个状态协议。

---

# 23. 响应式断点

如果第一阶段采用 Web/PWA：

```text
Desktop
≥ 1280

Tablet
768–1279

Mobile
< 768
```

Desktop：

```text
Sidebar + Workspace + Context Panel
```

Tablet：

```text
Collapsible Sidebar + Workspace
```

Mobile：

```text
Single Column
+
Bottom Navigation
+
Bottom Sheets
```

---

# 24. Mobile Bottom Navigation

建议只有：

```text
首页

任务

发财

文件

我的
```

中间：

```text
发财
```

可以作为主要自然语言入口。

不要手机下面放 9 个菜单。

---

# 25. Desktop Navigation

```text
首页

任务

发财能力

文件

自动化

────────

高级

设置
```

高级展开：

```text
Agent
Skill
Model
Backend
Executor
Server
Developer
```

---

# 26. 用户实际感知增强

每次 Facai 能力升级：

```text
Capability Foundry
        ↓
Capability Diff
        ↓
Benchmark
        ↓
Experience Translator
        ↓
┌─────────────┬─────────────┐
│ Desktop     │ Mobile      │
│ What's New  │ Update Card │
└─────────────┴─────────────┘
```

用户看到：

```text
发财变强了

以前：
部署失败后经常需要你处理。

现在：
发财可以识别部分失败原因，
重新制定计划并继续任务。

[看看示例]
```

不要只显示：

```text
planning.replan v2
```

---

# 27. Desktop 是控制台，Mobile 是遥控器

这一条直接冻结：

```text
DESKTOP
=
CREATE
CONTROL
OBSERVE
DEBUG
MANAGE


MOBILE
=
REQUEST
MONITOR
APPROVE
INTERVENE
RECEIVE
```

两边都可以下自然语言任务。

但是信息密度不同。

---

# 28. UI 开发必须跟 Core 同步

从现在开始，每个新 Core Feature PR 必须回答：

```text
CORE FEATURE
     ↓
产生什么 Event？
     ↓
对应什么 UserTaskState？
     ↓
Simple UI 怎么显示？
     ↓
Professional UI 怎么显示？
     ↓
Developer UI 怎么显示？
     ↓
Desktop 怎么显示？
     ↓
Mobile 怎么显示？
```

如果回答不了：

> 这个 Feature 还没有完成产品化。

---

# 29. 推荐代码目录

```text
apps/

├── web/
│   ├── desktop/
│   ├── responsive/
│   └── components/
│
├── mobile/
│   ├── screens/
│   ├── navigation/
│   └── components/
│
packages/
│
├── facai-design-system/
│
├── facai-task-ui/
│
├── facai-intent-ui/
│
├── facai-event-client/
│
├── facai-types/
│
└── facai-api-client/
```

共享：

```text
facai-types
facai-task-ui
facai-intent-ui
facai-event-client
```

不要复制业务逻辑。

---

# 30. 接下来正式开发顺序

## UI-P0A — Foundation

```text
Design Tokens
Shared Components
Responsive Shell
Task State Protocol
Event Client
Mission Store
```

## UI-P0B — Core Experience

```text
Plain Language Input
Intent Card
Intent Expansion
Mission Preview
Task Workspace
Timeline
Progress
```

## UI-P0C — Intervention

```text
Permission Card
Clarification Card
Error Card
Replan Card
Backend State Card
```

## UI-P0D — Completion

```text
Verification View
Result Card
Artifact View
Task Report
```

## UI-P0E — Cross Device

```text
Desktop sync
Mobile sync
Mission resume
Mission amendment
Notifications
```

## UI-P1

```text
Multi-task Dashboard
Capability Center
Before / After
What's New
Benchmark Experience
Advanced / Developer Console
```

---

# 31. 第一阶段必须一起做的页面

不要先只做 Desktop。

第一批直接成对开发：

```text
DESKTOP                  MOBILE

Home                 ↔   Home

Intent Preview       ↔   Intent Card

Mission Preview      ↔   Mission Preview

Task Workspace       ↔   Task Monitor

Timeline             ↔   Timeline

Permission           ↔   Approval Sheet

Replan               ↔   Replan Card

Error                ↔   Error Card

Result               ↔   Result Screen

Task Center          ↔   Task List
```

一个完成，另一端必须同步完成。

---

# 32. 最终验收场景

用户在电脑输入：

“帮我把这个项目部署上线。”

Desktop：

```text
发财正在理解
      ↓
发财听懂了
      ↓
Mission Preview
      ↓
开始执行
```

用户关掉电脑。

手机：

```text
部署项目
████████░░ 47%

发财正在构建项目
```

出现需要用户决定的事情：

```text
手机通知
↓
打开 Facai
↓
查看原因
↓
用户决定
```

任务继续。

之后：

```text
手机通知

✓ 网站已经部署完成
```

晚上打开电脑：

```text
Mission M-8821

COMPLETED

18 Steps
52 Actions
2 Auto Recoveries
1 Replan
Verification PASS

[打开成品]
[查看 Timeline]
[查看 Developer Report]
```

整个过程中：

**同一个 Mission。**

不是三个聊天记录。

---

# 33. 最终冻结原则

> **One Mission. Every Screen. Every Device.**

一个 Mission，贯穿所有页面、所有设备。

> **Desktop is the command center. Mobile is the remote control.**

电脑是总控台，手机是随身遥控器。

> **The UI reflects execution; it does not invent execution.**

UI 反映真实执行，不制造虚假执行感。

> **Simple outside. Powerful inside.**

普通用户看到极简操作，复杂能力留在内部。

> **A user should be able to start on desktop, approve on mobile, and receive the finished artifact anywhere.**

用户可以电脑下任务、手机看进度和处理确认，最终在任意设备接收成果。

这应该成为 Facai 接下来 UI 开发的正式基线。

下一步实现上，我会把 **Desktop 和 Mobile 视为同一个 P0 批次**，而不是“先把电脑做完以后再适配手机”。你现有架构已经要求 Event Bus 统一投影到 Web/iOS/Android，这正好成为跨端同步的底座。粘贴的文本 (1)

最关键的验收标准也可以直接定死：**电脑创建 Mission → 手机实时看到同一 Mission → 手机操作后电脑实时同步 → 最终 Result/Artifact 两端一致。**