# FACAI Authority Surface Inventory —— 全目标授权面清单

- **日期**：2026-10-04
- **依据（冻结基线）**：`docs/AUTHORITY_ARCHITECTURE.md`《FACAI Global Owner Authority Architecture》（操作员 2026-10-04 会话指令逐字落盘、未作改动；本文所有 §编号均指该文）。接线记录见 `HANDOFF-NEXT.md:248-263`（§十一F：P0-1..P0-16 先做稳，再批量接 Goose/Hermes/OpenCode/ZCode 适配器）。
- **来源 commit**：`48f2282a7f09dd41caa2865b793e17b954c39c63`（master）。取证方式：直接读 `facai-cloud/.git/refs/heads/master` 文件所得；本次全程只读，未运行任何 git 命令，工作树是否存在未提交改动未验证。
- **分类口径**：§6 五类 `LOCAL_BROKERABLE / LOCAL_DIRECT / CONFIGURABLE / REMOTE_PROVIDER / UNKNOWN`；管理等级 §20 T0–T3；`REMOTE_AUTHORITY_DEPENDENCY` 冻结规则 §18；拒绝归一 §14/§15；Secret 归属 §22。
- **检索方式**：7 路授权面检索（源码/实例走查，产出见 `vendor/authority-manifests/` 同名 JSON）+ 2 路直接登记（zcode / facai-native）。执笔在本会话对关键行做了独立复核（逐条列出，实测命令见文末"复核记录"）。
- **纪律**：每条 surface 均带真实 `file:line` 证据；宁可少报不编造。zcode 因无源码可走查，surfaces 留空（见其详情节）。

---

## 〇、操作员红线：不降权收录（全局声明）

按 §2（Capability ≠ Authorization）、§7（Capability = PRESERVED，Authority Root = FACAI OWNER）与 §27 十二条不变量，**本清单收录的每一个目标、每一个改根点，一律"能力完整保留、授权重新归属"**：

1. 不删除任何执行路径、工具、模型、provider、渠道或审批机制；改根只允许"新增签署/收口环节"与"替换供给方（远程→本地、远程A→远程B）"。
2. 任何集成目标（Agent/Skill/Model/API Adapter/Executor）不得在 Facai 内成为高于 Owner Mission Authority 的最终裁决者（§27-1、末段总定义）。
3. 远程 Provider 的拒绝只作为**后端状态**处理（§14：`THIS BACKEND DID NOT EXECUTE` ≠ `OWNER GRANT REVOKED`），由 P0-14 Router 换后端继续，绝不映射 `MISSION_FAILED`（§15）。
4. 凡把某远程 API 当作不可替换最高裁决器的依赖，冻结标记 `REMOTE_AUTHORITY_DEPENDENCY`，该目标只能标 `PARTIAL`，不得标 `FULLY_MANAGED`（§18）。

---

## 一、收录总表（9 目标）

| # | 目标 | 来源（origin） | 管理等级 | surfaces | 远程裁决点※ | 收录方式 |
|---|------|---------------|----------|---------|------------|----------|
| 1 | facai-adapter-core（FACAI 执行编排层） | LOCAL-INSTANCE：`facai-cloud/facai/` | **T0 NATIVE**（自有源码，可直改根） | 11 | 2 | 本地源码直纳；改根即改本仓 |
| 2 | FACAI 远程集成面（n8n/notify/backup/secretbox/ops/channels） | LOCAL-INSTANCE：`facai-cloud/facai/`（同仓远程面） | **T2 PARTIALLY MANAGED**（TronGrid/iLink/渠道为不可本地改根的裁决者） | 21 | 6 | 本地源码直纳；远程面走 Provider Adapter + 规范化 |
| 3 | goose（block/goose Rust monorepo） | github.com/block/goose（本地实例 `vendor/goose`，Cargo.toml:9 声明 `github.com/aaif-goose/goose`） | **T2 PARTIALLY MANAGED**（源码全量可控；默认配置下模型裁决在远端，另有硬编码远程端点） | 26 | 11 | 源码收录 + 配置级改根（Ollama/GLM）+ 少量补丁 |
| 4 | sst/opencode（TS monorepo） | github.com/sst/opencode（本地实例 `vendor/opencode`） | **T2 PARTIALLY MANAGED**（本地执行面可 Broker；模型/目录/更新/账户/装包默认远端） | 17 | 8 | 源码收录 + 配置级改根 + Facai Broker 收口 |
| 5 | hermes-local（Hermes Agent 本地实例） | LOCAL-INSTANCE（`D:/hermes-agent` 源码树 + `D:/hermes` 运行实例；上游 URL 待确认，见 §五） | **T2 PARTIALLY MANAGED**（代码/审批/SOUL 本地可控；模型端点/更新源/工具网关在远端） | 20 | 5 | 本地实例收录 + 配置级改根 |
| 6 | autoclaw（AutoClaw2 实例 + relay-bridge） | LOCAL-INSTANCE：`D:/goose/自动抓 GLM/AutoClaw2`（第三方打包 Electron 产物，main.cjs 已明文补丁）+ `facai-handoff/autoclaw-relay-bridge`（自研桥） | **T2 PARTIALLY MANAGED**（第三方产物不可全控；可补丁、可 DPAPI 解密、审批表本地） | 15 | 5 | 补丁级 + 适配器级收录（产物不可重建） |
| 7 | whatsapp-relay（Baileys 桥，Node） | LOCAL-INSTANCE：`facai-cloud/bridge/whatsapp-relay/` | **T1 FULLY MANAGED（候选）**——落地下述 Secret Broker 改根后达成；当前差一步（0640 明文 env + wa-auth 目录未 chmod） | 8 | 1 | 本地源码直纳；唯一远程面（WhatsApp 会话）显式声明为 Owner 已知依赖 |
| 8 | zcode | **EXTERNAL-BINARY**（本机闭源二进制安装器，无公开源码可获取） | **T3 EXTERNAL**（§20：T3 ≠ 能力弱，仅 Authority Coverage 较低） | 0（留空，见详情） | 0 | 适配器级收录（它本身已是可编排执行体） |
| 9 | facai-native | **T0 NATIVE**——FACAI 自身内核（authority 核即将落地） | **T0 FACAI NATIVE** | 0（authority 核未落地，见详情） | 0 | 原生；本清单的落点即 §四 P0 映射 |
| | **合计** | | | **118** | **38** | |

※ "远程裁决点" = surfaces 中 `remoteAuthorityDependency = true` 的条目数：这些依赖在本地改动后仍由远程服务器裁决自己的服务端规则（§17 后半），只能走 §14/§15/§18 的规范化 + Router 路径，不能假装本地可改。

---

## 二、各目标详情

> 表中"位置"为 `file:line`；"改根点"为落地动作摘要，完整版在各目标后续的 reRooting 列表与 `vendor/authority-manifests/<target>.json`（原样 TargetReport）。

### 2.1 facai-adapter-core（FACAI 执行编排层）——T0 NATIVE

**managementClass 理由**：模型端点、执行器与授权核全部在本仓库内可改，无第三方许可或二进制锁定，属 FACAI 自有源码（可直接改根），故 T0 NATIVE。编排层唯一网络出口是 `gateway.complete`，任务模型调用下放给 opencode/goose CLI 子进程（远端 provider 仍是最高裁决者，§17）；执行前确认**不存在任何 Owner 授权检查点**——即 P0-4 的插入位置。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK | `facai/gateway.py:192` | REMOTE_PROVIDER | 是 | 模型补全唯一本地网络出口（httpx POST 注册表 `model.endpoint`，上游 HTTP 状态经 `classify_response` 成最终裁决，gateway.py:73）。改根：models 注册表加本地 GLM 条目（endpoint `http://127.0.0.1:11434/v1`，api_key_env `GLM_LOCAL_KEY`），回环需 `FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`（secretbox.py:108-110）；gateway 与 api.py:983/1166/1334-1346 三调用点零改动。deepseek 条目保留 |
| NETWORK | `facai/opencode.py:174`（goose.py:47-50 同构） | REMOTE_PROVIDER | 是 | 任务实际跑在 CLI 子进程内，模型可用性/配额由远端与 CLI provider store 决定。改根：`--model` 指本地 GLM；execution.py:84-88 的 facai-user provider 块（baseURL）即现成父本 |
| CREDENTIAL | `facai/execution.py:103` | LOCAL_BROKERABLE | 否 | 用户模型明文密钥经子进程 env 注入（`{"FACAI_USER_MODEL_KEY": key}` → execution.py:281-284 → opencode.py:121-126 Popen）；且 `child_env = dict(os.environ)` 全量继承。改根：Broker 注入白名单变量 + 短时令牌；已知缺陷：goose.py:53 签名不收 env，配 user_model 的 goose 任务在 execution.py:283 抛 TypeError 被兜为 OPENCODE_FAILED |
| CREDENTIAL | `facai/scheduler.py:126` | LOCAL_BROKERABLE | 否 | 明文 api_key 拼进 model dict 交 gateway 直塞 HTTP 头；主钥来自 `FACAI_MASTER_KEY` 或 0600 `master.key`（secretbox.py:37-51,78-80）。改根：Broker 代持明文（调用方只传 handle） |
| SHELL_EXEC | `facai/opencode.py:124` | LOCAL_BROKERABLE | 否 | 任务执行主子进程（列表 argv、无 shell、cwd=mission workspace、默认全局串行 FACAI_OPENCODE_SERIAL，opencode.py:107/211-215；goose.py:64 复用同一 `_run`）。改根：P0-4 Owner Gate 的天然收口点——Broker executor（binary 白名单 + cwd 断言 + env 白名单 + 一次性令牌） |
| SHELL_EXEC | `facai/checkpoint.py:22` | LOCAL_BROKERABLE | 否 | git 子进程面（`["git", *args]` 列表、无 shell；commit/worktree/merge，checkpoint.py:124-125、execution.py:211-217）。改根：纳入 Broker 白名单执行器（子命令固定可枚举 + cwd 限定 root/workspaces）；`_WORKTREE_OPS_LOCK`（checkpoint.py:18）平移 |
| POLICY | `facai/execution.py:283` | LOCAL_DIRECT | 否 | **执行前无授权检查点**：API→adapter.execute 之间只有租户归属校验（require_mission api.py:481-485 仅按 owner 取 mission；scheduler.py:167 领取即执行）。改根：P0-4 插入 execution.py:276-284 + scheduler.py:167，语义复用 registries.py:98-107 |
| POLICY | `facai/opencode.py:157` | LOCAL_DIRECT | 否 | workspace 隔离仅为路径断言（parent==root/workspaces；execution.py:149-152 回传校验）；无 OS 沙箱，CLI 以服务进程同等权限运行。改根：路径断言保底 + OS 级限制（受限用户/只读挂载/Job Object） |
| POLICY | `facai/opencode.py:26` | LOCAL_BROKERABLE | 否 | redact 覆盖面仅 api_key/access_token/secret/password 四类键名 + Bearer；`FACAI_USER_MODEL_KEY=...`、`Authorization: Basic`、裸 `sk-` 不覆盖。改根：扩充模式集 + Broker 出口二次 redact（覆盖 _persist_output execution.py:106-119 与事件流） |
| CONFIGURABLE | `facai/registries.py:20` | CONFIGURABLE | 否 | models/runtimes/agents/skills 注册表是执行身份的本地开关（endpoint/api_key_env/opencode_model 同时决定 `--model` 与上游端点，execution.py:25-33；scheduler.py:136-150）。改根：新增本地 GLM 条目（改 DEFAULTS 或 owner 注册接口 api.py:924-942），注册表本身已是 owner 作用域（registries.py:151-162） |
| POLICY | `facai/registries.py:98` | LOCAL_BROKERABLE | 否 | 当前唯一"审批"机制 = 技能元数据闸 `review_status ∈ {pending, approved, rejected}`，未 approved 不得 enabled（registries.py:195-196）；含 license 必填与 raw api_key 拒绝（registries.py:117-126）。改根：作为 P0-4 语义模板扩展到执行请求 |

