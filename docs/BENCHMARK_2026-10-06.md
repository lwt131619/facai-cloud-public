# 对标总表 — FACAI vs 六对标 + 吸收清单 + 实网收录扩测（2026-10-06）

- **材料来源**（本文不新增外部检索，全部转述当日已产出情报）：
  ① 桌面对标研究 `reports/对标研究_2026-10-06_FACAI vs 六对标.md`（仓库外工作区产物，WebSearch+WebFetch 12 个权威页面，含逐条来源链接）；
  ② 实网收录简报 `docs/INGEST_AIDER_RUN.md`、`docs/INGEST_CREWAI_RUN.md`（同日实跑）。
- **基线差异（重要）**：早间对标研究写于 HEAD `9d0e6ad`；本文落盘时 HEAD 已至 `e0c7a1a`（本波次 `git log -1` 实证）——其间 **billing 波次落地**：balance-pool 语义（0 余额/无预算行账号 dispatch 被拒，`facai/billing.py:104-118`"余额为 0，请先充值" 402，非 bypass 账号不再无限跑）与渠道侧预算解释回复（`facai/api.py:1010-1027` `channel_budget_deferred` 向发送者回"充值到账或提高月度额度后…即可继续"）。早间报告"预算门默认无限、audit :74/:207 未修"两条**在 e0c7a1a 已过时**，本文按新基线修订。
- **四支柱**：① Agent 权限/审批（Owner Gate）；② 渠道宿主（TG/微信/WhatsApp/LINE 客户自有持久绑定）；③ 计费/配额（$95/$218 分列+预算门）；④ 插件生态（Capability Foundry）。
- 本文 FACAI 侧行号均为本波次实读（`authority_gate.py`、`billing.py`、`api.py`、`capabilities.py`、`registries.py`、`channels/*`、`HANDOFF-NEXT.md`、`CHANNELS.md`、`AUDIT_2026-10-04_10AGENT.md`）。

---

## 一、对标总表（5 peer × 四支柱矩阵）

5 个平台级 peer：**Dify / OpenHands / LangGraph / n8n / Coze Studio**；CrewAI 因同日完成实网收录（§三），以"已收录 peer"行并入矩阵。

