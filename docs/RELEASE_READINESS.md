# 发布前本地部署安全核查

**核查日期：** 2026-10-02（最后更新：2026-10-02）
**核查范围：** 本地 346 测试现状、Windows 公开访问核查结果、SSH 阻塞说明、安全部署步骤、可验证检查、已知阻塞项
**声明：** 本文档由 OpenCode 在本地仓库内核查生成。已在本地 Windows 通过 `deploy/check_public_access.ps1` 对 `whstapp.lol` 完成公开访问探测（权威 DNS、TCP 443、TLS、`/health` 200、匿名 401 均通过）；但**未能通过 SSH 登录生产服务器**（`77.221.148.118` 返回 `Permission denied (publickey,password)`），因此**未确认生产 HEAD、未执行任何部署操作、未做生产备份演练、未接触任何凭据**。
所有结论均基于：仓库文件静态分析、`git log`/`git diff` 输出、本地 `pytest` 执行结果、`deploy/check_public_access.ps1` 的本地公开访问探测，以及 `docs/DEPLOYMENT.md`、`docs/DEPLOYMENT_RECORD.md`、`docs/ACCEPTANCE_OPENCODE.md`、`docs/CHANNELS.md` 中的已记录事实。

---

## 1. 本地测试现状（已验证证据）

### 1.1 测试套件结果

```
python -m pytest -q
346 passed, 2 warnings
```

**本次最终验证（2026-10-02）：** 所有当前 FACAI 测试 **346 passed, 0 failed, 2 third-party warnings**。测试发现范围限定为项目自身 `tests/` 目录，避免 `work/` 中的临时上游仓库被误当作 FACAI 测试。两条弃用警告来自 `websockets` / uvicorn 测试依赖。

两条 `DeprecationWarning` 均来自第三方库（`websockets.legacy`、uvicorn 内部），与应用代码无关，不阻塞发布。

### 1.2 测试覆盖范围（推断自测试文件列表）

| 测试文件 | 覆盖点 |
| --- | --- |
| `test_auth.py` | 会话登录、CSRF、登出、限流、过期、Basic fallback |
| `test_backup.py` | 备份/验证/恢复轮回、密钥不出现在备份中 |
| `test_channels.py` | 渠道状态 API、WhatsApp webhook 验签/派发、WeChat 等待扫码 |
| `test_deploy_config.py` | nginx HTTP bootstrap 不需证书、TLS 配置无 `includeSubDomains` |
| `test_gateway.py` / `test_gateway_attribution.py` | 模型网关归因、上游失败分类 |
| `test_opencode_execution.py` | OpenCode 适配器、已完成步骤拒绝二次运行 |
| `test_console_layout.py` / `test_mobile_ui.py` | Playwright 浏览器布局（1440×900 桌面 / 390×844 手机） |
| `test_mission_isolation_e2e.py` | 跨 Mission 数据隔离 |
| `test_prompt_dispatch.py` / `test_wechat.py` | 渠道消息派发、WeChat iLink |
| `test_scheduler.py` | 调度器优先级、租约、暂停/恢复/取消 |
| `test_backup.py::test_restore_drill_*` | 备份演练（不触动生产库） |

**推断（未独立验证）：** 覆盖范围基于文件名推断，未逐行阅读每个测试；具体分支覆盖率未测量。

---

## 2. 当前工作树与生产状态对比

### 2.1 HEAD 提交

```
8a32852  Harden n8n callbacks and WhatsApp delivery  (2026-10-02 12:59 +0800)
```

### 2.2 生产最后记录提交（DEPLOYMENT_RECORD.md）

```
6275061  Cover channel send and reserved endpoints in API tests  (2026-10-02)
```

**差距（已验证）：** 本地 HEAD（`8a32852`）领先 `DEPLOYMENT_RECORD.md` 记录的生产提交（`6275061`）**18 个提交**。注意：`6275061` 只是部署记录文档中的值，**并非实时生产 HEAD**；生产真实 HEAD 必须 SSH 登录后确认（见 #B1/#B0）。