**reRooting**：
1. P0-4 插入点：`execution.py:276-284`（user_env 生成后、adapter.execute 前）+ `scheduler.py:167`（claim 后、执行 try 前）；语义复用 registries.py:98-107 的 pending/approved。
2. 模型改根到本地 GLM via Ollama：registries.py:20-26 增本地条目 + `FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`；CLI 侧 `--model` 经 execution.py:84-88 父本指本地 provider。deepseek 保留，能力不降。
3. 子进程经 Facai Broker 收口：opencode.py:124、goose.py:64、checkpoint.py:22 统一走 Broker executor；opencode.py:121 的 `os.environ` 全量继承改白名单；FACAI_USER_MODEL_KEY 由 Broker 注入短时变量/临时配置。
4. 隔离与脱敏加固（纯收口）：opencode.py:157-162 之上叠加 OS 级限制；redact（opencode.py:27-29）扩集并在 Broker 出口二次 redact。

### 2.2 FACAI 远程集成面（n8n/notify/backup/secretbox/ops/channels）——T2 PARTIALLY MANAGED

**managementClass 理由**：本地源码实例在 Facai 控制内，签名密钥/主密钥/outbox/cursor 等授权根均在本地位；但存在三个无法仅靠本地改动改根的远程裁决者——**TronGrid** 裁决链上资金真伪（本地无节点校验）、**腾讯 iLink** 裁决微信授权与接口版本、**渠道平台**（Telegram/WhatsApp）裁决消息投递真伪（§14/§17/§18），故不能标 FULLY_MANAGED。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK（资金裁决） | `facai/ops/tron_sync.py:38,146-158,204-252` | REMOTE_PROVIDER | 是 | `DEFAULT_API="https://api.trongrid.io"`；入账自动批准（PENDING→credited，tron_sync.py:227-230,255-258）完全建立在 TronGrid 返回上。改根：TRON_API_BASE/FACAI_TRON_NODE_URL env + 自托管 java-tron 全节点或 TronGrid OSS 本地索引器；可双源交叉校验；能力不降 |
| NETWORK（配额/退避） | `facai/ops/tron_sync.py:51-53,101-143,307-314` | REMOTE_PROVIDER | 是 | 可用性由 TronGrid 远程配额决定（429/5xx 指数退避；TRON-PRO-API-KEY 仅提额，tron_sync.py:118-120）。改根到本地节点后配额消失；429/5xx 最终失败规范化为 PROVIDER_REFUSAL（§15），待处理 recharge 配额恢复后自动补单 |
| CREDENTIAL/AUTH（微信授权） | `facai/channels/wechat.py:23,258-288,336-360,388-394` | REMOTE_PROVIDER | 是 | 二维码由 ilinkai.weixin.qq.com 签发、bot_token/ilink_user_id 由腾讯下发（wechat.py:351-357），本地仅保存。改根：微信降为可选 provider，通知/回复统一走 relay durable outbox（api.py:303-317 + relay.py:133-142），或换企业微信自建应用；WECHAT_* 凭据改 Secret Broker 派发；未授权时诚实 UNCONFIGURED、不阻塞他通道 |
| POLICY（接口版本强制） | `facai/channels/wechat.py:40-43,403,491` | REMOTE_PROVIDER | 是 | `BASE_INFO={"channel_version":"2.4.9","bot_agent":"OpenClaw/2.4.9"}`；版本过旧 QR 绑定被微信客户端拒绝——远程强制更新式权威。改根：版本字段 env 可配（WECHAT_ILINK_CHANNEL_VERSION/BOT_AGENT），"版本过旧"归一为 PROVIDER_REFUSAL，由本地 Router 切 relay/telegram |
| NETWORK（渠道投递+认证） | `facai/channels/telegram.py:12,140-148,157,188` | REMOTE_PROVIDER | 是 | 401/403/429 由 api.telegram.org 裁决。改根：保留 adapter，投递根切自托管 telegram-bot-api 或 relay outbox；bot token 走 Secret Broker；远程拒绝只记后端状态 |
| NETWORK（渠道投递+认证） | `facai/channels/whatsapp.py:34,181-198,242-248` | REMOTE_PROVIDER | 是 | graph.facebook.com 同时是副作用执行方与裁决方（code 190→AUTH_FAILED；v21.0 随远程演进）。改根：保留为可选后端，缺凭据/被拒时 relay 或 Telegram 兜底（notify.py:29）；凭据走 Secret Broker |
| NETWORK（出站投递） | `facai/n8n.py:90,98-142,227-267` | REMOTE_PROVIDER | 否 | `N8N_WEBHOOK_URL` + `N8N_SIGNING_SECRET` 均本地 env，校验不限主机（n8n.py:133-141）。改根：指向本机/自托管 n8n 或 FACAI 自有事件桥；失败按 RETRYABLE_ERRORS 有界重试、不伪造成功 |
| POLICY/APPROVAL（入站注入） | `facai/api.py:2058-2117`（+ n8n.py:183-209） | REMOTE_PROVIDER | 否 | 远程 n8n 经签名回调可注入事件并改 step 状态（api.py:2102-2109），公网可达（PUBLIC_PATHS api.py:149），门槛=本地 HMAC 密钥+时间窗。改根：密钥由 Owner 派发；回调降为"外部输入事件"，或经 Facai Broker 入站能力先验签再落库 |
| NETWORK（通知回传） | `facai/notify.py:29,55-91,116-141` | REMOTE_PROVIDER | 否 | 渠道集合与选择是本地常量+registry（channels/__init__.py:25-28）。改根：NOTIFY_CHANNELS 收敛为 relay+本地 outbox；失败已诚实落审计不阻塞 |
| NETWORK（外部数据源） | `facai/ops/github_digest.py:37-45,74-113,116-148` | REMOTE_PROVIDER | 否 | 日报从 GitHub search API + 两个 RSS 拉取，注册为 PENDING 元数据（github_digest.py:306），输出采纳权在本地。改根：数据源列表改 env/config；GITHUB_TOKEN 走 Secret Broker |
| CONFIG（端点信任） | `facai/channels/wechat.py:32,46-66,224-248,331-333` | CONFIGURABLE | 否 | 远程 redirect_host 只能落在本地白名单（weixin.qq.com/wechat.com）内。改根：OFFICIAL_HOST_SUFFIXES 提升为本地配置 |
| POLICY（入站鉴权） | `facai/channels/telegram.py:40-44`、`whatsapp.py:64-72`、`relay.py:83-109` | CONFIGURABLE | 否 | 三渠道入站 webhook 均在本地校验（secret token / x-hub-signature-256 / HMAC+时间窗）。无需改根；密钥交付改 Secret Broker 派发 |
| POLICY（模型端点守卫）★ | `facai/secretbox.py:96-133`（+ api.py:924-931） | CONFIGURABLE | 否 | 默认仅允许公网 HTTPS，放开私网后仍拒回环与 :8765——改根本地 GLM 的关键闸门。改根：`FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`；本地端点用非回环私网地址；必须走 127.0.0.1 时补丁 secretbox.py:127/:131（仅保留 :8765 拒绝） |
| NETWORK（模型调用·可改根本地 GLM） | `facai/ops/github_digest.py:151-198` | CONFIGURABLE | 否 | 翻译调用本地 registry 第一个非 claude 模型（端点/api_key_env 均由 registry 决定）。改根：registry 注册本地 Ollama（GLM，OpenAI 兼容），零代码 |
| NETWORK（链接基址） | `facai/ops/github_digest.py:424` | CONFIGURABLE | 否 | 视频链接用 `FACAI_PUBLIC_BASE`，默认 https://whstapp.lol。改根：env 设为 FACAI 自有域名/本地网关 |
| CREDENTIAL（Fernet 主密钥） | `facai/secretbox.py:37-89` | LOCAL_BROKERABLE | 否 | 所有用户自带模型密钥的加密根（FACAI_MASTER_KEY env 或 0600 data/master.key）。改根：主密钥改 Secret Broker/OS keyring 注入，Broker 派发 scoped key（§22） |
| CREDENTIAL（渠道令牌落盘） | `facai/channels/wechat.py:145-177` | LOCAL_BROKERABLE | 否 | WECHAT_BOT_TOKEN/ILINK_USER_ID 以 0600 明文 env 文件存 state_dir/wechat.env。改根：归 Secret Broker（或 secretbox.encrypt 加密后落库） |
| NETWORK/CHANNEL（桥接 outbox） | `facai/channels/relay.py:44-54,133-142`（+ api.py:303-317） | LOCAL_BROKERABLE | 否 | 最接近"本地收口"的通道：出站只写本地 durable outbox（QUEUED，operator 设备 AutoClaw 拉取）；`records_outbound_itself=True`（relay.py:53）。改根：作为其他通道的兜底投递根；签名密钥走 Secret Broker |
| SHELL_EXEC | `facai/ops/github_digest.py:224,259-276` | LOCAL_BROKERABLE | 否 | 日报视频 ffmpeg 两处 subprocess.run（固定 argv，textfile= 避免转义；缺 ffmpeg 诚实降级）。改根：纳入 Broker 进程能力（argv 白名单=ffmpeg、cwd 限定 root/media） |
| FILESYSTEM（备份/还原） | `facai/backup.py:30-47,99-163`（+ ops/backup_db.py:30-35） | LOCAL_BROKERABLE | 否 | sqlite backup / VACUUM INTO / os.replace 原子换入 / -wal/-shm 清理 / 保留最近 N 份。改根：注册为 Broker 存储动作（写路径白名单=data_dir 根）；还原前已有 verify+回滚快照（backup.py:107-136） |
| FILESYSTEM（媒体清理） | `facai/ops/github_digest.py:250-253,416-422` | LOCAL_BROKERABLE | 否 | root/media 工作目录 finally 清理、保留最新 14 个 digest-*.mp4。改根：Broker 文件能力（路径白名单 root/media，仅匹配 digest-* 前缀） |

