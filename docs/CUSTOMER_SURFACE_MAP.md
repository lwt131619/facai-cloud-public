# 客户可见面全图（CUSTOMER SURFACE MAP）

- 审计波次：客户视图枚举（防使用者反代转售平台 / 防客户逆向克隆源代码）
- 日期：2026-10-06；代码基线：工作树 = 46b31d0（Wave 7）+ 并行防泄漏波 + 本波修复
- 方法：本地真实 app（`facai.api.create_app` + TestClient）注册两个客户
  （carol/dave）+ 管理员；以客户会话/Bearer key 把可达端点全部枚举，逐响应体
  扫描内部路径、主机名、provider 真名、密钥形状、他人数据；管理路径与猜径
  全打断言拒绝码；静态资产逐一探测含备份后缀。
- 验收测试：`tests/test_customer_surface.py`（枚举即断言）+
  `tests/test_source_leak.py`（源码泄漏面，与源码泄漏波共管）。

判定口径：**泄漏** = 源码 / 他人数据 / 运营方凭据 / 服务器内部路径与主机名；
**OK** = 本人数据、产品信息（价格、DeepSeek/GLM 等 provider 产品名与公共端点）、
掩码形状（`facai_<id>_<secret>` 占位、`****尾4位`）。

## 一、修复的缺口（本波）

| # | 缺口 | 证据（修复前实测） | 修复 |
|---|------|--------------------|------|
| 1 | `/openapi.json`、`/docs`、`/redoc` 对任意登录客户开放：完整 92 条路径拓扑（含 7 条 `/api/v1/admin/*`）与全部请求/响应 schema 可枚举 | 客户会话 `GET /openapi.json` → 200，paths=92，含 `/api/v1/admin/members` 等 | `facai/api.py` `create_app`：`openapi_url=None, docs_url=None, redoc_url=None`；客户与匿名均 404 |
| 2 | `POST /api/v1/n8n/relay`、`POST /api/v1/scheduler/run` 无守卫：任意客户可 200 触发运营方交付泵/调度泵（响应含站点姿态 `CONFIG_REQUIRED`） | 客户会话 POST → 200 `{"dispatched":0,"state":"CONFIG_REQUIRED"}` | 两端点加 `require_admin_owner()`；客户 403，管理员仍 200 |
| 3 | `GET /api/v1/missions` 响应体带服务器绝对路径 `workspace`（部署布局外泄） | 探针实测 `C:\\...\\workspaces\\<mission_id>` | 并行源码泄漏波在 API 边界剥离（`facai/api.py` `public_mission` 等）；本波测试钉死 |

## 二、客户会话可达端点（GET）× 判定

所有端点均 owner 隔离（跨客户 id → 404），响应体经禁词扫描（口令哈希形状、
sk- 密钥、他人任务/用户名、堆栈、内部路径）零命中。逐条判定：

