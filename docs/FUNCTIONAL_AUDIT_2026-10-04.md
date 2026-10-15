# FACAI 功能体检报告（十路并行审计汇总）

- **审计日期**：2026-10-04（本报告汇总撰写于 2026-10-05）
- **仓库**：`facai-handoff/facai-cloud`
- **HEAD（撰写时实测）**：`435cbdc8c59cbfdfc9df5d636f91d7807d419953`（`git log -1` → 2026-10-05 04:06:33 +0800 "Session snapshot: production deploys, P0 authority+distill landed, functional audit in flight, quota lessons"；`git status --porcelain` 无输出，工作区干净）。体检对象主提交为 sprint 交付 `224c201`（"Sprint delivery (9 clusters, gate 707 green)"）；前端/部署两路复核时 HEAD 为 `b716135`（`224c201` 之上的纯静态 showcase 提交，经 `git diff --stat` 验证为 HEAD 祖先）。
- **发现总数**：68 条 = 8 HIGH + 24 MEDIUM + 36 LOW；其中 **verified（独立复核亲读代码 + 临时目录实测复现）31 条**（8 HIGH + 23 MEDIUM），**unconfirmed（原始发现，未在本轮复核实测）37 条**（1 MEDIUM + 36 LOW）。

---

## 一、总览

| # | 路由 | verdict（各路原样照抄） | 明细 H/M/L | verified H/M | 一句话结论 |
|---|------|------------------------|-----------|--------------|-----------|
| 1 | 认证会话 | ISSUES(1 high, 0 med, 4 low) | 1/0/4 | 1/0 | 主链路扎实（37 项点名测试全过），但 must_change 强制只写在会话分支，Basic/Bearer 完全绕过 |
| 2 | 任务生命周期 | ISSUES(1 high, 2 med, 3 low) | 1/2/3 | 1/2 | 租约算术正确；HIGH 是租约不含串行锁排队 → 并行分支被重复执行双倍付费；worktree 产物永久 404、冲突重试静默丢隔离 |
| 3 | 计费预算 | ISSUES(1 high, 4 med, 3 low) | 1/4/3 | 1/4 | 充值新守卫逐项验证正确；HIGH 是 /model/complete 恒写 0 元致预算门失明（$100 上限跑出 $12,240）；另证实 resume/retry、webhook 转换两条漏网花钱路径 |
| 4 | 渠道功能 | ISSUES(0 high, 3 med, 3 low) | 0/3/5 | 0/3 | 渠道闭环主体正确；缺陷集中在预算卡住任务的事件洪水（~43 万条/天）、微信凭据失效无重扫码入口、前端管理员判定硬编码 |
| 5 | 客户端API | ISSUES(1 high, 2 med, 2 low) | 1/2/2 | 1/2 | key 生命周期/作用域机制扎实；HIGH 是 retry/resume 绕过预算硬门；外来 Bearer 头 5 次即锁整 IP |
| 6 | 前端功能 | ISSUES(0 high, 5 med, 4 low) | 0/5/4 | 0/5 | UI 主链路健康、无可利用 XSS；5 个 MEDIUM：SSE 重放指标翻倍、充值轮询恒真、账单双渲染、骨架屏卡死、流水线误报失败 |
| 7 | 数据迁移 | ISSUES(2 high, 0 med, 1 low) | 2/0/1 | 2/0 | 基线代旧库升级 33/33 全绿；但更老的库首启即崩（本 sprint 回归），NaN/inf 成本钳零失效可锁死预算门 |
| 8 | 部署运维 | ISSUES(1 high, 1 med, 3 low) | 1/1/3 | 1/1 | executable 位/rsync 白名单/ReadWritePaths/nginx 上下文逐项核对通过；HIGH 是 deploy-now 链条无 fail-fast，残缺 bundle 可借 rsync --delete 清空生产受管目录 |
| 9 | 性能复查 | ISSUES(0 high, 2 med, 3 low) | 0/2/4 | 0/1 | 四项性能声明全部实测成立；遗留问题：前端每条 SSE 事件全量重取 /missions、调度器对积压空转 |
| 10 | 产品验收 | ISSUES(1 high, 6 med, 7 low) | 1/5/7 | 1/5 | 全旅程真实可用、租户隔离正确；HIGH 与认证路同源（must_change 绕过）；其余为三价并存、管理入口丢失、宣传与实现脱节、UI_SPEC 缺口 |

**合计**：68 条 findings；verified 31 条（HIGH 8、MEDIUM 23）；unconfirmed 37 条。

**计数口径说明**：verdict 字符串照抄各路自查结论；个别路 verdict 括号计数与逐条 findings 略有出入（渠道功能 low 记 3 实列 5、性能复查 low 记 3 实列 4、产品验收 med 记 6 实列 5），上表"明细"按逐条 findings 统计，以明细为准。

**跨路重复发现（必修清单已归并）**：① must_change 绕过（认证会话 HIGH / 客户端API MEDIUM / 产品验收 HIGH 三路独立命中）；② resume/retry 预算绕过（客户端API HIGH / 计费预算 MEDIUM）；③ SSE 断线重连重放（前端功能 MEDIUM / 产品验收 MEDIUM）；④ IS_ADMIN 硬编码（渠道功能 MEDIUM / 产品验收 MEDIUM verified，认证会话 LOW unconfirmed）；⑤ 计价口径混乱（计费预算 MEDIUM / 产品验收 MEDIUM）。

---

## 二、必修清单（verified 的 HIGH / MEDIUM，按影响排序）

> 共 25 条（7 HIGH + 18 MEDIUM，跨路重复已归并并标注来源）。"证据"均为复核人在仓库外临时目录实测/亲读所得，未改动仓库。

### HIGH-1 弱口令强制改密仅约束「会话+CSRF 写」，Basic 与 Bearer API key 两条路径完全绕过（三路独立命中）

- **级别/状态/来源**：HIGH · verified · 认证会话(high)、客户端API(med)、产品验收(high)
- **位置**：`facai/api.py:540-587`（会话分支检查在 `api.py:554-556`，API-key 分支 `540-545` 与 Basic 分支 `567-578` 均无检查）；`facai/auth.py:230-245`（admin_reset_password 置 flag 并删会话但不吊销 API key）；`facai/api.py:163-170`（MUST_CHANGE_ALLOWED_PATHS 仅 change-password/logout）
- **问题**：管理员把账号重置为弱临时密码后，强制改密的写限制只对浏览器会话生效；持弱密码的脚本客户端可经 HTTP Basic 直接写任务、签发新 API key，已有 Bearer key 也可自由写入，使强制改密对脚本路径形同虚设，且经 Basic 铸出的 key 在用户改密后依然有效（改密不吊销 key）。
- **证据（实测）**：admin 重置 buyer13 为 `qwertyui`（`must_change_password=True`）后，无 cookie 客户端：BASIC `POST /api/v1/missions`→201；BASIC `POST /api/v1/my/keys`→201（铸出新 key）；BEARER 该 key `POST /api/v1/missions`→201；同账号会话写→403「请先修改密码」；reset 前已有的 key 在 reset 后照常写→201；改密清除 flag 后经 Basic 窗口期签发的 key 仍可写→201（留下持久凭证）。与 `api.py:163-166` 注释 "everything else is refused so the flag is enforced server-side" 直接矛盾。唯一缓解 `FACAI_BASIC_READONLY=1` 为 opt-in 且默认关闭（`api.py:571-573`），且只堵 Basic 不堵 API key。`py -3.12 -m pytest tests/test_auth.py -k "must_change or admin_reset or basic_readonly" -q` → 3 passed：现有测试只覆盖会话分支。
- **修法**：把 must_change 检查提升到中间件认证解析之后、按 owner 统一判断（Basic/Bearer 命中 platform 账号时非安全方法仅放行 MUST_CHANGE_ALLOWED_PATHS）；或在 admin_reset_password/change_password 时吊销该账号全部 api_keys。补 Basic/Bearer 分支回归测试。

### HIGH-2 预算已耗尽账号可经 resume/retry 把任务重新入队并真实执行，硬门被绕过且 fail→retry 无限循环

- **级别/状态/来源**：HIGH · verified · 客户端API(high)、计费预算(med)
- **位置**：`facai/api.py:1445-1456`（job_action 无 budget_gate）；`facai/store.py:948-949`（resume_job PAUSED→QUEUED）、`store.py:972-994`（retry_job FAILED/CANCELLED→QUEUED 且 attempts=0）；对照同批补门的 `api.py:784/817/874/1391-1393/1417-1419/1515-1517`
- **问题**：budget_gate 覆盖了新建 mission/job、opencode_run、model_complete 等全部派发入口，唯独 job_action 的 resume/retry 直接改状态入队；调度器全程无预算检查（`grep budget facai/scheduler.py facai/execution.py` 零命中），超支任务照常跑 OpenCode/模型产生真实消费；retry 将 attempts 归零，fail→retry 可无限循环，与 `billing.py:4-7` "hard gate on NEW dispatch … no further missions may be created, converted or enqueued" 的不变量自相矛盾。
- **证据（实测）**：设 limit=$0.0001、add_cost $5 后：`POST /missions`→402、新建 job→402（对照组），但 `POST /jobs/{id}/retry`→200 且状态 QUEUED，`run_once()` 实际执行至 COMPLETED、适配器被调用；`pause→resume` 同样复活执行（累计调用 2 次）；同 mission 新建 job 被 402 拦截。tests 中无 budget×retry/resume 用例。
- **修法**：job_action 的 resume/retry（建议四个动作统一）改状态前调 `budget_gate(current_owner(), store)`，不允许时抛 402。

### HIGH-3 /model/complete 每行成本恒写 amount=0.0，预算门对 token 消耗结构性失明

- **级别/状态/来源**：HIGH · verified · 计费预算(high)
- **位置**：`facai/api.py:1539`（`store.add_cost(..., 0.0, tokens, ...)`）；`facai/billing.py:105-106`（gate 只比较账面 `spend["cost_usd"]`）；estimated_cost 仅用于展示（`api.py:963`、`index.html:735`），9 处 budget_gate 调用点无一使用
- **问题**：该端点精确记录 token 拆分却把金额硬编码为 0，账面支出恒为 0，已设月度上限的用户可无限消耗 token 而预算门永不触发，硬上限契约对该路径完全失效（无额外限流/token 封顶）。
- **证据（实测）**：TestClient 下 30 次 `/model/complete`（每次 2M 输入+1M 输出，monkeypatch `facai.api.complete`）后 `/billing/me`：`spend.cost_usd=0.0`、`estimated_cost.usd=12240.0`（30×(2M×$95+1M×$218)，为 $100 上限的 122 倍）、`allowed=True`；同状态 `POST /missions` 仍 201。
- **修法**：budget_gate 取 `max(cost_usd, estimated_cost(spend)['usd'])`；或 /model/complete 按 PRICING 将估算额写入 amount 并标注估算口径。

