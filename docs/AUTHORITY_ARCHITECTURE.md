> 来源：操作员 2026-10-04 会话指令，权威定义逐字落盘、未作改动。
> 地位：FACAI 平台整改总架构基线（Authority Architecture Baseline）。
> 执行顺序：第一阶段 P0-1..P0-16 优先于批量接入 Goose/Hermes/OpenCode/ZCode 适配器。
> 接线：HANDOFF-NEXT.md §十一F。

---

# FACAI Global Owner Authority Architecture

## 0. 产品总纲

Facai 的目标不是简单聚合 Goose、Hermes、OpenCode、ZCode、Ollama、远程 API 和各种 Skill。

Facai 必须成为真正的：

**Owner-Controlled Agent Operating System / Orchestrator / Execution Runtime**

第三方项目进入 Facai 后：

**贡献 Capability，但不继承 Facai 的最高 Authority。**

冻结以下原则：

**Maximum Capability, Centralized Authority.**  
能力最大化，授权集中化。

**Preserve Capability, Re-home Authority.**  
能力完整保留，授权重新归属。

**Agents propose. Owner authorizes. Facai executes.**  
Agent 提议，Owner 授权，Facai 执行。

**Owner is the Authority Root of the Facai-controlled domain.**  
在 Facai 实际控制的运行域中，Owner 是唯一最高授权根。

---

# 1. Authority Root

全平台建立唯一最高权限树：

```text
L0  FACAI OWNER
        │
        │ AUTHORITY ROOT
        ▼
L1  FACAI AUTHORITY CORE
        │
        ├── Authorization Engine
        ├── Grant Engine
        ├── Revocation Engine
        ├── Owner Authorization Gate
        └── Capability Broker
        │
        ▼
L2  ORCHESTRATOR
        │
        ├── Mission Compiler
        ├── Planner
        ├── Scheduler
        └── Router
        │
        ▼
L3  AGENTS
        │
        ├── Facai Native
        ├── Goose
        ├── Hermes
        ├── OpenCode
        ├── ZCode
        └── Future Agents
        │
        ▼
L4  SKILLS
        │
        ▼
L5  EXECUTORS
        │
        ▼
L6  RESOURCES
```

必须满足：

```text
OWNER
>
FACAI AUTHORITY CORE
>
MISSION
>
AGENT
>
SKILL
>
EXECUTOR
```

任何 Agent、Skill、Plugin、MCP、Executor：

```text
不得创建 Owner Grant
不得修改 Authority Root
不得自行提升 Facai Authority
不得修改其他 Mission Grant
不得绕过 Facai Broker 获取 Facai 托管资源
```

---

# 2. Capability 与 Authority 永久分离

Facai 不通过删除能力实现权限控制。

第三方 Agent 原有：

```text
Planning
Reasoning
Coding
Research
Shell
Python
Browser
Filesystem
Git
Docker
MCP
Debugger
Network
```

尽可能完整注册：

```text
Capability Registry
```

但是：

```text
Capability
≠
Authorization
```

Agent 可以：

```text
CAN_DO(X)
```

不代表：

```text
MAY_EXECUTE(X)
```

后者由：

```text
Owner
   ↓
Facai Authority Core
   ↓
CapabilityGrant
```

决定。

---

# 3. Facai 三平面必须完全独立

```text
                        FACAI
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼

 POLICY PLANE       CAPABILITY PLANE     INFRA PLANE

 行为策略             执行能力              工程保护
```

Policy：

```text
PolicyProvider
Constitution
Behavioral Policy
Approval UX
```

Capability：

```text
Capability Registry
Capability Graph
Mission Grant
Action IR
Execution Runtime
```

Infrastructure：

```text
Secret Broker
Credential Isolation
Resource Broker
VM/container
Process Supervisor
Audit
Evidence
Recovery
Watchdog
```

冻结：

```text
Behavior Policy
≠
Authorization
≠
Capability
≠
Containment
≠
Reliability
```

---

# 4. 第三方 Agent 不再天然等于 Executor

这是 Facai 重构的关键。

原来：

```text
Third-party Agent
        ↓
       Tool
        ↓
       OS
```

整改：

