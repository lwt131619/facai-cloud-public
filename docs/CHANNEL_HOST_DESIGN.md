# 多租户渠道宿主架构（Channel Host Design）

> 契约：**每客户通道默认持久** —— 自助绑定一次，云端 7×24。
> 本文档定义微信/WhatsApp 宿主（hosting）的第一刀（Schema + 查询层 + 三个自助端点）
> 与第二刀（微信 per-owner 凭据隔离 + QR 确认迁移 + per-owner 监控循环，均已落地，
> 见 §6 落点清单）。WhatsApp 配对码真实流程与套餐配额仍未实现，本文如实标注每部分的落点。

## 0. 目标与非目标

**目标**：自助注册的客户（tenant）把自己的个人微信 / WhatsApp 号码接入
FACAI Cloud，绑定一次后由云端持续收发，与站长（operator）自己的渠道互不干扰。

**非目标（第一刀不做，接口已预留；其中前两项与监控循环已由第二刀落地）**：

- QR 扫码确认后的凭据落盘与轮询（`poll_login` 确认路径）——**第二刀已落**；
- WhatsApp 配对码（pairing code）真实流程；
- per-owner 监控进程/循环的启动与停止——**第二刀已落**（微信）；
- 套餐（plan）驱动的配额计费。

## 1. 凭据命名空间：per-owner 隔离

现状：全部渠道凭据只有一份全局命名空间 —— 进程环境变量 +
`data/providers/wechat.env`（`facai/channels/wechat.py:142` 在启动时加载，
`wechat.py:157` 扫码确认后写入）。这是**站长专属**的：审计
（`docs/AUDIT_2026-10-04_10AGENT.md` HIGH 项）确认过，若租户能触达同一份
QR 管线与凭据文件，即可顶掉站长进行中的二维码、并把攻击者凭据持久化。

目标形态：每个 owner 一棵独立子树，凭据永不跨 owner 出现：

```
data/providers/                     ← 站长全局命名空间（现状，不动）
  wechat.env                        ← 站长个人微信凭据（0600）
data/providers/hosting/             ← 客户宿主命名空间
  <owner_id>/
    wechat.env                      ← 该客户的个人微信凭据（第二刀落盘：QR 确认即写）
    whatsapp.env                    ← 该客户的 WhatsApp 凭据（配对码流程落地后）
```

- `owner_id` 即注册用户名，字符集 `[A-Za-z0-9_\-]{3,32}`（`facai/auth.py:32`），
  天然路径安全，无需再转义；`..`、分隔符不可能出现。
- 第一刀只落约定与数据库行（`channel_host_bindings.session_dir` 存相对路径
  `providers/hosting/<owner_id>/<channel>`，相对数据根目录）。
- **第二刀落地**：host/start 由 `WeChatProvider.for_owner_state()`（私有 env
  映射、独立 state_dir）按 owner 构建实例并出码；QR 确认（`poll_login` 到
  AUTHORIZED）经该实例把凭据写进上述子树——与站长实例并存，互不读写。
  凭据文件 0600、目录 0700（provider 既有写入纪律），进程全局 `os.environ`
  永不被客户凭据触碰（宿主实例的 env 是独立映射，非进程环境）。

## 2. 绑定生命周期与状态机

每个 `(owner_id, channel)` 至多一行（`PRIMARY KEY (owner_id, channel)`），
即**每客户每通道 1 绑定**由表结构保证，不靠应用层自觉。

```
            start                (扫码确认, 下一刀)         stop / 吊销
  (无行) ──────────▶ BINDING ────────────────▶ ACTIVE ─────────────▶ UNBOUND
                       ▲  │                       │                    │
                       │  └────── stop（中途放弃）┘                    │ reopen
                       └──────────────────────────────────────────────┘
                                   start（重新开始）
```

- `BINDING`：绑定进行中（QR 已出码 / 配对码已下发），尚无可用凭据。
- `ACTIVE`：凭据确认、云端持续收发中（下一刀才会到达的状态）。
- `UNBOUND`：终态。**重开 = 把同一行更新回 `BINDING`**（新 `session_dir`、
  刷新 `updated_at`、保留首次 `created_at` 与行 `id`），不删行不插新行 ——
  历史可追溯，且不会因唯一键残留产生两行。
- 非法迁移（如 `ACTIVE→BINDING`、`UNBOUND→ACTIVE`、无行时直接置 `ACTIVE`）
  在 store 层抛 `ValueError`，API 层转 409/422。

## 3. 与站长通道的隔离原则

1. **端点分面**：站长通道管理仍走 `/api/v1/channels/*`（`require_admin_owner()`，
   `facai/api.py:539`）；客户宿主走 `/api/v1/my/channels/<channel>/host/*`
   （任何登录 owner 可操作**自己的**行，无管理员守卫，也不需要）。
   本刀新增端点不触碰任何站长全局状态（不改全局 env、不写 `data/providers/*.env`）。