### HIGH-4 并行分支租约不含 OpenCode CLI 串行锁排队时间：timeout≥300 的存活任务必然租约过期，被第二个调度进程重复执行（双倍付费 + job/step 状态背离）

- **级别/状态/来源**：HIGH · verified · 任务生命周期(high)
- **位置**：`facai/opencode.py:107`（FACAI_OPENCODE_SERIAL 默认 =1）、`opencode.py:211-215`（第二分支在 `_exec_lock` 上白等第一分支整个 timeout）；`facai/store.py:891-897`（租约 =max(600, timeout+300)，只覆盖自身 timeout）、`store.py:2141-2165`（requeue_expired 不设 not_before、无 owner 存活检查，全仓无任何租约续约/心跳写入）；`facai/scheduler.py:231-253`（run_once 持锁阻塞整个 batch）
- **问题**：串行锁下第二分支 busy 时间≈2×timeout>租约；timeout=300（dispatch 硬编码默认，`api.py:856`）时余量为零，>300 直接击穿（JobCreate 允许到 3600，`api.py:117`）。部署重叠/双实例共用一库时，requeue_expired 重排存活 job 后另一进程立即重认领并重复执行同一步骤，违反 `store.py:840-842` "never re-run concurrently by a second worker / atomic across processes" 的模块自身契约。
- **证据（实测）**：真实 Scheduler×2 + 串行 fake runner + 回拨租约：worker-2 立即重认领，runner 调用计数 {a:1, b:2}（步骤 b 执行 2 次）、job B 终态 FAILED `error='Invalid transition: COMPLETED -> COMPLETED'`（attempts=2）而 step B=COMPLETED、costs 表 3 行（b 双倍计费）、mission 仍 COMPLETED。claim_job 实测租约= max(600, timeout+300)（60/299/300/400/3600 → 600/600/600/700/3900s）。现有测试无一覆盖「串行锁排队×租约」场景（22 passed）。边界如实记录：生产 systemd 单进程 + dispatch 默认 timeout=300 使触发较窄（需第二存活调度进程 + 长分支），复核按保守口径视为 HIGH 偏 MEDIUM-HIGH 边界，但机制、双倍付费与状态背离全部实测成立。
- **修法**：租约把串行排队计入（按 mission 并行度乘系数，或拿到 `_exec_lock` 后/执行前对 RUNNING job 续租）；或 requeue_expired 重排前检查 lease_owner 心跳；至少给重排路径加 not_before 缓冲。

### HIGH-5 比 handoff 基线更老的旧库用新版首启即崩溃（本 sprint 引入的回归，且永不自愈）

- **级别/状态/来源**：HIGH · verified · 数据迁移(high)
- **位置**：`facai/store.py:188`（SCHEMA 内 `CREATE INDEX channel_messages_by_owner ... (owner_id, created_at)`）；`store.py:219-221`（`__init__` 无条件先 `executescript(SCHEMA)` 后 `_migrate`，无 try/except）；`store.py:253-255`（补 owner_id 列的 ALTER 在 _migrate，永远跑不到）
- **问题**：channel_messages 尚无 owner_id 列的库（早于 2026-10-02 handoff 基线）首次用新版启动即 `OperationalError: no such column: owner_id`，索引创建先于补列，崩后无自愈、无法自动升级；pre-sprint（224c201^）代码可正常启动同一库。Store 实例化在启动路径 `api.py:383`，构造即崩等于服务无法启动。
- **证据（实测）**：手工构造 pre-tenant 老库后：现行代码 → `CRASH: OperationalError: no such column: owner_id`（崩后 missions 仍无 owner_id，索引未建；同库重试同样崩）；224c201^ 旧代码载入同库 → BOOT OK 并补上列；迁移后的库再用现行代码 → BOOT OK；基线代库 + 现行代码 → BOOT OK（基线代不受影响，升级 33/33 全绿）。`git log -S channel_messages_by_owner` 仅命中 224c201。修复模拟（SCHEMA 去掉该索引、_migrate 后再建）→ BOOT OK。
- **修法**：把该索引从 SCHEMA 挪进 _migrate 补列之后创建，或像 `store.py:298-303` recharges_txid_unique 一样包 try/except 降级。

### HIGH-6 agent 上报 cost 为 NaN/Infinity 时钳零失效：inf 永久锁死预算门并使任务列表整页 500；NaN 令 job 被硬判 FAILED

- **级别/状态/来源**：HIGH · verified · 数据迁移(high)
- **位置**：`facai/opencode.py:59`（json.loads 接受裸 NaN/Infinity）、`opencode.py:69-71`（只查 isinstance(int,float)，bool 也通过）；`facai/store.py:700-706`（add_cost 只钳 `amount<0`）
- **问题**：上游 stdout JSON 行出现非有限 cost 时：inf 直接入库 → `user_spend_month.cost_usd=inf` → 预算门 `cost_usd>=limit` 永久 402，`store.list()` 的 `cost_total=inf` 使 JSONResponse 抛 `ValueError: Out of range float values are not JSON compliant` 整页 500；NaN 触发 IntegrityError，scheduler 判为非瞬态直接 finish_job FAILED（已成功的 agent 工作被硬判失败）。add_cost 注释自认 agent 上报 cost 为不可信输入，却在非有限值上失守。
- **证据（实测）**：`add_cost(inf, tokens=10)` 落库 amount=inf → budget_gate(限额10) allowed=False；`json.dumps({'cost_total': inf}, allow_nan=False)` 抛出与 evidence 一致的 ValueError；`add_cost(nan, tokens=10)` → `IntegrityError: NOT NULL constraint failed: costs.amount`。复核修正一处原 finding：finish_job(FAILED) 会把 RUNNING step 一并置 FAILED（非"step 停在 RUNNING"）；端到端触发依赖上游异常流（常见 CLI 序列化非有限值为 null，null 会被安全跳过）。
- **修法**：add_cost 对非有限值（`not math.isfinite`）走钳零/丢弃路径，或 summarize_output 累加前过滤非有限 cost 并排除 bool。

### HIGH-7 deploy-now.py 链条无 fail-fast：残缺 bundle 解包失败后仍继续部署，rsync --delete 会把生产受管目录整批删光

- **级别/状态/来源**：HIGH · verified · 部署运维(high)
- **位置**：`../deploy-now.py:96-135`（每步仅 `results.append((name, code==0, out))`，无 return/raise；`:115-118` 仅 `test -x deploy.sh` 即 `bash deploy/deploy.sh update`；`:134` 无条件 restart 三服务，无 rollback 调用）；`deploy/deploy.sh:129-139`（`rsync -a --delete --include '/facai/***' ... --exclude '/*'`）；`deploy/systemd/facai-cloud.service:29-31`
- **问题**：本地包残缺/远端解包中途失败（如 /tmp 满）时，staging 缺 facai/ 但 deploy.sh 仍可执行（tar 成员按字母序，deploy.sh 排第 10、facai/ 自 48 起），继续 update 即用 `--delete` 把生产 /opt/facai-cloud 下受管目录里源侧缺失的文件删光，随后 uvicorn 因缺 facai/ 启动失败、脚本仍无条件重启 → 生产宕机。审计 MEDIUM#6 明确要求"失败即时中断并调用 deploy.sh rollback"（docs/AUDIT_2026-10-04_10AGENT.md:168），本项未采纳。
- **证据（实测）**：`head -c {60K,120K,250K,400K}` 截断 bundle 后 tar 解包均 exit=2 但分别留下 33/35/63/87 个文件，四种截断下 deploy.sh 均 present 且可执行；`tar -tzf 截断包 | wc -l` rc=0（步骤 1 检查被掩蔽报 PASS）。docker + rsync 3.5.0 逐字运行该 rsync 行：SRC=无 facai/ 的 staging 对完整生产树 → exit=0 且 facai/ 35→0、tests/ 59→0、docs/ 17→3（data/与 .venv/ 幸存）；SRC=半残 facai/ → 生产 facai/ 剩 16 文件。措辞修正（不翻案）："bundle 上传中断"这半句不成立——`sftp.put` 异常会直接终止脚本；真实触发是包残缺/解包中途失败，后者已完整复现。backup_app_dir 留有 /var/backups 快照可手工回滚，但链路自身无防护。
- **修法**：deploy-now.py 每步 `code!=0` 立即 return 1（先清 staging）；deploy.sh sync_code 前加 SRC_DIR 完整性自检（如 `test -f "${SRC_DIR}/facai/api.py"`）。

### MED-1 渠道 webhook 自动入站先调付费转换模型、后过预算门：超预算发送者每条新消息仍消耗一次运营方 conversion 调用

- **级别/状态/来源**：MEDIUM · verified · 计费预算(med)
- **位置**：`facai/api.py:1760`（convert_channel_text→complete_with_bound_model，真实付费 POST）早于 `api.py:1786/1616`（ensure_channel_dispatch 内 budget_gate）；对照手动端点 `api.py:2015` 与 `/missions/auto` `api.py:874` 均先门禁
- **问题**：预算是对 NEW dispatch 的硬门（billing.py:4-9），但 webhook 路径连 mission 都创建了、转换模型也调用了，才被门禁拦下；默认绑定 deepseek-flash 用运营方 `DEEPSEEK_API_KEY` 计费。
- **证据（实测）**：admin 超预算（$500/$10）时向 relay webhook 投递签名新消息 → HTTP 503 DEFERRED/BUDGET_EXCEEDED，但 complete spy 恰好被调用 1 次，事件序列 MISSION_CREATED→PROMPT_CONVERTED→DISPATCH_DEFERRED；对照手动 dispatch 202 且 spy 0 次、/missions/auto 402 且 spy 0 次。
- **修法**：convert_and_link_mission 在领取 lease 成功后、调用转换模型前先跑 budget_gate，超预算直接走 channel_budget_deferred（`api.py:931` 已有该助手）。

### MED-2 过期仍 PENDING 的充值单永久占用 txid：真实到账无法挂新单、链上补账永不触发，用户资金卡死只能管理员手批旧单