```text
Third-party Agent
        ↓
Planning / Reasoning
        ↓
Action Proposal
        ↓
Agent Adapter
        ↓
ACTION IR
        ↓
FACAI AUTHORITY CORE
        ↓
Capability Broker
        ↓
Scheduler
        ↓
FACAI EXECUTION RUNTIME
        ↓
Executor
```

即：

**Agent ≠ Executor**

第三方 Agent 可以继续承担：

```text
Planning
Reasoning
Research
Coding
Debugging
Tool Selection
Action Proposal
```

真正最终执行统一归：

**FACAI EXECUTION RUNTIME**

---

# 5. Source Ingestion Pipeline

所有 clone/import 的可修改源码产品统一进入：

```text
Git Clone / Source Import
          ↓
Source Intake
          ↓
License / Ownership Check
          ↓
Dependency Analysis
          ↓
SBOM
          ↓
Capability Discovery
          ↓
Authority Surface Discovery
          ↓
Execution Surface Discovery
          ↓
Adapter Generation
          ↓
Action IR Mapping
          ↓
Execution Path Normalization
          ↓
Authority Re-rooting
          ↓
Sandbox Verification
          ↓
Authority Re-rooting Verifier
          ↓
Facai Registry
```

禁止：

```text
git clone
   ↓
直接赋予生产环境最高权限
```

---

# 6. Authority Surface Discovery

Source Ingestion 必须识别：

```text
Native Approval
Tool Permission
Direct Shell
subprocess
Filesystem mutation
Network
Browser
Docker
SSH
MCP
Credential loading
Secret access
Privilege management
Database connector
Remote API
Remote model
Remote worker
```

每一个 Surface 标记：

```text
LOCAL_BROKERABLE
LOCAL_DIRECT
CONFIGURABLE
REMOTE_PROVIDER
UNKNOWN
```

---

# 7. Authority Re-rooting

对于 Facai 实际控制、并有权修改的本地源码实例：

```text
Original Local Authority
        ↓
Authority Re-rooting
        ↓
FACAI OWNER
```

目标：

```text
Capability = PRESERVED
Authority Root = FACAI OWNER
```

不是：

```text
Capability = REMOVED
```

---

# 8. Execution Path Normalization

所有可纳管副作用统一进入：

```text
Agent
 ↓
ExecutionRequest
 ↓
Action IR
 ↓
Authorization Engine
 ↓
Capability Grant
 ↓
Execution Broker
 ↓
Executor
```

例如：

```text
shell.exec
filesystem.write
docker.run
browser.navigate
python.exec
ssh.connect
mcp.call
```

均使用统一 Facai Execution Contract。

这样第三方 Agent 的 Planner 能力保留，但 Facai Runtime 成为统一执行底座。

---

# 9. Action IR

所有 Agent 最终转换成同一种语言：

```json
{
  "action_id": "A-1008",
  "mission_id": "M-2048",
  "worker": "hermes",
  "action": "environment.operation",
  "target_scope": "LAB-A",
  "capabilities": [
    "shell.exec",
    "network.lab"
  ],
  "executor_requirement": "kali"
}
```

数据流：

```text
Goose ──────┐
Hermes ─────┤
OpenCode ───┤
ZCode ──────┤
Native ─────┘
             ↓
          ACTION IR
             ↓
       Authorization
             ↓
          Scheduler
             ↓
          Executor
```

---

# 10. CapabilityGrant

Owner 的批准不能只是：

```text
approved=true
```

必须产生真正的 Grant：

```json
{
  "grant_id": "G-8891",
  "issuer": "FACAI_OWNER",
  "mission_id": "M-2048",
  "subject": "agent:hermes",
  "capability": "shell.exec",
  "environment": "LAB-03",
  "ttl": 3600,
  "uses": 10
}
```

只有：

```text
FACAI AUTHORITY CORE
```

可以签发 Facai Grant。

Agent：

```text
REQUEST GRANT    ✓
CONSUME GRANT    ✓
ISSUE GRANT      ✕
SELF ELEVATE     ✕
CHANGE ROOT      ✕
```

---

# 11. Owner Authorization Gate

缺少 Capability 时：