2. **第一刀的已知边界（已由第二刀消除）**：第一刀的微信 QR 出码复用站长
   单例 provider，客户出码会顶掉站长进行中的二维码、确认后还会写全局
   `wechat.env`。第二刀起 host/start 改走 owner 自己的
   `WeChatProvider.for_owner_state()` 实例：`self._login`、iLink 游标与
   凭据文件全部按 owner 隔离，双实例并行由测试
   （`tests/test_channel_host.py::test_station_and_host_wechat_instances_do_not_cross_talk`）
   钉死。
3. **凭据互不可见**：客户的宿主行不存任何 secret（只有状态、路径、名称）；
   per-owner 凭据只落 owner 子树，写入纪律（0600 文件 / 0700 目录、临时文件
   + 原子替换）与站长侧同一套代码（`facai/channels/wechat.py` 的
   `_save_credentials`）。

## 4. 配额

- **每客户每通道 1 绑定**：由 `PRIMARY KEY (owner_id, channel)` 结构性保证；
  `HOST_BINDING_QUOTA_PER_CHANNEL = 1` 是该事实的显式命名，应用层不做第二次
  （可能说谎的）检查。
- **上限随套餐**：当前系统没有套餐模型（`facai/billing.py` 只有月度预算
  `user_budgets`），因此**本刀不发明套餐数**。预留：套餐行落地后，把
  「可宿主通道数 / 可宿主通道集合」挂到套餐上，`host/start` 在配额超限时
  返回 409 并引用套餐名；在套餐模型出现之前，任何此类数字都是编造。
- 未列入 `HOST_CHANNELS` 的通道（telegram/line/relay…）不开放宿主：telegram/line
  走既有 `channel_bindings` token 绑定（`facai/api.py:1229` 起），relay 是站长通道。

## 5. 审计与吊销

- **行级审计**：`created_at`（首次绑定）+ `updated_at`（每次迁移）落在
  `channel_host_bindings` 行上；`UNBOUND` 不删行，吊销历史可查。
- **事件审计**：第一刀在 `channel_events` 追加 `HOST_BINDING_STARTED` /
  `HOST_BINDING_STOPPED`（`message_id` 承载 `host:<owner_id>`）；第二刀追加
  `HOST_BINDING_ACTIVATED`（QR 确认迁移 ACTIVE 时）与 `HOST_MONITOR_STOPPED`
  （凭据失效 3 连停轮询时，payload 带原因与连击数）。事件不携带任何 secret。
- **吊销**：客户自助 `POST .../host/stop`（幂等）；第二刀的 stop 额外结束该
  owner 的监控任务，但**不删凭据文件**——站长侧强制吊销（含删除 owner 凭据
  子树）与文件清理仍是后续批次，当前如实不提供"删了但文件还在"的半吊子版本，
  未清理的文件只存在于该 owner 自己的子树内。

## 6. 落点清单（已有 / 本刀 / 后续）

| 项 | 状态 | 位置 |
|---|---|---|
| telegram/line token 绑定（secretbox） | 已有 | `channel_bindings` 表，`facai/store.py:238`、`facai/store.py:1920` |
| NOTIFY 循环优先走 owner 自己的 bot | 已有 | `facai/notify.py:120`（`_owner_bot`） |
| 站长微信 QR 管线（出码/轮询/验证码） | 已有 | `facai/channels/wechat.py:288`，管理员端点 `facai/api.py` |
| 管理员守卫（租户 403） | 已有 | `require_admin_owner()`，`facai/api.py:539` |
| `channel_host_bindings` 表 + 状态机 CRUD + `list_by_owner` | 第一刀 | `facai/store.py` |
| `/my/channels/{wechat,whatsapp}/host/{start,status,stop}` | 第一刀 | `facai/api.py`（wechat 出码走 per-owner provider；whatsapp 501 占位） |
| `HOST_BINDING_STARTED/STOPPED` 事件审计 | 第一刀 | `channel_events` |
| per-owner provider 实例 + 凭据写入 `providers/hosting/<owner>/<channel>.env` | **第二刀** | `WeChatProvider.for_owner_state`（`facai/channels/wechat.py`），host/start 改走 owner 实例 |
| QR `poll_login` 确认 → `BINDING→ACTIVE` 迁移 + `HOST_BINDING_ACTIVATED` 审计 | **第二刀** | `GET/POST /api/v1/my/channels/wechat/host/login[/verify]`，`facai/api.py` |
| per-owner 监控循环生命周期（绑定→循环起→解绑/凭据失效 3 连→循环停） | **第二刀** | `host_wechat_monitor` + 启动扫描 `active_host_bindings`（`facai/api.py`，任务挂 `app.state.host_monitor_tasks`） |
| 站长/客户消息流互不串扰（owner 作用域去重与重放、ack 按 owner 路由） | **第二刀** | `facai/store.py`（`record_channel_message`/`channel_message_by_external_id`/`pending_inbound_messages` 加 owner 作用域）、`facai/api.py`（`send_channel_ack`、站点循环重放限 `owner_id="admin"`） |
| WhatsApp 配对码真实流程（→ `ACTIVE`） | 未实现 | Meta Cloud API pairing code |
| 宿主吊销时的凭据文件清理（含站长侧强制吊销） | 未实现 | §5 所述吊销流程的一部分；本刀 stop 只停轮询，不删文件 |
| 宿主 mission 的 NOTIFY 进度推送按 owner 实例路由 | 未实现 | `facai/notify.py` 的 `_send` 仍查站长 registry（见 §8 已知边界） |
| 套餐配额（可宿主通道数/集合） | 未实现 | 套餐模型落地后 |