- **级别/状态/来源**：MEDIUM · verified · 计费预算(med)
- **位置**：`facai/store.py:1234-1259`（attach 仅限 PENDING+窗口内，冲突抛 ValueError）；唯一索引 `recharges_txid_unique`（`store.py:299-300`）；grep 全仓无 EXPIRED 状态或 txid 释放逻辑；`facai/ops/tron_sync.py:207,234-236,249`（过期 PENDING 窗口校验永假且被 continue 跳过）
- **问题**：5 分钟支付窗口（`api.py:1148`）+ 15 分钟宽限过后到账的真实转账：同 txid 无法 attach 到新订单（HTTP 409），match_recharges 永不 auto_approve，transfer 停留 UNMATCHED、预算不入账；管理员 REJECT 旧单也不释放 txid。过期 PENDING 还永久占据 limit=500 最老优先扫描队列头。
- **证据（实测）**：R1 attach 后过期 → R2 attach 同 txid → `ValueError: txid already attached to another recharge`；两轮 match_recharges 对 R1/R2/同额新单 R3 均 `auto_approved=[]`；对照组（宽限内到账）可自动入账；管理员 approve 旧单可行（唯一出路），reject 后 R2 仍报同错。
- **修法**：过期 PENDING 结转为 EXPIRED 并释放 txid（或允许同用户改挂新单），chain-sync 据此停止等待。

### MED-3 同屏三种互相矛盾的计价口径：/pricing $400 vs PRICING $95/$218 vs 硬编码 10 倍「倍率估算」

- **级别/状态/来源**：MEDIUM · verified · 计费预算(med) + 产品验收(med) 双路命中
- **位置**：`facai/api.py:909-920`（/pricing 默认 400，docstring 自称 "used by the console's billing panel"）对照 `facai/billing.py:24-27`（PRICING={95,218}，注释 "so two screens can never quote different rates"）；`facai/static/index.html:651-655`（billingMultiplier fallback=10 且输入框 hidden）、`:736/:743/:755-763`（两面板相邻渲染）
- **问题**：任务「工作量与费用」按 $400/百万输出折算，「我的账单」与 estimated_cost 按 $95/$218，相邻又按 10×台账成本显示「倍率估算」；预算闸门实际按原始 cost_usd，与三个展示数均不同口径。设 `FACAI_PRICE_OUTPUT_USD_PER_MTOK=123` 只改 /pricing 不改 /billing/me，两口径无法靠部署配置对齐（deploy/、packaging/、docs/ 均未设置该 env）。
- **证据（实测）**：同 app 实例：`GET /pricing` → `output_usd_per_mtok=400.0`；`GET /billing/me` → `pricing={95.0, 218.0}`，`SAME RATE? False`。`python -m pytest tests/test_billing.py -q` → 10 passed（95/218 是被断言的冻结契约）；tests/test_auto_mission.py 反而断言默认 400，把分叉固化。
- **修法**：/pricing 改返回 billing.PRICING（或同一 env），前端统一取数；账单面板移除 10× 倍率估算行，统一一种口径。

### MED-4 带任意非 facai_ Authorization 头的请求每次记一次 per-IP 登录失败，5 次把整个出口 IP（含正确凭证与登录表单）锁进 429，且锁不定时过期

- **级别/状态/来源**：MEDIUM · verified · 客户端API(med)
- **位置**：`facai/api.py:579-581`（Basic 分支 else：只要有 authorization 头就 `record_login_failure(client_ip)`）；`api.py:565-566`（is_locked 先于凭证校验回 429）；`facai/auth.py:212-224`（bearer_api_key 只认 facai_ 前缀）、`auth.py:254-284`（默认 5 次/300s，is_locked 同时闸住 Basic 与 /auth/login）；`store.py:1068-1082`（按 IP 单键计数，陈旧重置只在 record_login_failure 内）
- **问题**：第三方工具常见的 `Bearer sk-...` 打错头即计入失败；NAT/办公网下一台配置错误的客户端可把全公司（包括站长）锁在门外——且复核实测比原发现更重：锁死期间两条记录路径都被 429 拦在前面，陈旧重置永不触发，等 32 秒后仍 429，需手工清库才解锁（login_attempts 落盘、全库无清理，重启也不清）。该逻辑由上一轮 9a2ec5f 引入，非本 sprint 新增，但本 sprint 的脚本客户端面放大了触发概率。
- **证据（实测）**：5 次 `Bearer sk-openai-style` → 401×5 后：正确 Basic GET→429、正确账密 `POST /auth/login`→429 "Too many failed attempts"；对照：7 次错误 facai_ key 或 7 次无头请求后正确 Basic 仍 200（API key 分支不计失败，设计正确）；FACAI_LOGIN_WINDOW=30 下等 32 秒仍 429。
- **修法**：仅对 Basic 凭证验证失败（解析出用户名但密码错）才 record_login_failure；外来 scheme 只回 401 不计失败；is_locked 读取时做陈旧判断。

### MED-5 worktree（并行分支）运行产生的日志 artifact 永远 404，合并成功后文件被删，产物中心留下永久悬空记录

- **级别/状态/来源**：MEDIUM · verified · 任务生命周期(med)
- **位置**：`facai/execution.py:106-119`（_persist_output 写到 `result["workspace"]/.facai/`，worktree 运行时即 worktree 路径）；`facai/store.py:651-653`（add_artifact 只校验在 root/workspaces 下）；`facai/api.py:1503-1506`（artifact_content 要求共享工作区出现在 `path.parents`）；`facai/checkpoint.py:13,59-61,145-159`（.gitignore 含 `.facai/`；合并成功 `remove_worktree --force` 删目录）；`api.py:1491-1495`（recent_artifacts 无文件存在性过滤）
- **问题**：并行分支是默认路径（DEFAULT_CONCURRENCY=2、MAX_PARALLEL_PER_MISSION=3、`scheduler.py:92-112` 对任一并行 step 建 worktree），真实用户跑并行任务必触发：日志 GET 永远 404（父链不含共享工作区），合并后文件物理消失，产物中心行永久悬空、点击必 404；grep 全包无任何 artifacts 清理逻辑。唯一缓解：stdout/stderr 行同时进 mission 事件流（有限条数、截断）。
- **证据（实测）**：真实 create_app + 三步 DAG 走完整生产流程：`scheduler run: 200 {'executed': 3}`、WORKTREE_CREATED/MERGED 各 2，合并后 worktree 目录 `exists: False`；产物中心 3 行中两条 worktree 日志 GET→404；worktree 存活期对照实验：同内容放共享区 GET→200，证明 404 由 `api.py:1505` 父链校验直接导致。
- **修法**：worktree 运行时把日志持久化到共享 mission 工作区（或合并前拷回 base）；或 artifact_content 放行同 mission 的 worktree 路径并在 remove_worktree 前迁移；至少在产物中心过滤/标记失效行。

### MED-6 合并冲突残留的 worktree 与分支使重试的 prepare_worktree 必然失败（同名分支已存在）→ 重试静默退回共享工作区，隔离被无声丢弃

- **级别/状态/来源**：MEDIUM · verified · 任务生命周期(med)
- **位置**：`facai/execution.py:210-217`（冲突路径刻意保留 worktree "for inspection"，全仓无任何清理/复用机制）；`facai/checkpoint.py:124-130`（prepare_worktree 撞名 return None，无清理无日志）；`facai/scheduler.py:164-177`（None 时静默用共享工作区，无事件无日志）；后缀=step_id[:8]（`scheduler.py:168-169`）+ requeue 复用同一 job 行（`store.py:1016-1019`）→ 每次重试必然撞同名
- **问题**：隔离降级无任何信号；降级任务的写入与 checkpoint（`git add -A`/commit）不受门控，与仍在 RUNNING 的同级分支待执行 merge 竞态（_MERGE_LOCK 只串行化 merge 不串行化 agent 写入）；两个冲突步骤同批重试时两个 agent 同时写共享树；残留 worktree/分支永久累积占盘。
- **证据（实测，真实 git 2.55）**：制造 CONFLICT 后 worktree 目录仍在、分支仍在；同后缀再次 prepare_worktree→None（raw `git worktree add -b` rc=255 "branch already exists"）；不同后缀成功，证明失败特定于重试必然复用的后缀。`py -3.12 -m pytest tests/test_parallel_isolation.py -q` → 8 passed（覆盖冲突保留与 fallback，但无残留→重试撞名路径）。
- **修法**：prepare_worktree 撞名时先 remove_worktree 清残留（或用 attempt 序号作后缀）；至少退回共享区时写 WORKTREE_FALLBACK 事件，并在同级分支仍 RUNNING 时推迟重试。

### MED-7 预算卡住的渠道任务被微信 monitor 每 0.2s 重复追加 DISPATCH_DEFERRED 事件，无限洪水直到月底/提额

- **级别/状态/来源**：MEDIUM · verified · 渠道功能(med)
- **位置**：`facai/api.py:1957`（wechat_monitor 每轮 `_retry_deferred_dispatches`）→ `api.py:1940`（无条件 ensure_channel_dispatch）→ `api.py:1616-1621`（门不通过即无条件 append，无去重/状态标记）；`facai/store.py:1682-1689`（LEFT JOIN jobs 为空选中无 job 任务，预算卡住任务每轮重新入选）；轮询间隔 `api.py:421` 默认 0.2s；`store.py:520-523` events() 无 LIMIT 全量读取
- **问题**：每条卡住消息每天在 events 表写入数十万行（默认 0.2s ≈ 5 条/秒 ≈ 43 万条/天），无限增长；/events 与控制台任务页全量读取随行数线性劣化。
- **证据（实测）**：mock iLink、FACAI_WECHAT_POLL_INTERVAL=0.05：monitor 空转 1.5~2 秒后该任务事件 = {MISSION_CREATED:1, PROMPT_CONVERTED:1, DISPATCH_DEFERRED:24~30}，再等 1 秒增至 45；对照 channel_events 仅各 1 条（洪水特定于 mission events 表）。无按 mission+reason 查重、无 deferred 状态标记；tests/test_budget_limits.py:281 仅断言至少 1 条。
- **修法**：追加前查同 mission 是否已存在未解决的同 reason 事件（或在 mission 上加 deferred 状态标记、只在状态翻转时记事件）。

### MED-8 微信凭据失效（token 过期/解绑）后控制台无重扫码/解绑入口：登录端点永久短路返回 AUTHORIZED，全仓无清凭据端点，状态页还报一切就绪