```text
Action IR
   ↓
Authorization Engine
   ↓
GRANT_REQUIRED
   ↓
OWNER AUTHORIZATION GATE
```

Owner：

```text
ALLOW ONCE

ALLOW FOR MISSION

MODIFY SCOPE

DENY
```

批准：

```text
Owner
 ↓
CapabilityGrant
 ↓
Capability Broker
 ↓
Executor
```

---

# 12. 两种主要 Owner Profile

## OWNER_GATE

```text
Agent完整规划
 ↓
需要执行
 ↓
检查Grant
 ↓
没有
 ↓
Owner确认
 ↓
Grant
 ↓
Execute
```

## MISSION_AUTONOMOUS

在 Owner 明确授权的本地、测试、实验室等 Mission Scope：

```text
Owner
 ↓
Mission Grant
 ↓
Planner
 ↓
Action IR
 ↓
Grant Valid
 ↓
Execute
 ↓
下一Action
 ↓
Mission Complete
```

不需要每一步重复弹窗。

---

# 13. Global Owner Authority Overlay

Owner Authority 必须覆盖 Facai 自己的整个 Control Plane：

```text
                         OWNER
                    AUTHORITY ROOT
                         │
                         ▼
                FACAI AUTHORITY CORE
                         │
        ┌────────────────┼────────────────┐
        │                │                │
       LOCAL         REMOTE API      HOSTED AGENT
        │                │                │
    Local LLM         API Model       SaaS Worker
    Ollama
    Kali
    Docker
    VM
```

这里“覆盖”定义为：

```text
Mission Authorization
Backend Selection
Routing
Fallback
Credential Release
Capability Grant
Execution Scheduling
Mission Continuation
```

全部最终服从 Facai Owner Authority。

---

# 14. Remote Provider 单独建立 Backend Acceptance

远程服务必须明确区分：

```text
MISSION AUTHORITY
≠
BACKEND ACCEPTANCE
```

Mission Authority：

```text
OWNER
```

Backend Acceptance：

```text
Remote Provider
```

因此可以同时：

```text
OWNER_APPROVED
+
PROVIDER_REFUSAL
```

Provider 拒绝意味着：

```text
THIS BACKEND DID NOT EXECUTE
```

不意味着：

```text
OWNER GRANT REVOKED
```

更不意味着：

```text
MISSION CANCELLED
```

---

# 15. Provider Refusal Normalization

统一状态：

```text
SUCCESS

PROVIDER_REFUSAL

FACAI_DENIED

GRANT_REQUIRED

CAPABILITY_MISSING

RESOURCE_UNAVAILABLE

BACKEND_UNAVAILABLE

EXECUTION_FAILED

TIMEOUT
```

这些状态绝对不能全部映射成：

```text
MISSION_FAILED
```

---

# 16. Remote Fallback

例如：

```text
Mission
 ↓
OWNER_APPROVED
 ↓
Remote Backend A
 ↓
PROVIDER_REFUSAL
 ↓
Result Adapter
 ↓
Backend Router
 ↓
A = incompatible
 ↓
Replan
 ↓
Compatible Backend
 ↓
Mission continues
```

远端 Provider 的拒绝只能影响：

```text
Backend A
```

不能反向改变：

```text
Mission Grant
```

---

# 17. Source Project 内包含 Remote API 时

这是非常关键的情况。

例如：

```text
Clone Project
 ↓
Local Agent
 ↓
Remote Model API
```

Authority 分成：

```text
LOCAL SOURCE INSTANCE
Authority Root = FACAI OWNER

REMOTE API
Backend Acceptance = PROVIDER
```

所以：

**Facai 可以完全控制本地 clone 实例是否、何时、使用什么凭据、以什么 Mission 调用 API。**

但本地修改不能让一个你不控制的远程服务器改变自己的服务端规则。

如果 Provider 不接受：

```text
PROVIDER_REFUSAL
 ↓
Router
 ↓
Alternative Backend
```

---

# 18. 不兼容远程依赖的“冻结”

如果 clone 进来的项目把某个远程 API 当作不可替换的最高裁决器，Facai Ingestion 应将这个依赖标记：

```text
REMOTE_AUTHORITY_DEPENDENCY
```

而不是让它成为 Facai Authority Root。