## 7. API（第一刀 + 第二刀）

| 方法/路径 | 语义 | 失败语义 |
|---|---|---|
| `POST /api/v1/my/channels/wechat/host/start` | 在**该 owner 自己的 provider 实例**上出 QR，行置/重开 `BINDING`，QR 数据**原样**返回 | provider 失败→502 且不动行；已 `ACTIVE`→409 |
| `GET /api/v1/my/channels/wechat/host/login` | 轮询自己的 QR（第二刀）：确认→凭据落 `hosting/<owner>/wechat.env`、行迁移 `ACTIVE`、审计、**启动该 owner 的监控任务** | 无 BINDING 行→409 |
| `POST /api/v1/my/channels/wechat/host/login/verify` | 提交自己 QR 的验证码（`need_verifycode` 路径，第二刀） | 无 BINDING 行→409；码形非法→422 |
| `POST /api/v1/my/channels/whatsapp/host/start` | 配对码流程**占位**：如实 501 Not Implemented，附流程说明；不建行（没有开始就没有 BINDING 行） | 501 |
| `GET /api/v1/my/channels/{channel}/host/status` | 读自己那行；无行→`state:"NONE"` | 未知通道→404 |
| `POST /api/v1/my/channels/{channel}/host/stop` | 置 `UNBOUND`；幂等（重复 stop→200）；结束该 owner 的监控任务（第二刀） | 无行→404 |

全部要求登录（走既有中间件），只操作 `current_owner()` 自己的行；
租户 403 语义（站长端点）不变。

## 8. per-owner 监控（第二刀）与如实边界

- **循环**：每个 ACTIVE 绑定一条 asyncio 任务（`app.state.host_monitor_tasks`，
  每 owner 至多一条）。每轮先读绑定行——非 ACTIVE 即退出（stop 条件一）；
  未配置或 `fetch_updates` 返回 `UPSTREAM_REJECTED` 记一次凭据失效，
  **连续 3 次**（`HOST_WECHAT_CREDENTIAL_FAILURE_LIMIT`）即停并审计
  `HOST_MONITOR_STOPPED`；OK/超时/网关 5xx 等非拒绝结果重置连击（长轮询
  空闲 524 是常态，不是凭据问题）。轮询间隔
  `FACAI_HOST_WECHAT_POLL_INTERVAL`（默认 5s）。
- **归属**：收到的消息走既有 `process_channel_message`，但 `owner_id` 固定为
  绑定 owner——消息行、任务、预算闸门、ack 全部落在客户名下；站长的监控循环
  重放面收敛到 `owner_id="admin"`，永不重放客户消息（反之亦然）。
- **停机恢复**：进程启动时扫 `active_host_bindings("wechat")`，逐行构建
  owner 实例（构造即从盘上凭据文件加载）并重启监控任务。
- **已知边界（如实声明，后续批次）**：
  1. `facai/notify.py` 的进度推送（阶段完成/失败）仍查站长 registry——客户
     宿主 mission 的进度行可能走站长实例；任务创建即时的 ack 已按 owner
     路由（`send_channel_ack`），进度推送的 per-owner 路由未做。
  2. 凭据失效停轮询后，绑定行保持 ACTIVE（重启再做一次有界 3 击探测）；
     不自动 UNBOUND、不删凭据文件。
  3. 真实 iLink 双账号并发扫码（两台真机同时出码/确认/收发）只在 mock 层
     验证过隔离逻辑，真机行为待真机验证。
  4. 宿主 mission 的发送方向（客户主动回复）只有站长的管理端 reply 端点；
     客户自服务回复端点未做。
