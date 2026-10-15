# CAPABILITY_GRAPH — Facai 五路能力开采图谱

- 生成日期：2026-10-05
- 机器可读数据：`vendor/capability-graph/<source>.json`（位于 `facai-handoff/vendor/capability-graph/`，schema `facai.capability-graph/1`：`{source, license, method, summary, capabilities[]}`，每条能力含 `id/name/evidence/level/verdict/provider/remote_removal/native_candidate`）
- 范围：五路开采（facai-desktop / goose / sst-opencode / hermes-agent / autoclaw-zcode）共 **78 条能力**，其中 **57 条 native 候选**
- 本文档是汇总层，不新增证据；全部 file:line 证据由五路开采会话 grep/read/sed 实测，原文存于各 JSON 的 `evidence` 字段。开采均为只读静态走查，未运行被扫仓库的测试套件。

## 总览

| 源（图谱文件） | 许可 | 能力数 | L0 | L1 | L2 | L3 | L4 | new | better | duplicate | dependency | native 候选 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| facai-desktop（`facai-desktop.json`） | MIT | 19 | 1 | 5 | 12 | 1 | 0 | 11 | 7 | 1 | 0 | 14 |
| goose（`goose.json`） | Apache-2.0 | 15 | 0 | 0 | 0 | 15 | 0 | 9 | 5 | 1 | 0 | 12 |
| sst/opencode（`opencode.json`） | MIT | 14 | 0 | 1 | 13 | 0 | 0 | 11 | 3 | 0 | 0 | 11 |
| hermes-agent（`hermes-agent.json`） | MIT | 16 | 0 | 1 | 0 | 14 | 1 | 10 | 4 | 1 | 1 | 14 |
| autoclaw-zcode（`autoclaw-zcode.json`） | UNKNOWN | 14 | 0 | 12 | 0 | 0 | 2 | 3 | 3 | 5 | 3 | 6 |
| **合计** | — | **78** | **1** | **19** | **25** | **30** | **3** | **44** | **22** | **8** | **4** | **57** |

说明：
- 等级语义：L0_REFERENCE=仅参考格式 / L1_WRAPPED=外部本体包装 / L2_ADAPTED=源码级改编 / L3_FORKED=已 fork 可整片纳入 / L4_FACAI_NATIVE=Facai 自研实现。
- 源名与文件名的映射：源字段 `sst/opencode（vendor/opencode 本地实例，v1.18.34，MIT）` → `opencode.json`；`autoclaw-zcode（AutoClaw2 闭源产品树 + ZCode 本机可观测格式面）` → `autoclaw-zcode.json`；其余同名。
- 数字口径：一律按逐条 capability 数据统计。两处源摘要自述与逐条数据有 ±1 出入，均以逐条数据为准：goose 摘要写"10 new / 4 better"，逐条实测 9 new / 5 better；opencode 摘要写"4 条 better"，逐条实测 3 条 better（11 条 new，其中 session_share 为 new 但 native=false）；autoclaw 摘要写"4 条 dependency"，逐条实测 3 条。

## Nativeization Queue

全部 57 条 native 候选分三队。分队规则：
- **P0（20 条）**：补 Facai 已点名的安全核/执行核缺口——P0-4 Owner Gate 审批与规则引擎、Broker 执行收口与执行令牌、SOUL 身份与指令文件写保护、上下文压缩（交接文档明载"无上下文压缩/审批三档"）、P0-3 worktree 隔离、凭据 broker、Router/Fallback、Skill 安全链（交接文档自评 35%）。
- **P1（27 条）**：内核能力升级——推理循环进程内化、MCP 接入、技能运行时、工作流工件、委派编排、cron 时间表、会话库、STT、记忆、辅助模型路由、协议契约、编排沙箱等。
- **P2（10 条）**：产品面增值与工具性收录——i18n、Web UI、LSP 门面、钱包、市场、中继、二进制补丁流水线等，收录不阻塞安全核。

### P0（20 条）