| 项目 | ① 权限/审批 vs Owner Gate | ② 渠道宿主 | ③ 计费/配额 | ④ 插件生态 | 许可（发行约束） |
|---|---|---|---|---|---|
| **Dify**（~158k★） | 未见动作级审批门（未发现，非确证不存在） | 无原生 TG/微信/WA/LINE；whatsapp-bot 插件+社区 TG 插件+LangBot 中转 | $590 / $1,590 per workspace/年 + credits；用尽可切自己 key（BYOK 亮点） | Marketplace 插件制（v1.0 2025-02 起，数百级，官方未公布总数）；**插件独立进程+manifest** | **Dify OSL**：Apache2.0+1a 禁自建多租户 SaaS、1b 保 console LOGO |
| **OpenHands**（~90k★） | **最接近对标物**：confirmation mode 三策略（Always Ask 默认/Never/仅风险）+ SecurityAnalyzer 风险评分；但无授权根、无 Grant/TTL/次数语义 | Slack/GitHub/Linear/Notion（开发者协作，非客户渠道） | Cloud $20 免费 credits 后 **at-cost 零加价**——唯一与 FACAI 同哲学 | ACP 接第三方 agent | MIT |
| **LangGraph**（~42.7k★） | `interrupt()`→**checkpointer 持久化**→`Command(resume)`；是原语非平台策略——其持久化恰是 FACAI 短板（`authority_gate.py:14` grant 仅进程内存） | 无 | LangSmith $0/$39 per seat/月 + traces；Platform 部署需 Plus | 框架层，无插件市场 | MIT |
| **n8n** | Wait node + webhook/chat 恢复；HITL **不能放 agent 工具链内部**（官方社区 2025-02 确认） | **节点最广**：TG/WA/Slack/Discord/LINE（工作流节点，非租户持久绑定） | ~$24/$60 per 月（2.5k/10k executions；检索汇总非官方页直读） | **13,403 社区节点包**（npm registry API `total` 实查） | **Sustainable Use License**（fair-code）：禁把 builder 作为付费产品卖给客户 |
| **Coze Studio**（21.7k★，2025-07 开源） | 未见动作级审批门（未发现） | **OSS 仅 API/Chat SDK**；TG/Discord/微信等一键发布=商业版专属；OSS 单账号无多租户 | OSS 无；商业版积分体系 | 商业版插件商店成熟；OSS 插件需配第三方鉴权键 | Apache 2.0 |
| **CrewAI**（~59.4k★，已收录 peer，见 §三） | `human_input=True`=任务产出后 CLI 反馈（**后审**）；前置工具审批不原生（BeforeToolCallHook 开放 feature request）。源码佐证：agent 协作 `agent_tools.py` delegate/ask 与 `BaseTool._run:388,556` 均进程内直调，初扫+高配额控制实验均 0 审批面（未发现口径） | 无 | CrewAI AMP 企业定制 $60-120k/年（第三方汇总，官方只说 custom） | 自定义工具+MCP，规模未量化 | MIT（收录自探与上游一致） |
| **FACAI 基线**（e0c7a1a） | **唯一授权根**：MISSION_AUTONOMOUS 非 default-open、OWNER_GATE 动作级、fail-closed（`authority_gate.py:1-17`）；短板=Grant 仅进程内存不持久、无风险分级、无审批交互层 | **唯一客户自有渠道 7×24 持久绑定**（四通道适配器+CONFIGURED/UNCONFIGURED/UNIMPLEMENTED 诚实状态机，`CHANNELS.md:16`）；短板=媒体/群聊/Discord/Slack 缺、无自助向导 | **唯一成本实报分列**（$95/$218，`HANDOFF-NEXT.md:279,282`；`/api/v1/pricing` $400/M 输出，`HANDOFF-NEXT.md:47`）+ 预算门（e0c7a1a 起 balance-pool）；短板=无 80% 软阈值、无 per-channel 分列、无 seat 维度 | **生态规模 0**；但 Foundry 独有吸收合规：L0..L4 分级+source/license 必录（`capabilities.py:9-11`） | 自有代码+依赖 license 字段强制登记，**结构性免疫** Dify 1a/n8n SUL 类陷阱 |

**真优势（六对标无一同时具备）**：① Owner 授权根 + fail-closed Grant（OpenHands 是"每次问一下"，Owner Gate 是"授权是签发出来的稀缺资源"）；② 客户自有渠道持久绑定（Coze/Dify OSS 渠道为 0）；③ 成本实报分列 + 预算门（唯 OpenHands 同哲学且其无租户账单闭环）。

**最大劣势（按 e0c7a1a 修订）**：① 生态规模 0 vs n8n 13,403；② **Grant 不持久 + 无审批交互层**——渠道宿主与 Owner Gate 两根最该相乘的支柱没相乘（`authority_gate.py:14`）；③ ~~预算门默认无限~~ → **已由 balance-pool 关闭**（残余：无 80% 软阈值 Owner 告警、无 per-channel 成本分列、无 seat 双轨）。

---

## 二、差距与吸收清单

每条：peer | 机制 | 适配到 FACAI 哪个模块 | 优先级。P0=独占位/堵漏，P1=高性价比，P2=战略储备。