- **级别/状态/来源**：MEDIUM · verified · 渠道功能(med)
- **位置**：`facai/api.py:2034-2043`（`if provider.configured(): return {"state":"AUTHORIZED"}`）；`facai/channels/base.py:141-142` + `wechat.py:126`（configured() 仅查 env 存在性，从不校验上游）；`wechat.py:420-421`（ret!=0 → FAILED/UPSTREAM_REJECTED）；`api.py:1963-1966` + `api.py:422`（监控仅 3s backoff 死循环，无失败上报）；grep 全仓无 clear/reset/logout 凭据路由；`index.html:1154-1160`（AUTHORIZED 即提示已授权并关窗）
- **问题**：token 失效后所有收发持续失败，唯一扫码入口永久返回 AUTHORIZED，GET login 返回 NOT_STARTED（死路），控制台 wechat 条目仍显示 configured:true/send_ready:true「可收发消息」；管理员必须 SSH 删凭据文件并重启（运维文档 docs/SESSION_2026-10-03_*.md:28-29 证实此为唯一流程，生产还有 /etc 与 data 两处副本）。入站消息有持久化重放缓冲，无数据丢失。
- **证据（实测）**：预置含过期 token 的 wechat.env + 假 iLink 全请求返回 `{"ret":-14}`：POST login→200 AUTHORIZED 且上游命中 0 次（无轻量校验）；GET login→200 NOT_STARTED；监控 8 秒 3 次被拒后 configured 仍 True、文件仍在；GET /channels 仍报 verify_ready:true/send_ready:true。
- **修法**：增加 admin-only 解除授权端点（删 wechat.env 并清 provider env，注意 /etc 副本）；或 configured 时对上游做轻量校验、失败允许重走扫码。

### MED-9 前端 IS_ADMIN 硬编码 `username==='admin'`：按文档配置 FACAI_ADMIN_USER 改名后，真实站长的控制台隐藏全部管理面板（含微信扫码入口），而后端实际放行（三路命中）

- **级别/状态/来源**：MEDIUM · verified · 渠道功能(med) + 产品验收(med)；认证会话另有 LOW unconfirmed 记录
- **位置**：`facai/static/index.html:1723`（`IS_ADMIN=(!s.auth_required)||((s.username||'admin')==='admin')`，全文件唯一赋值点）、`:1725-1730`（非管理员 remove channel-panel/n8n-panel/daily-progress-panel/ops-brief-panel/registry-panel/reconciliation-panel）、`:880`（renderAdminPending 同判定）；对照 `facai/auth.py:125-126`（`os.getenv("FACAI_ADMIN_USER", "admin")`）、`api.py:498-503`（require_admin_owner 按 auth.username 放行）、`api.py:639-649`（/auth/session 无 is_admin 字段）；文档化配置见 `deploy/facai.env.example:13`、`docs/CLIENT_DELIVERY.md:7-8`
- **问题**：改名后站长浏览器端看不到渠道/充值审核/重置密码/看板/对账等全部管理功能（后端 API 全部 200 可用），前端 UI 与后端权限判定割裂；默认 admin 配置不受影响。
- **证据（实测）**：`FACAI_ADMIN_USER=boss` 起真实 uvicorn：boss 登录 200、GET /channels 与 /billing/recharges?status=PENDING 均 200（对照租户 403，证明守卫真实）；session payload 无 is_admin/role；按 1723 行表达式 IS_ADMIN=False → 面板被删、各 load 跳过。
- **修法**：/auth/session 返回服务端判定的 is_admin（复用 auth.username 比较），前端 1723/880 改用该字段。

### MED-10 SSE 断线自动重连后服务端从旧 after 全量重放（不读 Last-Event-ID），前端逐条 push 无去重：任务面板调用次数/token/成本整体翻倍（双路命中）

- **级别/状态/来源**：MEDIUM · verified · 前端功能(med) + 产品验收(med)；产品验收路另记录 UI_SPEC.md:23 承诺的「断开/重连中」状态提示完全缺失（grep onerror/reconnect 零匹配）
- **位置**：`facai/api.py:1031-1043`（stream 签名无 Request 对象，只能读 query after；`store.py:520-523` events() 按 sequence>after 全量回放）；`facai/static/index.html:1643`（EventSource 把打开时游标写死进 URL）、`:1644`（push 无 sequence 判断）
- **问题**：每次重连重复追加已收事件，billingMetrics 求和（`index.html:677-704`）随之成倍虚增并渲染进任务头部与阶段卡；生产 nginx SSE `proxy_read_timeout 3600s`（deploy/nginx/facai-cloud.conf:69-79），任务开着超 1 小时或任何网络抖动/服务重启必触发；重复持续到重新打开任务。仅影响控制台展示（客户端派生），服务端账本与 /billing/me 不受影响。
- **证据（实测）**：真实 uvicorn：首连 ?after=0 收 seq[1,2,3] 且每条带 `id:` 行；模拟 EventSource 原生重连（同 URL + `Last-Event-ID: 2/3`）→ 服务端原样重发全部已投递事件（`server_respected_last_event_id: false`）；?after=3 → 0 条旧事件。node 执行真实 billingMetrics：重放前 {calls:1, input:1000, cost:$0.05} → 重放后 {calls:2, input:2000, cost:$0.1}。
- **修法**：前端 push 前按 event.sequence 去重；或 stream 端点解析 Last-Event-ID 头作为游标；补 onerror 断连状态提示（对齐 UI_SPEC.md:23）。

### MED-11 充值记录行 data-txid 空值恒输出属性，`[data-rid][data-txid]` 存在性选择器匹配全部行：只要任有充值记录就每 10 秒对全部行（含已到账/已驳回）发 /match 请求且永不停止

- **级别/状态/来源**：MEDIUM · verified · 前端功能(med)
- **位置**：`facai/static/index.html:944`（`data-txid="${r.txid?1:''}"` 无 txid 输出空属性）、`:890/:958`（存在性选择器）、`:892-894`（先发请求再判断）、`:959`（hasPending 恒真 → setInterval 10s 无终态停轮，无 visibilitychange；rechargeMatchTimer 仅 3 处引用）
- **问题**：任何有 ≥1 条充值记录的用户在整个页面会话内每 10s 轮询全部行；附过 txid 的终态行还反复触发服务端 `store.chain_transfer(txid)` 链上查询（`api.py:1197`）。
- **证据（实测）**：node 逐字执行 944 行模板：无 txid 行输出 `data-txid=""`；lxml 以存在性谓词 `//*[@data-rid][@data-txid]`（CSS `[attr]` 标准语义）解析：3/3 行命中、2 行属性值为空。
- **修法**：条件输出属性（按 matchLine 同款条件 `r.txid && r.status==='PENDING'`），或选择器改用 `[data-txid="1"]` 并配合条件输出。

### MED-12 「我的账单（本月）」面板同时渲染进侧栏与 #detail 两处：同一页面两份 `id="my-billing-title"`，违反 id 唯一性，客户视图也见两块重复账单卡

- **级别/状态/来源**：MEDIUM · verified · 前端功能(med)
- **位置**：`facai/static/index.html:738`（billingSummary() 硬编码 id，唯一生成点）、`:722-725`（写入侧栏 #billing-summary-slot，boot 无条件执行 :1744）、`:1551`（renderDetail 无守卫再次内插）→ `:1628/1642` 写入 #detail；boot 默认自动打开第一个任务（`:1758-1759`）→ 重复是默认页面状态；client-view CSS（`:264-277`）不隐藏 .billing-panel
- **问题**：两份内容一旦分叉即错配，任何按该 id 的 DOM 查询只命中侧栏副本；两处 aria-labelledby 都解析到侧栏那份（复核措辞修正：两份 h3 文本相同时读屏播报的可访问名仍是正确字符串，实际危害是 id 唯一性破坏 + 两张完全重复的账单卡）。
- **证据（实测）**：node 抽取真实 formatMoney/billingSummary 源码按应用真实方式两处插入：`id="my-billing-title"` 出现 2 次、aria-labelledby 2 次、billing-panel my-billing 2 份。
- **修法**：内嵌副本去 id/aria-labelledby 或改唯一 id（如 my-billing-title-detail）；或 detail 内只保留 billingPanel。

### MED-13 任务列表 #missions 与能力注册 #registries 的骨架屏在请求失败后永不消失（无失败态、无重试入口，违背本代码库自身约定）

- **级别/状态/来源**：MEDIUM · verified · 前端功能(med)
- **位置**：`facai/static/index.html:1265`（refresh() catch 仅 `err(e);return []`，1261 行是唯一写 #missions 处）、`:787`（loadRegistries 同型）；对照 `:806`（loadApiKeys 失败正确替换 '<p class="empty">API Key 加载失败</p>'，loadMyModels/loadInbox/loadArtifactsCenter/loadN8n/loadDailyProgress 同）；boot 时 SSE 未建立 → refresh 无重触发源、mission-picker 为空、无替代入口（唯一 setInterval :1249 只轮渠道）
- **问题**：后端 5xx/网络抖动后主任务列表无限转圈，#error 有文本但面板内无重试入口，用户只能整页刷新；#registries 仅管理员可见，#missions 对所有用户可见。
- **证据（实测）**：node stub DOM + 抛错 fetch 执行真实函数：`#missions still skeleton? true`、`#registries still skeleton? true`；对照组 loadApiKeys → `false`。
- **修法**：两个 catch 里同样写入失败态并绑定重试，与 loadApiKeys 对齐。

### MED-14 五节点流水线条 execute 节点用 `steps.some(FAILED)` 判定：并行任务单步永久失败（其余仍 RUNNING）时显示红✕，与「运行中」label 自相矛盾，客户误以为整个任务失败

