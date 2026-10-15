# Facai Cloud 客户交付与授权验收

本说明区分已实现并由本地模拟测试覆盖的能力，与必须由客户实际授权后才能验收的外部连接。当前交付代码不表示 Meta、Telegram 或微信账号已经接通，也不表示已部署到客户生产环境。

## 账号、访问权限与容量边界

- 可使用单管理员兼容配置（`FACAI_ADMIN_USER` / `FACAI_ADMIN_PASSWORD`），或配置最多五个登录用户。多用户配置使用 `FACAI_USERS_JSON`，值为用户名到密码的 JSON 对象，例如 `FACAI_USERS_JSON='{"alice":"<长随机密码>","bob":"<长随机密码>"}'`。通过部署环境文件保存，文件权限保持 `0640 root:facai`；不要把真实密码放入命令行、聊天或仓库。配置无效或超过五人时服务会在启动阶段拒绝启动。
- 每个用户只能读取自己的 Mission、关联消息和费用合计；历史 Mission 与渠道消息在数据库迁移时归到 `FACAI_ADMIN_USER`（默认 `admin`）。多用户仍使用同一权限级别，没有角色或每用户配额；模型/Agent/Runtime 注册表、渠道账号及其外部平台授权为实例级共享设置。通过 HTTPS 登录，脚本可使用各自账号的 HTTP Basic。
- 登录失败按客户端地址限流，默认连续 5 次失败后，在 300 秒窗口内拒绝登录；这不是 API 通用限流。
- 调度器默认并发 2，可用 `FACAI_MAX_WORKERS` 调整，范围 1–8（每个应用实例）。这是同时运行任务上限，不是用户或月度调用配额；月度消费上限按「费用与用量」一节强制执行。应用没有渠道用户数配额。
- **任务创建双重限额（防转售批发）**：每用户同时在飞（WAITING/RUNNING）任务有硬上限，默认 10，可用部署环境变量 `FACAI_MAX_ACTIVE_MISSIONS_PER_OWNER` 调整；达到上限后新建任务返回 HTTP `402`「活跃任务已达上限（N/上限）」，任务完成或将步骤取消即释放名额。在 60 秒滑动窗口内每用户最多创建 6 个任务，超速返回 HTTP `429`「任务创建过于频繁」；402 与 429 文案不同，容量问题与频率问题分开报告。
- **边缘每 IP 限速（已配置于部署侧）**：nginx 对 `/api/`、`/login` 与任务事件流按客户端 IP 限速 `10r/s`、`burst=20 nodelay`（`deploy/nginx/facai-cloud.conf` 的 `facai_edge` 区）。同一路由器/NAT 后的多个客户端、同一客户端换任意 User-Agent，共享同一 IP 的请求预算——单个出口 IP 无法靠并发连接或多身份伪装放大请求速率。放大转售吞吐必须换 IP，这本身就是可审计的异常信号。
- 出站消息文本接口限制为 1–4096 个字符。Telegram 与 WhatsApp webhook 请求体限制 1 MiB。外部服务另有自己的配额、限流、模板政策与消息窗口规则，应按相应平台当前控制台和文档确认。

## 费用与用量

### 月度消费硬上限与 402 拦截

Facai 对每个用户强制月度消费硬上限（USD）。未显式配置的用户采用实例默认值 `FACAI_DEFAULT_MONTHLY_LIMIT_USD`；0 表示该用户不设上限。消耗取两个口径中较大者：成本表记录的 USD 台账，以及按运营方冻结价目表对上游报告 token 用量的估算（只报总量、不拆分输入/输出的响应按保守估算计价，不会显示为免费）。

达到上限后，应用拦截所有**新任务**：Web/API 创建、转换或入队任务返回 HTTP `402`，并附「本月已消耗 $X / 上限 $Y」；渠道入站消息被标记 `BUDGET_EXCEEDED` 并暂缓（DEFERRED），渠道会收到一条指引回复。已在执行中的任务不会被追溯取消。下个自然月额度自动重置，或由运营方提高额度后立即恢复。用户可用 `GET /api/v1/billing/me` 查看当月消耗与上限，用 `POST /api/v1/billing/check` 做干跑检查。

### USDT（TRC20）充值入账

收费以 USDT 为准：单笔最低 $100，支付窗口 5 分钟；收款地址只从部署环境变量 `FACAI_USDT_TRC20_ADDRESS` 读取。用户在控制台提交充值请求并附上链上 txid 后，TRON 链上同步守护进程与管理员核准共享同一条入账路径：核准金额**叠加**进该用户的月度额度（月度额度同时充当已入账余额），因此充值到账即提高可消费上限。一个 txid 只能对应一笔充值。