```
8a32852  Harden n8n callbacks and WhatsApp delivery
9e8b560  Render workflow dependencies as responsive DAG
51a8bca  Add dependency-aware mission DAG scheduling
b050c01  Auto-dispatch Telegram inbound messages safely
d93f530  Document open-source workflow patterns for Facai
fedbfb8  Clarify workflow nodes and real stage progress
e2f9d69  Configure Anthropic provider safely and support output limits
d1900f8  Stream OpenCode output into mission events
9a384b8  Add Anthropic Messages API gateway support
c095ef3  Enable safe cross-mission parallel execution
f89f3d4  Fix WeChat authorization QR rendering
5ff3db5  Implement personal WeChat iLink authorization and messaging
d3f361a  Add signed n8n event relay with idempotent callbacks
c0641b1  Route inbound channel messages into audited, auto-dispatched missions
b75bf67  Implement Telegram text adapter with authenticated deduplicated webhooks
90a7f7c  Show channel refresh feedback in console
f02146a  Add live channel status refresh control
09720c3  Record the verified live channel-layer deployment
```

**待确认阻塞（#B1）：** 必须在部署前确认生产服务器 `/opt/facai-cloud` 当前 HEAD 是否真的为 `6275061`（在 VPS 上执行 `git -C /opt/facai-cloud rev-parse HEAD`）。若生产已有后续提交但未记录在 `DEPLOYMENT_RECORD.md`，需先补记再评估差距。**当前因 SSH Permission denied（见 #B0）无法执行此核查。**

### 2.3 工作树未提交变更（已验证）

`git status` 显示以下文件有本地修改（**尚未提交**）：

```
M  README.md
M  docs/CHANNELS.md
M  facai/api.py
M  facai/registries.py
M  facai/store.py
M  facai/channels/wechat.py
M  facai/channels/whatsapp.py
M  facai/static/index.html
M  tests/test_channels.py
M  tests/test_console_layout.py
M  tests/test_phase1.py
M  tests/test_prompt_dispatch.py
M  tests/test_registry_binding.py
M  tests/test_scheduler.py
M  tests/test_wechat.py
?? docs/HANDOFF_2026-10-02.md
?? docs/OPEN_SOURCE_SKILLS.md
?? docs/RELEASE_READINESS.md
?? opencode.json
?? pytest.ini
?? tests/test_skill_registry_validation.py
?? work/
```

`git diff --check HEAD` 输出：**无空白错误**，仅有 LF→CRLF 行尾警告（15 个已修改文件），原因是本机 Windows 环境与仓库 LF 行尾的交互，不影响服务器端部署（`.gitattributes` 已存在）。pytest 配置现限定在 FACAI 自己的 `tests/`，不扫描 `work/` 内的临时上游克隆。

**阻塞（#B2）：** 上述 15 个已修改文件及新增文档/测试、`opencode.json`、`pytest.ini`、`work/` 目录尚未提交，若发布则这些变更**不会**随 `deploy.sh update`（rsync 托管路径）同步到生产。需要决策：
- 若这些变更需要发布，必须先提交并推送；
- 若属于本地临时调试，需确认不影响生产逻辑后方可跳过。

---

## 3. `git diff --check` 结果

**无阻塞性错误。** 仅有行尾警告（非错误），属于 Windows 本地环境问题，不影响生产 Linux 部署。原文输出：

```
warning: in the working copy of 'README.md', LF will be replaced by CRLF ...
warning: in the working copy of 'docs/CHANNELS.md', LF will be replaced by CRLF ...
warning: in the working copy of 'facai/api.py', LF will be replaced by CRLF ...
warning: in the working copy of 'facai/channels/wechat.py', LF will be replaced by CRLF ...
warning: in the working copy of 'facai/channels/whatsapp.py', LF will be replaced by CRLF ...
warning: in the working copy of 'facai/static/index.html', LF will be replaced by CRLF ...
warning: in the working copy of 'tests/test_channels.py', LF will be replaced by CRLF ...
warning: in the working copy of 'tests/test_console_layout.py', LF will be replaced by CRLF ...
warning: in the working copy of 'tests/test_prompt_dispatch.py', LF will be replaced by CRLF ...
warning: in the working copy of 'tests/test_wechat.py', LF will be replaced by CRLF ...
```

---

## 4. 已知阻塞项（上线前须逐项复核）

### #B0 — SSH 访问被拒，无法核实生产状态或执行部署/备份演练（**发布阻塞**）