| # | peer | 机制 | 适配到 FACAI 哪个模块 | 优先级 |
|---|---|---|---|---|
| 1 | LangGraph | `interrupt()` + checkpointer **持久化中断**：审批状态重启可恢复 | 待 Owner 审批的 Grant 落 SQLite（store 已有 SCHEMA 基建），状态机 pending→approved/denied/expired + TTL（env 模式对齐 `authority_gate.py:44-46` `FACAI_MISSION_GRANT_TTL_SECONDS`），超时自动 DENY（fail-closed） | **P0** |
| 2 | OpenHands | **SecurityAnalyzer 风险评分**：按动作风险决定是否打扰人 | `facai/authority.py` ActionIR 加 `risk_level` + 规则表（backend/action/resource 三元组→low/medium/high），判定集中在 `authorize_for()`（`authority_gate.py:105-110`）：low 自动过、high 强制 OWNER_GATE；先覆盖 SHELL_EXEC/CREDENTIAL/REMOTE_PROVIDER 三类 | **P0** |
| 3 | 自有（Gate×渠道乘积，resume 语义借 LangGraph） | **渠道内审批交互**：审批请求推 Owner 渠道，回复即 resume | `facai/channels/telegram.py`/`wechat.py` 收"批准/拒绝"→ 恢复 #1 的持久化 Grant。**六对标无人有此位**（OpenHands 仅 UI 确认、n8n Wait 不能进工具链、LangGraph 仅原语） | **P0** |
| 4 | n8n | Wait node 超时/软阈值语义 | `facai/billing.py` `budget_gate`（月累计查询点已在，`billing.py:111-112`）加 80% 阈值经 `notify.py` 推 Owner 渠道告警（100% 拒绝与解释回复已由 e0c7a1a 落地） | P1 |
| 5 | Dify | **插件 manifest×进程隔离**：声明权限→独立进程执行 | 插件声明所需 ActionIR 类别 → Owner 签发受限 grant → 进程隔离执行；挂点 = review_status 三态（`registries.py:11` pending/approved/rejected）扩为签发闸，让 Foundry 与 Gate 合流——对标插件天然可信，我们的插件必须过 Gate | P1 |
| 6 | n8n | 节点凭证 **test 预检** | `facai/channels/base.py` ChannelProvider 加 `test_credential()`（TG getMe / WA me 等只读 API），接入绑定 API（`/api/v1/my/channels*`，`notify.py` 为现成挂点）当场验证 token | P1 |
| 7 | Coze 商业版 | **三步绑定向导**（选平台→引导建 bot/OAuth→验证绑定） | 绑定 API 已有；补 3 步向导页 + TG BotFather 深链；微信扫码已有 `/api/v1/channels/wechat/login` | P1 |
| 8 | CrewAI/aider（实网收录反馈） | 扫描器三修 | `facai/ops/ingest.py`：`_INFERRED_CRED_RULES` 补 `import litellm`/`litellm.completion` 面（两仓 LLM 凭据流均藏于 litellm）；`tests/` 目录降权（crewai 46 面 REMOTE_PROVIDER 里 37=测试夹具）；源码档优先级/配额策略（千文件级仓库截断回归） | P1 |
| 9 | n8n | 社区/认证/伙伴**三级分层发布** | `registries.py:11` 三态映射三级，认证级=人工审，写入 `capability_diff` 判定 | P2 |
| 10 | LangBot/Dify 生态 | **外部 relay 扩渠道**（一个 relay 服务接 QQ/飞书/Discord） | `facai/channels/relay.py` + `facai/n8n.py` HMAC 中转协议文档化为公开扩展点，借社区补 Discord/Slack 不自研 | P2 |
| 11 | Dify | credits→**BYOK 混合档** | 租户"自备 key 零抽成"档（模型 registries 机制已有），$95/$218 明确定位为托管 key 溢价 | P2 |
| 12 | LangSmith | **seat + consumption 双轨** | auth 层 5 用户/租户上限演化为 per-seat 计价（第 2-N 成员位加价） | P2 |

> 无动作项（对标印证现状正确）：Coze 插件第三方鉴权键 ≈ 我们 secretbox/分类 provider env（0600）同构，路线不变；Dify 1a/n8n SUL 对多租户 SaaS 的限制反证 FACAI 自有代码发行路线安全。

---

## 三、实网收录扩测两份简报要点