### 自助下调额度

租户可通过 `PUT /api/v1/billing/users/{username}/budget` **只下调**自己的月度上限（且必须大于 0）；上调或设为 0（不限制）仅运营方可操作。查看自己的预算为自助接口，查看他人预算仅运营方可。

### 记录口径与上游费用

Facai 记录执行过程可获得的 token / 上游成本信息，供任务审计与月度上限判定使用；成本数字基于上游实际报告的用量，不构成成本保证。模型与消息平台费用由客户分别向对应上游支付。

部分模型响应只返回 token 用量而未提供价格，成本表会记录 `USD 0.00` 占位值；上限判定对此类响应按保守估算计入，不会视为免费。这不代表调用免费，也不能替代财务对账；请以模型供应商账单为准。WhatsApp 的费率及免费额度会随 Meta 当期政策、消息类别、地区和业务账户而变化；Telegram Bot API 的收费功能/限额也以上游账户和官方当前说明为准。

## 渠道实际授权

服务端必须有稳定 HTTPS 公网地址，且该路径由客户控制。凭据只能通过部署的分类环境文件配置（参见 [部署说明](DEPLOYMENT.md)）；不要贴在聊天、工单、命令行参数、截图或仓库中。填写后重启服务，并从管理员控制台确认渠道状态为 `CONFIGURED`。状态仅表示本地配置齐全，不代表第三方授权或真实收发已经成功。

### Telegram Bot

1. 客户在 Telegram 中通过官方 `@BotFather` 创建 Bot，并妥善保管其 Bot Token。
2. 在 `/etc/facai-cloud/providers/telegram.env` 配置 `TELEGRAM_BOT_TOKEN` 和自行生成的 `TELEGRAM_WEBHOOK_SECRET`；secret 需符合适配器支持的字符与长度约束。`deploy/providers/telegram.env.example` 注释了安装位置与权限。
3. 用 Bot API `setWebhook` 将 `https://<客户域名>/api/v1/channels/telegram/webhook` 注册为回调，并把完全相同的 secret 作为 `secret_token`。Webhook 必须公网可达且 TLS 有效。
4. 用真实测试账号向 Bot 发送唯一测试文本。确认渠道收件箱保留原话、提示转换记录、Mission 和队列状态可追溯；再测试重复 webhook 不会重复创建 Mission/job，以及错误配置会明确失败。

当前只处理文本消息和 `sendMessage`；编辑、媒体、回调查询不支持。适配器不会自动重试出站发送；由调用方处理明确的失败状态。

### 客户自助绑定自己的 Telegram（每客户持久通道）

除实例级的站长 Telegram 通道外，每个登录客户可以绑定**自己在 `@BotFather` 创建的 Bot**，让任务进度 7×24 发到自己的聊天：绑定一次，云端持续生效，无需站长参与、也不占用站长通道。

1. 客户在 Telegram 通过官方 `@BotFather` 创建自己的 Bot 并复制完整 Bot Token；与该 Bot 建立对话（或把它拉入群/频道），取得接收进度的 `chat_id`（私聊为正整数、群为负数，频道可用 `@用户名`）。
2. 登录后（控制台会话或 HTTP Basic）调用 `PUT /api/v1/my/channels/telegram`，请求体 `{"bot_token": "<完整token>", "chat_id": "<chat_id>"}`。重复调用即换绑 token/chat，首次绑定时间保留。
3. `GET /api/v1/my/channels` 查看绑定状态（只返回 `bound`/`chat_id`/`created_at`，**任何接口都不回显 token**）；`DELETE /api/v1/my/channels/telegram` 解绑（未绑定时返回 404）。
4. 绑定后，该客户名下来源于聊天渠道的任务进度**优先经客户自己的 Bot** 发送到绑定的 chat（站长 Bot 未配置也一样可发）；发送失败如实记录为 `FAILED` 出站审计、不阻塞任务流水；解绑后自动回落站长级通道，站长通道行为不变。各客户绑定按账号互相隔离，互不可见、互不可删。

安全边界：token 使用与自有模型密钥相同的 secretbox（Fernet）加密落库，主密钥在部署环境（`FACAI_MASTER_KEY` 或数据目录 `master.key`，见部署说明），解密只发生在发送进度的那一刻；token 永不写日志、审计行或任何 API 响应。绑定接口要求交互式登录凭据（控制台会话/HTTP Basic），客户 API key 不能操作绑定。