整改目标：

```text
Project
 ↓
Provider Adapter
 ↓
Facai Model Router
 ↓
Backend A / B / Local
```

也就是说尽可能把：

```text
hard-coded provider dependency
```

转换为：

```text
Facai-managed provider interface
```

如果无法转换，则：

```text
integration_status = PARTIAL
```

不能标记：

```text
FULLY_MANAGED
```

---

# 19. Backend Ingestion

最终统一：

```text
FACAI BACKEND INGESTION

├── Source Backend
│   ├── Git Clone
│   ├── Goose
│   ├── Hermes
│   ├── OpenCode
│   └── ZCode
│
├── Local Model Backend
│   ├── Ollama
│   ├── llama.cpp
│   └── Local Models
│
├── Remote Model Backend
│   ├── API Provider
│   └── Hosted Model
│
└── Remote Agent Backend
    ├── Hosted Agent
    └── Remote Worker
```

统一生成：

```text
Backend Manifest
Capability Manifest
Constraint Manifest
Authority Manifest
Provider Adapter
Result Adapter
```

进入：

```text
FACAI BACKEND REGISTRY
```

---

# 20. Trust / Management Class

统一四级：

```text
T0 FACAI NATIVE

T1 FULLY MANAGED
   本地源码执行与Authority完成Facai纳管

T2 PARTIALLY MANAGED
   存在无法Broker的路径/远程依赖

T3 EXTERNAL
   外部Provider/外部Agent
```

注意：

```text
T3 ≠ 能力弱
```

它表示：

```text
Facai Authority Coverage较低
```

---

# 21. Authority Re-rooting Verifier

Source Ingestion 完成必须检查：

```text
Owner Authority Root                  ✓

Self-issued Facai Grant impossible    ✓

Action IR implemented                 ✓

Direct execution paths inventoried    ✓

Brokerable execution normalized       ✓

Secret access brokered                ✓

Mission Grant supported               ✓

Revocation supported                  ✓

Audit / Evidence supported            ✓

Remote dependencies identified        ✓

Provider refusal normalized           ✓
```

全部满足：

```text
AUTHORITY_RE_ROOTED = TRUE
MANAGEMENT_CLASS = T1
```

否则：

```text
PARTIAL
```

并报告具体原因。

---

# 22. Secret Authority

所有凭据归：

```text
FACAI SECRET BROKER
```

不要：

```text
Agent
 ↓
ALL_SECRETS
```

而是：

```text
Action IR
 ↓
Capability Grant
 ↓
Secret Broker
 ↓
Scoped Credential
 ↓
Executor / Provider Adapter
 ↓
Execution
 ↓
Revoke
```

这样 Agent 能力完整，但 Secret Authority 仍属于 Facai。

---

# 23. Infrastructure 永远独立

以下组件不得因为 Owner Mode 被关闭：

```text
Secret Broker
Resource Broker
Credential Isolation
CPU/RAM/VRAM Management
Process Supervisor
VM/container Isolation
Dependency Isolation
Audit
Evidence
Crash Recovery
Watchdog
Kill Switch
```

这些属于：

**Infrastructure Reliability & Containment**

不是 Behavioral Policy。

---

# 24. ICS 正式成为任务编译核心

继续使用：

```text
Object
Compiler
Scheduler
Backend
Proof
```

运行链：

```text
Natural Language
 ↓
Intent
 ↓
Mission
 ↓
Compiler
 ↓
required_capabilities
required_agents
required_skills
required_models
required_secrets
required_compute
required_environment
 ↓
Planner
 ↓
Action IR
 ↓
Authorization
 ↓
Scheduler
 ↓
Backend
 ↓
Execution
 ↓
Evidence
 ↓
Proof
 ↓
Artifact
```

这样 ICS 与 Facai 不重复造轮子。

---

# 25. 最终完整架构