**状态：** 阻塞中
**现象：** 对所有可用本地密钥，VPS SSH 返回：
```
Permission denied (publickey,password)
```
**影响：** 以下操作在阻塞解除前**均无法执行**：
- 确认生产 HEAD（#B1）
- 推送代码并执行 `deploy.sh update`
- 执行 `facai.backup drill` 生产演练（#B5）
- 验证 `systemctl status`、nginx 配置、数据库完整性

**操作：** 联系服务器管理员恢复 SSH 访问权限（重新授权本地公钥，或重置 VPS 控制台访问），在解决前不得声称任何生产验证已完成。

---

> **已验证（Windows 本地，2026-10-02）：公开访问探测已通过**
>
> 使用本地 Windows 的 `deploy/check_public_access.ps1` 对 `whstapp.lol` 探测结论：
> - 权威 DNS：apex `whstapp.lol` 及 `www.whstapp.lol` 均指向 `77.221.148.118`（已确认）
> - TCP 443：可连接
> - TLS：握手成功
> - `GET /api/v1/health` → HTTP 200
> - 匿名访问 `/api/v1/missions` → HTTP 401
> - **尚未执行**：登录探测（需凭据）；服务器端完整 `check_public_access.sh` 运行（需 SSH，见 #B0）

---

### #B1 — 必须确认最新生产提交与预期一致

**状态：** 待办
**操作：** 在 VPS 执行：
```bash
sudo -u facai git -C /opt/facai-cloud rev-parse HEAD
```
期望输出：`6275061...`（若 `DEPLOYMENT_RECORD.md` 记录准确）。
若生产 HEAD 不符合预期，必须先查明原因（手动热修复？未记录的 rsync？）再继续。

### #B2 — 工作树有未提交变更，不会随部署同步

**状态：** 待办
**已变更文件（本地 diff，共 1135 处增改，97 处删除）：**
`README.md`、`docs/CHANNELS.md`、`facai/api.py`、`facai/channels/wechat.py`、`facai/channels/whatsapp.py`、`facai/registries.py`、`facai/static/index.html`、测试文件等（共 15 个已修改文件，另有新增文档/测试及配置）。
**核心变更（静态阅读 diff）：** WhatsApp webhook dispatch/parser 完善、WeChat response hardening、FACAI workflow UI 更新、README/CHANNELS 文档更新、test updates、项目 OpenCode config（`opencode.json`）新增。这些是实质性逻辑变更，**不可以"未提交"状态发布**。
**操作：** 评估后决定提交、丢弃或暂存；提交后重新运行全套测试再部署。

### #B3 — 单管理员架构（非多租户）

**状态：** 已知设计限制，非本次发布阻塞，但须向所有使用方明确告知。
**证据（README.md）：** 认证为单一 `admin` 用户，凭 `FACAI_ADMIN_PASSWORD` 控制全部访问；HTTP Basic 和 Session Cookie 均只有一个权限级别。无 RBAC、无租户隔离、无每用户配额。
**影响：** 若同一实例对多个业务方或外部用户开放，所有人共享同等管理权限。当前设计仅适合单一管理员自用或小团队内部使用。
**行动项：** 上线前向运营方书面确认本次发布范围不涉及多租户需求；否则需先实现 RBAC 再发布。

### #B4 — 消息渠道需要真实账号凭据，当前均未配置

**状态：** 待办（渠道功能不可用，但不阻塞主控制台发布）

**代码能力 vs 生产状态（必须区分）：** 下表两列分别表示本地代码中已实现的渠道能力，以及 `DEPLOYMENT_RECORD.md` 记录的生产部署/配置状态。**代码中存在实现，不代表线上已部署，也不代表已配置真实凭据或已完成端到端验收。**