### 3.1 aider（`docs/INGEST_AIDER_RUN.md`）— 中等体量仓库，三修扫描器全面生效

- **实跑**：`python -m facai.ops.ingest https://github.com/paul-gauthier/aider --name aider` exit=0 **一次成功**（无需重试/镜像）；clone HEAD `5dc9490`，691 文件 / 180 源码文件 / 147 .py。
- **面数**：395 = 源码档 195（**未截断**）+ DOC_REFERENCE 180 + 其余 20；**REMOTE_PROVIDER 44 全部出自源码**（urls.py 15、onboarding/openrouter 密钥校验端点、analytics posthog），文档误报 **0/44**（swarm 修复前 46/46=100% 文档误报）。
- **INFERRED_CREDENTIAL=0 = 真阴性**：grep 全仓零 `OpenAI(/Anthropic(` 构造，LLM 全走 litellm 抽象（`aider/llm.py:37` 惰性导入）——真实客户端构造在 litellm 依赖内部，暴露**二阶传递盲区**；真实凭据以 21 面 env 流在案（OPENAI_API_KEY×4 `main.py:616` 等）。
- **信号质量**：LOCAL_BROKERABLE 41（CREDENTIAL 21 + SHELL_EXEC 20），`brokerable_paths` 从 swarm 时代的平凡 PASS 变**真实 PENDING**（41 面待收口）；能力候选 3/3 触发（shell.exec=`commands.py:974` subprocess.run；secret.request=help.py:138 TOKENIZERS 子串误报，如实记录；model.invoke=analytics.py:57 posthog）。
- **verdict**：`T2_PARTIAL`（5 PASS / 5 PENDING，未纳管 150 **全真实**）；license 自探 Apache-2.0；395/395 where 带 file:line 且文件全在（零伪造）；INDEX/Registry 不含 aider（防越界）；定向 `pytest tests/test_ingest.py -q` = 23 passed（**非全量 972 基线**，如实标注）。

### 3.2 crewAI（`docs/INGEST_CREWAI_RUN.md`）— 千文件级仓库暴露截断回归 + 编排盲区

- **实跑**：同型命令 exit=0 **16.8s**；clone HEAD `738c8e1`，31868 文件 / **1400 源码文件**；license 自探 MIT 与上游一致。
- **截断回归**：200 面即打满，只扫 **177/1400=12.6%**（字母序截断于 `a2ui/server_extension.py`）；200 面里 **lib/cli 占 173 面（86.5%）**，框架核心仅 20 面（10%）且 **0 个 tool/协作面**——`crew.py`/`task.py`/`tools/` 零覆盖。
- **控制实验**（框架核心 547 文件 `scan_source(limit=5000)` → 153 面）证实**双重盲区**：
  - 截断盲区（扫到即可见）：MCP stdio 传输 `mcp/transports/stdio.py:61 subprocess.Popen`（tool server 子进程拉起点）、CREDENTIAL 10 面（OpenAI/Anthropic/Azure/Google KEY）、INFERRED 5 面（`llms/providers/openai/completion.py:458` `OpenAI(**client_config)` 真构造点）——实跑全不可见；
  - 结构性盲区（扫了也 0 面）：agent 委托 `agent_tools.py` delegate_work/ask_question 与 `BaseTool._run` 均进程内方法调用，无 env/URL/subprocess 任一正则可命中。
- **误报**：REMOTE_PROVIDER 46 里 **37 面（80.4%）是 tests/ 认证夹具**；源码内文档 URL（`llm.py:341` docs.crewai.com）逃过降权。
- **verdict**：`T2_PARTIAL`（未纳管 121）；CHECK-1..8 全过；定向测试 23 passed（全量未跑——并行波次改树无法归因，如实说明）。

### 3.3 两仓对照与对标增量