**reRooting**：
1. **链上资金改根**：新增 TRON_API_BASE/FACAI_TRON_NODE_URL 替换 tron_sync.py:38 的 DEFAULT_API，指向自托管 java-tron 或 TronGrid OSS 本地索引器，使自动入账裁决根离开 api.trongrid.io；TRONGRID_API_KEY 保持可选（:118-120），429/5xx 最终失败规范化为 PROVIDER_REFUSAL（§15）。
2. **模型改根本地 GLM**：`FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1` + registry 注册本地 Ollama(GLM)；必须用 127.0.0.1 时补丁 secretbox.py:127/:131（仅保留 :8765 拒绝）。
3. **渠道改根**：N8N_WEBHOOK_URL 指向自托管 n8n；通知/回复统一走 relay durable outbox 作为本地投递根，Telegram/WhatsApp/微信 iLink 降为可选 provider；微信版本字段提升为 env 可配、版本拒绝当 PROVIDER_REFUSAL。
4. **凭据改根**：FACAI_MASTER_KEY 改 Secret Broker/OS keyring 注入；data/master.key 与 wechat.env 明文统一收归 Broker 按 scoped credential 派发（§22）；加解密与签名逻辑不改。

### 2.3 goose（block/goose Rust monorepo）——T2 PARTIALLY MANAGED

**managementClass 理由**：本地源码全量可控、provider 层可配置级改根到本地 Ollama/GLM，但默认配置下模型可用性裁决在远端，且存在若干硬编码远程端点（PostHog URL、GitHub releases/attestations、models.dev、OSV 默认端点、mesh-llm relays），须改配置或打补丁/关 feature 才能完全收口。纯本地 Agent（无独立 goose-server crate；server 侧即 crates/goose/src/acp 本地协议服务）。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK | `crates/goose/src/providers/openai_def.rs:287` | REMOTE_PROVIDER | 是 | 默认 `https://api.openai.com`（同族 anthropic_def.rs:52、tetrate.rs:56 等）。改根：GOOSE_PROVIDER/GOOSE_MODEL（config/providers.rs:69-82）+ OLLAMA_HOST 默认 localhost:11434（ollama_def.rs:66-72；常量 goose-providers/src/ollama.rs:31-35），Ollama 跑 GLM；或 declarative provider（declarative_providers.rs:218-224） |
| NETWORK | `crates/goose/src/providers/nanogpt.rs:17` | REMOTE_PROVIDER | 是 | 聚合/代理长尾（avian.rs:8、typesafe.rs:8 等）。同上一行改根；这些厂商仅作备选 backend，不入 Facai 授权链 |
| CREDENTIAL | `crates/goose/src/config/signup_openrouter/mod.rs:19-20,167` | REMOTE_PROVIDER | 是 | OpenRouter OAuth 注册远程签发 API key。改根：禁用 signup，凭据由 FACAI 授权核签发写入本地 secret；模型调用指本地 |
| CREDENTIAL | `crates/goose/src/config/signup_tetrate/mod.rs:20-21`（acp/mod.rs:24、acp/server.rs:1686） | REMOTE_PROVIDER | 是 | Tetrate 注册/校验/余额远程裁决（credits_exhausted + top_up_url）。改根：provider 不走 tetrate；计费归 FACAI 本地，top_up_url 提示重写 |
| LICENSE | `crates/goose/src/providers/githubcopilot.rs:131-139` | REMOTE_PROVIDER | 是 | Copilot 订阅权益远程裁决。改根：标 PARTIAL，能力保留但不入授权链；本地等价能力用 declarative provider + 本地 GLM |
| CREDENTIAL | `crates/goose/src/providers/huggingface_auth.rs:21-25` | REMOTE_PROVIDER | 是 | HF OAuth + client-metadata 从 goose-docs.ai 拉。改根：自托管 HF 镜像；client-metadata 补丁指 FACAI |
| AUTOUPDATE | `crates/goose-cli/src/commands/update.rs:325,130-132,270` | REMOTE_PROVIDER | 是 | 从 GitHub releases 下载 + attestations SLSA 校验（无 attestation 拒绝安装）。改根：Cargo feature `disable-update`（goose-cli/Cargo.toml:146）关闭（运行时 bail，update.rs:311-314）；或两 URL 换 FACAI release 源 + 自签名公钥 |
| AUTOUPDATE | `ui/desktop/src/utils/githubUpdater.ts:458-461`（+ autoUpdater.ts:130-136） | CONFIGURABLE | 是 | 桌面更新检查走 api.github.com releases/latest（owner=aaif-goose/repo=goose）。改根：`GITHUB_OWNER/GITHUB_REPO` env 指 FACAI fork（无需改码）+ electron-updater feed 换 FACAI 源 |
| POLICY | `crates/goose/src/agents/extension_malware_check.rs:363,17-21`（+ stdio.rs:21） | REMOTE_PROVIDER | 是 | OSV 远程一票否决扩展安装（`DEFAULT_OSV_ENDPOINT=https://api.osv.dev/v1/query`，env OSV_ENDPOINT 可覆盖）。改根：OSV_ENDPOINT 指 FACAI 自托管镜像或经 Broker 代理 |
| POLICY | `crates/goose/src/permission/permission_inspector.rs:225-256` | CONFIGURABLE | 是 | SmartApprove 用"当前 provider 模型"判定只读才自动放行——权限裁决实际由模型服务作出。改根：GOOSE_PROVIDER 指本地 GLM 即变本地裁决；或 GOOSE_MODE=approve（goose_mode.rs:22-31） |
| NETWORK | `crates/goose-cli/src/commands/roam.rs:66-70`（+ goose-roaming/src/relay.rs:16-26） | CONFIGURABLE | 是 | roaming 默认四个第三方 iroh relay。改根：GOOSE_ROAM_RELAYS（:51-53）指 FACAI 自建 relay 或 RelaySettings::Disabled 仅走 LAN |
| NETWORK | `crates/goose/src/model_catalog.rs:5,13-19` | REMOTE_PROVIDER | 否 | 启动后台拉 models.dev/api.json（内嵌快照兜底，失败仅告警）。改根：关 feature online-model-meta（goose-cli/Cargo.toml:111,113）或补丁 CATALOG_URL；构建期数据在仓内（build.rs:5-13） |
| TELEMETRY | `crates/goose/src/posthog.rs:16-17,50,20-23` | REMOTE_PROVIDER | 否 | 匿名遥测默认 opt-in 关闭（is_telemetry_enabled 默认 false）。加固：GOOSE_TELEMETRY_OFF=1；URL 硬编码，彻底移除需补丁 posthog.rs:17 |
| TELEMETRY | `crates/goose/src/otel/otlp.rs:245-266,228` | CONFIGURABLE | 否 | OTLP 导出端点 env/config 决定（缺省不启用）。改根：指 FACAI collector 或 OTEL_SDK_DISABLED=1 |
| NETWORK | `crates/goose/src/dictation/whisper.rs:68,97` | REMOTE_PROVIDER | 否 | 本地 whisper 模型从 HuggingFace 下载（离线后完整）。改根：GOOSE_PATH_ROOT 重定向数据目录（config/paths.rs:38-44）预置模型文件 |
| NETWORK | `crates/goose/src/oauth/mod.rs:25`（+ skills/mod.rs:110） | REMOTE_PROVIDER | 否 | MCP OAuth client-metadata / 技能文档根 = goose-docs.ai。改根：常量补丁指 FACAI 自托管 |
| NETWORK | `crates/goose/src/dictation/providers.rs:60-110,131-141` | CONFIGURABLE | 否 | 听写远端 ASR/TTS（OpenAI/Groq/ElevenLabs/Google）可切换，另有 Local=whisper。改根：选 Local provider |
| NETWORK | `crates/goose/src/gateway/telegram.rs:13,828` | CONFIGURABLE | 否 | 可选 Telegram 网关（非默认启用）。改根：不用即不触达，或换 FACAI 消息通道 |
| CREDENTIAL | `crates/goose/src/config/base.rs:31,216-220,393-408` | CONFIGURABLE | 否 | keyring（service="goose"）默认；GOOSE_DISABLE_KEYRING 退回 secrets.yaml。改根：GOOSE_DISABLE_KEYRING=1 + GOOSE_PATH_ROOT 收敛到 FACAI 管控目录；GOOSE_ADDITIONAL_CONFIG_FILES（:173-177）注入 FACAI 策略 |
| POLICY | `crates/goose-provider-types/src/goose_mode.rs:22-31`（+ config/base.rs:1263-1282、config/permission.rs:17-30） | CONFIGURABLE | 否 | 审批模式 auto/approve/smart_approve/chat + permission.yaml 决定放行。改根：GOOSE_MODE=approve + permission.yaml 落 FACAI 管控目录；审批回调接 Owner Gate |
| POLICY | `crates/goose/src/security/scanner.rs:89-105`（+ classification_client.rs:68-77） | CONFIGURABLE | 否 | 命令/提示注入分类器模型与端点由 env 决定（默认 false）。改根：指本地 GLM/本地分类器；默认关闭即不依赖远程 |
| NETWORK | `crates/goose/src/security/egress_inspector.rs:365-379`（+ tool_inspection.rs:7） | LOCAL_BROKERABLE | 否 | 出网检测器当前只 LOG 不拦截——现成 Broker 收口挂钩。改根：Allow 升级为 RequireApproval/Deny 交 Facai Broker |
| SHELL_EXEC | `crates/goose/src/agents/platform_extensions/developer/shell.rs:687`（+ mod.rs:150） | LOCAL_BROKERABLE | 否 | shell 工具本地执行（受审批模式约束）。改根：GOOSE_MODE=approve + Broker 挂审批回调，shell 实现不动 |
| SHELL_EXEC | `crates/goose/src/agents/extension_manager/stdio.rs:21,33-35`（+ subprocess.rs:48） | LOCAL_BROKERABLE | 否 | MCP stdio 扩展以本地子进程启动（npx/uvx/任意命令）。改根：Broker 拦在 connect() 前统一签发/审计；OSV 端点换本地镜像 |
| SHELL_EXEC | `crates/goose/src/agents/platform_extensions/code_execution.rs:216-279`（+ stdio.rs:46 docker exec） | LOCAL_BROKERABLE | 否 | bash/TS 沙箱执行 + docker exec 通道。改根：同 shell，审批模式 + Broker 挂钩 |
| NETWORK | `crates/goose/src/acp/server.rs:2677,1667`（+ goose/Cargo.toml:111） | LOCAL_BROKERABLE | 否 | 无独立 server crate；server 侧即本地 ACP 协议服务，端点/鉴权不在本仓硬编码。改根：top_up_url 提示改指 FACAI 计费 |