| 源 | 能力 | 一句理由 |
|---|---|---|
| goose | permission.mode_gate | 落地到每次工具调用的 auto/approve/smart_approve/chat 三态审批 + permission.yaml + 持久化（TTL），可直接插在 Facai AuthorizationEngine 之下补 P0-4 Owner Gate 的工具粒度裁决。 |
| opencode | opencode.permission_rules_engine | deny/allow/ask wildcard 匹配 + once/always 记忆 + reject 级联拒绝的完整规则引擎，是 Owner Gate 规则层的现成模板（Facai 现仅 registries.py 的 review_status 元数据闸）。 |
| hermes-agent | security.approval_triage | 三档危险命令审批 + guardian LLM 防注入评审 + 楼层预闸 + 熔断，交接文档明载 Facai"无审批三档"，直接补缺口。 |
| facai-desktop | desktop.chat-approval-flow | once/session/always/deny 四态 + gateway request_id 防伪造 + 控制字符净化，且补丁未触碰、完整存活，是 Owner Gate 的 UI/协议层样板。 |
| autoclaw-zcode | autoclaw.governance.session-approval-rules | 行级会话审批规则表（side_effect 三级 + 可撤销留痕），把 Facai 授权粒度从 MISSION_AUTONOMOUS/OWNER_GATE 模式开关降到行级规则。 |
| goose | security.tool_inspection_chain | 权限/安全/对抗/出网四检查器统一裁决工具调用，是 Facai Broker 的现成挂载位（egress 现只 LOG，需升级为拦截）。 |
| opencode | opencode.tool_framework | Tool.define 契约 ctx.ask 每工具必经 + InvalidArgumentsError + 截断落盘，是 Broker 收口的进程内工具框架范本。 |
| opencode | opencode.pty_ticket | PTY 一次性 ticket（UUID+过期+匹配+消费制）正是 P0-3/P0-11 Broker 执行令牌的参考实现。 |
| goose | hooks.lifecycle_events | 12 事件钩子（PreToolUse/BeforeShellExecution…）直接映射 Owner Gate 审计检查点与 Broker executor 收口，Facai 无进程内钩子机制。 |
| facai-desktop | desktop.secrets-provider | env/command 双后端可插拔密钥框架（never-throw + 三级解析序），command 后端指 Facai Secret Broker CLI 即成 vault，直接衔接 adapter-core 已定位的明文凭据注入点。 |
| autoclaw-zcode | facai-proxy.pivot-9900 | 本地反代已实现双协议路由/key 池/failover（operator 自研在运行），是 Facai Router/Fallback P0 待建项的现成运行参考。 |
| hermes-agent | identity.soul_layer | SOUL.md 身份槽每消息热重载 + 上游已删默认人格 + 模板升级永不覆盖用户手写，authority 核 P0 的落地承载。 |
| facai-desktop | desktop.soul-identity | 每 profile 一份 SOUL.md 的读/写/重置协议 + 编辑屏，Facai SOUL 身份核（P0 未落地、无实现可引）可直接采用。 |
| hermes-agent | security.instruction_guard | SOUL/AGENTS/config 等指令文件写保护默认开启 + 加载期注入扫描按信任分级，封死提示注入写路径。 |
| hermes-agent | context.compress_pipeline | 分层压缩 + commit fence 串行化 + 快照 worker + 冷却阶梯，交接文档明载 Facai 无上下文压缩，长 mission 必撞窗。 |
| goose | agent.context_compaction | 阈值触发自动压缩 + HistoryReplaced 原子替换会话，与上条互补的第二实现。 |
| opencode | opencode.session_compaction | 溢出检测→剪枝→工具输出截断→摘要→插件可换提示词的完整生命周期，第三实现可对照取优。 |
| hermes-agent | exec.subagent_worktree | 子代理独立 worktree + 仅凭证据回收（零提交 AND 干净双探针），P0-3 并行节点 worktree 隔离的现成参照。 |
| hermes-agent | skills.hub | 多源注册表 + SSRF 安全客户端 + lock 校验 + 索引缓存 + 审计日志，补 Skill 安全面 35% 缺口的安装/版本锁定半边。 |
| hermes-agent | skills.ast_audit | AST 级动态 import/非字面 getattr/计算键扫描，Skill 安全链的深审计半边（自我定位为人工评审诊断）。 |

### P1（27 条）