- **级别/状态/来源**：MEDIUM · verified · 前端功能(med)
- **位置**：`facai/static/index.html:1790`（`const failed=steps.some(s=>s.status==='FAILED')`）、`:1798`（failed 判定置于 running 之前）；混合状态真实可达：finish_job 裸 SQL 置步骤 FAILED（`store.py:922`）而 mission 状态仅在 attempts≥max_attempts 时经 _refresh_blocked_jobs 重算（`store.py:929`）
- **问题**：触发窗口为并行分支 + 非瞬态永久失败 + 剩余重试额度，持续到兄弟步骤结束（分钟级）；标准失败路径（step_status → `store.py:558-560` mission 即 FAILED）无此窗口。复核措辞修正：整条 strip 的 data-state 只取 mission.status（`:1800`），实测保持 live 不变——是 execute 单节点误示红✕，非整条误报失败。
- **证据（实测）**：node 执行真实 driver：S8（steps=[COMPLETED,FAILED,RUNNING]，mission RUNNING）→ `execute:"failed", stripState:"live", label:"任务流水线 · 运行中"`；真实 Store 并行 claim 后对步骤 B finish_job('FAILED')（attempts=1<3）→ `mission.status=RUNNING`、steps=[RUNNING,FAILED]，混合状态坐实。
- **修法**：execute 节点 failed 判定改为 `mission.status==='FAILED'`，或 FAILED 且仍有 RUNNING 步骤时显示 active/partial。

### MED-15 前端每条 SSE 事件触发全量 /missions 重取并重渲染整个侧栏：活动任务高频出事件时网络/服务端/渲染成本被事件数放大

- **级别/状态/来源**：MEDIUM · verified · 性能复查(med)
- **位置**：`facai/static/index.html:1644`（`addEventListener('mission', … rerender(); refresh()`，无任何节流）→ `refresh()` 首行 `request('/missions')`（`:1257-1263`）；`facai/store.py:400-449`（list() SELECT * 全列 + 7 组子查询）；`execution.py:264-269`（流式执行每行输出一条事件，OUTPUT_LINE_LIMIT=200）；全包无 GZip/ETag/Cache-Control，无 /missions 限流
- **问题**：任务详情页是监控运行中任务的标准用法，边跑边看时每行输出触发一次全列表拉取 + 两侧全量 DOM 重建；一个流式步骤可产生 200+ 条事件。
- **证据（实测）**：`py -3.12 measure_missions_payload.py`（临时目录、真实 Store）：200 任务 payload = 1,201,691 bytes（realistic）/ 505,491（去 prompt 列）/ 244,491（裸任务），原 finding 的 535KB 落在区间中部；每步突发 200 事件 ≈ 重取 47MB~230MB，1000 事件 ≈ 239MB–1.1GB 量级。
- **修法**：事件回调只 rerender 当前任务详情，列表刷新节流（≤1 次/秒）或仅原位更新对应任务卡。

### MED-16 宣传 chips「微信 / Telegram / WhatsApp 随时派活」对注册客户误导：渠道是站长私人接入，租户访问渠道接口一律 403，旅程中不存在该入口

- **级别/状态/来源**：MEDIUM · verified · 产品验收(med)
- **位置**：`facai/static/index.html:422`（capability-band 静态 HTML，无 JS 隐藏）、`facai/static/login.html:60,68`；对照 `HANDOFF-NEXT.md:148`（明文：渠道是站长私人入口，"别把站长渠道当成'平台渠道'宣传或共享"）、`api.py:1561-1575` + `api.py:498-503`（渠道 GET 均要求 admin，实测租户 403）；前端 IS_ADMIN 隐藏模式（`index.html:1725-1729`）唯独漏掉静态 chips
- **问题**：每个自注册租户登录即看到「随时派活」，但控制台无渠道面板、API 403、无自助接入，按宣传找不到入口且被误导。属实现遗漏而非设计意图（fix 可直接复用 IS_ADMIN 条件）。
- **证据（实测）**：TestClient：注册 buyercheck → 登录 → `GET /channels` → 403 {"detail":"仅管理员可访问"}（/channels/telegram、/channels/telegram/messages 同）；admin → 200；`grep -rn my/channels facai/` 无结果。
- **修法**：客户端视图按 IS_ADMIN 条件渲染渠道 chips，或改文案为「管理员渠道派活，客户走网页/API」。

### MED-17 docs/CLIENT_DELIVERY.md 三处声称「应用没有 API 调用预算、模型消费硬上限」，与已实现并实测返回 402 的预算硬门完全相反

- **级别/状态/来源**：MEDIUM · verified · 产品验收(med)
- **位置**：`docs/CLIENT_DELIVERY.md:10,15,57`（否认存在硬上限）对照 `facai/billing.py:92-110`（budget_gate）、`facai/api.py:781-786` 等 5 处 402 门；git 佐证：文档最后变更在基线导入 79b29eb，预算功能在更晚的 38fa461 落地，文档未随功能更新
- **问题**：上限可由租户自助设置（PUT /billing/users/{u}/budget）或运营方 env 设置，/billing/me 与 /pricing 均向用户暴露；客户按文档预期「绝不拦截」却会在配置后建单被 402 拒且文档无说明。全新未配置实例不拦人（默认不限额，tests/test_budget_limits.py::test_zero_limit_never_blocks）。
- **证据（实测）**：设上限 $200、add_cost $250 后：`POST /missions` → 402 {'detail': '预算已用尽：本月已消耗 $250.00 / 上限 $200.00'}（/missions/auto、/billing/check 同）；`python -m pytest tests/test_budget_limits.py -q` → 15 passed。
- **修法**：更新 CLIENT_DELIVERY.md「费用与用量」章节：写明月度上限、402 拦截、USDT 充值入账规则。

### MED-18 224c201 对 facai-backup/facai-digest 单元的安全加固（NoNewPrivileges/ProtectSystem=strict/ReadWritePaths）在部署链上没有任何一步会安装生效，服务器继续跑旧单元

- **级别/状态/来源**：MEDIUM · verified · 部署运维(med)
- **位置**：`deploy/facai-backup.service:15-18`、`deploy/facai-digest.service:22-25`（加固仅在仓库内；`git diff e0177eb..224c201` 确认）；`deploy/deploy.sh:273-278`（install_systemd 只装 facai-cloud.service；`:342-344` update 自述要重装 unit "so changes are not silently left behind" 却未贯彻）；`../deploy-now.py:28,134`（仅 restart 三个服务）；全仓 grep '/etc/systemd/system' 仅 deploy.sh:275 一处自动化安装
- **问题**：服务器按 2026-10-03 手工初装的旧单元继续运行，加固静默不生效（属纵深防御缺口而非直接可利用面——单元本就以非特权用户运行）；现有测试测不出漂移（test_unattended_units_are_hardened 只断言仓库内文件、test_update_reinstalls 只断言调了 install_systemd）。无补救性人工步骤记录（DEPLOYMENT_RECORD.md grep 零命中）；上一轮审计 :171 已独立复核同一缺口。
- **证据（亲读）**：`grep -iE 'backup\.service|digest|tron|timer' deploy/deploy.sh` 零命中；deploy-now.py 两条路径均不写 /etc/systemd/system、不 daemon-reload。局限：无 SSH，未能远程验证服务器实际单元内容；但「部署链无传播步骤」为正向证实，不依赖服务器实况。
- **修法**：install/update 增加循环 `install -m 0644 deploy/facai-*.service|*.timer /etc/systemd/system/ && systemctl daemon-reload`，或让 deploy-now.py 显式执行。

---

## 三、待观察（unconfirmed，37 条）

> 以下为各路原始发现，本轮复核未做实测复现/证实，**不代表不成立**；多数为 LOW。标 ⚠ 的一条为复核后反驳。需要真机/外网验证的已注明。

### 认证会话（4）

1. `facai/api.py:541` + `facai/auth.py:372` — **LOW**：verify_api_key 在作用域检查之前就 touch last_used_at，被 403 拒绝的越权请求也刷新「最近使用」，误导 key 持有者。修法：touch 挪到 api_key_allowed 通过之后。
2. `facai/api.py:1131-1135` — **LOW**：签 key 上限先查后插（check-then-insert），并发理论上可突破每账号 20 个；SQLite 写串行化收窄窗口，6 线程实测未复现溢出（终态恰 20），属理论缺陷。
3. `facai/static/index.html:1697-1702`（服务端 `facai/auth.py:226-227`）— **LOW**：改密成功后服务端立即吊销全部会话，前端只显示「密码已更新」不提示需重新登录，用户下次操作才被 401 弹回 /login。修法：成功回调直接提示并跳转 /login。
4. `facai/static/index.html:1723` — **LOW**：IS_ADMIN 硬编码的原始记录（含 `(s.username||'admin')` 在 username 缺失时误判站长）。已并入必修 MED-9。

### 任务生命周期（3）

5. `facai/execution.py:89-103` — **LOW**：用户自备模型的 opencode.json 在 worktree 运行时逃过 git 排除（linked worktree 的 .git 是文件，`execution.py:94` mkdir 抛 NotADirectoryError 被吞）被提交进分支并合并进共享历史（含私有 endpoint URL）；实测无明文 key 泄漏。修法：用 `$GIT_DIR/info/exclude` 或把配置写到工作区外经 OPENCODE_CONFIG 传入。
6. `facai/store.py:687` 对比 `store.py:441-445` — **LOW**：文档承诺「RUNNING 步骤 checkpoint 提交后显示 90%」不可达：add_checkpoint 写 GIT_CHECKPOINT 事件永远 step_id=None，而 90% 判定要求 step_id 非空 → RUNNING 步骤进度恒 1%（仅显示问题，不影响完成判定）。修法：add_checkpoint 增加 step_id 参数。
7. `facai/scheduler.py:250-252` 对比 `facai/store.py:840-842` — **LOW**：requeue_expired 并非注释所称「每秒运行」：run_once 用 pool.map 阻塞到 batch 结束，恢复循环在 batch 期间被饿死（实测两个串行 1s fake runner 的 run_once 耗时 2.14s）；timeout=3600 的 batch 进行中，崩溃 worker 的任务最长等整个 batch 才被恢复。单进程不双跑正是靠这个饿死「意外」保证（与 HIGH-4 关联）。修法：recover 拆为独立定时线程。

### 计费预算（3）

8. `facai/billing.py:30-40,57-60` vs `facai/store.py:696-698` — **LOW**：estimated_cost 对「只报输入、输出未知」的行按输出=0 计价且 conservative=False（宣称精确），与 add_cost「NULL 必须按未上报而非零读」契约矛盾；实测单侧上报 → {usd:95.0, conservative:False}。修法：未知侧保守计价或置 conservative=True。
9. `facai/opencode.py:70-71` → `facai/store.py:691-724` — **LOW**：NaN/inf 成本（与必修 HIGH-6 同源，此为计费路的原始记录）。
10. `facai/api.py:2149-2153` — **LOW**：预算拦截（消息已入库、任务已建）对 relay/telegram bridge 回 503，会触发上游按重试策略无休止重投直到预算恢复；语义上应是已受理的 202。修法：BUDGET_EXCEEDED 的 DEFERRED 回 202/200，仅 RETRY/瞬态保留 503。