| 端点 | 客户可见字段（实测） | 判定 |
|------|----------------------|------|
| `GET /api/v1/auth/session` | 本人 username、csrf_token、expires_at、is_admin=false、must_change_password、assist（协助模式知情权） | OK（均为本人会话事实） |
| `GET /api/v1/health` | `{status, phase:2, auth_required}` | OK（产品信息；`phase` 为发布阶段编号，无内部语义） |
| `GET /api/v1/pricing` | $95/$218 价目、本人 monthly_limit_usd、中文口径文案 | OK |
| `GET /api/v1/billing/me` | 本人限额/消费/告警、PRICING、estimated_cost | OK |
| `POST /api/v1/billing/check` | 同上（干跑） | OK |
| `GET /api/v1/billing/users/{u}/budget` | 仅本人（他人 → 403） | OK |
| `GET /api/v1/billing/recharge/config` | TRC20 网络、min $100、窗口 300s、地址（未配置时空） | OK（收款地址本就是要给客户打的） |
| `GET /api/v1/billing/deposit-address` | 同上 | OK |
| `GET /api/v1/billing/recharges/mine` | 本人充值单（id/金额/状态/txid） | OK |
| `GET /api/v1/billing/recharges/{id}/match` | 本人单（他人/不存在 → 403/404） | OK |
| `GET /api/v1/missions` | 本人任务（id/title/status/steps/owner_id=自己）；**无 workspace**（修复 3） | OK |
| `GET /api/v1/missions/{id}` | 同上 + artifacts（无 path） | OK |
| `GET /api/v1/missions/{id}/events` | 本任务事件流（sequence/type/payload） | OK |
| `GET /api/v1/missions/{id}/stream` | 同上（SSE） | OK |
| `GET /api/v1/missions/{id}/proof` | 持久证据（grants/backends/steps/costs/artifacts），无内存授权状态 | OK |
| `GET /api/v1/missions/{id}/diff`、`/jobs`、`/channel-messages`、`/artifacts/{aid}` | 均本人任务作用域 | OK |
| `GET /api/v1/artifacts` | 本人产物中心（无服务器路径） | OK |
| `GET /api/v1/inbox` | 本人通道消息（owner 过滤） | OK |
| `GET /api/v1/scheduler` | 并发数/running/worker id/最近错误 | OK（站点运行指标，无数据、无路径；worker 为随机 hex 非主机名） |
| `GET /api/v1/registries/{agents,models,runtimes,skills}` | 共享默认目录 + 本人自建条目；`api_key` 字段被 `public_registry_value` 递归剥离；deepseek 条目含 `endpoint: https://api.deepseek.com` 与 `api_key_env: DEEPSEEK_API_KEY`（变量名，非值） | OK（provider 产品名/公共端点属产品信息；写入侧另有 api_key_env 仅限站长 403） |
| `GET /api/v1/my/models` | 本人自备模型 + `api_key_masked: "****尾4位"`，key 原文零出现 | OK |
| `GET /api/v1/my/keys` | 本人客户端 key 的 `prefix`（仅 id + 4 字符）、created_at；**无 secret、无 hash** | OK |
| `GET /api/v1/my/channels` | telegram/line 是否绑定 + chat_id + 时间；token 原文零出现 | OK |
| `GET /api/v1/my/channels/{wechat,whatsapp}/host/status` | 本人宿主绑定状态/session_dir（相对路径 `providers/hosting/<owner>/<channel>`，数据目录相对，无绝对盘符） | OK（相对命名空间路径，本人自己的） |

## 三、客户端 API key（Bearer `facai_<id>_<secret>`）作用域

- 放行：`/api/v1/missions*`、`/api/v1/jobs*`、`/api/v1/artifacts*`、`/api/v1/billing/me`（精确）。
- 其余一切（含 `/api/v1/my/keys` 本身——key 不能铸 key、不能看 key、不能吊销
  key）→ 403 `该 API key 无权访问此接口`；无效/吊销 → 401。
- 中间件在 handler 之前拒绝（`facai/api.py` `API_KEY_PATH_PREFIXES` / `api_key_allowed`）。

## 四、管理/内部/猜径清单（客户身份全打，本表即断言基准）

客户会话 + 有效请求体，期望 401/403/404/405，**绝不 200/500**：

| 路径 | 客户实测 |
|------|----------|
| `GET /api/v1/admin/audit-report` | 403 |
| `GET /api/v1/admin/members` | 403 |
| `POST /api/v1/admin/members/{u}/top-up` `/freeze` `/unfreeze` | 403 |
| `POST/DELETE /api/v1/admin/assist/{u}` | 403 / 403 |
| `POST /api/v1/auth/admin-reset-password` | 403 |
| `GET/PUT /api/v1/billing/users/dave/budget` | 403 / 403 |
| `GET /api/v1/billing/recharges`（全量列表） | 403 |
| `POST /api/v1/billing/recharges/{id}/approve` `/reject` | 403 |
| `GET /api/v1/billing/reconciliation` | 403 |
| `GET /api/v1/ops/brief` | 403 |
| `GET /api/v1/daily-progress` | 403 |
| `GET /api/v1/channels`、`/channels/{c}`、`/channels/{c}/messages` | 403 |
| `POST /api/v1/channels/telegram/send` | 403 |
| `POST/GET /api/v1/channels/wechat/login`、`POST /wechat/unbind` | 403 |
| `GET /api/v1/n8n` | 403 |
| `POST /api/v1/n8n/relay`（修复 2） | 403 |
| `POST /api/v1/scheduler/run`（修复 2） | 403 |
| `GET /openapi.json` `/docs` `/docs/` `/redoc`（修复 1） | 404 |
| `GET /admin` `/admin/members` `/internal/*` `/debug/*` `/api/v1/admin` `/api/v1/internal` `/api/v1/debug` | 404（路由不存在，无存在性泄漏） |
| `GET /.env` `/.git/config` `/docs/ARCHITECTURE.md` `/facai/api.py` `/data/facai.db` | 404 |