| 源 | 能力 | 一句理由 |
|---|---|---|
| facai-desktop | desktop.localmax-switch | isRemoteOnlyMode 恒 false 一行解锁全部降级面 + 四路脱钩，补丁已验证可整片纳入 Facai fork 基线，桌面吸收的第一步。 |
| facai-desktop | desktop.kanban-workbench | SQLite 五实体 9 状态看板 + 依赖链 promote，直连 kanban.db（WAL）即可绕开 CLI 子进程，dispatcher 并入 Facai scheduler。 |
| facai-desktop | desktop.skills-manager | SKILL.md frontmatter 解析/搜索/装卸已实现，目录源指 Facai registry 即全本地，与技能安全链配套。 |
| facai-desktop | desktop.messaging-gateway-catalog | 20 平台目录含 7 家国内平台定义 + env 校验 + 连通测试，直接对接 Facai channels 三实现（须恢复被补丁删除的 risk 审批门）。 |
| facai-desktop | desktop.provider-mirror | 桌面↔config.yaml provider 双向文本拼接镜像（保留注释与未知字段），改指 Facai gateway 端点即用。 |
| facai-desktop | desktop.run-stream-transport | /v1/runs 四端点 + 七项能力协商是现成协议规范，把 Facai gateway 单发完成升级为 run 生命周期 API。 |
| goose | agent.inference_loop | 进程内 planner-executor 状态机循环（max_turns 预算 + 流式事件），吸收后 Facai 执行不再依赖 CLI 子进程承载循环。 |
| goose | mcp.extension_manager | 五类扩展动态增删 + 初始化排序，是把外部工具生态接入 Facai ToolProvider 契约的现成通道（Facai 现无任何 MCP 支持）。 |
| goose | skills.runtime_loading | load_skill 渐进加载 + {{参数}}替换 + supporting files 注入，与 Facai skills.py 的解析半边正好互补成完整链路。 |
| goose | recipe.workflow | 参数化 + sub_recipes + JSON schema 输出校验的可持久化工作流工件，可定为 Facai mission 模板的官方格式。 |
| goose | agent.lead_worker | orchestrator 四工具委派 + 防递归/兄弟限定授权规则 + subagent max_turns，Facai 完全没有进程内 lead-worker。 |
| goose | scheduler.cron_jobs | 5/6 段 cron 兼容 + 定时跑 recipe，补 Facai scheduler（enqueue/run_pending 队列）缺失的时间表语义。 |
| goose | voice.local_dictation | 4 档 GGUF 本地 whisper 推理（llamacpp/mlx），渠道语音转写的本地化底座。 |
| goose | provider.declarative_engine | 新增模型后端变成一份 YAML 声明而非代码，可并入 Facai registries 作 owner 作用域条目。 |
| opencode | opencode.codemode_interpreter | 受限 JS 子集解释器（三重资源预算 + 渐进披露 + 归一化诊断），4373 行 MIT 源码可整包移植为 Facai 脚本编排沙箱。 |
| opencode | opencode.protocol_api_contract | 单源 HTTP 契约自动出 OpenAPI + 生成 typed SDK + 有界 SSE，是 api.py 手写 FastAPI 的升级样板。 |
| opencode | opencode.snapshot_revert | git blob 单次编辑粒度快照与精确 revert，与 Facai checkpoint.py 的 mission 粒度快照互补。 |
| opencode | opencode.plugin_hooks | 15+ 生命周期挂点（可改参数/输出/注入鉴权头），permission.ask 是 Owner Gate 的又一挂点。 |
| opencode | opencode.task_subagent | 子代理派生 + 权限派生 + 背景任务通知 + task_id 恢复 + 深度限制，补 Facai 单层编排。 |
| hermes-agent | stt.local_first | 显式 local 无静默云回退 + 租约预热，补交接文档明载的"渠道语音未实现"缺口。 |
| hermes-agent | state.session_db | SQLite WAL 会话库 + CJK-bigram FTS5（tokenizer 回退自愈），Facai 会话检索的现成升级。 |
| hermes-agent | state.checkpoints | 每回合透明快照到共享 shadow git store（对象去重 + 自动修剪），LLM 不可见的回滚基建。 |
| hermes-agent | auth.credential_pool | 按模型冷却 + 按状态分档耗尽 TTL 的多凭据池，凭据经 Owner 授权核注入后整链本地。 |
| hermes-agent | aux.router | 压缩/搜索/视觉旁路任务与主模型解耦 + 原子中断保护，配本地 GLM 即用。 |
| hermes-agent | memory.curated | MEMORY/USER 冻结快照进提示（前缀缓存零失效）+ 单工具批量操作 + 跨平台文件锁。 |
| autoclaw-zcode | autoclaw.im.delivery-outbox | 租约式四态投递队列（effect_id 幂等 + claim 联动 CHECK + 重试调度），把 Facai _relay_outbox 从 QUEUED 记录升级为真队列，桥端零改动兼容。 |
| autoclaw-zcode | autoclaw.governance.guest-allowances | IM 访客三件套（四元主键白名单 + 配对码只存 71 字符 bcrypt hash + 渠道级 allowlist），纯本地准入机制。 |

### P2（10 条）

| 源 | 能力 | 一句理由 |
|---|---|---|
| facai-desktop | desktop.i18n-12lang | 12 语言 × 24 命名空间静态词条零远程，zh-CN/zh-TW 现成，随桌面/Web UI 收录即可。 |
| facai-desktop | desktop.memory-workbench | 记忆工作台 UI 完整（条目级增删改 + 容量闸），六家外部 provider 面板需替换为 Facai 后端或删。 |
| facai-desktop | desktop.registry-marketplace | 四类目录市场管线现成，但须先把 REGISTRY_REPO 换 Facai 自有源，否则等于给上游导流。 |
| facai-desktop | desktop.wallet-base | safeStorage(DPAPI) 加密 + ERC-20 只读查询本地闭环，涉及资金托管须专项安全审计后再收录。 |
| facai-desktop | desktop.dashboard-relay | 回环 ws 中继 + 关闭码白名单，桌面化时本地直连免中继，模式保留为远程通道。 |
| opencode | opencode.lsp_facade | 14 方法 LSP 门面 + 30+ 语言装配 + 诊断合并去重，编辑辅助语义查询增值。 |
| opencode | opencode.web_session_ui | 直连本地 server SSE 的 React 会话界面，配合 protocol 契约低成本获得浏览器管控台。 |
| hermes-agent | context.window_metadata | 五级缓存窗口元数据 + 显式 pin + 仅本地源偏差告警，models_dev.url 指内网镜像即可。 |
| autoclaw-zcode | autoclaw.patch.observable-binary | 闭源树 tagged 备份→单点替换→cmp 校验的明文补丁协议，固化为 re-patch 流水线的工具性收录。 |
| autoclaw-zcode | zcode.format.dynamic-workflow | TS 片段式工作流格式（编排/门禁/缓存三语义）可作 scheduler 之上的脚本方言；证据以运行时契约为准（本机无已保存脚本）。 |