**reRooting**：
1. 模型面统一改根：`GOOSE_PROVIDER=ollama + GOOSE_MODEL=<glm> + OLLAMA_HOST=http://127.0.0.1:11434`；非 ollama 引擎用 declarative provider（base_url 指本地 GLM 网关）。provider 能力全保留。
2. 执行/审批面统一收口：GOOSE_MODE=approve + permission.yaml 落 FACAI 管控目录；tool_inspection.rs inspector 链挂 Facai Broker；EgressInspector 的 Allow 升级为 RequireApproval。
3. 远程端点改根 patch 清单：OSV_ENDPOINT→FACAI 镜像；GITHUB_OWNER/GITHUB_REPO→FACAI fork；CLI 自更新 disable-update 或换 URL；CATALOG_URL 换镜像/关 feature；GOOSE_ROAM_RELAYS 换 FACAI relay。
4. 环境/凭据面：GOOSE_PATH_ROOT 收敛状态 + GOOSE_DISABLE_KEYRING=1 + GOOSE_ADDITIONAL_CONFIG_FILES 注入策略 + GOOSE_TELEMETRY_OFF=1 + OTEL 指 FACAI collector。

### 2.4 sst/opencode（TS monorepo）——T2 PARTIALLY MANAGED

**managementClass 理由**：本地执行面（shell spawn / 文件写 / Permission.ask）可被 FACAI Broker 完整收口，但模型推理、模型目录、自动更新、console 账户与组织远程配置、npm 动态装包、桌面 electron-updater、LSP 二进制下载默认均以远端为裁决者；多数点已有本地改根句柄（baseURL / OPENCODE_MODELS_PATH / autoupdate:false / OPENCODE_AUTH_CONTENT / OPENCODE_DISABLE_SHARE），未改根前不能标 FULLY_MANAGED（§18）。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| AUTOUPDATE | `packages/opencode/src/cli/upgrade.ts:10,42`（+ installation/index.ts:147,151-156,208） | REMOTE_PROVIDER | 是 | patch 级发布自动执行升级；curl 方式把 `https://opencode.ai/install` 脚本经 stdin 喂本地 shell 执行。改根：`autoupdate:false`（core/src/v1/config/config.ts:64）或 OPENCODE_DISABLE_AUTOUPDATE（upgrade.ts:10 即守卫）；Installation.latest()（:208）/安装脚本换 FACAI 签名源 |
| NETWORK | `packages/opencode/src/provider/provider.ts:1810-1812,1831-1832,229-241` | REMOTE_PROVIDER | 是 | 推理打到 provider 远程 API；baseURL 由 config options.baseURL 或远程目录 model.api.url 决定；无 key 按 cost.input 删付费模型。改根：`provider.<id>.options.baseURL` + `options.apiKey`（core/src/v1/config/provider.ts:93-94）指本地 GLM/Ollama；`model`/`small_model`（config.ts:74,77）绑本地模型 |
| CREDENTIAL | `packages/opencode/src/account/account.ts:390`（+ config/config.ts:495-518,370） | REMOTE_PROVIDER | 是 | console 设备码登录换 token；活动组织远程 `{url}/api/config` 以 global 级 merge 进本地 config（可覆写 provider/permission）。改根：不激活 account（config.ts:495 无 active_org_id 整块跳过）；凭据走 OPENCODE_AUTH_CONTENT；如需 console 能力把 account.url 指 FACAI 自建 |
| POLICY | `packages/opencode/src/config/config.ts:374-388,407` | REMOTE_PROVIDER | 是 | auth.json 中 type=wellknown 条目拉 `{url}/.well-known/opencode` 并以 global 合入本地 config。改根：不写 wellknown 条目；或指 FACAI 本地 endpoint；本地兜底 OPENCODE_CONFIG（:415）/OPENCODE_CONFIG_CONTENT（:482） |
| AUTOUPDATE | `packages/core/src/npm.ts:137,87,100`（+ provider.ts:1868-1875、plugin/shared.ts:211） | REMOTE_PROVIDER | 是 | provider SDK/plugin 运行时从 npm registry 装到 cache 并 import() 执行。改根：项目 .npmrc `registry=` 指 FACAI 内网镜像（npm-config.ts:37 读取）；model.api.npm 在 config 本地固定；预热 cache 离线复用（npm.ts:133 命中本地不联网） |
| AUTOUPDATE | `packages/desktop/src/main/updater.ts:16,33,40` | REMOTE_PROVIDER | 是 | electron-updater 从 GitHub Releases feed 检查/下载/安装（channel=latest）。改根：打包期 feed 指 FACAI 内部更新服务；放行权归 Owner |
| AUTOUPDATE | `packages/opencode/src/lsp/server.ts:180,183-189,600-653,405` | REMOTE_PROVIDER | 是 | 首次用某语言从 GitHub/官网下载二进制或源码 zip 解压执行（版本由远端 latest 裁决）。改根：预置二进制到 Global.Path.bin（core/src/global.ts:24）命中本地短路；config.lsp 关自动下载；出口白名单到内网镜像 |
| NETWORK | `packages/opencode/src/share/share-next.ts:210,273-279,23` | REMOTE_PROVIDER | 否 | share 把 session/diff/model 推到远程（默认 https://opncd.ai）。改根：OPENCODE_DISABLE_SHARE=1 或 `share:"disabled"`（share/session.ts:29）；需要时 enterprise.url 指 FACAI 自建 |
| NETWORK | `packages/core/src/models-dev.ts:160,176,184,222,257` | REMOTE_PROVIDER | 否 | 模型目录默认远程拉取（5 分钟 TTL、60 分钟刷新）。改根：OPENCODE_MODELS_PATH 指 FACAI 本地 models.json；OPENCODE_MODELS_URL 指镜像；OPENCODE_DISABLE_MODELS_FETCH=1 彻底禁网 |
| NETWORK | `packages/opencode/src/tool/mcp-websearch.ts:4-7`（+ websearch.ts:36-40） | REMOTE_PROVIDER | 否 | websearch 硬编码 exa/parallel MCP。改根：OPENCODE_WEBSEARCH_PROVIDER 固定后端；或禁用、改用 FACAI 本地搜索 MCP |
| TELEMETRY | `packages/core/src/observability/otlp.ts:7,51-53` | CONFIGURABLE | 否 | OTLP 仅显式设端点时启用；全仓无 posthog/sentry 强制上报。改根：不设即零上报；需要时指 FACAI collector |
| CREDENTIAL | `packages/opencode/src/auth/index.ts:10,59-62,79`（+ provider.ts:1647-1656） | CONFIGURABLE | 否 | provider 密钥明文 JSON（0600）存 auth.json，可被 OPENCODE_AUTH_CONTENT 完全覆盖。改根：Owner 注入 AUTH_CONTENT，auth.json 本体纳入 FACAI Secret 管理（§22） |
| SHELL_EXEC | `packages/opencode/src/tool/shell.ts:484,263-284`（+ permission/index.ts:67-105、session/tools.ts:81） | LOCAL_BROKERABLE | 否 | bash 工具唯一 shell 出口，spawn 前必经 Permission.ask。改根：Broker 实现 Tool.Context.ask 或 Permission.ask 即收口全部 shell 副作用 |
| DB | `packages/opencode/src/tool/write.ts:54-55`（+ edit.ts:102-103、apply_patch.ts:206-207、shell.ts:505-511、truncate.ts:68-71） | LOCAL_BROKERABLE | 否 | 文件写/改与超限输出落盘，均在 ctx.ask("edit") 后。改根：同 shell 收口点 + FSUtil/truncation-dir Broker 写代理 |
| PRIVILEGE | `packages/opencode/src/cli/network.ts:15,19-24`（+ server/auth.ts:18,24-31、server/mdns.ts:10） | LOCAL_DIRECT | 否 | 本地 HTTP API 默认 127.0.0.1 随机端口；未设 OPENCODE_SERVER_PASSWORD 时无鉴权（mdns 改绑 0.0.0.0）。改根：Owner 强制下发密码、禁 --mdns、端口纳入纳管配置 |
| NETWORK | `packages/opencode/src/mcp/index.ts:272,279,344-349,966` | CONFIGURABLE | 否 | config.mcp 可挂远程 HTTP/SSE MCP 或本地子进程 MCP（远程带 OAuth）。改根：仅保留 FACAI 本地 MCP；远程 MCP 走 FACAI 网关凭据代理 |
| NETWORK | `packages/opencode/src/cli/cmd/github.handler.ts:325,1596,1073` | REMOTE_PROVIDER | 是 | `opencode github` 依赖 GitHub App 安装状态与 api.github.com（含 api.opencode.ai 自建端点）。改根：指自建 Gitea/GHE + FACAI 自持 App installation 端点 |