### 渠道功能（5）

11. `facai/api.py:940-943`（对照 `api.py:2054`）— **LOW**：预算 DEFERRED 中文指引与 send_channel_ack 经微信发送时不带 context_token，若 iLink 要求会话内回复携带该令牌则会被拒；mock sendmessage 实测 ctx: None。**需真机验证**（本环境无法连 iLink）。
12. `facai/channels/relay.py:98-100` — **LOW**：relay HMAC 签名在 300s 时间戳窗口内可重放（无 nonce/去重缓存）；缓解：入站按 external_id 幂等去重不会重复建单，TLS 内网部署下风险有限。
13. `facai/api.py:2019`（对照 `api.py:1735`）— **LOW**：控制台手动派发端点调 convert_and_link_mission 未传 owner_id、依赖默认 "admin"，与消息查找的 owner 隔离不对称；当前因所有入站消息归属 admin 而不可达，属潜在归属错位。
14. `facai/notify.py:130-133` — **LOW**：notify 非多渠道 fanout：每个 mission 只回复 NOTIFY_CHANNELS 顺序中第一个有 sender 的渠道（与模块注释语义一致，按设计报告）；实测双渠道来源任务只送达第一个。
15. `tests/test_channel_conversion_claim.py::test_concurrent_duplicate_webhook_deliveries_call_model_once` — **LOW**：并发去重用例在多文件同跑时出现过一次超时失败，单独与整批复跑均通过，判环境性 flaky（非产品缺陷）。

### 客户端API（2）

16. `facai/api.py:205-236` — **LOW**：精确路径作用域 /api/v1/billing/me 不匹配尾斜杠变体 /api/v1/billing/me/（带 key 得 403 而非 FastAPI 的 307 规范化重定向）；前缀类路径不受影响。修法：匹配前对 path 做规范化。
17. `facai/api.py:1131-1135` — **LOW**：与认证会话#2 同源（签 key 上限竞态），代码层推断。

### 前端功能（4）

18. `facai/static/index.html:1659`（配合 `:1781`）— **LOW**：任务选择器选回「选择 Mission…」只清空 #detail 不清全局 current，五节点条卡在上一个任务状态（live 动画持续）。修法：空值分支补 `current=null; source.close()`。
19. `facai/static/index.html:1796-1797` — **LOW**：手动模式新建 mission（WAITING、无事件）使 compile 与 authorize 双 active 呼吸动画，与「only the active node animates」注释相悖；入队后链路本身正常。
20. `facai/static/index.html` 多处（1255/1261/793/831/968/1076-1077/1474/1570）— **LOW**：多处动态插值未过 escapeHtml（option value/data-* 属性），与同函数内用户可控字段全转义风格不一致；因这些 id 均为服务端 uuid4().hex（`store.py:383/730/1205`）暂无注入向量。修法：统一补转义助手，消除对服务端 id 格式的隐式依赖。
21. `facai/static/index.html:968` — **LOW**：管理员「待审核」块写成 `<details secondary open>`（缺 `class=`），details.secondary 样式不命中，该块失去卡片视觉（9a2ec5f 引入）。

### 数据迁移（1）

22. `facai/store.py:1807-1811` — **LOW**：运营日报 by_owner 的 missions 计数按 LEFT JOIN costs 后行数 COUNT(*) 统计，一个任务有几条 cost 行就翻几倍（实测 alice 1 任务+3 cost 行 → [('alice',3,6.0)] 而 missions_created=1）；前置遗留（a554cbb 引入，非本 sprint 回归）。修法：`COUNT(DISTINCT m.id)`。

### 部署运维（3）

23. `deploy/deploy.sh:5,40-41` — **LOW**：deploy.sh 自身无 SRC_DIR==APP_DIR 防护，头部用法注释仍示范 `sudo ./deploy/deploy.sh update`——在生产树上直接执行时 rsync 自拷贝空转却报 update complete（自动化主路径已由 deploy-now.py:110-118 staged 执行绕开，手动路径仍会踩）。
24. `../make-bundle.py:15` + `deploy/deploy.sh:131-138` — **LOW**：bundle 携带 bridge/whatsapp-relay 全部代码（实测 4 成员），但 rsync 白名单无 /bridge、relay 生产位置在 /opt/facai-whatsapp-relay——deploy-now.py 每次重启 facai-whatsapp-relay 却无任何步骤更新其代码，relay 侧修复永远不会被链路送达而 SUMMARY 可能全 PASS（链路假绿）。
25. `deploy/validate_config.sh:48-61` + `tests/test_deploy_config.py:64-69` — **LOW**：校验未覆盖本轮新元素（limit_req_zone/limit_req、CSP img-src data:、FACAI_BASIC_READONLY 均无断言）；本机无 nginx 时 nginx -t 段整体 SKIP（实测输出 "SKIP: nginx not installed"），limit_req_zone 若被误挪进 server{} 的上下文错误本机不被拦截。

### 性能复查（5，含 1 条已反驳）

26. ⚠ **已被复核反驳（refuted，不建议修）**：`facai/store.py:833` — 原判 MEDIUM：_refresh_blocked_jobs 每 tick 对每个含 QUEUED 任务的 mission 无条件 `UPDATE missions SET status=?`（实测 200 积压 1409 语句/tick、同值写成立）。但复核用真实 Store 连续 30 tick 测得 **WAL 增长 0 字节**，隔离对照：200 条同值 UPDATE 产生 0 字节、改值产生 4120 字节——SQLite 3.50.4 对内容不变的 UPDATE 直接跳过物理写入，「写放大」在存储层不发生；残余成本 ~0.7ms/s CPU。**原发现的观察可复现，但 MEDIUM 定级依据被直接 I/O 实测推翻**；「仅状态变化才写回」可作低成本代码卫生改进（模拟修复后语句 1404→1204，阻塞传播语义不变）。
27. `facai/store.py:858` — **LOW**：空队列时 claim_job 每 tick 也 BEGIN IMMEDIATE 取写锁（空库 idle tick 实测 4 连接/9 语句/9.9ms，含 BEGIN IMMEDIATE 空转），与 Web 层写入短暂串行。
28. `facai/scheduler.py:77` — **LOW**：recover_stale_channel_conversions 每秒至少跑 2 次（调度 tick + 微信轮询），EXPLAIN 走索引非全表扫，50k 行全 NULL 最坏 5.28ms/次 ≈ 10.6~31.7ms/s。
29. `facai/api.py:421` — **LOW**：微信轮询默认 0.2s（错误退避 3s），对 iLink 长轮询端点最高约 5 req/s 常驻。**仅代码证据**：本环境无法连接 iLink 实测上游行为。
30. `facai/store.py:863` — **LOW**：存在 QUEUED 积压时 claim 候选查询每 tick 全量重算且子查询逐行相关执行；20k jobs（约 2k QUEUED）实测 7.888ms/次（DROP 索引后 23.595ms），可接受；积压常超万级再优化。

### 产品验收（7）

31. `docs/UI_SPEC.md:8` 对照 `facai/static/index.html:440-441,1257-1266` — **LOW**：UI_SPEC 承诺「searchable mission cards」，实现只有下拉与平铺列表，无搜索/状态筛选。
32. `docs/UI_SPEC.md:12` 对照 `index.html:1498-1499` — **LOW**：事件时间线承诺「filterable by stage/type/severity」，实现为纯平铺列表。
33. `docs/UI_SPEC.md:9` 对照 `facai/api.py:1445-1456` — **LOW**：UI_SPEC 承诺 mission 级 pause/resume/cancel（where supported），实际只有 job 级四动作有端点与按钮。
34. `facai/api.py:982-1000` — **LOW**：用户自助下调月度上限的端点已实现（含 403 守卫），但控制台无任何表单调用——「实现了但用户找不到」。
35. `facai/static/index.html:1795` — **LOW**：纯网页创建的任务流水线条首节点「渠道」无条件绿✓（`set('channel','done')` 演示性取值），与「真实流水线（无模拟进度）」承诺相悖。
36. `tests/test_channel_conversion_claim.py` — **LOW**：声称的 707 passed 基线未复现：全量 `python -m pytest -q` = 1 failed（并发 webhook 用例）+ 706 passed，单独重跑 6 passed，属并发时序 flaky 而非功能回归。
37. `docs/UI_SPEC.md:7` 对照 `index.html:403` — **LOW**：顶栏要求展示「pending human actions」计数，实现只有进行中数量、健康状态与用户 pill。

---

## 四、分路详情

### 1. 认证会话 — ISSUES(1 high, 0 med, 4 low)

主链路基本扎实：37 个指定测试全过（`py -3.12 -m pytest tests/test_auth.py tests/test_client_api.py tests/test_registration.py -q` → 37 passed in 9.61s），全套 707 passed，py_compile 通过。must_change 的会话分支强制（读放行/写 403「请先修改密码」/改密后清标志并吊销全部会话）、FACAI_BASIC_READONLY 两态切换、每账号一次性 warning、api_keys 全生命周期（明文一次/仅存哈希/列表只出 prefix/吊销后 401/last_used_at 更新）均实测通过。唯一 HIGH 见必修 HIGH-1（must_change 只写会话分支，Basic/Bearer 完全绕过）。测试覆盖缺口：Basic/Bearer 分支的 must_change 行为零测试（正是 HIGH 藏身处），Basic 路径防爆破锁定也无测试（手动实测通过：3 次失败→429）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | api.py:540-587 | must_change 仅会话分支强制，Basic/Bearer 绕过（必修 HIGH-1） |
| LOW | unconfirmed | api.py:541 + auth.py:372 | 403 拒绝请求也刷新 last_used_at |
| LOW | unconfirmed | api.py:1131-1135 | 签 key 上限先查后插竞态 |
| LOW | unconfirmed | index.html:1697-1702 | 改密成功后前端不提示需重新登录 |
| LOW | unconfirmed | index.html:1723 | IS_ADMIN 硬编码 'admin'（并入 MED-9） |

### 2. 任务生命周期 — ISSUES(1 high, 2 med, 3 low)