## 去远程化汇总

78 条中 **36 条** remote_removal 判定纯本地（纯文件协议/本地 git/本地 SQLite/静态资源/Electron 本地能力），无收口动作，不列。下表 **42 条**含残留远端点/子进程面/需换源面，按源分组给收口句柄；收口后绝大多数为纯配置级/单点补丁级，少数为功能本体远端（已标注诚实边界）。

### facai-desktop（11 条）

| 能力 | 残留远端/触达面 | 收口句柄 |
|---|---|---|
| desktop.kanban-workbench | 执行经 execFile hermes CLI；SSH 模式透传远程 | 树内已依赖 better-sqlite3，直连 kanban.db（WAL）绕开子进程；SSH 保留为可选通道 |
| desktop.skills-manager | 本地安装经 hermes skills CLI 子进程 | 解析器已在树内（skills.ts:36-60），安装直落盘 external_dirs，目录源指 Facai registry |
| desktop.memory-workbench | 六家外部记忆 provider 外链面板 | 仅为展示非强制依赖：换 Facai 记忆后端或删面板 |
| desktop.messaging-gateway-catalog | 各平台 Bot API（功能本体）；7 家国内平台无适配器；补丁删除了 risk:"high" 审批门 | 对接 Facai 已有 Telegram/WhatsApp/微信实现；国内平台自建适配器；按 patch 1127-1173 逆向恢复 risk 门 |
| desktop.ssh-remote-control | 远程主机为功能本体 | 保留 LOCAL-MAX 补丁语义：本地权限不随连接模式下降 |
| desktop.provider-mirror | 推理端点（补丁后已指 localhost:9900/v1） | 改指 Facai gateway 端点并 env 化 |
| desktop.registry-marketplace | 上游 GitHub 三处（REGISTRY_REPO/RAW_BASE/api.github.com，补丁只断图标 CDN） | REGISTRY_REPO（registry.ts:34）单点换 Facai 自有 repo 或改本地目录源；不换源=给上游导流 |
| desktop.wallet-base | Base 主网 RPC（wallet-balances.ts:17） | env 可配或自托管节点；涉资金托管须专项安全审计 |
| desktop.office3d-mission | claw3d.ts:20 dev-server 会 clone 外部 hermes-office repo | 吸收内嵌 office3d、弃 claw3d 外部启动路径即零远程 |
| desktop.dashboard-relay | 中继设计目的即代理远程 dashboard ws | 本地模式直连 127.0.0.1 免中继；中继仅保留为远程通道 |
| desktop.run-stream-transport | run 流传输本体是 dashboard HTTP/SSE API | 四端点 + 七项协商平移为 Facai gateway 自持 API |

### goose（12 条）

| 能力 | 残留远端/触达面 | 收口句柄 |
|---|---|---|
| agent.inference_loop | 模型出口 | GOOSE_PROVIDER=ollama + OLLAMA_HOST=127.0.0.1:11434，或 declarative provider 指 FACAI 本地 GLM 网关 |
| agent.context_compaction | 压缩时一次摘要模型调用 | 摘要模型随 provider 改根本地 GLM（GOOSE_ADDITIONAL_CONFIG_FILES 注入策略） |
| permission.mode_gate | SmartApprove 的 LLM 只读裁决 | permission.yaml 落 FACAI 管控目录（GOOSE_PATH_ROOT 收敛）+ 裁决模型改本地 GLM |
| security.tool_inspection_chain | OSV 查询、LLM 分类端点、egress 只 LOG | OSV_ENDPOINT 指 FACAI 本地镜像；SECURITY_*_CLASSIFIER_ENDPOINT 指本地 GLM；egress 升级 RequireApproval/Deny 交 Broker |
| mcp.extension_manager | OSV 恶意预检；StreamableHttp 远程 MCP | stdio 启动前 Facai Broker 统一签发/审计；OSV 指本地镜像；远程 MCP 仅显式配置时触达 |
| skills.runtime_loading | builtin goose-doc-guide 文档根 | GOOSE_DOCS_ROOT 配置键/补丁占位符指 FACAI 自托管 |
| agent.lead_worker | 子代理模型出口 | 同 inference_loop 改根本地 GLM |
| voice.local_dictation | 首次 GGUF 模型下载 | GOOSE_PATH_ROOT 预置模型文件离线化（落盘即数据根 models/） |
| roaming.p2p_endpoint | 默认四条 mesh-llm relay | GOOSE_ROAM_RELAYS 指自建 relay（配 token 时拒绝静默回退）或 RelaySettings::Disabled 仅 LAN |
| acp.local_server | 提示级 top_up_url | 补丁（acp/server.rs:1686）改指 FACAI 计费 |
| provider.declarative_engine | 内置 30+ 厂商默认端点（备选 backend） | 引擎本身即改根机制（engine=ollama/base_url=127.0.0.1:11434）；厂商端点不入 FACAI 授权链 |
| model.catalog_refresh | models.dev 在线刷新 | 关 feature online-model-meta 或 CATALOG_URL 指 FACAI 镜像；构建期内嵌快照本就离线 |

