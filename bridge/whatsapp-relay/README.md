# Facai WhatsApp 中继桥（Baileys 克隆版）

把 AutoClaw（zwork）内嵌的 WhatsApp 连接栈原样克隆进 Facai 项目：
**@whiskeysockets/baileys 6.7.24**（与 AutoClaw `main.cjs` 内的版本一致）。
跑在服务器上，直接拥有 WhatsApp 链接能力，不再依赖本机 AutoClaw 开着。

> **风险边界（与 relay 渠道文档一致）**：Baileys 驱动个人 WhatsApp 属非官方接入，
> 封号风险由站长自担；Facai 服务端仍然只通过 relay 渠道收发（HMAC 签名），
> 本桥是 relay 协议的官方桥接客户端。

## 架构

```
WhatsApp ⇄ [Baileys 本桥] ⇄ HTTPS + HMAC ─⇄ Facai relay 渠道
              │
              └─ wa-auth/ 凭据落盘（扫码一次，长期在线）
```

- **入站**：WhatsApp 文本 → `POST /api/v1/channels/relay/webhook`
  `{external_id, sender, text}`，头 `X-Facai-Signature: sha256=<hex>` over
  `<timestamp>.<body>` + `X-Facai-Timestamp`。
- **出站**：轮询 `POST /api/v1/channels/relay/send`（同签名），投递成功后
  `ack_ids` 确认；没投出去的不 ack，Facai 侧保持 QUEUED（at-least-once）。
- **持久链 24/7**：凭据落盘 `wa-auth/`（扫码一次）；断线 3s 自动重连
  （登出 15s）；轮询循环吞网络错误，失败按指数退避（`3×WA_POLL_MS` 起、
  `WA_POLL_BACKOFF_MAX_MS` 封顶，成功后立即复位）；systemd `Restart=always` +
  `StartLimitIntervalSec=0`。
- **日志噪音**：Baileys 首次登录阶段对 `init queries`/`fetchProps`/`presence
  update requests` 的 `Timed Out` 属其内部重试，非连接失败，已降级为 `debug`
  不刷屏；真正的断线/状态码仍经 `connection.update` 照常告警与重连。需要排查
  时把 `WA_LOG_LEVEL`（或仅 `WA_BAILEYS_LOG_LEVEL`）设为 `debug` 即可看到全部。

## 服务器部署

```bash
# 1. 目录 + 依赖（Node >= 18）
mkdir -p /opt/facai-whatsapp-relay && cd /opt/facai-whatsapp-relay
#    把 bridge/whatsapp-relay/ 下的 package.json index.mjs 传上来，然后：
npm install --omit=dev

# 2. 密钥：直接复用 relay.env（Facai 服务与本桥共用同一 RELAY_SHARED_SECRET）
#    systemd 单元里已写 EnvironmentFile=/etc/facai-cloud/providers/relay.env

# 3. 常驻
cp facai-whatsapp-relay.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now facai-whatsapp-relay

# 4. 链接（首次）：看日志里的二维码
journalctl -u facai-whatsapp-relay -f
#    手机 WhatsApp → 设置 → 已链接的设备 → 链接设备 → 扫码
#    或者配对码方式：Environment=WA_PAIR_PHONE=8613800000000 重启后看日志取码
```

## 本地运行

```bash
export RELAY_SHARED_SECRET=...            # 与服务器 relay.env 同值
export FACAI_BASE_URL=https://whstapp.lol
npm install && npm start                  # 二维码打在终端
```

## 配对码宿主（per-owner hosted sessions，第四刀）

站长的账号之外，本桥还为**客户宿主**提供独立会话：每个 owner 一棵
`wa-auth/hosting/<owner_id>/` 凭据子树、一条独立 Baileys 连接。客户免扫码——
Facai 服务端把客户手机号转给本桥，本桥 `sock.requestPairingCode(phone)` 铸出
8 位配对码原样返回，客户在自己手机 WhatsApp（设置 → 已链接的设备 →
用手机号链接）输入即完成链接。本桥同时启动时恢复所有已注册的客户会话
（`recoverHostSessions`），断线自动重连，与站长链路互不影响。

### 管理面（Host admin API）

本桥内置一个小 HTTP 管理面，**全部请求必须带 relay HMAC 签名**
（`X-Facai-Signature: sha256=<hex>` over `<timestamp>.<body>` +
`X-Facai-Timestamp`，与 relay 渠道同一把 `RELAY_SHARED_SECRET`，±300s 时窗）：