租约算术本身正确（实测 timeout=1→600s、300→600s、3600→3900s；claim 请求≤0 按原样 taken；requeue_expired 三种情形行为符合设计），list() 批量预取重写后与 get() 逐字段完全一致（实测 dict 相等）。但 HIGH：并行分支租约 max(600,timeout+300) 不覆盖 OpenCode CLI 串行锁（默认开启）排队时间，timeout≥300 的并行分支租约必然在 worker 存活时过期，双调度进程下同一步骤被双重执行（双倍付费），job 以误导性的 'Invalid transition: COMPLETED -> COMPLETED' 收尾（必修 HIGH-4）。合并门两条 MEDIUM：worktree 产物永久 404/被删（MED-5）、冲突重试静默丢失隔离（MED-6）。测试全绿：test_scheduler.py 11 passed、test_opencode_execution+test_user_model_execution+test_checkpoints_and_cost 31 passed、test_parallel_isolation+test_deferred_redispatch+test_dispatch_heal 22 passed、py_compile 通过。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | opencode.py:107,211-215 + store.py:891-897,2141-2165 + scheduler.py:231-253 | 租约不含串行锁排队 → 存活任务被重复执行（必修 HIGH-4） |
| MEDIUM | verified | execution.py:107-119 + api.py:1503-1506 + checkpoint.py:157 | worktree 日志 artifact 永久 404/被删（必修 MED-5） |
| MEDIUM | verified | checkpoint.py:125-128 + execution.py:213-217 + scheduler.py:165-169 | 冲突残留使重试必然撞名 → 静默退回共享区丢隔离（必修 MED-6） |
| LOW | unconfirmed | execution.py:89-103 | opencode.json 逃过 git 排除进分支历史（无明文 key） |
| LOW | unconfirmed | store.py:687 | RUNNING 步骤 90% 里程碑不可达（恒 1%） |
| LOW | unconfirmed | scheduler.py:250-252 vs store.py:840-842 | requeue_expired 被 batch 饿死，非「每秒」 |

### 3. 计费预算 — ISSUES(1 high, 4 med, 3 low)

九路 sprint 计费侧主体功能经实测可用：31 项 billing/recharge/chain_sync、22 项 budget/credit-guard、27 项 cost/execution 测试全部通过（py -3.12）。充值链路新守卫（txid 窗口绑定、一对一消耗、双审互斥、负值钳零、对账口径）逐项验证正确。但预算门存在 HIGH 资金漏洞：门只比较账面 cost_usd 而 /model/complete 恒写 amount=0.0，实测 $100 上限下跑出 $12,240 估算花费仍全程放行（必修 HIGH-3）；另实测确认 resume/retry（必修 HIGH-2）与渠道 webhook 转换（必修 MED-1）两条漏网花钱路径，以及过期充值单永久占用 txid 的资金卡点（必修 MED-2）与 /pricing(400) 与 PRICING(218) 同屏两价（必修 MED-3）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | api.py:1539 + billing.py:105-106 | /model/complete 恒写 0 元，预算门失明（必修 HIGH-3） |
| MEDIUM | verified | api.py:1445-1456 + store.py:948-994 | resume/retry 绕过预算门（必修 HIGH-2，客户端API路判 HIGH） |
| MEDIUM | verified | api.py:1760 早于 1786/1616 | webhook 先转换（付费）后门禁（必修 MED-1） |
| MEDIUM | verified | store.py:1249-1258 | 过期 PENDING 永久占用 txid，资金卡死（必修 MED-2） |
| MEDIUM | verified | api.py:909-918 vs billing.py:27 | 双计价口径同屏（必修 MED-3） |
| LOW | unconfirmed | billing.py:30-40,57-60 | estimated_cost 单侧上报按 0 计价且 conservative=False |
| LOW | unconfirmed | opencode.py:70-71 → store.py:691-724 | NaN/inf cost（并入 HIGH-6） |
| LOW | unconfirmed | api.py:2149-2153 | 预算 DEFERRED 回 503 诱发 bridge 重试风暴 |

### 4. 渠道功能 — ISSUES(0 high, 3 med, 3 low；明细实列 3 med + 5 low)

渠道闭环（TG/WA/微信/relay 入站→建单→回复→通知）主体功能正确：TestClient+MockTransport 离线实测，admin 会话（cookie+X-CSRF-Token，即前端 request() 的身份）可正常调用全部 4 个微信端点，租户会话/客户端 API key/无 CSRF 均被 403 拒绝；relay HMAC 签名（错签/过期时间戳/错前缀均 403）与 outbox ack（ack 后 acked=0、不再重发）工作正常；预算超限的 DEFERRED 中文指引在 relay outbox、微信 sendmessage（mock）、telegram stub 三条路径均实测送达。3 个 MEDIUM：预算卡住任务的事件洪水（MED-7）、微信凭据失效后无重扫码入口（MED-8）、前端 IS_ADMIN 硬编码（MED-9）。测试：点名渠道测试全绿（127 passed；并发 claim 用例首轮多文件同跑失败过一次，单独与整批复跑均通过，判环境性 flaky）。未做真实 iLink/Graph/Telegram 网络验证（离线环境）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| MEDIUM | verified | api.py:1616-1621（触发链 1957→1940） | DISPATCH_DEFERRED 每 0.2s 重复追加，~43 万条/天（必修 MED-7） |
| MEDIUM | verified | api.py:2038-2039 + wechat.py:420-421 | 凭据失效后无重扫码/解绑入口（必修 MED-8） |
| MEDIUM | verified | index.html:1723 对照 auth.py:126 | IS_ADMIN 硬编码，改名后站长丢管理面板（必修 MED-9） |
| LOW | unconfirmed | api.py:940-943 | 预算指引/ack 不带 context_token（需真机验证） |
| LOW | unconfirmed | relay.py:98-100 | HMAC 300s 窗口内可重放（幂等去重缓解） |
| LOW | unconfirmed | api.py:2019 vs 1735 | 手动派发未传 owner_id（当前不可达） |
| LOW | unconfirmed | notify.py:130-133 | 通知非多渠道 fanout（按设计报告） |
| LOW | unconfirmed | test_channel_conversion_claim.py | 并发用例环境性 flaky |

### 5. 客户端API — ISSUES(1 high, 2 med, 2 low)

实测基础：`py -3.12 -m pytest tests/test_client_api.py -q` → 8 passed in 4.11s（系统默认 python 无 pytest）；另在临时目录补写 7 个探针（7 passed）覆盖仓库用例缺口。核心机制扎实：Bearer 分支 CSRF 豁免正确、吊销原子且下次请求即 401、列表永不泄露 key_hash、明文只返回一次、面板 JS 字段与 /my/keys 完全一致、作用域外一律 403。HIGH：retry/resume 绕过预算硬门（必修 HIGH-2）；MEDIUM：must_change 会话分支外绕过（必修 HIGH-1）、外来 Bearer 头锁整 IP 且锁不定时过期（必修 MED-4）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | api.py:1445-1456 | retry/resume 绕过预算硬门，无限循环（必修 HIGH-2） |
| MEDIUM | verified | api.py:546-577 | must_change 仅会话分支强制（必修 HIGH-1） |
| MEDIUM | verified | api.py:580-581 | 外来 Bearer 头 5 次锁整 IP，锁不定时过期需清库（必修 MED-4） |
| LOW | unconfirmed | api.py:205-236 | /billing/me/ 尾斜杠 403 |
| LOW | unconfirmed | api.py:1131-1135 | 签 key 上限竞态（代码层推断） |

### 6. 前端功能 — ISSUES(0 high, 5 med, 4 low)

UI v1.3 主链路基本健康：node --check 两个 script 块通过；前端引用的 keys/recharge/match/billing.me/session/pricing 字段与后端一一对应（pytest tests/test_client_api.py tests/test_recharge.py = 16 passed）；充值 txid 输入→POST /tx→提示→10s 轮询→到账就地刷新链路完整；用户可控文本全部经 escapeHtml，未发现可利用 XSS；sw.js network-first 不缓存旧控制台。5 个 MEDIUM：SSE 重放指标翻倍（MED-10）、充值轮询恒真（MED-11）、账单双渲染重复 id（MED-12）、骨架屏失败永不消失（MED-13）、流水线条单步失败误报（MED-14）。实际 HEAD 为 b716135（224c201 之上的纯静态 showcase 提交，已单独检查无脚本风险；见头部 HEAD 说明）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| MEDIUM | verified | index.html:1644 + api.py:1031-1043 | SSE 重连全量重放，指标翻倍（必修 MED-10） |
| MEDIUM | verified | index.html:944（选择器 890/958） | data-txid 空属性 → 恒真 10s 轮询全部行（必修 MED-11） |
| MEDIUM | verified | index.html:725 与 1551（模板 738） | 账单面板双渲染，重复 id（必修 MED-12） |
| MEDIUM | verified | index.html:1265 与 787 | 骨架屏失败永不消失（必修 MED-13） |
| MEDIUM | verified | index.html:1798 | 单步失败时 execute 节点误报红✕（必修 MED-14） |
| LOW | unconfirmed | index.html:1659/1781 | picker 空值不清 current，流水线条卡旧态 |
| LOW | unconfirmed | index.html:1796-1797 | 手动 mission 双 active 节点 |
| LOW | unconfirmed | index.html 多处 | 动态 id 插值未 escapeHtml（暂无注入向量） |
| LOW | unconfirmed | index.html:968 | `<details secondary open>` 缺 class= |

### 7. 数据迁移 — ISSUES(2 high, 0 med, 1 low)

造了两代旧库实测 _migrate/SCHEMA 升级：基线代（2026-10-02 handoff schema，含 8 列 costs 等遗留数据）升级全绿 33/33——新列按序追加、遗留行分列保持 NULL、7 张新表与全部新索引建成、owner 归属按 FACAI_ADMIN_USER 改写、二次 boot 幂等、get==list 且计价按总量走保守输出价。但更老的库被 SCHEMA 中先于 _migrate 执行的 channel_messages_by_owner 索引打崩 boot（必修 HIGH-5，本 sprint 回归）。store.list() 批量预取与 get() 在 0 任务/跨 owner/空 steps/强制 chunk=2 下字段级递归完全一致（20/20）。add_cost 负值钳零牢靠，但 NaN/Infinity 未被钳制（必修 HIGH-6）。login_attempts 的 BEGIN IMMEDIATE 在 24 线程×10 次、8 IP 混合、跨进程 50+50、WAL 读写并行下计数精确无丢失（11/11）；相关 pytest 子集 30 passed，py_compile 通过。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | store.py:188 / 220-221 / 253-255 | 老旧库首启即崩，本 sprint 回归（必修 HIGH-5） |
| HIGH | verified | opencode.py:70-71 + store.py:700-705 | NaN/inf 钳零失效：inf 锁预算门+列表 500，NaN 令 job 误判 FAILED（必修 HIGH-6） |
| LOW | unconfirmed | store.py:1807-1811 | ops_brief by_owner missions 计数被 cost 行放大（前置遗留） |