**reRooting**：
1. 模型改根：`provider.<id>.options.baseURL` + `options.apiKey`（core/src/v1/config/provider.ts:93-94）+ `model`/`small_model`（config.ts:74,77）指本地 GLM/Ollama；推理能力保留、授权根改本地。
2. 目录改根：OPENCODE_MODELS_PATH 指本地 models.json（models-dev.ts:184）；OPENCODE_MODELS_URL 指镜像（:160）；可选 OPENCODE_DISABLE_MODELS_FETCH=1（:222）。
3. 更新改根：autoupdate:false / OPENCODE_DISABLE_AUTOUPDATE；Installation.latest()（installation/index.ts:208）与 opencode.ai/install（:147）换 FACAI 签名 release manifest，升级能力不删。
4. 凭据/策略改根：OPENCODE_AUTH_CONTENT（auth/index.ts:59）由 Owner 注入；不激活 account 组织；well-known 指 FACAI endpoint；OPENCODE_DISABLE_SHARE=1 + share:"disabled"。

### 2.5 hermes-local（Hermes Agent 本地实例）——T2 PARTIALLY MANAGED

**managementClass 理由**：代码树、配置、执行面、审批与 SOUL 身份全部本地可控（`D:/hermes-agent` 源码树与 `D:/hermes` 运行实例可直接改），但主模型推理的端点/模型可用性（api.z.ai 在线探测）、官方更新源（NousResearch/hermes-agent.git + api.github.com）与 Nous 托管工具网关仍把最高裁决留在远程。上游仓库 URL 待操作员确认（见 §五）。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK | `D:/hermes/config.yaml:134,101`（+ `D:/hermes-agent/hermes_cli/auth.py:194`） | REMOTE_PROVIDER | 是 | 主模型 provider="zai"（api.z.ai/open.bigmodel.cn），默认 glm-4.5-flash。改根：provider 改 "custom"/"ollama" + model.base_url=`http://127.0.0.1:11434/v1`；或 env GLM_BASE_URL（providers.py:46 的 base_url_env_var）；model.default 换本地 GLM 等价模型 |
| NETWORK | `D:/hermes-agent/hermes_cli/auth_zai_kimi.py:70,42-45,105` | REMOTE_PROVIDER | 是 | z.ai 端点在线探测（4 候选端点×模型 slug，结果缓存 auth.json）把"哪个端点可用"交远程裁决。改根：设 env GLM_BASE_URL（:107-108 env_override 优先于探测），探测路径自然不执行（保留代码供他用） |
| AUTOUPDATE | `D:/hermes-agent/hermes_cli/update_cmd_git.py:246`（+ source_check.py:103、config.yaml:2186、banner.py:274） | REMOTE_PROVIDER | 是 | 自动更新以官方 GitHub 仓库为唯一权威源（api.github.com compare 落后提交数）。改根：运行树 `git remote set-url origin <FACAI 内部镜像>`（保留 hermes update 能力）+ updates.check 设 false |
| NETWORK | `D:/hermes-agent/tools/managed_tool_gateway.py:157,17,159-165`（+ web_tools.py:127） | REMOTE_PROVIDER | 是 | Nous 托管工具网关（`<vendor>-gateway.nousresearch.com`），能否用由 Nous 账户 entitlement 裁决。改根：env TOOL_GATEWAY_DOMAIN/TOOL_GATEWAY_SCHEME（:151-161 已支持覆盖）指本地网关；或 web 改本地 SEARXNG_URL |
| CREDENTIAL | `D:/hermes-agent/hermes_cli/auth_constants.py:55,57-59`（+ config.yaml:2170） | REMOTE_PROVIDER | 是 | Nous Portal 身份裁决者（登录/令牌/匿名免费层推理 host 由 NAS 每次 token exchange 下发）。改根：adopt_external_logins 设 false 并移除 Portal 令牌（auth.json）；身份/配额归 FACAI Owner；如沿用则 portal_url 可配（config_defaults.py:1836） |
| NETWORK | `D:/hermes/config.yaml:87-93` | REMOTE_PROVIDER | 否 | 已激活的远程聚合器 agnes（apihub.agnes-ai.com/v1）/hb-api（hb-api.online/v1），切换 provider 即成主供方。改根：base_url 改本地聚合器（本地 LiteLLM）或删条目指本地 GLM |
| NETWORK | `D:/hermes/config.yaml:1679,1727-1728`（+ transcription_tools.py:249、config.yaml:1712） | REMOTE_PROVIDER | 否 | STT enabled 且 provider 未指定走自动检测（local 优先，缺本地 faster-whisper 则上传云）。改根：stt.provider 显式设 "local"（:1712-1722 已配 base+VAD），音频不出本机 |
| TELEMETRY | `D:/hermes-agent/hermes_cli/config_defaults.py:2348`（+ config.yaml:2157-2159） | CONFIGURABLE | 否 | 共享指标遥测出口 telemetry.nousresearch.com，当前 enabled:false/send:false。改根：维持 send:false；需指标则 endpoint 改本地 collector |
| NETWORK | `D:/hermes-agent/agent/models_dev.py:27,254-256` | CONFIGURABLE | 否 | 模型元数据目录从 models.dev 拉取（离线回退磁盘缓存）。改根：config.yaml `models_dev.url` 指本地镜像 |
| NETWORK | `D:/hermes-agent/tools/skills_hub_search.py:27`（+ skills_hub_clawhub.py:66、skills_hub_skillssh.py:24） | REMOTE_PROVIDER | 否 | 在线技能注册表检索/安装（Hermes 索引、clawhub、skills.sh、GitHub）。改根：skills.external_dirs（config.yaml:1190-1192）指向本地技能目录（已有机制）；安装改指内部索引 |
| NETWORK | `D:/hermes/config.yaml:77-79`（+ plugin-catalog/hermes-telemetry.yaml:2-3） | REMOTE_PROVIDER | 否 | 插件安装经 git clone 从远程拉取（条目已 sha pin）。改根：插件 repo 改本地私有镜像 + 本地缓存目录 |
| TELEMETRY | `D:/hermes-agent/hermes_cli/diagnostics_upload.py:13` | REMOTE_PROVIDER | 否 | 诊断包上传默认发 Portal 域。改根：env HERMES_DIAGNOSTICS_BASE_URL 指本地接收端或禁用（现成覆盖点） |
| CREDENTIAL | `D:/hermes-agent/hermes_cli/auth.py:497`（+ config.yaml:2305-2347） | CONFIGURABLE | 否 | 凭据链 = ~/.hermes/auth.json + D:/hermes/.env + 可选 secrets 外部管理器。改根：启用 config.yaml secrets.command（:2343-2347 本地 CLI 助手，启动期读数不落盘）；auth.json 仅留本地 provider 状态 |
| CREDENTIAL | `D:/hermes-agent/hermes_cli/github_api.py:27,50,85` | CONFIGURABLE | 否 | GitHub API 凭据阶梯（GITHUB_TOKEN→GH_TOKEN→gh CLI），无 token 匿名降额。改根：改指内部 Gitea/镜像 API；或 secrets.command 注入内部 token |
| SHELL_EXEC | `D:/hermes/config.yaml:449-450`（+ tools/terminal_tool.py:1367） | LOCAL_BROKERABLE | 否 | terminal 本地执行（backend=local，cwd="."，timeout 180）。改根：terminal.backend 改 docker/自定义 wrapper（config.yaml:484-489 现成收口形态）或 security 钩子（:2115-2122 pre_tool_call） |
| SHELL_EXEC | `D:/hermes/config.yaml:1805-1810`（+ tools/code_execution_tool.py） | LOCAL_BROKERABLE | 否 | 本地 Python 持久内核（timeout 300s、最多 50 次 RPC）。改根：Broker 侧对 kernel 子进程命名空间/审计包装 |
| SHELL_EXEC | `D:/hermes/bin/hermes.cmd:2` | LOCAL_BROKERABLE | 否 | 启动器 cmd 内嵌 base64 Python 引导（sys.path=C:\hermes\hermes-agent、HERMES_HOME）。改根：sys.path 指 FACAI 受控源码树 |
| PRIVILEGE | `D:/hermes-agent/hermes_cli/approval_mode.py:16`（+ config.yaml:597-604） | CONFIGURABLE | 否 | 危险命令审批三档（manual/smart/off）+ 指令文件保护闸（默认人工批准）。改根：显式写 protected_instruction_files:true，审批根收口 FACAI Owner 授权核（transport 保持 builtin、fallback=deny） |
| POLICY | `D:/hermes/SOUL.md:1-36` | LOCAL_DIRECT | 否 | 运行实例行为授权结构为本地重写版（满保真交付、破坏性操作先确认）。改根：显式写入"以 FACAI Owner 授权核为最高授权"条款；能力缺口用本地 GLM 补齐 |
| LICENSE | `D:/hermes-agent/LICENSE:1-3`（+ README.md:12） | LOCAL_DIRECT | 否 | MIT（Nous Research），无远程许可校验。无需改根；保留声明并在 SOUL/策略层注明 FACAI Owner 为运行授权源 |

**reRooting**：
1. provider 绑定改根：model.provider 由 "zai" 改 "custom"/"ollama" + model.base_url=本地 GLM（或 env GLM_BASE_URL，auth_zai_kimi.py:105-108 现成覆盖点，设后自动跳过远程探测）；agnes/hb-api 一并改本地或删除（能力由本地 GLM 承接）。
2. 更新与遥测改根：运行树 git origin 改 FACAI 内部镜像（保留 hermes update），updates.check 设 false；遥测保持 send:false（:2159）；诊断上传设 HERMES_DIAGNOSTICS_BASE_URL 指本地。
3. 工具与音频出口改根：env TOOL_GATEWAY_DOMAIN 指本地网关（managed_tool_gateway.py:161 支持覆盖）或 SEARXNG_URL 本地实例；stt.provider 显式 "local"。
4. 授权核收口：审批保持 builtin 并显式写 protected_instruction_files:true；密钥改 secrets.command 注入；"远程 API 拒绝 = 最终裁定"统一改为 FACAI Owner 授权核裁定，任何远程供方（zai/Nous/agnes）降为可替换供方。

> 补充观察（来源于检索路，未做运行时验证）：`D:/hermes/config.yaml` 存在两个顶层 `display` 键（:15 与 :1865），加载器为 ruamel safe/1.1（hermes_yaml.py:22-38），后者可能覆盖前者的 `language: zh`——未验证生效结果，登记待查。