### WhatsApp Business Platform Cloud API

1. 客户须拥有 Meta Business Portfolio、WhatsApp Business Account 和已登记的业务电话号码，并在 Meta Developer App 中启用 WhatsApp 产品。此集成仅支持官方 Cloud API，不支持个人 WhatsApp 账号或 WhatsApp Web/二维码自动化。
2. 从 Meta 管理界面取得业务号码的 Phone Number ID、可用于发送的 Access Token 和 App Secret；客户自行生成 `WHATSAPP_VERIFY_TOKEN`。将这些值填入 `/etc/facai-cloud/providers/whatsapp.env` 对应的 `WHATSAPP_PHONE_NUMBER_ID`、`WHATSAPP_ACCESS_TOKEN`、`WHATSAPP_APP_SECRET`、`WHATSAPP_VERIFY_TOKEN`。`deploy/providers/whatsapp.env.example` 注释了安装位置与权限。
3. 在 Meta App 的 Webhooks 设置中配置回调 `https://<客户域名>/api/v1/channels/whatsapp/webhook`，验证令牌填写同一个 `WHATSAPP_VERIFY_TOKEN`，并订阅 WhatsApp Business Account 的 `messages` 字段。完成 Meta 要求的 WABA 授权/订阅。
4. 在 Meta 测试环境或经客户批准的真实号码间发送一条唯一文本；核对原文、专业提示、Mission、派发状态和回复。重放相同 webhook 应复用既有消息/Mission，不重复执行。另验证错误签名会被拒绝。

当前只解析文本及按钮/交互回复中的文字；媒体本体/语音处理、模板消息、出站媒体均未接入。回复窗口、模板批准、号码/业务验证、区域费率和消息发送限制由 Meta 账户状态与当期规则决定，Facai 不代替 Meta 审批或承诺送达。

### 个人微信 iLink

1. 管理员登录 Facai 控制台，使用“个人微信扫码授权”生成二维码，再由账号所有者用目标微信账号实际扫码确认。
2. 等待登录状态确认成功，并向该账号发送唯一文本；检查收件箱原文、专业提示、Mission、任务状态和回复。
3. 授权令牌保存在 `$FACAI_DATA_DIR/providers/wechat.env`，文件权限应为 `0600`、目录 `0700`。不要复制或分享该文件。

这是腾讯公开 iLink 协议的个人账号适配，不等同于微信开放平台公众号/企业微信授权。当前仅支持文本私聊，不支持图片、文件、语音或群聊；iLink 上游服务可用性及账号资格需由客户实际扫码验收。

## 客户验收清单

- [ ] 管理员密码已设置；HTTPS、公网访问和匿名拒绝/登录成功已验证。
- [ ] 默认 Agent、Model、Skill/Runtime 绑定真实有效；否则入站消息会保留并显示暂缓/失败，不能当作已派发。
- [ ] 需要使用的渠道由客户完成真实授权；状态检查与一条真实收发测试通过。
- [ ] 用唯一文本核对原话完整保留、转换依据、Mission、事件时间线、最终执行结果和可用成本/token 指标。
- [ ] 重复投递验证幂等；若入站因缺绑定显示暂缓，补齐绑定后通过管理员接口 `POST /api/v1/channels/{channel}/messages/{message_id}/dispatch` 重试，并确认只创建一个 Mission/job；验证错误凭据、签名或授权时显示失败而非成功。
- [ ] 客户确认单管理员权限模型适用于使用人群；如需多客户/多角色隔离，先作为上线阻断项完成实现。
- [ ] 客户已确认月度上限策略：Facai 对超限新任务返回 402、渠道入站暂缓，充值入账与额度调整见「费用与用量」；上线前仍应核对上游费率与账户配额，供应商侧预算/告警建议另行建立。

以上验收涉及客户账号授权和真实消息，仅应由客户账号所有者在其控制的环境执行。本开发与本地测试没有请求真实凭据、注册真实 webhook、扫码或发送上游消息。

## 上游官方资料

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Meta WhatsApp Business Platform Cloud API 集合与入门条件](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api)
- [Meta WhatsApp Business Platform Webhook 参考](https://www.postman.com/meta/whatsapp-business-platform/folder/tduohwq/webhook-payload-reference)
- [腾讯 openclaw-weixin iLink 协议实现](https://github.com/Tencent/openclaw-weixin)

平台政策和价格会变更；本文件描述 Facai 当前代码能力，实际接入时应再次核对平台官方控制台与最新条款。