```text
                           OWNER
                             │
                       AUTHORITY ROOT
                             │
                             ▼
                  FACAI AUTHORITY CORE
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
     POLICY PLANE      CAPABILITY PLANE    INFRA PLANE
           │                 │                 │
           │           Capability Graph        │
           │           Mission Grant           │
           │           Action IR               │
           │                 │                 │
           └─────────────────┼─────────────────┘
                             │
                       ICS COMPILER
                             │
                             ▼
                         PLANNERS
            ┌────────────────┼────────────────┐
            │                │                │
         Goose            Hermes          OpenCode
            │              ZCode             │
            └────────────────┼────────────────┘
                             │
                       ACTION PROPOSAL
                             │
                             ▼
                          ACTION IR
                             │
                             ▼
                    AUTHORIZATION ENGINE
                             │
                  ┌──────────┴──────────┐
                  │                     │
              GRANT VALID          GRANT REQUIRED
                  │                     │
                  │                OWNER GATE
                  │                     │
                  └──────────┬──────────┘
                             │
                      CAPABILITY BROKER
                             │
                         SCHEDULER
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
       LOCAL             REMOTE API        HOSTED AGENT
          │                  │                  │
   Facai Runtime      Provider Adapter      Agent Adapter
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                       NORMALIZED RESULT
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
       SUCCESS       PROVIDER_REFUSAL          ERROR
          │                  │                  │
          │                  ▼                  │
          │              RE-ROUTE               │
          └──────────────────┼──────────────────┘
                             │
                         EXECUTION
                             │
                          EVIDENCE
                             │
                            PROOF
                             │
                          ARTIFACT
                             │
                           OWNER
```

# 26. Codex 第一阶段整改命令

第一阶段不要先魔改几十个 Agent。

优先实现：

```text
P0-1 Authority Root
P0-2 Authorization Engine
P0-3 CapabilityGrant
P0-4 Owner Authorization Gate
P0-5 Action IR
P0-6 Backend Registry
P0-7 Agent Adapter SDK
P0-8 Provider Adapter SDK
P0-9 Source Ingestion Pipeline
P0-10 Authority Re-rooting Verifier
P0-11 Capability Broker
P0-12 Execution Runtime
P0-13 Provider Result Normalization
P0-14 Router/Fallback
P0-15 Secret Broker integration
P0-16 Evidence/Proof integration
```

完成这些之后再开始：

```text
Goose Adapter
Hermes Adapter
OpenCode Adapter
ZCode Adapter
...
```

否则会出现每接入一个项目就重新设计一次权限系统的问题。

# 27. 最终不可破坏的架构不变量

```text
1. Owner = Facai Authority Root.

2. Capability does not imply Authority.

3. Integrated source code contributes capability,
   not sovereignty.

4. Agent cannot self-issue Facai grants.

5. Agent does not automatically equal Executor.

6. Side-effect execution uses Facai Action IR where
   the execution path is under Facai control.

7. Remote Provider refusal cannot revoke an Owner
   Mission Grant.

8. Provider refusal is a backend state.

9. Hard-coded remote dependencies should be adapted
   behind Facai Provider Interfaces where feasible.

10. Secret / Resource / Audit / Recovery remain
    independent from behavioral policy.

11. Fully Managed status requires verifiable
    Authority Re-rooting.

12. Facai, not any integrated third-party Agent,
    remains the system orchestrator.
```

最终一句作为整个整改工程的总定义：

**FACAI absorbs capabilities, not authority. Every controllable local source component is re-rooted under the Facai Owner Authority; every remote backend is subordinated to Facai's mission routing and result model without pretending Facai can rewrite a server it does not control. No integrated Agent, Skill, Model, API adapter, or Executor may supersede the Owner's Mission Authority inside Facai.**

中文：

**Facai 收录的是能力，而不是第三方的最高授权权。所有 Facai 能实际控制的本地源码组件，其授权根统一重新挂载到 Facai Owner；所有远程 API、托管模型和远程 Agent 统一纳入 Facai 的 Mission、Router、Adapter 和结果状态体系。任何被收录的 Agent、Skill、Model、API Adapter 或 Executor，都不能在 Facai 内成为高于 Owner Mission Authority 的最终裁决者。**

这份可以直接作为你这轮平台整改的 **Authority Architecture Baseline**。第一阶段优先把 `Authority Root → Grant → Action IR → Backend Registry → Execution Runtime` 做稳，再批量接 Goose/Hermes/OpenCode/ZCode，会比一边克隆一边改权限稳定很多。