| 渠道 | 本地代码能力 | 生产记录 / 线上状态 | 阻塞描述 |
| --- | --- | --- | --- |
| WhatsApp | 已实现 webhook 验签与派发 | 凭据与 webhook 未配置（生产状态未知） | 需填写 `/etc/facai-cloud/providers/whatsapp.env`（`WHATSAPP_PHONE_NUMBER_ID`、`WHATSAPP_TOKEN`、`WHATSAPP_VERIFY_TOKEN`、`WHATSAPP_APP_SECRET`）并在 Meta Developer Portal 完成 webhook 注册 |
| WeChat iLink | 已实现 iLink 授权与消息收发 | 生产状态未知 | 需用户（管理员）在控制台点击"个人微信扫码授权"完成 iLink 授权；Bot Token 写入 `$FACAI_DATA_DIR/providers/wechat.env`；无法预先写入静态凭据 |
| Telegram | 本地实现已存在（text adapter、带认证的去重 webhook） | 线上是否部署未知 | 需填写 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET`，并通过 Bot API `setWebhook` 注册 HTTPS 回调地址后，再做真实账号收发验收 |

**不可绕过的事实：** 渠道测试（`test_channels.py`）均为本地模拟 upstream，无法替代真实账号端对端验收。渠道功能上线前必须完成真实凭据配置和至少一轮真实收发测试。

### #B5 — 备份演练（drill）尚未在生产数据上执行

**状态：** 待办
**证据（README.md / docs/DEPLOYMENT.md）：** `facai.backup drill` 在本地已有单元测试覆盖（`test_restore_drill_rehearses_without_touching_live_data`），但生产 VPS 上的演练执行情况未在 `DEPLOYMENT_RECORD.md` 中记录。
**操作（在 VPS 上执行，不影响生产库）：**
```bash
python -m facai.backup create --data-dir /opt/facai-cloud/data
python -m facai.backup drill  --data-dir /opt/facai-cloud/data
```
确认输出显示"counts match"且无报错后记录结果。

### #B6 — n8n 集成已实现但未配置，无真实事件投递记录

**状态：** 已知，非阻塞（主功能不依赖 n8n）
**证据（README.md）：** "The n8n adapter is implemented but unconfigured, so no event has been delivered to a real workflow."
**行动项：** 若本次发布包含 n8n 事件投递需求，需在 `/etc/facai-cloud/providers/n8n.env` 填写 `N8N_WEBHOOK_URL` 和 `N8N_SIGNING_SECRET`，并验证至少一次真实 webhook 投递（含签名校验）。

---

## 5. 安全部署步骤（参考 docs/DEPLOYMENT.md，此处为操作顺序摘要）

> 以下步骤适用于新提交上线（即 #B2 已解决后），在 VPS 以 root 执行。

```
# 0. 前提：确认生产 HEAD（#B1）、确认工作树已提交（#B2）
sudo -u facai git -C /opt/facai-cloud rev-parse HEAD

# 1. 在本地先跑测试确保全绿
python -m pytest -q

# 2. 登录 VPS，进入源码目录
ssh <vps>

# 3. 拉取代码（或推送后同步）
cd /root/facai-cloud-src && git pull   # 或按实际 rsync 流程准备源

# 4. 执行 update（自动备份 → scoped rsync → venv 刷新 → env 分类 → systemd/nginx 重应用）
sudo ./deploy/deploy.sh update

# 5. 检查服务健康
sudo ./deploy/deploy.sh status   # 期望：active/running，NRestarts=0，health=200

# 6. 执行公开访问验证
FACAI_ADMIN_PASSWORD=<secret> ./deploy/check_public_access.sh whstapp.lol 77.221.148.118
# 期望：所有 PASS，最后一行 "PUBLIC ACCESS VERIFIED for whstapp.lol"