### opencode（5 条）

| 能力 | 残留远端/触达面 | 收口句柄 |
|---|---|---|
| opencode.lsp_facade | 远端 LSP 二进制下载 | 预置二进制到 Global.Path.bin 命中本地短路 + flags.disableLspDownload 兜底 |
| opencode.provider_baseurl_config | 内置 provider 目录/模型端点 | baseURL+apiKey 指本地 GLM/Ollama + OPENCODE_MODELS_PATH 本地目录 |
| opencode.session_compaction | 摘要模型调用 | 摘要用已配置 provider，指本地 GLM |
| opencode.plugin_hooks | 第三方插件 npm 安装 | .npmrc 内网镜像/预热 cache 离线复用 |
| opencode.session_share | opncd.ai 云分享（源码 diff 外发面，高危） | 默认 OPENCODE_DISABLE_SHARE=1 + share:"disabled"；确需时 enterprise.url 指自建 share 服务再升 L2 |

### hermes-agent（8 条）

| 能力 | 残留远端/触达面 | 收口句柄 |
|---|---|---|
| context.compress_pipeline | 摘要走 auxiliary 辅助模型（OpenAI 兼容 HTTP） | auxiliary base_url/api_key 指本地 GLM/Ollama |
| context.window_metadata | models.dev 远端目录 | config models_dev.url 指内网静态 JSON 镜像；磁盘缓存已支持全离线冷启动 |
| security.approval_triage | smart 档 guardian LLM | guardian 指本地 GLM；manual 档 builtin 人工批准（transport_fallback=deny 已 fail-closed） |
| skills.hub | 多源注册表索引 URL / git 安装 | skills.external_dirs 本地只读挂载；索引 BASE_URL 改内部索引；git 改内部镜像 |
| tools.managed_gateway | 能力本体在 Nous 远程（entitlement 裁决） | env TOOL_GATEWAY_DOMAIN/SCHEME/<VENDOR>_GATEWAY_URL 指本地网关，或弃用改 SEARXNG_URL 本地实例 / firecrawl 自托管 |
| stt.local_first | 云 ASR 后端序列（groq/openai/…） | config stt.provider 显式设 "local"（faster-whisper base+VAD 已配好），音频不出机 |
| auth.credential_pool | 凭据来源（auth.json/env/secrets.command） | 改 FACAI Owner 授权核注入后整链本地；状态机本身纯本地 |
| aux.router | auxiliary OpenAI 兼容出口 | auxiliary.base_url/api_key 指本地 GLM/Ollama |

### autoclaw-zcode（6 条）

| 能力 | 残留远端/触达面 | 收口句柄 |
|---|---|---|
| autoclaw.model.provider-registry | 上游 zai/deepseek 等远端 baseUrl；token 5 级回退含内置会员 token 兜底 | baseUrl 单点补丁重指（:406832 已实测 /ds-anthropic 先例，:407274 同法）；token 供给改 broker 注入、删 :41 内置兜底 |
| autoclaw.channel.baileys-whatsapp | WhatsApp 服务器侧会话签发/吊销（功能本体） | 诚实边界：不可本地替代；凭据 broker 托管+0700、waWebSocketUrl env 可换适配器 |
| autoclaw.agent.pi-coding-agent | 外部 npm 引擎本体 | 无法去远程化；收口点是执行授权（审批规则表裁决）；替换路径=Facai execution.py 自有执行面 |
| autoclaw.update.electron-updater | 智谱 OSS 双通道 feed（main.cjs:449333-449336） | feed 改自托管目录或 file://，签名根换本地公钥，放行权归 Owner |
| facai-bridge.relay-protocol | FACAI_BASE_URL 目标根 + winreg 回退取 secret | 目标根指本地 Owner 内核；RELAY_SECRET 改 broker 下发 scoped credential；HMAC/去重/回执语义原样保留 |
| facai-proxy.pivot-9900 | 上游白名单（config.js:6）+ HKCU\Environment key 池 + 内置共享 token | 上游收口本地 GLM/Ollama；key 池迁 secretbox.py 注入；删 patch_zaitoken.py:41 内置 token |

## 各源详情

### 1. facai-desktop — hermes-desktop v0.7.7 fork（MIT，19 条）

方法：只读走查 src/main + src/shared + src/renderer（约 400 个 TS/TSX 文件），对照 facai-changes.patch（1174 行/17 文件）与《Facai独立产品化交付.md》。桌面壳不含 agent 内核，执行本体是外部 hermes Python CLI 子进程，故多数能力为 L1 包装；补丁已完成 LOCAL-MAX 本地权限总闸与上游脱钩（L3），但同一补丁删除了 Gateway 高危工具审批门——收录时必须恢复。