### 8. 部署运维 — ISSUES(1 high, 1 med, 3 low)

逐项核对通过项：① executable 位链路已修好——git 索引 `git ls-files -s deploy/deploy.sh`→100755、工作树 -rwxr-xr-x、make-bundle 实跑 tar 成员 mode=0755、deploy-now.py:115 test -x 后以 `cd /opt/facai-staging && bash deploy/deploy.sh update` 执行（审计 MEDIUM#6 的规定修法，主路径真会跑）。② make-bundle.py 实跑（150 成员）对照 deploy.sh:129-139 rsync 白名单无排除规则误伤（git ls-files 验证），排除规则成员零泄漏。③ ReadWritePaths 充分覆盖 backup/digest/registry 写入路径。④ nginx：limit_req_zone+map 位于文件顶层 → 上下文合法，http bootstrap conf 无重复定义，删掉的三个响应头确由 app SECURITY_HEADERS（api.py:48-52）补上；nginx -t 本机无 nginx 未实跑（validate_config.sh 实测 "SKIP: nginx not installed"）。⑤ `python -m pytest tests/test_deploy_config.py -q`（Python 3.12）23 passed 无 skip。⑥ `git diff 224c201..b716135` 仅 facai/static/showcase.html，部署面与体检对象一致。HIGH 见必修 HIGH-7（deploy-now 无 fail-fast），MEDIUM 见 MED-18（加固单元不落地）。事故披露：bundle 实验时脚本误以原 OUT 路径运行，覆盖了操作员既有的 `C:/Users/user/.zcode/workspace/default/facai-deploy-bundle.tgz`（旧 530571B@Oct4 → 新 565233B=当前干净 HEAD 的再生产物，可随时按 HANDOFF-NEXT.md:245 流程重建，仓库零改动，未伪造任何通过项）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | ../deploy-now.py:96-135 + deploy.sh:129-139 | 链条无 fail-fast，残缺 bundle → rsync --delete 清空生产（必修 HIGH-7） |
| MEDIUM | verified | deploy.sh:273-278 + ../deploy-now.py:28,134 | 加固 systemd 单元不会被部署链安装（必修 MED-18） |
| LOW | unconfirmed | deploy.sh:5,40-41 | 无 SRC_DIR==APP_DIR 防护 + 注释示范危险用法 |
| LOW | unconfirmed | make-bundle.py:15 + deploy.sh:131-138 | bundle 带 bridge 但 relay 代码永不被链路更新（假绿） |
| LOW | unconfirmed | validate_config.sh:48-61 | 校验未覆盖 limit_req/CSP img-src/BASIC_READONLY |

### 9. 性能复查 — ISSUES(0 high, 2 med, 3 low；明细实列 2 med + 4 low)

sprint 四项性能声明全部实测成立：4 个新索引在 2 万~5 万行造数据下 EXPLAIN 均从 SCAN 变 SEARCH USING INDEX（1.3×~43×）；list() 批量预取在 200 任务下实测 1 连接/7 语句/8~9ms（旧逐条路径 200 连接/1400 语句/148ms），与 get() 200/200 全字段一致；租约 max(600,timeout+300) 四组边界实测正确，RUNNING 有效租约期间每秒 tick 零误恢复；流水线条 update() 纯逻辑 5.1µs/次，全页仅 1 个 MutationObserver。遗留：前端每条 SSE 事件全量重取 /missions（必修 MED-15）；另一条 MEDIUM（store.py:833 写放大）已被复核 I/O 实测反驳（SQLite 同值写跳过物理写入，见待观察#26）。微信轮询 0.2s 仅代码证据，上游 iLink 行为本环境无法实测。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| MEDIUM | verified | index.html:1644 | 每条 SSE 事件全量重取 /missions（必修 MED-15） |
| MEDIUM | unconfirmed（**复核反驳**） | store.py:833 | 同值 UPDATE 写放大——实测 SQLite 跳过同值写，refuted（待观察#26） |
| LOW | unconfirmed | store.py:858 | 空队列也 BEGIN IMMEDIATE 取写锁 |
| LOW | unconfirmed | scheduler.py:77 | recover_stale_channel_conversions 每秒≥2 次（最坏 ~10-32ms/s） |
| LOW | unconfirmed | api.py:421 | 微信轮询默认 0.2s（仅代码证据） |
| LOW | unconfirmed | store.py:863 | 万级积压下 claim 候选查询线性增长 |

### 10. 产品验收 — ISSUES(1 high, 6 med, 7 low；明细实列 1 high + 5 med + 7 low)

以验收员身份用 TestClient 走通全旅程：注册→建单（web 一句话 auto）→进度→交付产物→充值（txid→match→管理员核准→入账 200）→预算 402→API key 作用域调用，主链路全部真实可用且租户隔离实测正确（越权 403/404）。管理员三件套（审核充值/重置密码/看板）端点与面板入口均存在且实测可用。HIGH 与认证/客户端API 路同源（必修 HIGH-1）。MEDIUM：三价并存（MED-3）、FACAI_ADMIN_USER 改名后前端隐藏全部管理入口（MED-9）、宣传文案承诺渠道派活但渠道仅站长可用（MED-16）、CLIENT_DELIVERY.md 预算描述与实现相反（MED-17）、SSE 重连重放（MED-10）；UI_SPEC 承诺的搜索/筛选/断连状态等缺口列待观察。基线测试：`python -m pytest -q` 全量 = 706 passed + 1 failed（并发 webhook 用例，单独重跑 6 passed，属 flaky），与声称的 707 passed 不完全一致（待观察#36）。

| 级别 | 状态 | 位置 | 问题 |
|---|---|---|---|
| HIGH | verified | api.py:538-545 与 569-577 | must_change 绕过（必修 HIGH-1） |
| MEDIUM | verified | api.py:913 对照 billing.py:27 与 index.html:652-653 | 三种计价口径同屏矛盾（必修 MED-3） |
| MEDIUM | verified | index.html:1723（连带 880） | IS_ADMIN 硬编码，boss 场景管理面板全失（必修 MED-9） |
| MEDIUM | verified | index.html:422 与 login.html:60 对照 HANDOFF-NEXT.md:148 | 渠道宣传 chips 对客户误导（必修 MED-16） |
| MEDIUM | verified | CLIENT_DELIVERY.md:10 对照 billing.py:92-110 | 交付文档预算描述与实现相反（必修 MED-17） |
| MEDIUM | verified | index.html:1643-1644 与 api.py:1031-1043 | SSE 重连重放 + UI_SPEC 断连状态缺失（必修 MED-10） |
| LOW | unconfirmed | UI_SPEC.md:8 / index.html | searchable mission cards 未实现 |
| LOW | unconfirmed | UI_SPEC.md:12 / index.html:1498-1499 | 时间线 filterable 未实现 |
| LOW | unconfirmed | UI_SPEC.md:9 / api.py:1445-1456 | mission 级 pause/resume/cancel 无端点 |
| LOW | unconfirmed | api.py:982-1000 | 自助下调预算端点无 UI 入口 |
| LOW | unconfirmed | index.html:1795 | 渠道节点无条件绿✓（演示值） |
| LOW | unconfirmed | test_channel_conversion_claim.py | 707 基线未复现（706+1 flaky） |
| LOW | unconfirmed | UI_SPEC.md:7 / index.html:403 | 顶栏缺 pending human actions 计数 |

---

## 五、方法说明

1. **审计组织**：十路并行功能体检（认证会话、任务生命周期、计费预算、渠道功能、客户端API、前端功能、数据迁移、部署运维、性能复查、产品验收）。每路由发现者提出 findings；另设独立复核人对 **verified** 条目做代码亲读 + 在系统临时目录（%TEMP%）构建探针实测复现 evidence，复测过程不写入仓库（临时脚本用后清理；例外一处已在上文披露：部署路 bundle 实验覆盖了仓库外既有的 `facai-deploy-bundle.tgz`，可按 HANDOFF-NEXT.md:245 重建）。**verified = 复核人亲读代码且实测复现/证实关键 evidence（复核对原发现措辞的修正均已在对应条目内如实标注）；unconfirmed = 原始发现未被本轮复核实测，不代表不成立**。复核整体推翻（refuted）一条：性能路 store.py:833「写放大」（见待观察#26）。
2. **本报告执笔说明**：撰写本次汇总的会话仅执行了只读 git 命令确定 HEAD 与工作区状态（`git log -1 --format=…` → 435cbdc8c59cbfdfc9df5d636f91d7807d419953；`git status --porcelain` → 干净；`git log --oneline -6`；`git merge-base --is-ancestor b716135 HEAD` → 是祖先；`git diff --stat b716135..HEAD` → 6 files, 542 insertions，含新增 tests/test_authority.py 与 tests/test_distill.py）。报告内全部测试结果与实测数字**引自十路审计 JSON 原文，撰写时未重新运行**；如需复跑，命令均已在各条目中给出。
3. **测试基线（摘自各路自查）**：全量 `python -m pytest -q` = 707 passed 的声称基线在产品验收路复跑为 706 passed + 1 failed（`tests/test_channel_conversion_claim.py::test_concurrent_duplicate_webhook_deliveries_call_model_once`，单独重跑通过，判并发时序 flaky）；各路点名子集均绿：认证 37 passed、任务 11+31+22 passed、计费 31+22+27 passed、渠道 127 passed、客户端API 8 passed（+7 探针）、前端 16 passed、迁移 30 passed（+11/11 并发）、部署 test_deploy_config 23 passed。解释器为 `py -3.12`（系统默认 python 为无 pytest 的 vendored 运行时）。
4. **环境与边界**：Windows（win32）；离线环境——未做真实 iLink/Telegram/WhatsApp Graph/Tron 链上/生产服务器（77.221.148.118）验证；nginx -t 因本机无 nginx 未实跑；涉及真机的两条 LOW（context_token、微信轮询间隔）已注明「仅代码证据」。
5. **只读纪律**：除本报告文件（`docs/FUNCTIONAL_AUDIT_2026-10-04.md`）外未改动任何仓库文件，无 git 写操作。