### 2.6 autoclaw（AutoClaw2 实例 + relay-bridge）——T2 PARTIALLY MANAGED

**managementClass 理由**：第三方打包产物不可全控，但 `resources/app/dist/main.cjs` 明文可补丁（已有补丁痕迹）、凭据经本机 DPAPI safeStorage 加密可本地解、执行面/DB/审批表均在本机可收口；桥为自研本地件。四个方向的远程最高裁决者：默认模型智谱云、electron-updater 更新源、账号/会员 token、APMPlus 遥测。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK | `D:/goose/自动抓  GLM/AutoClaw2/resources/app/dist/main.cjs:407274,424377,130701` | REMOTE_PROVIDER | 是 | 默认模型出口 api.z.ai/open.bigmodel.cn coding paas（配额错误 insufficient_quota 远端裁决）。改根：baseUrl 指 127.0.0.1:9900 本地反代或本地 GLM/Ollama（proxy/config.js:6 是参考实现）；token 改 broker 注入（patch_zaitoken.py:40-41 现成补丁点） |
| AUTOUPDATE | `main.cjs:449335-449336`（+ `%APPDATA%/AutoClaw-oversea-official/updates/app-update.yml:1-2`） | REMOTE_PROVIDER | 是 | electron-updater@6.6.2 从智谱 OSS 拉更新（oversea z.ai / cn aminer.cn）。改根：feed URL 单点替换为自托管目录或 file://；签名校验根换本地公钥；放行权归 Owner |
| NETWORK | `facai-handoff/autoclaw-relay-bridge/bridge.py:37,261,276` | REMOTE_PROVIDER | 是 | 桥把 IM 流量 HMAC 出站到 https://whstapp.lol，由 Facai 规划并运行 mission（bridge.py:14）。改根：FACAI_BASE_URL（bridge.py:81 env 覆盖点）指本地 FACAI Owner 内核；mission 规划由本地 GLM 承担；转发/回执/HMAC 全保留 |
| LICENSE | `.../accounts/<id>/projects/Default/proxy/patch_zaitoken.py:40-41` + `proxy/.zai_member_token`（+ main.cjs:415756） | REMOTE_PROVIDER | 是 | 会员/账号 token 决定免费模型通道与额度（内置会员 token 兜底、本地 token 文件），账号由远端智谱控制面裁决。改根：会员校验归本地 Owner 授权核（token 入 broker 密钥库）；删除内置共享 token，缺口用本地 GLM 补齐 |
| TELEMETRY | `main.cjs:698708-698709,544982,548130` | REMOTE_PROVIDER | 否 | 内嵌 APMPlus SDK 向火山引擎外发（x-arms-license-key）。改根：上报地址与 license key 改本地采集器或关闭外发；本地 logs/diagnostics 保留 |
| REMOTE_PROVIDER | `main.cjs:680906,677010,673527`（+ `runtime/whatsapp-connection.json:1`） | REMOTE_PROVIDER | 是 | 内嵌 Baileys 6.7.24 驱动 WhatsApp（承载面在 WhatsApp 服务器）；当前 DB 中 whatsapp 行全 abandoned、未发现 creds/noiseKey 落盘（未完成配对）。改根：会话凭据改 broker 保管注入（桥已示范对 runtime.sqlite 的 IM 表读写）；Baileys 通道能力保留 |
| DB | `bridge.py:167-174,225-241`（+ main.cjs:466309） | LOCAL_BROKERABLE | 否 | 桥只读 im_inbound_receipts、直插 im_delivery_outbox(pending)，App 侧写 receipts 并消费 outbox——同一 runtime.sqlite 双向授权面（实测 106 表；inbound 32 行 telegram、outbox 50 行）。改根：broker 独占该 SQLite 读写与状态机，schema 不动 |
| SHELL_EXEC | `main.cjs:147570,148185,274696`（+ pi-coding-agent package.json:4；child_process 85 处） | LOCAL_BROKERABLE | 否 | agent 引擎自带 read/bash/edit/write 工具链含 shell:true 子进程。改根：bash/exec 经 FACAI Broker 审批与白名单（复用本地 governed_tool_session_approval_rules） |
| POLICY | `.../runtime/runtime.sqlite`（governed_tool_session_approval_rules / work_im_guest_allowances / model_connections） | LOCAL_BROKERABLE | 否 | 审批与配额规则已落本地表。改根：broker 作为审批/额度表唯一写入者与裁决者；远程配额判断改读本地额度表 |
| PRIVILEGE | `autoclaw-relay-bridge/start-bridge.bat:4-7`（+ Startup/FACAI-RelayBridge.lnk、install-task.ps1:25-31） | LOCAL_BROKERABLE | 否 | 用户启动文件夹→bat 无限循环拉起 pythonw bridge.py（schtasks 查询 FACAI-AutoClaw-RelayBridge 未注册）。改根：自启迁 FACAI Owner 常驻 supervisor（保留 24/7 与 30s 自愈）或纳入 owner 管控清单 |
| CREDENTIAL | `Startup/UncensoredProxy9900.cmd:3`、`proxy/.zai_member_token`、`patch_zaitoken.py:41`、HKCU\Environment | LOCAL_BROKERABLE | 否 | 明文凭据落盘面（启动脚本硬编码 ZAI_TOKEN、token 文件、内置会员 token、注册表明文 API key 群，报告内脱敏）。改根：密钥迁 broker 密钥库、由 broker 注入进程 env；删内置共享 token |
| CREDENTIAL | `main.cjs:539253-539254,544248-544250`（+ credentials.enc 等） | CONFIGURABLE | 否 | 应用凭据库经 Electron safeStorage（DPAPI）加密，解密根在本机用户。改根：保留 DPAPI 本地根，解密与注入权限收口 broker |
| NETWORK | 实测 `127.0.0.1:3917,18432,59110,60208`（pid 69456 AutoClaw2.exe） | LOCAL_DIRECT | 否 | 4 个回环 HTTP 口带 `Access-Control-Allow-Origin: *` + Private Network 放行、未见鉴权挑战。改根：加本地 token/Origin 白名单，或统一经 broker 转发审计 |
| SHELL_EXEC | `.../proxy/server.py:28,30,58`（+ proxy/config.js:6-10；实测 127.0.0.1:9900 LISTENING） | LOCAL_BROKERABLE | 否 | 本地反代 pivot（operator 已建改根载体，上游可指 open.bigmodel.cn 等）。改根：上游白名单收口本地 GLM/Ollama，纳入 Owner 授权核 |
| NETWORK | 实测 `python.exe -m http.server 8899 --bind 127.0.0.1`（pid 10292） | LOCAL_DIRECT | 否 | vendor python 起回环静态文件服务（无鉴权）。改根：收口 broker（加鉴权或改受控目录），回环绑定保留 |

**reRooting**：
1. 模型出口改根：main.cjs:407274/424377 的 zai provider baseUrl 指 127.0.0.1:9900 本地反代或本地 GLM；会员 token 供给改 broker 密钥库（patch_zaitoken.py:40-41 补丁点）；provider/工具能力一项不删。
2. 中继改根：FACAI_BASE_URL（bridge.py:81）指本地 FACAI Owner 内核，mission 规划由本地 GLM 承担；HMAC/去重/回执语义原样保留。
3. 更新改根：main.cjs:449335-449336 的 feed URL 换自托管/内网、签名根换本地公钥；electron-updater 全套 staged 更新能力保留。
4. 凭据与执行收口：明文 token 与 HKCU\Environment 密钥迁 broker 注入；agent shell 面与审批走本地 governed_tool_session_approval_rules；删内置共享 token 的缺口用本地 GLM 绑定补齐。

### 2.7 whatsapp-relay（Bridge，Node/@whiskeysockets/baileys 6.7.24）——T1 FULLY MANAGED（候选）

**managementClass 理由**：桥的一切本地权威（出站基址、HMAC 密钥、凭据目录、轮询/重连/退避）均为 env/config 可控且源码在手；无本机监听、无子进程、无自更新、无远端遥测（四项 grep 零命中），故可被 Facai Broker 完整纳管。**当前差一步**：密钥仍以 0640 明文 env 文件注入、wa-auth 目录未 chmod（README:68 声称 0700、代码无 mode）——落地下述 Secret Broker 改根后达成 T1（§21 verifier：全部满足才可 T1）。唯一真实 REMOTE_PROVIDER 面是它桥接的 WhatsApp 会话链路（会话签发/吊销只在 wss://web.whatsapp.com 裁决）。

| 类别 | 位置 | 分类 | 远程依赖 | 改根点 |
|------|------|------|----------|--------|
| NETWORK | `whiskeysockets/baileys/lib/Defaults/index.js:32,12`（使用点 `bridge/whatsapp-relay/index.mjs:134`） | REMOTE_PROVIDER | 是 | Baileys 长连 WhatsApp（401/403/419 强制登出只能服务端裁决）。改根：env 注入 waWebSocketUrl/transport（WA_WS_URL/WA_TRANSPORT）使"用哪条通道"回到本地配置；诚实边界：会话签发/吊销无法本地替代，只做显式声明 + 可切自托管 API 网关 + 凭据 broker 下发 |
| NETWORK | `bridge/whatsapp-relay/index.mjs:131-132`（URL 在 generics.js:190） | REMOTE_PROVIDER | 否 | 启动从 GitHub raw 拉 baileys-version.json（失败仅告警回退 baked）。改根：优先读包内 baileys-version.json，远端探测仅在 WA_ALLOW_REMOTE_VERSION=1 时启用 |
| CREDENTIAL | `bridge/whatsapp-relay/index.mjs:31,90-92`（+ facai-whatsapp-relay.service:11、deploy/providers/relay.env.example:21） | LOCAL_BROKERABLE | 否 | 双向 HMAC 共享密钥（缺省拒跑），systemd EnvironmentFile root:facai 0640 明文注入。改根：向本地 Secret Broker 领取 scoped credential（inbound/outbound 分离 + 轮换）；服务端 facai/channels/relay.py:49 required_env 同步改 |
| CREDENTIAL | `bridge/whatsapp-relay/index.mjs:32,94,129`（读写 use-multi-file-auth-state.js:35,87,117） | LOCAL_BROKERABLE | 否 | wa-auth 目录明文 JSON 逐文件读写、扫码一次长期在线；mkdirSync 无 mode。改根：凭据存储接口 broker 托管；最小落地 = mkdirSync 加 0700 + 属主校验 |
| CREDENTIAL | `bridge/whatsapp-relay/index.mjs:152-162` | LOCAL_BROKERABLE | 否 | QR 原始载荷落盘 wa-qr.txt（不限权不清理）、配对码进 pino 日志。改根：纳入 broker 短时凭据生命周期；wa-qr.txt 单次消费即删；配对码不进日志 |
| CONFIGURABLE | `bridge/whatsapp-relay/index.mjs:30`（+ facai/api.py:1900、channels/relay.py:83-109） | CONFIGURABLE | 否 | 出站基址 env 决定，默认硬编码 https://whstapp.lol（=本仓 facai 服务端）。改根：去掉硬编码默认，未设 FACAI_BASE_URL 即拒跑（与 :90 SECRET 同规格），默认指本地 Owner 授权核 |
| PRIVILEGE | `bridge/whatsapp-relay/index.mjs:97-105`（校验端 facai/api.py:1915-1916、channels/relay.py:83-109；豁免端 facai/api.py:166-169） | CONFIGURABLE | 否 | 签名 = sha256 over "<ts>.<body>" + 300s 窗 + secure_equals；`/relay/send` 列 PUBLIC_PATHS 免会话，全部授权即一把共享密钥（单因子）。改根：升级为 broker 签发的双向分离 scoped credential（inbound 只能投递/outbound 只能取件）+ 豁免分支加 scope 校验 |
| TELEMETRY | `bridge/whatsapp-relay/index.mjs:302-303,206,257` | LOCAL_BROKERABLE | 否 | 无远端遥测；pino 只写 stdout；密钥仅以 sha256 前 8 位指纹出现。改根：保持，仅把指纹换成 broker credential id 指针 |