| 能力 id | 等级 | verdict | native | 能力 | 关键锚点 |
|---|---|---|---|---|---|
| desktop.localmax-switch | L3_FORKED | new | ✅ | 本地权限总闸与上游脱钩配方 | src/main/hermes.ts:175 |
| desktop.kanban-workbench | L1_WRAPPED | new | ✅ | SQLite 看板任务编排 | src/main/kanban.ts:20-77 |
| desktop.skills-manager | L1_WRAPPED | new | ✅ | 技能工作台（SKILL.md 解析/搜索/装卸） | src/main/skills.ts:36 |
| desktop.memory-workbench | L2_ADAPTED | new | ✅ | 记忆工作台（MEMORY.md/USER.md 协议） | src/main/memory.ts:14 |
| desktop.messaging-gateway-catalog | L2_ADAPTED | better | ✅ | 消息网关目录（20 平台含 7 家国内） | src/shared/messaging-platforms.ts:639-883 |
| desktop.chat-approval-flow | L2_ADAPTED | better | ✅ | 聊天内联审批流（四态+request_id） | src/shared/chat-approval.ts:1 |
| desktop.secrets-provider | L2_ADAPTED | better | ✅ | 可插拔密钥 provider 框架 | src/main/secrets/provider.ts:16-36 |
| desktop.ssh-remote-control | L2_ADAPTED | new | — | SSH 全功能远程管控 | src/main/ssh-tunnel.ts:17-23 |
| desktop.terminal-launch | L2_ADAPTED | new | — | 跨平台终端启动器 | src/main/terminal-launcher.ts:31-38 |
| desktop.cron-schedules | L1_WRAPPED | duplicate | — | 定时任务桌面管理 | src/main/cronjobs.ts:35 |
| desktop.provider-mirror | L2_ADAPTED | better | ✅ | 桌面↔config.yaml provider 双向镜像 | src/main/agent-config-providers.ts:5-16 |
| desktop.registry-marketplace | L1_WRAPPED | new | ✅ | Discover 市场（四类目录） | src/main/registry.ts:34 |
| desktop.soul-identity | L2_ADAPTED | better | ✅ | SOUL 身份文件协议 | src/main/soul.ts:4 |
| desktop.wallet-base | L2_ADAPTED | new | ✅ | Base 主网钱包（DPAPI 加密） | src/main/wallet-store.ts:55-59 |
| desktop.office3d-mission | L2_ADAPTED | new | — | three.js 3D 虚拟办公室 + mission bus | office3d/interactions/missionBus.ts:1-31 |
| desktop.dashboard-relay | L2_ADAPTED | better | ✅ | 本地回环 WebSocket 中继 | src/main/dashboard-websocket-relay.ts:14-16 |
| desktop.run-stream-transport | L1_WRAPPED | better | ✅ | Agent 运行流传输协议（四端点+七项协商） | src/main/run-stream.ts:34-52 |
| desktop.i18n-12lang | L0_REFERENCE | new | ✅ | 12 语言静态词条体系 | src/shared/i18n/index.ts:618 |
| desktop.web-preview-inspector | L2_ADAPTED | new | — | 内嵌 Web 预览元素检查器 | src/main/web-preview-inspector.ts:11-12 |

### 2. goose（Apache-2.0，15 条，全部 L3_FORKED）

方法：在 authority-manifests/goose.json（26 面）基础上补能力视角，全量扫描 15 个 crate，逐条 grep/sed 定位证据；verdict 对照 facai-adapter-core manifest 与 facai/ 实模块。MERGE_FIXES.md 证实为在维护的 fork。

| 能力 id | 等级 | verdict | native | 能力 | 关键锚点 |
|---|---|---|---|---|---|
| agent.inference_loop | L3_FORKED | new | ✅ | 进程内 planner-executor 推理循环 | crates/goose/src/agents/agent.rs:2079 |
| agent.context_compaction | L3_FORKED | new | ✅ | 对话超阈值自动压缩（HistoryReplaced） | crates/goose-context-management/src/lib.rs:32 |
| permission.mode_gate | L3_FORKED | better | ✅ | 四模式工具审批门 + permission.yaml + 持久化 | goose-provider-types/src/goose_mode.rs:22 |
| security.tool_inspection_chain | L3_FORKED | new | ✅ | 可插拔 ToolInspector 检查器链（四实现） | crates/goose/src/tool_inspection.rs:34 |
| mcp.extension_manager | L3_FORKED | new | ✅ | MCP 扩展全生命周期管理（五类） | crates/goose/src/agents/extension.rs:156 |
| skills.runtime_loading | L3_FORKED | better | ✅ | SKILL.md 技能运行时（load_skill+参数化） | crates/goose/src/skills/client.rs:59-67 |
| recipe.workflow | L3_FORKED | better | ✅ | Recipe 可持久化工作流工件（schema 校验） | crates/goose/src/recipe/mod.rs:43-84 |
| agent.lead_worker | L3_FORKED | new | ✅ | lead-worker 多会话委派（授权规则） | platform_extensions/orchestrator.rs:383 |
| scheduler.cron_jobs | L3_FORKED | better | ✅ | cron 定时调度（5/6 段兼容） | crates/goose/src/scheduler/full.rs:100 |
| voice.local_dictation | L3_FORKED | new | ✅ | 本地 whisper 语音听写（4 档 GGUF） | crates/goose/src/dictation/whisper.rs:64-92 |
| roaming.p2p_endpoint | L3_FORKED | new | — | P2P roaming 远程接管（iroh+peerbook） | crates/goose-cli/src/commands/roam.rs:51-53 |
| acp.local_server | L3_FORKED | new | — | ACP 协议本地服务 | crates/goose/src/acp/server.rs:2677 |
| hooks.lifecycle_events | L3_FORKED | new | ✅ | 12 事件插件钩子生命周期 + HookContext | crates/goose/src/hooks/mod.rs:55-68 |
| provider.declarative_engine | L3_FORKED | better | ✅ | 声明式 provider 引擎（30+ 厂商） | goose-providers/src/declarative.rs:148-163 |
| model.catalog_refresh | L3_FORKED | duplicate | — | 模型目录在线刷新（models.dev） | crates/goose/src/model_catalog.rs:5 |

