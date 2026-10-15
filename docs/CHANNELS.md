# 消息渠道基础层（Message Channels）

这一层把外部消息服务接进 Facai Mission Control，并保持“不伪造接通”的原则：没有完整凭据的渠道一律报告未配置并拒绝发送，绝不假装已经连通。

## 抽象

`facai/channels/` 定义三件事：

- **统一入站模型** `InboundMessage`：`channel / external_id / sender / recipient / kind / text / received_at / raw / metadata`。
- **统一出站模型** `OutboundReply`：`channel / recipient / text / reply_to / metadata`。
- **渠道契约** `ChannelProvider`：`required_env`、`configured()`、`status()`、`verify_challenge()`、`verify_signature()`、`parse_inbound()`、`send()`。

`ChannelRegistry` 一次性构建全部 provider，`statuses()` 返回不含任何密钥值的健康状态：

```
state: CONFIGURED | UNCONFIGURED | UNIMPLEMENTED
missing: [环境变量名...]   # 只有名字，永远没有值
verify_ready / send_ready: bool
```

## 能力状态一览

区分“代码已实现”“需要配置或授权”“尚未实现”，避免把未配置的适配器当成已上线：

| 渠道 | 已实现 | 需配置 / 授权 | 未实现 |
| --- | --- | --- | --- |
| Telegram | 文本 Webhook 验签、按 `update_id` 去重、入站统一派发、`sendMessage` 出站 | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET`，并用 `setWebhook` 注册回调 | 编辑、媒体、回调查询 |
| WhatsApp | Webhook 验证/验签、文本/按钮/交互回复类入站统一派发（重复投递幂等、缺绑定可重投）、Cloud API 出站（含 `reply_to` 引用回复） | Meta `WHATSAPP_*` 凭据 | 出站仅文本；模板与媒体消息 |
| 个人微信 iLink | 二维码授权、后台 `getupdates` 长轮询入站统一派发、文本回复 | 管理员必须完成真实扫码授权 | 图片、文件、语音、群聊 |
| Relay（AutoClaw 中继） | n8n 同款 HMAC 验签入站、按 `external_id` 幂等去重、持久化 outbox 出站（poll/ack） | `RELAY_SHARED_SECRET`，以及用户侧运行的 AutoClaw 桥接客户端 | FACAI 主动直连 WhatsApp（刻意不做） |

以上“已实现”均只经过本地模拟上游测试；没有真实凭据或授权前，所有渠道保持 `UNCONFIGURED`，也不会产生真实消息收发。

## WhatsApp（已实现文本收发骨架，需配置真实 Meta 凭据）

采用官方 Meta Cloud API 形状：

- **Webhook 验证（GET）**：校验 `hub.mode=subscribe` 与 `hub.verify_token`，返回 `hub.challenge`。
- **Webhook 接收（POST）**：用 `X-Hub-Signature-256` 对原始请求体做 HMAC-SHA256 验签，只消费 `messages` 变更并解析 Cloud API 入站消息；必须有 Meta `wamid` 和发送方 ID 才接收入站路径，确保重复投递可幂等。结构异常的正文不会让 webhook 解析器崩溃，原始消息对象仍保留用于审计。
- **出站发送**：`POST https://graph.facebook.com/{version}/{phone_number_id}/messages`，Bearer 令牌来自环境，成功返回上游 `messages[].id`。`OutboundReply.reply_to` 会转成 Cloud API 的 `context.message_id`，实现引用回复；空白的 `reply_to` 直接拒绝，不会发出无引用的消息。
- **入站派发**：官方 WhatsApp Business Platform Cloud API 文本 Webhook 与 Telegram 走同一条流程——原话逐字保存 → 专业提示转换 → 创建 Mission → 队列派发。按 Meta 的 `messages[].id` 幂等：重复投递复用同一 Mission 与 job，不重复建单；若首次因缺少有效默认绑定而暂缓，补齐绑定后由平台重投即可完成入队。支持文本及按钮/交互回复中的文字；媒体文件本体与语音转写暂未接入，原始媒体对象留在审计 payload，不会伪装成可执行文本。

凭据从进程环境读取，服务器上来自分类环境文件 `/etc/facai-cloud/providers/whatsapp.env`（`deploy/providers/whatsapp.env.example` 模板，`0640 root:facai`，systemd 单元通过 `EnvironmentFile=-/etc/facai-cloud/providers/*.env` 加载）。密钥不写入数据库、日志或 Git。

缺少任一凭据时：GET/POST webhook 与 `send` 都返回 `503`，并只列出缺失变量名。此适配只支持官方 Business Platform Cloud API；不尝试自动化个人 WhatsApp 账号或 WhatsApp Web。

## Telegram（文本适配器已实现，真实账号未接通）