**reRooting**（原文摘录）：
1. `index.mjs:31` — RELAY_SHARED_SECRET 改向本地 Secret Broker 领取 scoped credential；服务单元 :11 的 EnvironmentFile 缩减为 broker 地址；服务端 relay.py:49 同步。
2. `index.mjs:94/:129` — wa-auth 目录改由 Secret Broker 托管；最小补丁加 `{mode:0o700}` 并校验属主（兑现 README:68）。
3. `index.mjs:134` — waWebSocketUrl/transport 从 env 注入，WhatsApp 传输做成可换适配器。
4. `index.mjs:131` — fetchLatestBaileysVersion 改优先读包内 baked 版本，去掉启动期对 raw.githubusercontent.com 的外呼。

### 2.8 zcode——T3 EXTERNAL（EXTERNAL-BINARY）

**managementClass 理由**：ZCode 本机为闭源二进制安装器（无公开源码可获取），无法做源码级 Authority Surface Discovery；按 §20 记 **T3 EXTERNAL**——"T3 ≠ 能力弱"，仅表示 Facai Authority Coverage 较低。收录方式 = **适配器级**（它本身已是可编排执行体，后续经 P0-7 Agent Adapter 接入）。

**surfaces：留空（0 条）**。原因：无源码可走查、无 `file:line` 证据可引；本清单纪律为"宁可少报不编造"，故不为 zcode 推断任何 surface 行。其真实授权面应在 P0-7 适配器接入时按运行时观察（进程、配置、API 出口）登记，而非由本清单臆造。

**reRooting**：
1. 适配器级收录：以 P0-7 Agent Adapter 接入，ZCode 只贡献 Planning/Reasoning/Action Proposal，副作用统一经 Action IR 收口 FACAI Execution Runtime（§4 `Agent ≠ Executor`：`docs/AUTHORITY_ARCHITECTURE.md:236-294`；§9 数据流 `:443-478`）。
2. 其二进制与配置不在本清单登记改根点（无源码证据）；接入实测后再补登 surface（届时以运行时观察为准）。

### 2.9 facai-native——T0 FACAI NATIVE

**managementClass 理由**：FACAI 自身内核 = 授权根的唯一合法承载者（§1 `docs/AUTHORITY_ARCHITECTURE.md:38-107`）。Owner 即授权根，不存在第三方授权面，故 **T0 NATIVE**。

**surfaces：留空（0 条）**。原因：authority 核**尚未落地**——P0-1..P0-16 为待建项（`HANDOFF-NEXT.md:257-261`），无已实现的核代码可引 `file:line`；已上线的原生模块（gateway/execution/scheduler/secretbox/channels/ops）的授权面已分别登记在 facai-adapter-core（2.1）与 FACAI 远程集成面（2.2）两个 target 内，本目标重复登记会造成重复计数。本目标的"落地"即 §四的 P0 → facai/ 模块映射。

**reRooting**：
1. 按 §26（`docs/AUTHORITY_ARCHITECTURE.md:1183-1206`）顺序实现 P0-1..P0-16：Authority Root → Authorization Engine → Grant → Owner Gate → Action IR → Backend Registry → Agent/Provider Adapter SDK → Source Ingestion/Verifier → Capability Broker → Execution Runtime → Result Normalization → Router/Fallback → Secret Broker → Evidence/Proof。
2. 其余 8 份 manifest 中的全部改根动作，最终以本内核为授权根（§7：`Capability = PRESERVED, Authority Root = FACAI OWNER`）。

---

## 三、Re-rooting 总方案（跨目标归并）

全部 118 条 surface 的改根动作归并为**三类**（与 §6/§7/§8/§18 对应）。红线：三类动作一律"只换根、只加签署、不删能力"。

### A 类·配置级改根（CONFIGURABLE / 最小改动、可回退、零执行路径变更）

手段：env / config 文件 / registry 条目 / CLI 参数。适用面：模型端点（本地 GLM via Ollama）、模型目录、自动更新开关、遥测开关、share/分享、数据源镜像、relay 基址、审批模式、凭据注入通道、STT 本地化。

代表动作：
- **A1 模型改根本地 GLM（全局第一优先）**：goose `GOOSE_PROVIDER=ollama + OLLAMA_HOST=127.0.0.1:11434`（openai_def.rs:287 上游被替换）；opencode `provider.<id>.options.baseURL`（provider.ts:1810 分支）；hermes `provider:custom + base_url` 或 `GLM_BASE_URL`（auth_zai_kimi.py:105-108）；autoclaw `baseUrl→127.0.0.1:9900`（main.cjs:407274）；facai 侧 `models` registry 增本地条目 + `FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`（secretbox.py:108-110）。
- **A2 关闭/本地化远程控制面**：各 autoupdate/share/telemetry 开关（opencode `autoupdate:false`、goose `disable-update` feature 或 `GITHUB_OWNER/REPO` 指 fork、hermes `updates.check:false`、autoclaw feed 换源）；n8n webhook 指自托管；数据源改镜像（models.dev/models.opencode.ai/github_digest 源列表）。
- **A3 显式化硬编码常量**：wechat 版本字段、OFFICIAL_HOST_SUFFIXES、relay 默认基址去硬编码、z.ai 端点探测走 env 短路。

### B 类·Broker 收口（LOCAL_BROKERABLE / LOCAL_DIRECT → 本地核必经）

手段：FACAI Capability Broker / Secret Broker / Execution Runtime（P0-11/12/15）。把"能用"变为"必经本地核"：

代表动作：
- **B1 凭据收口（最先做）**：所有明文密钥面 → Secret Broker scoped credential（§22）。落点：facai `secretbox.py:37-89` 主密钥、`wechat.py:145-177` 渠道令牌、`execution.py:103` 用户模型 env、`scheduler.py:126` 明文 api_key；hermes auth.json/.env；autoclaw 明文 token 群与 DPAPI 解密注入；relay 的 0640 明文 env；goose secrets.yaml（GOOSE_DISABLE_KEYRING=1 落 FACAI 目录）；opencode OPENCODE_AUTH_CONTENT。
- **B2 子进程收口**：Broker executor（binary 白名单 + cwd 断言 + env 白名单 + 一次性令牌）。落点：facai `opencode.py:124`/`goose.py:64`/`checkpoint.py:22`/`github_digest.py:259-276`；goose shell/MCP stdio/code_execution（tool_inspection 链）；opencode shell.ts:484 + Permission.ask；hermes terminal/code_execution 内核；autoclaw shell:true 面 + 本地 governed_tool_session_approval_rules。
- **B3 Owner Gate 插入**：本地 Owner 授权核签发一次性令牌后才放行（P0-4）。落点：facai `execution.py:276-284` + `scheduler.py:167`（语义复用 registries.py:98-107）；goose `GOOSE_MODE=approve` + 审批回调；opencode Permission.ask 实现处；hermes approval_mode + protected_instruction_files；autoclaw 审批表写入权。
- **B4 隔离/脱敏加固**：OS 级限制叠加在路径断言之上（facai opencode.py:157-162；autoclaw 回环面加 token/Origin）；redact 扩集并在出口二次 redact（opencode.py:27-29）；hermes 遥测/诊断出口本地化。

### C 类·Provider Adapter 改造（REMOTE_PROVIDER → §14/§15/§18 规范化）

手段：P0-8 Provider Adapter SDK + P0-13 Result Normalization + P0-14 Router/Fallback。**不改远程服务器，只改本地怎么对待它**：

代表动作：
- **C1 拒绝归一**：远程 401/403/429/5xx/版本过旧/配额耗尽 → §15 前五状态（PROVIDER_REFUSAL / RESOURCE_UNAVAILABLE / BACKEND_UNAVAILABLE…），绝不映射 MISSION_FAILED。落点：facai `gateway.py:73 classify_response` 已具雏形；tron_sync 429/5xx 最终失败；wechat "版本过旧"；telegram 401/403/429（telegram.py:138）；whatsapp code 190。
- **C2 双源/多后端**：TronGrid 本地索引器为主、TronGrid 为交叉校验第二源；通知渠道 relay（本地 outbox）为投递根、Telegram/WhatsApp/微信降为可选 provider；模型多后端（本地 GLM / deepseek / 聚合器）由 registry 决定路由。
- **C3 无法改根的标 PARTIAL**（§18）：腾讯 iLink（授权签发+版本强制）、WhatsApp 会话链路（含 autoclaw Baileys）、z.ai 会员/账户通道、GitHub 平台（copilot entitlement / releases attestations / App installation）、Nous Portal —— 这些目标/依赖在 manifest 保持 `remoteAuthorityDependency=true`，管理等级封顶 T2/T3，接入后由 Router 处理 refusal。