### 3. sst/opencode — v1.18.34（MIT，14 条）

方法：只读走读 packages/ 下 14 个关键包，逐行实测取证；先验基础为 authority-manifests/opencode.json（17 面）与 facai-cloud/docs/CAPABILITY_REGISTRY.json（3 条 L0），本路只补能力视角。

| 能力 id | 等级 | verdict | native | 能力 | 关键锚点 |
|---|---|---|---|---|---|
| opencode.codemode_interpreter | L2_ADAPTED | new | ✅ | 受限 JS 子集解释器（三重资源预算） | packages/codemode/src/codemode.ts:10-17 |
| opencode.permission_rules_engine | L2_ADAPTED | better | ✅ | Permission.ask 收口 + wildcard 规则引擎 | packages/opencode/src/permission/index.ts:28 |
| opencode.lsp_facade | L2_ADAPTED | better | ✅ | LSP 客户端门面 14 方法 + 30+ 语言装配 | packages/opencode/src/lsp/lsp.ts:121-137 |
| opencode.provider_baseurl_config | L2_ADAPTED | better | — | provider 全配置化加载（baseURL/黑白名单/分层） | packages/opencode/src/provider/provider.ts:1808-1833 |
| opencode.protocol_api_contract | L2_ADAPTED | new | ✅ | 单源 HTTP API 契约（OpenAPI+生成 SDK） | packages/protocol/src/api.ts:28-60 |
| opencode.session_compaction | L2_ADAPTED | new | ✅ | 会话上下文自动压缩（剪枝/截断/摘要） | packages/opencode/src/session/compaction.ts:28-33 |
| opencode.snapshot_revert | L2_ADAPTED | new | ✅ | git blob 文件快照与精确回滚 | packages/opencode/src/snapshot/index.ts:36-47 |
| opencode.plugin_hooks | L2_ADAPTED | new | ✅ | 插件全生命周期钩子系统（15+ 挂点） | packages/plugin/src/index.ts:234-300 |
| opencode.task_subagent | L2_ADAPTED | new | ✅ | 子代理编排（背景任务/task_id/深度限制） | packages/opencode/src/tool/task.ts:24-51 |
| opencode.session_share | L1_WRAPPED | new | — | 会话云分享（opncd.ai 外发面） | packages/opencode/src/share/session.ts:27-35 |
| opencode.web_session_ui | L2_ADAPTED | new | ✅ | React Web 会话界面（直连本地 SSE） | packages/app/package.json:2 |
| opencode.acp_adapter | L2_ADAPTED | new | — | Agent Client Protocol 适配器 | packages/opencode/src/acp/agent.ts:1-33 |
| opencode.pty_ticket | L2_ADAPTED | new | ✅ | 持久伪终端 + 一次性 ticket 消费制 | packages/core/src/pty/ticket.ts:22-25 |
| opencode.tool_framework | L2_ADAPTED | new | ✅ | Effect 工具框架（ctx.ask/截断落盘） | packages/opencode/src/tool/tool.ts:24-35 |

### 4. hermes-agent（MIT，16 条）

方法：只读扫描 D:/hermes-agent（8048 个 .py），先读 hermes-local.json 20 面先验与两份 GBK 交接文档（Facai 现状：Skill 安全面 35%、调度并行 45%、渠道语音未实现、无上下文压缩/STT/审批三档），再对 7 个重点能力族逐文件取证。