- **同为 INFERRED_CREDENTIAL=0，语义相反**：aider=真阴性（litellm 抽象），crewai=截断假阴性——**读 0 必须先看截断标志**。
- **对矩阵的源码级佐证**：crewAI 编排核心（协作/工具执行）在初扫+高配额扫描下均无审批/授权类面（未发现口径，非存在性确证），与桌面研究"前置审批不原生"互证；同时提示**能力吸收不能只靠正则初扫**——进程内 tool 调用是扫描器结构性盲区，裁决需人工+控制实验。
- **扫描器进化判据**：三修在 ≤几百源码文件的仓库成立（aider）；千文件级需源码档优先级/配额策略（crewai）——即 §二 #8。

---

## 四、TASKQUEUE 种子建议

- [ ] **审批持久化（P0）**：待 Owner 审批的 Grant 落 SQLite（复用 store 既有 SCHEMA），状态机 pending→approved/denied/expired + TTL（env 模式对齐 `facai/authority_gate.py:44-46` `FACAI_MISSION_GRANT_TTL_SECONDS`），超时自动 DENY 保持 fail-closed；验收=进程重启后待审批状态可恢复且过期 replay 给明确拒绝理由（修 `authority_gate.py:14` "grants live in process memory only" 短板），补 pytest 覆盖 approve/deny/expire/重启四路径。
- [ ] **渠道内审批交互（P0，依赖上条）**：审批请求经 `facai/channels/telegram.py` 推给 Owner 绑定渠道，Owner 回复固定"批准/拒绝"命令（TG callback 或 `/approve <grant_id>`）即 resume 对应 Grant 并写审计事件；先做 TG 单通道端到端（Gate×渠道乘积特性六对标均无：LangGraph 仅原语、OpenHands 仅 UI 确认、n8n Wait 不能进 agent 工具链）。
- [ ] **ActionIR 风险分级（P0）**：`facai/authority.py` ActionIR 加 `risk_level` 字段 + 规则表（backend/action/resource 三元组→low/medium/high，吸收 OpenHands SecurityAnalyzer 语义），判定集中在 `authorize_for()`（`facai/authority_gate.py:105-110`）：low 自动过、high 强制 OWNER_GATE；规则表首版覆盖 SHELL_EXEC/CREDENTIAL/REMOTE_PROVIDER 三类并带测试。
- [ ] **预算 80% 软阈值告警（P1）**：`facai/billing.py` `budget_gate`（`billing.py:111-112` 月累计查询点已备）加 0.8 限档触发——经 `facai/notify.py` 向 Owner 绑定渠道推"本月已消耗 X%/限额 Y"告警（每租户每日至多 1 次防刷屏）；100% 拒绝与用户解释已由 e0c7a1a（`billing.py:104-118` + `api.py:1010-1027`）落地，本条只补 Owner 侧预警。
- [ ] **凭证预检 test_credential（P1）**：`facai/channels/base.py` ChannelProvider 加 `test_credential()`（TG getMe、WhatsApp me、LINE 验证端点、微信状态接口各一），接入 `/api/v1/my/channels*` 绑定 API 当场验证 token 有效性，绑定即报 UNCONFIGURED 而非等第一条消息（吸收 n8n 节点 test 步骤），失败信息含平台原始错误码。

---

## 五、局限（如实声明）

- n8n/CrewAI 具体价格为**检索多源汇总**，未直读官方 pricing 页；Dify Marketplace 精确总数官方未公布（仅"数百级"）；"Dify/Coze OSS 未见动作级审批门""crewAI 未见审批/授权面"均为**未发现**结论，非穷尽确证；OpenHands 审批机制结论来自搜索摘要 + arXiv 2025-11 SDK 论文 + DataCamp 二手源（官方 confirmation-mode 页未逐字读到）。
- §三全部数字转述自当日两份收录简报，本波次未重跑 ingest；本文唯一新产出即本文件，未改代码、未 commit。
- 早间报告的 audit 行号引用（billing.py:30 等）为 `9d0e6ad` 时点；本文行号一律以 `e0c7a1a` 实读为准。