遵循 [Telegram Bot API](https://core.telegram.org/bots/api)。POST Webhook 校验
`X-Telegram-Bot-Api-Secret-Token`，规范化新文本消息并持久化；按 update_id 去重，
重启与并发重投不会重复写入。同一外部 ID 在不同渠道相互独立。
`send` 使用 `sendMessage`，校验上游 ok 和 message_id 后才报告 SENT；
区分认证失败、收件人拒绝、限流、超时和传输错误，不自动重试发送。
文本 Webhook 入站会在保存原话后立即走同一条专业提示转换、Mission 创建、默认绑定和入队流程；重复 update_id 不重复建 Mission 或 job，且会重试之前因缺少绑定而暂缓的队列操作。没有有效绑定时 Webhook 返回 503 和明确的 deferred 结果，消息及 Mission 仍保留，补齐绑定后 Telegram 的重投可以完成入队。转换失败时原话保留并返回显式 `CONVERSION_FAILED` 结果。Webhook 请求最大 1 MiB。编辑、媒体及回调查询暂不处理。

配置 `/etc/facai-cloud/providers/telegram.env` 的两个变量，重启服务，再通过
Telegram `setWebhook` 注册 HTTPS 回调地址及相同 secret_token。
凭据完整仅表示 CONFIGURED，尚需真实账号收发验收；本次只有本地模拟上游测试，未部署。

## Relay（AutoClaw 中继，已上线）

面向外部桥接客户端（AutoClaw）的通用中继渠道。**风险边界必须明确：AutoClaw 驱动个人
WhatsApp 账号属于非官方接入，封号风险由用户自担；FACAI 与 WhatsApp 之间没有任何直接
连接，FACAI 只接收 AutoClaw 的 HTTP 转发并把回复放回 outbox。**

- **入站**：AutoClaw `POST /api/v1/channels/relay/webhook`，body 为
  `{external_id, sender, text}`（text 原话逐字保存），签名复用 n8n 方案：
  `X-Facai-Signature: sha256=<hex>` over `<timestamp>.<body>` + `X-Facai-Timestamp`
  （默认 ±300s 时间窗，`RELAY_MAX_SKEW` 可调）。入站走与其他派发渠道完全相同的管线：
  幂等去重、转换租约、原话永久保留、全 DAG 派发、owner 归属操作员账号、聊天 ack。
- **出站**：FACAI 永不推送 WhatsApp。每条回复（任务 ack + 进度行）进入
  `channel_messages` 持久化 outbox（`OUTBOUND/QUEUED`）；AutoClaw 轮询
  `POST /api/v1/channels/relay/send`（同一签名）拉取待投递消息，投递成功后用
  `{"ack_ids": [...]}` 确认；已确认的 id 标记 `SENT` 且不再返回（at-least-once）。
  未 ack 的消息会在下次轮询中重复返回。
- **凭据**：`/etc/facai-cloud/providers/relay.env` 的 `RELAY_SHARED_SECRET`
  （HMAC 共享密钥，AutoClaw 侧配置同一值）。缺失时渠道诚实报告 `UNCONFIGURED`，
  webhook 返回 503，outbox 保持为空。

## Discord（适配器已实现，等待凭据）

- 凭据文件：`/etc/facai-cloud/providers/discord.env`
  - `DISCORD_BOT_TOKEN=`：Developer Portal → Bot 页的三段式 Bot Token（出站推送用）
  - `DISCORD_WEBHOOK_SECRET=`：与入站桥共享的随机串（进站验签用）
- 入站：信任模型与 WhatsApp 桥相同——客户侧桥把消息事件打包成 `{"messages":[…]}` POST 到
  `/api/v1/channels/discord/webhook`，`X-Facai-Signature` 携带原始 body 的 hex HMAC-SHA256；
  无签名/错签名/未配置一律 403/503 关闭。原生 Interactions 的 Ed25519 验签需要原生加密
  依赖，刻意不走（见 channels/discord.py 模块注释）。
- 出站：`POST /channels/{id}/messages`（Bot token），2xx 且回显具体 message id 才记
  `DELIVERED`；2000 字上限诚实拒绝（TEXT_TOO_LONG）。
- 客户自助绑定：`PUT /api/v1/my/channels/discord`（bot_token + chat_id=频道 ID），绑定前
  真实调 `GET /users/@me` 预检。

## Slack（适配器已实现，等待凭据）

- 凭据文件：`/etc/facai-cloud/providers/slack.env`
  - `SLACK_BOT_TOKEN=xoxb-…`：Bot User OAuth Token（出站推送用）
  - `SLACK_SIGNING_SECRET=`：App 的 Signing Secret（进站验签用）
- 入站：App 的 Event Subscriptions 指向 `https://whstapp.lol/api/v1/channels/slack/webhook`，
  订阅 `message` 事件；平台用 Slack 原生 `v0` HMAC 验签（`v0:<ts>:<body>`）+ 5 分钟重放窗口，
  bot 回声与 subtype（编辑/删除）不入队。
- 出站：`chat.postMessage`（xoxb-），2xx 且 `ok:true` + 具体 `ts` 才记 `DELIVERED`；
  Slack 的鉴权失败是 200 + `ok:false`，原因码从响应体读（invalid_auth→AUTH_FAILED，
  rate_limited→RATE_LIMITED）。40000 字上限诚实拒绝。
- 客户自助绑定：`PUT /api/v1/my/channels/slack`（bot_token + chat_id=频道 ID），绑定前
  真实调 `auth.test` 预检。

## 个人微信 iLink（已接入适配器，等待扫码）

遵循腾讯 `openclaw-weixin` 项目公开的 iLink HTTP/JSON 协议：管理员在控制台点击“个人微信扫码授权”，Facai 申请二维码并轮询授权状态；确认后 Bot Token 写入 `$FACAI_DATA_DIR/providers/wechat.env`，目录 `0700`、文件 `0600`，不返回给浏览器、不写数据库或日志。重启后从该分类文件恢复。

授权后后台使用 `getupdates` 长轮询，逐字保存原话（包括首尾空白）和会话上下文，自动转换为专业提示、创建 Mission 并进入现有绑定/队列。只有本批消息都已进入本地持久化收件箱后，才确认并推进 iLink 游标；若保存失败则保留旧游标，让上游可重放。首次没有可用绑定时，消息与 Mission 会保留为暂缓状态；后台在管理员补齐绑定后自动重试派发，不依赖上游再次投递。按入站消息回复时保留 `context_token`。目前适配文本；图片、文件、语音和群聊尚未接入。微信不是普通 Webhook 渠道。本地已用真实 `create_app` + 模拟 iLink 服务验证完整链路：扫码授权 → 重启后从 `providers/wechat.env` 恢复 → 长轮询收文本 → 建 Mission/入队 → 依 `context_token` 回复 SENT，并验证未持久化消息时不推进游标。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/channels` | 全部渠道状态（管理员） |
| GET | `/api/v1/channels/{channel}` | 单渠道状态（管理员） |
| GET | `/api/v1/channels/{channel}/messages` | 渠道消息审计（管理员） |
| GET | `/api/v1/inbox` | 跨渠道收件箱（管理员，原话逐字保留） |
| GET | `/api/v1/channels/{channel}/messages/{message_id}/events` | 单条消息的转换/派发事件（管理员） |
| POST | `/api/v1/channels/{channel}/messages/{message_id}/dispatch` | 转换原话 → 建 Mission → 入队（管理员，幂等） |
| GET | `/api/v1/missions/{mission_id}/channel-messages` | Mission 关联的来源消息（管理员） |
| GET | `/api/v1/channels/{channel}/webhook` | 渠道 Webhook 验证（公开，靠令牌） |
| POST | `/api/v1/channels/{channel}/webhook` | 渠道 Webhook 接收（公开，靠签名） |
| POST | `/api/v1/channels/{channel}/send` | 出站发送（管理员） |
| POST | `/api/v1/channels/relay/send` | AutoClaw outbox 轮询+确认（公开，靠 relay HMAC 签名） |
| POST | `/api/v1/channels/wechat/login` | 创建个人微信二维码（管理员） |
| GET | `/api/v1/channels/wechat/login` | 轮询扫码状态（管理员） |
| POST | `/api/v1/channels/wechat/messages/{message_id}/reply` | 依原会话上下文回复（管理员） |

Webhook 在传输层是公开的，但端点内部用 verify token / HMAC 签名自我认证；其余渠道接口仍受会话认证保护。

## 入站消息 → Mission 路由

`dispatch` 把一条入站消息转成可执行 Mission，规则保持“不伪造”：

1. **原话永久保留**：`channel_messages.text` 从不被改写；专业提示单独存在 `missions.professional_prompt`。
2. **转换失败不丢消息**：内容为空等无法转换时，消息状态置 `CONVERSION_FAILED`、记录 `channel_events`，返回 422，消息仍在库中可重试。
3. **幂等**：同一消息重复派发返回同一 Mission；建 Mission 与消息绑定在单个 `BEGIN IMMEDIATE` 事务里，并发派发只会得到一个 Mission。
4. **自动绑定与入队**：仅在存在**真实有效**的默认绑定（适配器已接线的 Agent + 可解析的 Model + Runtime）时才自动绑定并 `enqueue`，同时写入 `DISPATCH_QUEUED` 事件；否则只写 `DISPATCH_DEFERRED` 并保持 `WAITING`，绝不假装已入队。
5. **可审计时间线**：`PROMPT_CONVERTED` 事件携带原话、专业提示与 `basis`（转换依据），UI 在 Mission 详情中同时展示原话、专业提示、转换依据、来源消息与阶段进度。

## 当前配置状态（2026-10-07 实测）

- 已 CONFIGURED：Telegram（@myaleratbot 实测活）、微信 iLink（token 已扫码落库）、
  Relay（whatsapp-relay 桥自持会话 `85260712923`）。
- 保持 UNCONFIGURED（等凭据，诚实示人）：LINE（等官方号 token）、Discord、Slack
  （env 文件就位即 CONFIGURED，无需改码）。
- 客户自助绑定四通道开放：telegram / line / discord / slack（`/api/v1/my/channels/*`，
  绑定前一律真实预检）。
- 反向代理：`/api/v1/channels/*/webhook` 沿用 nginx HTTPS 入口（公网可达、签名自我认证），
  未单列限速策略（/api/ 边缘限速已覆盖）。