| 能力 id | 等级 | verdict | native | 能力 | 关键锚点 |
|---|---|---|---|---|---|
| context.compress_pipeline | L3_FORKED | new | ✅ | 分层上下文压缩管线（commit fence/快照 worker/冷却阶梯） | agent/compression_facade.py:218-327 |
| context.window_metadata | L3_FORKED | new | ✅ | 模型上下文窗口元数据（五级缓存+pin） | agent/models_dev.py:438-493 |
| security.approval_triage | L3_FORKED | new | ✅ | 三档危险命令审批 + guardian LLM + 楼层预闸 | hermes_cli/approval_mode.py:16 |
| security.instruction_guard | L3_FORKED | new | ✅ | 指令文件完整性双闸（写保护+加载期扫描） | tools/file_tools_write_guards.py:189 |
| identity.soul_layer | L3_FORKED | new | ✅ | SOUL 身份层（每消息热重载+模板安全升级） | hermes_cli/default_soul.py:9 |
| skills.hub | L3_FORKED | better | ✅ | 技能中心（多源注册表+SSRF 安全客户端+审计） | tools/skills_hub.py:1-12 |
| skills.ast_audit | L3_FORKED | new | ✅ | 技能 Python 源 AST 级深度审计 | tools/skills_ast_audit.py:1-4 |
| tools.managed_gateway | L1_WRAPPED | dependency | — | Nous 托管工具网关客户端 | tools/managed_tool_gateway.py:17 |
| stt.local_first | L3_FORKED | new | ✅ | 语音转写本地优先 + 租约预热 | tools/transcription_tools.py:249 |
| state.session_db | L3_FORKED | better | ✅ | SQLite 会话状态层（CJK-bigram FTS5） | hermes_state.py:1-6 |
| state.checkpoints | L3_FORKED | new | ✅ | 工作目录透明检查点（shadow git store） | tools/checkpoint_manager.py:1-9 |
| exec.subagent_worktree | L3_FORKED | better | ✅ | 子代理 git worktree 隔离（仅凭证据回收） | tools/subagent_worktree.py:1-4 |
| auth.credential_pool | L3_FORKED | better | ✅ | 多凭据池（按模型冷却/按状态定 TTL） | agent/credential_pool.py:210 |
| aux.router | L3_FORKED | new | ✅ | 辅助模型路由器（旁路解耦+中断保护） | agent/auxiliary_client.py:1 |
| memory.curated | L3_FORKED | new | ✅ | 策展记忆文件（冻结快照/前缀缓存零失效） | tools/memory_tool.py:1-5 |
| channel.gateway | L4_FACAI_NATIVE | duplicate | — | 多平台消息网关（Facai 已自成管线） | gateway/__init__.py:1-3 |

### 5. autoclaw-zcode — AutoClaw2 闭源产品树 + ZCode 可观测格式面（许可 UNKNOWN，14 条）

方法：闭源树只读定点 grep（31MB 打包产物 main.cjs）+ 四份 .bak 备份 cmp/diff 确认活补丁 + node_modules manifest + AppData 运行时面（9900 反代）+ facai-handoff 自研件（bridge.py）全文 + Facai 现状对照；ZCode 无源码树（T3），仅登记 SKILL.md 与动态工作流两个可观测格式，未虚构更多。

| 能力 id | 等级 | verdict | native | 能力 | 关键锚点 |
|---|---|---|---|---|---|
| autoclaw.im.inbound-receipts | L1_WRAPPED | duplicate | — | IM 入站回执幂等账本表 | main.cjs:15245-15261 |
| autoclaw.im.delivery-outbox | L1_WRAPPED | better | ✅ | 租约式 IM 投递工作队列 | main.cjs:17905-17930 |
| autoclaw.governance.session-approval-rules | L1_WRAPPED | better | ✅ | 工具会话审批规则表（side_effect 三级） | main.cjs:17836-17850 |
| autoclaw.governance.guest-allowances | L1_WRAPPED | new | ✅ | IM 访客准入三件套（bcrypt 配对码） | main.cjs:18359-18363 |
| autoclaw.secure.safestorage-vault | L1_WRAPPED | duplicate | — | DPAPI 加密凭据库（引用式 secretKey） | main.cjs:539253-539254 |
| autoclaw.model.provider-registry | L1_WRAPPED | dependency | — | 多 provider 模型连接注册表 | main.cjs:407274 |
| autoclaw.channel.baileys-whatsapp | L1_WRAPPED | duplicate | — | 内嵌 Baileys WhatsApp 多设备协议栈 | main.cjs:680906 |
| autoclaw.agent.pi-coding-agent | L1_WRAPPED | dependency | — | pi-coding-agent 0.83.0 代码代理引擎 | node_modules/@earendil-works/pi-coding-agent/package.json:2-4 |
| autoclaw.update.electron-updater | L1_WRAPPED | dependency | — | electron-updater 双通道 staged 自动更新 | main.cjs:449333-449336 |
| autoclaw.patch.observable-binary | L1_WRAPPED | new | ✅ | 打包产物明文补丁机制（tagged 备份+cmp 校验） | dist/main.cjs 四份 .bak，活补丁差异 @ main.cjs:406832 |
| facai-bridge.relay-protocol | L4_FACAI_NATIVE | duplicate | — | AutoClaw↔Facai HMAC 签名中继协议客户端 | bridge.py:106-116 |
| facai-proxy.pivot-9900 | L4_FACAI_NATIVE | better | ✅ | 本地多上游模型反代 pivot | proxy/server.py:25 |
| zcode.format.skill-md | L1_WRAPPED | duplicate | — | ZCode 技能包 SKILL.md 格式 | C:\Users\user\.agents\skills\autoglm-browser-agent\SKILL.md:1-12 |
| zcode.format.dynamic-workflow | L1_WRAPPED | new | ✅ | ZCode 动态工作流脚本格式 | 运行时契约（.zcode/workflows 实测为空目录，无文件证据） |
