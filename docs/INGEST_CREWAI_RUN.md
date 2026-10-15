# INGEST 第二实网收录报告 — crewAIInc/crewAI（2026-10-06）

[9] Ingestion Automation（`facai/ops/ingest.py`）对第二个真实开源 Agent 仓库的实网收录记录。
选型：**crewAI**（`https://github.com/crewAIInc/crewAI`，MIT，Python，角色制多 Agent 编排框架
——Agents/Tasks/Crews/Flows；出处：[GitHub](https://github.com/crewAIInc/crewAI)、
[crewai.com](https://crewai.com)）。它是 FACAI 的直接对标域（多 Agent 编排），体量中等但远大于
swarm，因此本报告除标准产物校验外，**专项回答：多 Agent 编排相关的面（tool 调用、agent 协作）
在扫描器初扫里表现如何**。所有数字均来自当日实跑。

## 1. 结论摘要

- **管线可用**：一条命令 clone→初扫→双 manifest→Verifier 判定→报告，exit=0，**16.8s 一次成功，未触发重试**。
- **verdict 如实**：`T2_PARTIAL`（5 项 PASS、5 项 PENDING，未纳管面 121），无接线声明即降级，未虚报。
- **license 探测正确**：MIT（根 LICENSE "Permission is hereby granted" 命中；与上游公开信息一致）。
- **最关键发现（对齐 swarm 首跑教训但更严重）**：crewAI 全仓 **1400 个源码文件**，初扫按字母序
  只扫到第 **177** 个（截断点 `lib/crewai/src/crewai/a2a/extensions/a2ui/server_extension.py`；
  最后出面文件第 174 个 `…a2ui/client_extension.py`，第 175–177 个被扫但命中被配额拒收）就打满
  200 面——**源码覆盖率 12.6%**。编排核心 `crew.py`/`task.py`/`tools/base_tool.py`/`tools/agent_tools/*`
  **全部在未扫区域**。200 面里 86.5%（173 面）来自 `lib/cli`（CLI 脚手架与其测试），框架核心
  `lib/crewai/src/crewai` 仅 **20 面（10%）**，且其中 **0 个是 tool 调用或 agent 协作面**。
- **双重盲区**（控制实验证实）：
  1. **截断盲区**——扫到即可见的面没被扫：10 处 provider API KEY 直接 env 读、5 处 SDK 传递凭据
     （INFERRED_CREDENTIAL）、MCP stdio 传输的 `subprocess.Popen`（tool server 子进程拉起点）；
  2. **结构性盲区**——扫了也看不见的面：agent 间协作（delegate_work/ask_question 委托）与
     进程内 tool 执行（`BaseTool._run`）不产生 env/URL/subprocess 任一正则命中，**高配额扫描依然 0 面**。

## 2. 命令与耗时

实跑命令（仓库根 `facai-cloud`，HEAD 9d0e6ad，Python 3.12）：

```
C:/Users/user/AppData/Local/Programs/Python/Python312/python.exe -m facai.ops.ingest \
  https://github.com/crewAIInc/crewAI --name crewai \
  --vendor-dir "C:/Users/user/.zcode/workspace/default/facai-handoff/vendor"
```

| 项 | 值 |
|---|---|
| 结果 | **exit=0，一次成功**（网络失败重试条款未触发） |
| 耗时 | 16.8s |
| clone | `cloned` → `vendor/crewai`，HEAD `738c8e1`（feat(flow): add experimental job lifecycle…#7864） |
| 克隆体量 | 31868 文件（不含 .git），其中 **1400 个源码扩展名文件**（1396 个 .py） |

## 3. 产物清单（全部为新建，未触碰既有 vendor 内容）

| 产物 | 路径 |
|---|---|
| 浅克隆 | `vendor/crewai/`（31868 文件） |
| Authority Manifest | `vendor/authority-manifests/crewai.json`（200 面） |
| Capability Graph 骨架 | `vendor/capability-graph/crewai.json`（71 条目） |

## 4. 面计数与 Verdict

**扫描**：200 面（源码档**截断**，`truncated=true`；文档档 0 面、未扫到），实际扫描文本文件
**177 个（源码 177 / 其余 0）**。

分类：`REMOTE_PROVIDER=46`、`LOCAL_BROKERABLE=25`、`UNKNOWN=129`。
kind：`NETWORK=140`、`CONFIG=35`、`SHELL_EXEC=21`、`CREDENTIAL=4`、`INFERRED_CREDENTIAL=0`。

**Verifier 判定**（`verify_source`）：`T2_PARTIAL`，未纳管面 **121**，降级理由：
`关键项 PENDING（未纳管）：brokerable_paths, secret_access, remote_dependencies, provider_result, evidence_linkage`。

| 检查 | 结果 |
|---|---|
| owner_root / self_grant / action_ir / grant_revocation / direct_exec_inventory | PASS×5 |
| brokerable_paths / secret_access / remote_dependencies / provider_result / evidence_linkage | PENDING×5 |

**能力候选 3 条**：`shell.exec`、`secret.request`、`model.invoke`（均 verdict=new、L0_REFERENCE）；
外部依赖主机 68 个（其中 okta/auth0/microsoftonline/workos/keycloak 等认证测试夹具主机占约 3/4）。

## 5. 产物校验矩阵（本波次实际执行，全过）

```
CHECK-1 json.load ×2        → 两份 JSON 解析通过
CHECK-2 manifest 六键       == goose.json 基线（target/origin/managementClass/summary/surfaces/reRooting）
CHECK-3 where 可回溯        → 200/200 带文件:行号后缀；200/200 文件存在于克隆（0 伪造路径）
CHECK-4 INDEX.json          → 9 源，不含 "crewai"
CHECK-5 CAPABILITY_REGISTRY → 全文不含 "crewai"
CHECK-6 verify_source 重算  → T2_PARTIAL、未纳管 121、降级理由逐字一致（5 PASS/5 PENDING）
CHECK-7 by_class/by_kind    → 与 manifest 统计复核一致（46/25/129；140/35/21/4/0）
CHECK-8 license             → graph license=MIT；LICENSE 首行 "Copyright (c) 2025 crewAI, Inc." + MIT 授权文本
```

补充校验：`pytest tests/test_ingest.py -q` → **23 passed in 0.51s**（管线自身测试绿）。
未重跑全量套件：并行波次正在改树（HEAD 提交自含 972 green），本波次结果无法归因，如实说明（同 INGEST_FIRST_RUN §9 先例）。

说明：任务书指「产物校验同 aider 组」。aider 为并行波次，本波次执行校验矩阵时其简报尚未落盘，
故按仓库已文档化的收录校验协议（`docs/INGEST_FIRST_RUN.md` §8/§10.4）执行上表；aider 简报
（`docs/INGEST_AIDER_RUN.md` §8）落盘后已逐项核对，其检查清单与本表一致（json.load×2、六键、
where 核验、verify_source 只读重算、INDEX/Registry 防越界、定向 test_ingest.py），无遗漏项。

## 6. 多 Agent 编排面专项：tool 调用与 agent 协作在初扫的表现

### 6.1 实跑 200 面的区域分布：框架核心被边缘化

| 区域 | 面数 | 占比 | 内容 |
|---|---|---|---|
| `lib/cli`（CLI 脚手架+测试） | 173 | 86.5% | 其中 SHELL_EXEC 全部 21 面、REMOTE_PROVIDER 37 面（认证测试夹具） |
| `lib/crewai/src/crewai`（框架核心） | 20 | 10% | 6 NETWORK + 7 CONFIG + 2 CREDENTIAL，其余 a2a 文档 URL；**0 个 tool/协作面** |
| 根目录 `conftest.py` | 5 | 2.5% | 含 `api.openai.com/v1` 测试夹具 |
| `docs/*.js`（官网埋点脚本） | 2 | 1% | 被当源码扫（.js ∈ SOURCE_EXTS），`cdn.cr-relay.com` 埋点被判 REMOTE_PROVIDER |

框架核心 20 面明细：`constants.py`×3、`context.py:47`（CREWAI_PLATFORM_INTEGRATION_TOKEN）、
`execution.py`×2、`llm.py`×6、`a2a/auth/server_schemes.py`×2 + `a2a/extensions`×5。
**`crew.py`、`task.py`、`agents/`、`tools/`、`utilities/` 零覆盖**——不是这些文件干净，
是根本没扫到（扫 177/1400=12.6%，截断于 a2a/extensions/a2ui/ 目录内）。

### 6.2 tool 调用面：截断盲区 + 结构性盲区各半

- **截断盲区（扫到即可见，但没扫到）**：控制实验对框架核心
  `lib/crewai/src/crewai`（547 文件）高配额 `scan_source(limit=5000)` → **153 面**：
  - `SHELL_EXEC=2`：**`mcp/transports/stdio.py:61 subprocess.Popen`**——crewAI 经 MCP stdio
    传输把 tool server 以子进程拉起，这是 tool 调用链上最真实的 shell 执行面；另有
    `events/listeners/tracing/utils.py:242`；
  - `CREDENTIAL=10`：`OPENAI_API_KEY`×2（`llms/providers/openai/completion.py:432`、
    `rag/chromadb/config.py:61`）、`ANTHROPIC_API_KEY:361`、`AZURE_API_KEY:128`、
    `GOOGLE_API_KEY:85`、`GEMINI_API_KEY:86`、`AZURE_CREDENTIAL_SCOPES:170`、
    `TOKENIZERS_PARALLELISM:66` 等——模型凭据的直接 env 读，实跑全不可见；
  - `INFERRED_CREDENTIAL=5`：`llms/providers/openai/completion.py:458`（`OpenAI(**client_config)`，
    精确命中现有 openai 规则）、`anthropic/completion.py:313`、`bedrock/completion.py:1928`、
    `telemetry/tracing/gen_ai_shapes.py:69,87`——实跑 INFERRED_CREDENTIAL=**0**。
  - 即：实跑 LOCAL_BROKERABLE=25 面里**框架贡献 2 面**（context.py:47 + a2a auth AUTH_TOKEN），
    其余 23 面全是 CLI 的。
- **结构性盲区（扫了也没有面）**：tool 执行核心 `tools/base_tool.py:388,556`（`BaseTool._run`）
  是进程内方法调用——控制实验中该文件被扫且 0 命中。crewAI 的运行时 tool 调用（含
  CodeInterpreter 类代码执行工具）不经过 subprocess/env/URL 任一正则，扫描器规则集对它天然失明。

### 6.3 agent 协作面：结构性零可见

- **agent 间委托**（crewAI 协作核心）：`tools/agent_tools/agent_tools.py:6-33` 注册
  `DelegateWorkTool`/`AskQuestionTool`（`delegate_work`/`ask_question`）——该文件**高配额扫描后
  依然 0 面**（控制实验验证）。agent↔agent 协作是进程内方法调用，无任何正则可命中。
- **hierarchical 编排**（`crew.py` 的 manager agent 模式）：未扫到；即便扫到，同理 0 面。
- **A2A（agent-to-agent 协议）**：实跑扫到 `a2a/extensions/*` 的 5 个面，但全是
  `a2a-protocol.org`/`a2ui.org` 文档链接（UNKNOWN）；`a2a/auth/server_schemes.py` 出的 2 面
  是 AUTH_TOKEN env 与 URL。A2A 真实通信/凭据面在截断线之后。okta/auth0/entra 等 37 面
  REMOTE_PROVIDER 全是 `lib/cli/tests/authentication/*` 的**测试夹具**，不是运行时裁决点。

### 6.4 误报与错位（给 Verifier 的具体反馈）

1. **REMOTE_PROVIDER 误报率仍高**：46 面里 37 面（80.4%）来自 `lib/cli/tests/authentication/*`
   测试夹具（okta 16、workos 6、auth0 4、entra 4、keycloak 2、其余 5）；真实 provider 目录
   仅 `lib/cli/src/crewai_cli/model_catalog.py` 5 面（api.openai.com/groq/cerebras/anthropic/
   generativelanguage——这是 CLI 的模型目录命令，非框架运行时）。**测试文件命中应如文档一样
   降权或独立计数**（现规则只降权 docs/examples 段与 .md/.rst/.txt，不降权 `tests/`）。
2. **源码内文档 URL 逃过降权**：`lib/crewai/src/crewai/llm.py:341`
   `https://docs.crewai.com/en/learn/llm-connections` 因 URL 含 "llm" 关键词被判
   REMOTE_PROVIDER（`remoteAuthorityDependency=true`）——DOC_ 降权只对 pass-2 其余文件开启，
   **源码 pass（infer=True）无降权**，.py 里的文档链接是残余误报源（swarm §7-2 修复未覆盖此路径）。
3. **网站埋点脚本被当源码**：`docs/common-room-tracking.js`（官网 tracking 脚本）因 .js ∈
   SOURCE_EXTS 无降权，`cdn.cr-relay.com` 埋点端点进了 REMOTE_PROVIDER。
4. **大仓库下源码档字母序截断重现 swarm 首跑问题且更严重**：swarm 是 examples 挤掉源码，
   本次是**源码挤源码**（a2a < agents < crew.py 字母序靠后全部落榜）。crewAI 框架核心全扫仅
   153 面，200 配额本可容纳——问题在 1400 源码文件的仓库没有优先级策略。建议：核心包目录
   优先或源码档配额提高/取消，tests 目录降权腾配额。
5. **传递性凭据规则可扩 litellm**：crewAI 模型调用经 `litellm` 抽象（`llm.py:98 import litellm`），
   端点/凭据都藏在 litellm 内部；`_INFERRED_CRED_RULES` 现只认 OpenAI(/Anthropic(/httpx/requests，
   补一条 `import litellm|litellm.completion` 即可覆盖此类框架的最大凭据流。

### 6.5 一句话回答

**多 Agent 编排相关的面在初扫里表现≈不可见**：tool 调用只见 CLI 侧的 21 个无关子进程面
（框架自身的 MCP stdio `subprocess.Popen`、10 处 provider KEY env、5 处 SDK 传递凭据全被截断）；
agent 协作（delegate/ask、hierarchical manager）结构性 0 面（进程内调用无正则命中，控制实验证实
扫了也是 0）。verdict 方向仍正确（无接线声明必 T2_PARTIAL），但按现有清单做人工复核会把
86.5% 的精力花在 CLI 测试夹具上，而真正的编排裁决面一个都不在清单里。

### 6.6 与 aider 同日实跑的对照（扫描器改进的增量证据）

| 指标 | aider（180 源码文件） | crewai（1400 源码文件） | 判定 |
|---|---|---|---|
| 源码档截断 | 无（180 全扫） | **有（扫 177/1400=12.6%）** | 三修在 ≤几百源码文件的仓库成立，**千文件级仓库截断回归** |
| INFERRED_CREDENTIAL 实跑 | 0（**真阴性**：litellm 抽象，全仓零直接构造） | 0，但控制实验=5（`llms/providers/openai/completion.py:458` 真构造点）→ **截断假阴性** | crewai 的 0 是漏报，aider 的 0 不是——同为 0 语义相反，须看截断标志 |
| REMOTE_PROVIDER 构成 | 44 全源码（urls.py 等） | 46 里 37（80.4%）为 **tests/ 夹具** | 下一修：**tests/ 目录降权**（对应 docs 降权先例） |
| LOCAL_BROKERABLE | 41 全真实 | 25 里 23 出自 lib/cli，框架仅 2 | 编排框架的 brokerable 信号被脚手架淹没 |

与 aider §6 改进反馈相互印证：aider 提出的「INFERRED 规则表补 litellm 构造/导入点」在 crewai
同样成立（`llm.py:98 import litellm`、providers 直接构造点在案）；crewai 额外证明**截断偏置是
体量问题而非结构问题**——需要源码档优先级/配额策略，而非更多正则。

## 7. 纪律声明

- 未登记 Registry：收录产物停在 vendor 草案层；INDEX.json 9 源、CAPABILITY_REGISTRY.json 均不含
  crewai（CHECK-4/5 程序化复核）；登记待人工过 `facai/ops/verifier.py:register_with_gate` 门。
- 源码树只读：本波次仅新增 `docs/INGEST_CREWAI_RUN.md` 与 vendor 三件（克隆+双 manifest），
  未改任何代码；vendor 既有内容未触碰。
- 控制实验只读：对克隆调用 `scan_source` 不落盘、不改树。

## 8. 验证记录（本波次实际执行的命令）

```
python -m facai.ops.ingest <url> --name crewai   → exit=0，16.8s，200 面/T2_PARTIAL
pytest tests/test_ingest.py -q                   → 23 passed（0.51s）
程序化校验 CHECK-1..8                            → 全过（§5）
控制实验 scan_source(框架核心, limit=5000)       → 547 文件 153 面，SHELL_EXEC=2/CREDENTIAL=10/
                                                   INFERRED_CREDENTIAL=5，agent_tools/base_tool 0 面
截断点定位                                       → os.walk 字母序重建：最后出面文件排 174，
                                                   实际扫到 177（a2ui/server_extension.py 命中被拒收）
aider 简报交叉核对（落盘后）                     → 校验清单与本表一致，无遗漏项（§5 说明）
```