### 落地先后顺序（跨目标统一）

```
① A 类全量先行（零风险、可回退、立即可做）
   1. A1 本地 GLM via Ollama 建服务 + 各目标模型端点改根
   2. A2 关闭/本地化 autoupdate·telemetry·share·n8n·数据源
② B1 Secret Broker（P0-15）→ 全部凭据换根（§22）
③ B2 Broker executor（P0-11）+ B3 Owner Gate（P0-4）→ 执行面收口
④ B4 隔离/脱敏加固（纯收口，可与 ③ 并行）
⑤ C1 Result Normalization（P0-13）+ C2 Router（P0-14）→ 远程面规范化
⑥ C3 对不可改根项落 PARTIAL 标记 + §21 verifier 复检（REFER: P0-10）
```

理由：A 类不依赖任何新代码即可执行且可回退；B 类的三个 Broker 必须先于"批量接适配器"（§26 明确 P0 先于 Goose/Hermes/OpenCode/ZCode 适配器）；C 类依赖 P0-13/14 的代码落地，且必须等 B 类把本地执行面收口后再合入，避免出现"远程失败 → 本地绕过"的双轨。

---

## 四、P0-1..P0-16 → facai/ 模块落地映射

> 现有模块行号均已在本会话复核（见文末记录）。"依赖谁"指落地排序中的前置项；P0-9/P0-10/P0-16 不在本轮映射范围（按操作员清单）。

| P0 项 | 现有最接近的 facai 模块 | 要新增什么 | 依赖谁 |
|-------|------------------------|-----------|--------|
| **P0-1 Authority Root** | `facai/auth.py`（登录/会话）+ `facai/api.py:481-485` `require_mission`（按 owner 取 mission 的租户边界） | `facai/authority/root.py`：Owner 身份与唯一授权根定义，与 store 的 owner 模型（`store.py` registries 表 owner_id 作用域）衔接；"谁能签发"的白名单（只有 Authority Core） | 无（最先） |
| **P0-2 Authorization Engine** | `facai/registries.py:98-107` 的 pending/approved 元数据闸（唯一现存"审批"） + `gateway.py:73 classify_response`（状态归一雏形） | `facai/authority/engine.py`：Action IR → `GRANT_REQUIRED / ALLOW / DENY` 判定，输出 §15 状态枚举 | P0-1、P0-3（Grant 存储）、P0-5（IR 输入） |
| **P0-3 CapabilityGrant** | **无**（`store.py` 全部 CREATE TABLE 清单中无 grant 表，grep "grant" 零命中） | `store.py` 新增 `grants` 表（grant_id/issuer/mission_id/subject/capability/environment/ttl/uses，§10 字段）+ `facai/authority/grants.py`（签发/消费/撤销/审计事件） | P0-1；消费点由 P0-2/P0-11 调用 |
| **P0-4 Owner Gate** | `registries.py:98-107` review_status 模式（语义模板）；插入点 `execution.py:276-284`、`scheduler.py:167` | `facai/authority/gate.py`：ALLOW ONCE / ALLOW FOR MISSION / MODIFY SCOPE / DENY（§11）四档；挂到两个插入点签发一次性令牌 | P0-2、P0-3 |
| **P0-5 Action IR** | **无**；最接近的是 api.py 的请求 DTO（`api.py:1215-1232` OpenCodeRun 等）与 execution/scheduler 的 prompt/step 结构 | `facai/action_ir.py`：schema（§9 字段）+ 校验 + 与 execution/scheduler 的接线；planner 输出统一转换点 | P0-1（被 P0-2 消费；P0-6/7/8 的输入格式） |
| **P0-6 Backend Registry** | `facai/registries.py:20-26,151-162`（models/runtimes/agents/skills，天然 owner 作用域、DEFAULTS+persisted 合并） | registries 新增 `backends`/`providers` kind 或独立 backend registry：登记 9 个 target 的 managementClass（T0–T3）、authority manifest 挂载、endpoint/refusal 策略 | P0-5（登记接口不含执行）；store 持久化 |
| **P0-7 Agent Adapter SDK** | `facai/opencode.py` / `facai/goose.py` 的 `execute(...)` 协议（opencode.py:174、goose.py:47；execution.py:281-284 的 override 契约） | `facai/adapters/base.py` + adapter 注册：Adapter 只输出 Action Proposal/IR，不再直接执行（§4）；ZCode/Hermes 适配器经此接入 | P0-6、P0-4（令牌）、P0-12（执行下放）；goose 侧补 env 缺陷 |
| **P0-8 Provider Adapter SDK** | `facai/gateway.py complete()`（gateway.py:192）+ `facai/channels/*`（telegram/whatsapp/wechat/relay）+ `facai/ops/tron_sync.py` | provider adapter 接口：请求/响应规范化 + refusal 归一（§14/§15）；把 tron/渠道/模型三族远程面统一进 `facai/providers/` | P0-15（凭据）、P0-13（归一） |
| **P0-11 Capability Broker** | **收口点已定位**：`opencode.py:124` Popen、`goose.py:64` runner、`checkpoint.py:22` git、`github_digest.py:259-276` ffmpeg | `facai/broker/executor.py`：binary 白名单 + cwd 断言 + env 白名单 + 一次性执行令牌；改造上述 4 个调用点；`_WORKTREE_OPS_LOCK`（checkpoint.py:18）平移 | P0-3、P0-4（令牌签发） |
| **P0-12 Execution Runtime** | `facai/execution.py`（编排）+ `facai/scheduler.py`（dispatch/claim/重试）+ `facai/checkpoint.py`（worktree/merge） | 执行契约服务化：会话生命周期、证据采集（接 P0-16）、与 Broker 的边界（Runtime 调度、Broker 放行） | P0-11、P0-16（Evidence） |
| **P0-13 Result Normalization** | `gateway.py:73 classify_response`（HTTP→MODEL_* 枚举）+ `execution.py:149-152`（结果 workspace 校验）+ OPENCODE_FAILED/CONFLICT 事件族 | `facai/results/normalize.py`：§15 九状态枚举（SUCCESS/PROVIDER_REFUSAL/FACAI_DENIED/GRANT_REQUIRED/CAPABILITY_MISSING/RESOURCE_UNAVAILABLE/BACKEND_UNAVAILABLE/EXECUTION_FAILED/TIMEOUT）；adapter/provider 结果统一入口 | P0-8、P0-12 |
| **P0-14 Router/Fallback** | `scheduler.py` 的 `_finish_or_requeue`/重试逻辑 + registries 的 model 解析（scheduler.py:136-150） | `facai/router.py`：PROVIDER_REFUSAL → 后端标记 incompatible → 换 backend/重规划（§16）；Mission Grant 不受后端状态影响 | P0-13、P0-6 |
| **P0-15 Secret Broker** | `facai/secretbox.py`（Fernet 主密钥 + encrypt/decrypt，:37-89）+ `execution.py:103` 环境注入 + `scheduler.py:124-126` 明文拼装 | `facai/secrets/broker.py`：scoped credential 签发/租约/撤销（§22 数据流）；改造 3 个使用点 + 渠道凭据族（wechat.env 等） | P0-1、P0-3；被 P0-4/P0-11 调用 |

---

## 五、待操作员确认（只有 Owner 能拍板的三项）

1. **hermes 上游 URL**：任务说明为"无 git remote"；检索实测 `D:/hermes-agent` 确无 `.git`，但运行树 `C:/hermes/hermes-agent/.git/config` 的 origin 为 `https://github.com/NousResearch/hermes-agent.git`（promisor=true，partial clone）——**该 URL 未经操作员确认**。update 改根（§2.5 reRooting）需要操作员给出唯一权威仓库/镜像 URL 后才可执行。
2. **AutoHunter URL**：`HANDOFF-NEXT.md:128` 原文"② AutoHunter 仓库待用户给准确 URL 后收录"——本轮未收录该目标；获得 URL 后按 §5 Source Ingestion 流程补登记。
3. **individual-plan 额度**：仓库内可查到的是 `HANDOFF-NEXT.md:174-176` 的"GLM 流量包额度（用户新户资源包，2026-10-02 起）：glm-4.7 约 500 万 tokens、glm-4.6v 600 万、glm-4.5-air 1200 万、搜索 98 次、图片/视频生成 20 次"。**"individual-plan"（个人套餐）的确切档位与配额在本仓文档中未检到**（grep `individual-plan|individual|个人版|coding plan` 于 docs/ 与 HANDOFF-NEXT.md 无命中；hermes `auth_zai_kimi.py:42-45` 仅把 "Coding Plan" 作为候选探测端点标签）。需操作员给出确切 plan 名称与额度，才能写入 Provider Adapter 的 quota policy 与本地模型预算表。

---

## 附：复核记录（本会话实测）

以下为本执笔在本次会话中独立执行的行级核对（read/sed/grep 实测，非引用检索报告）：

| 目标 | 复核点 | 结果 |
|------|--------|------|
| 来源 commit | `cat facai-cloud/.git/refs/heads/master` → `48f2282a7f09dd41caa2865b793e17b954c39c63` | ✓ |
| facai-adapter-core | gateway.py:172/181/192、opencode.py:121-126、execution.py:103/276-284、registries.py:20-26/98-107、secretbox.py:108-110 | ✓ |
| FACAI 远程集成面 | tron_sync.py:38、api.py:481-485/1215-1235、gateway.py:73-85、store.py 表清单（无 grant 表） | ✓ |
| goose | providers/openai_def.rs:287、ollama_def.rs:64-72、goose_mode.rs:22-31 | ✓ |
| opencode | cli/upgrade.ts:10、provider/provider.ts:1808-1814 | ✓ |
| hermes-local | config.yaml:101/134、auth_zai_kimi.py:40-46 | ✓ |
| autoclaw | main.cjs:407274、:449335-449336 | ✓ |
| whatsapp-relay | index.mjs:30-32、:93-95、:154-156 | ✓ |
| 清单自检 | `python -c "import json,glob; [json.load(open(f,encoding='utf-8')) for f in glob.glob('*.json')]"` 于 `vendor/authority-manifests/`：9 份 manifest 全部可解析；机读汇总 surfaces=118、remoteAuthorityDependency=true=38，与本报告总表一致 | ✓ |
| 未做 | 各目标完整 surfaces 的逐条二次复核（仅按检索报告原样收录）；zcode/facai-native 无源码，无法复核 | 如实声明 |