# 7. 回滚（若验证失败）
sudo ./deploy/deploy.sh rollback   # 恢复 app-latest.tar.gz
# 数据库回滚：停服务 → facai.backup restore → 重启
```

**凭据从不出现在以上命令行参数或日志中。** `FACAI_ADMIN_PASSWORD` 仅用于验证探针，不传递给 deploy 脚本。

---

## 6. 可验证检查清单（逐项复核）

每项需要真实输出或截图作为证据，不接受"看起来正常"。

| # | 检查项 | 期望结果 | 证据形式 |
| --- | --- | --- | --- |
| C1 | `python -m pytest -q` 本地全绿 | `346 passed, 0 failed`（2026-10-02 当前工作树） | 命令输出 |
| C2 | `git diff --check HEAD` 无错误 | 0 行错误（警告可忽略） | 命令输出 |
| C3 | 工作树已提交（`git status` 清洁） | `nothing to commit` | 命令输出 |
| C4 | 生产 HEAD 与预期一致（#B1） | `git rev-parse HEAD` 输出符合预期 | VPS 命令输出 |
| C5 | `deploy.sh update` 完成无错 | 最后一行 `update complete` | 终端日志 |
| C6 | `systemctl status facai-cloud` | `active (running)`，`NRestarts=0` | 终端输出 |
| C7 | `GET /api/v1/health` → 200 | `{"status": "ok"}` | curl 输出 |
| C8 | 匿名访问 `/api/v1/missions` → 401 | HTTP 401 | check_public_access 输出 |
| C9 | Basic 认证访问 → 200 | HTTP 200 | check_public_access 输出 |
| C10 | `check_public_access.sh` 全 PASS | `PUBLIC ACCESS VERIFIED` | 命令输出 |
| C11 | TLS 证书有效期 | `2026-12-30`（据 DEPLOYMENT_RECORD.md），未过期 | openssl 输出 |
| C12 | nginx 配置无语法错误 | `nginx -t` → `syntax is ok` | 命令输出 |
| C13 | `facai.backup drill` 在生产数据上通过 | "counts match"，无报错（#B5） | 命令输出 |
| C14 | 数据库完整性 | `PRAGMA integrity_check` → `ok` | sqlite3 输出 |
| C15 | 密钥不在 Git 历史中 | `git log -p` / byte scan 未发现真实凭据 | scan 脚本输出 |

---

## 7. 已验证证据 vs 推断/待做项汇总

### 已验证（本次核查期间直接确认）

- 本地 `pytest -q`：**346 passed, 0 failed，2 第三方弃用警告**（2026-10-02 当前工作树）
- `git diff --check HEAD`：**无空白错误**，仅 LF/CRLF 警告（15 个已修改文件）
- 工作树有 15 个已修改文件和 7 个未追踪项目条目，均尚未提交；提交前需审查 `work/` 并排除临时克隆/日志与密钥
- 本地 HEAD 为 `8a32852`；相对 `DEPLOYMENT_RECORD.md` 记录值 `6275061` 领先 18 个提交（该记录值不是实时生产 HEAD）
- **Windows 公开访问探测已通过**（`deploy/check_public_access.ps1`）：权威 DNS apex/www 全指 `77.221.148.118`、TCP 443/TLS 握手成功、`/health` 200、匿名访问 401；**未执行**登录探测
- **SSH Permission denied**：无法核实生产 HEAD、执行部署或备份演练（#B0，发布阻塞）
- `deploy/deploy.sh` 逻辑：scoped rsync、先备份后同步、凭据不出现在命令行
- `deploy/check_public_access.sh` 逻辑：检查权威 DNS + TCP + TLS + HTTP + 认证
- 测试套件覆盖：auth、backup、channels、deploy_config、gateway、opencode、layout、isolation、scheduler
- 单管理员架构（无多租户）：源于 README 和认证代码
- 渠道代码能力 vs 生产状态：WhatsApp 凭据/webhook 未配置、WeChat iLink 需用户在控制台扫码授权、Telegram 本地实现已存在但线上是否部署未知（详见 #B4）
- n8n：已实现未配置，无真实投递记录
- 备份演练：本地测试覆盖，生产演练未有记录

### 推断/待做（未在本次核查中直接验证）

- **SSH 访问恢复**（#B0，当前 Permission denied，所有生产核查均被阻塞）
- 生产 VPS 当前实际 HEAD（需登录 VPS 确认，#B1，依赖 #B0）
- 未提交变更是否已通过充分代码审查（#B2）
- 生产服务 `facai-cloud.service` 当前运行状态
- TLS 证书实际剩余有效期（需 `openssl s_client` 或 `check_public_access.sh` 输出）
- WhatsApp/WeChat/Telegram 真实凭据是否已填写（#B4）
- 生产数据库 `facai.backup drill` 通过情况（#B5）
- n8n 真实 webhook 投递测试（#B6）
- 本次 18 个新提交是否已完整通过 VPS 上的部署验证门控（#B0 解除后方可核查）

---

## 8. 不在本文档范围内的事项

- 本文档不含任何凭据值、API key、域名密码、Bot token
- 本文档不声称已执行任何部署操作
- 本文档不替代 `deploy/check_public_access.sh` 的实际运行输出
- 生产服务器状态需登录 VPS 实测，本文档无法代为确认