注：FastAPI 的 body 校验先于 handler 守卫——无有效请求体时这些 POST/PUT 返回
通用 422（字段缺失文案，不泄存在性）；本表与测试均带**有效请求体**，验证的是
守卫本身的 403。

## 五、静态资产面

白名单（`facai/static/`，由 `tests/test_source_leak.py` 钉死全集合）：
`index.html`（控制台）、`billing.html`、`login.html`、`guide.html`（使用说明书）、
`mobile.html`、`showcase.html`、`facai-ui.css`、`sw.js`、`manifest.webmanifest`、
`icon-192.png`、`icon-512.png`。

| 探测 | 结果 | 判定 |
|------|------|------|
| `GET /static` `/static/` `/static/.` | 404 | 无目录列表 OK |
| 全部 8 个页面 × `.bak/.old/.orig/.save/.swp/~` 后缀 | 404 ×48 | 无备份可猜 OK |
| `/static/.env` `/static/.git/config` `/static/master.key` `/static/*.map` | 404 | OK |
| `/static/api.py` `/static/registries.py.bak-20261005` `/static/tron_sync.py.bak-20261005` | 404 | OK（仓库内的两个 `.bak-20261005` 在 `facai/` 与 `facai/ops/` 下，不在挂载点；**建议清理**，属打包卫生） |
| `/static/../facai/api.py`、`%2e%2e` 编码穿越 | 404 | OK |
| 页面文本扫描 | 无源码标记（`FACAI_ADMIN_PASSWORD`/`os.getenv`/`secretbox`/`pbkdf2` 零命中）、无 `sk-`/真实 `facai_` key 形状、外链主机仅 `whstapp.lol`/`api.deepseek.com`/`github.com`/`www.w3.org` | OK（showcase/guide 里的 `facai_<id>_<secret>` 为掩码示例，产品文档） |

前端 `request()` 全清单（index.html）与 API 面一一对应，无未鉴权的额外 fetch；
`mobile.html` 仅用 missions/my-channels/artifacts 等本人端点。

## 六、公开传输层端点（自证签，非会话面）

| 端点 | 无凭证行为 |
|------|------------|
| `GET/POST /api/v1/channels/{c}/webhook` | 403/405/503（challenge/签名在端点内验证） |
| `POST /api/v1/channels/relay/send` | 400/403（HMAC 签名必验） |
| `POST /api/v1/n8n/callback` | 503/403（签名密钥未配置或验签失败） |
| `GET /api/v1/media/{name}` | 404（文件名即能力凭证：`digest-` 前缀 + 随机 hex 白名单校验，穿越 404） |

## 七、其他防线复核（本波顺带实测）

- 资金池 0 余额拦截：新注册无 `user_budgets` 行客户建单 → **402 「余额为 0，
  请先充值后再发起任务」**（`@pytest.mark.raw_budget` 真实语义下复测）。
- 客户端 key 铸造：仅会话可铸（`POST /api/v1/my/keys`），key 本身不可铸 key；
  原文只在铸造响应出现一次，存储只有 SHA-256（`facai/auth.py` `issue_api_key`）。
- 注册名额/保留用户名/弱口令：`facai/auth.py` `RESERVED_USERNAMES`、5 账户上限
  （既有测试覆盖，本波未重复断言）。

## 八、遗留建议（不阻塞验收）

1. `facai/registries.py.bak-20261005`、`facai/ops/tron_sync.py.bak-20261005`：
   从仓库删除（HTTP 面已证不可达，仅打包/仓库卫生）。
2. `GET /api/v1/health` 的 `phase: 2` 若无产品含义可移除（信息级，无风险）。
3. 生产核验（主会话执行，本波无 SSH 权限未做）：nginx 上确认 `/openapi.json`、
   `/docs` 亦可 404（纵深防御），以及 `limit_req` 对 `/static/` 的覆盖。