| 方法/路径 | 语义 | 应答（200，状态在 body 里） |
|---|---|---|
| `POST /host/pair` `{owner, phone}` | 以配对码模式（重）启该 owner 的会话并铸码 | `{state:"PAIRING_CODE", code, phone}` / `{state:"PAIRING_WAIT", phone}`（码未铸出，轮询取）/ `{state:"LINKED", phone}`（已在链）/ `{state:"RELINKING", phone}`（有存量凭据，重连原号，配对码不适用） |
| `GET /host/session?owner=…` | 该 owner 会话的当前状态 | `{state:"NONE"\|"PAIRING", code?, phone?}` / `{state:"LINKED", phone}`；过期配对码报 `code:null` |
| `POST /host/logout` `{owner}` | 注销该 owner 会话：`sock.logout()` 服务器侧解除配对，并**删除**该 owner 的 `hosting/<owner>/` 凭据目录 | `{state:"LOGGED_OUT"}` / `{state:"NONE"}` |

- owner 形如 Facai 用户名（`[A-Za-z0-9_-]{3,32}`），phone 为 7–15 位数字
  （E.164 去掉 `+`）；不合法 400，签名不对 403。
- **诚实边界**：客户会话的 `messages.upsert` 本刀**不转发**（debug 日志记
  dropped）——按 owner 路由收发是下一刀；在那之前客户流量绝无可能漏进站长
  relay 通道。绑定行 ACTIVE 的含义是"该客户会话已在桥上链接在线"。
- 配对码一次性且短命（`WA_HOST_CODE_TTL_MS`，默认 90s），过期即报 null；
  重新 `pair` 会重启会话铸新码，旧码随之作废。桥侧日志只记码长度，不记码值。
- `POST /host/logout` 是真正的吊销：不清凭据目录的话，桥会在其后静默重连
  客户号码——所以该目录必须删除（与微信宿主"只停轮询不删文件"不同，一条
  活的 WhatsApp 链接不能在客户解绑后继续存活）。

### 部署注意

- 管理面默认绑 `127.0.0.1:8790`（`WA_HOST_PORT=0` 整体停用）。Facai 服务与
  本桥不同机时，设 `WA_HOST_BIND=0.0.0.0` 并用防火墙/安全组只放行 Facai 主机
  IP —— HMAC 保护应用层，暴露面仍应最小化。
- Facai 侧配置 `FACAI_WHATSAPP_BRIDGE_URL=http://<host>:8790` 即接通；
  签名密钥复用 relay.env 的 `RELAY_SHARED_SECRET`，服务器上不存在第二把钥匙。
- `ProtectSystem=strict` 下 `ReadWritePaths` 已覆盖 `/opt/facai-whatsapp-relay`
  （`wa-auth/hosting/` 在其下），单元文件无需改动。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RELAY_SHARED_SECRET` | 必填 | relay HMAC 共享密钥（缺失直接退出，绝不空跑） |
| `FACAI_BASE_URL` | `https://whstapp.lol` | Facai 地址 |
| `WA_AUTH_DIR` | `./wa-auth` | Baileys 凭据目录（0700） |
| `WA_POLL_MS` | `5000` | outbox 轮询间隔 |
| `WA_PAIR_PHONE` | 空 | 配对码登录用手机号（可选，免扫码） |
| `WA_HOST_PORT` | `8790` | 配对码宿主管理面端口（`0` = 停用管理面） |
| `WA_HOST_BIND` | `127.0.0.1` | 管理面绑定地址；跨机访问设 `0.0.0.0` 并加防火墙白名单 |
| `WA_HOST_PAIR_WAIT_MS` | `10000` | `/host/pair` 等待铸码时长，超时回 `PAIRING_WAIT` 由轮询取码 |
| `WA_HOST_CODE_TTL_MS` | `90000` | 配对码视为有效的时长，过期后 `/host/session` 报 `code:null` |
| `WA_LOG_LEVEL` | `info` | 本桥（pino）日志级别 |
| `WA_BAILEYS_LOG_LEVEL` | 同 `WA_LOG_LEVEL` | Baileys 内部日志级别；首次登录的查询/在线状态超时噪音（`unexpected error in 'init queries'`、`fetchProps`、`presence update requests`）降级为 `debug`，真正的错误照常输出（见下） |
| `WA_POLL_BACKOFF_MAX_MS` | `60000` | outbox 轮询失败时的指数退避上限（起点 `3×WA_POLL_MS`，逐次翻倍后封顶） |

## 消息边界

- 只转发**文本**（含图片/视频说明文字）；媒体本体、语音、贴纸不转发，不伪装成可执行文本。
- 群发列表/广播（`@broadcast`/`@newsletter`）不转发；自己发的消息回环跳过。
- 以 Baileys 的 `key.id` 做幂等键，Facai 侧按 `external_id` 去重，重复投递不重复建单。
