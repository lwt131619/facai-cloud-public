# 会话记录 2026-10-03 深夜 — WhatsApp 桥 / 微信排查 / 收费端链同步

本文件由会话整理而来，作为主仓库（facai-cloud）的持续记录。权威现状仍以
`HANDOFF-NEXT.md` + `docs/DEPLOYMENT_RECORD.md` 为准，本文件补齐 2026-10-03
深夜这一轮的改动与结论。

## 一、WhatsApp（个人号）中继桥 — 已上线并端到端验证

- **来源**：从 AutoClaw（zwork）内嵌的 WhatsApp 连接栈克隆 —— 同一库
  **`@whiskeysockets/baileys` 6.7.24**（AutoClaw `main.cjs` 内即为该版本）。
- **落点**：仓库 `bridge/whatsapp-relay/`（`index.mjs` / `package.json` /
  `facai-whatsapp-relay.service` / README）。服务器部署在 `/opt/facai-whatsapp-relay`。
- **链路**：入站 WhatsApp 文本 → `X-Facai-Signature` HMAC → `relay/webhook` 建任务；
  出站任务回复进 `channel_messages` 持久 outbox → 桥轮询 `relay/send` → 投递 → ack
  （at-least-once，未投递保持 QUEUED）。服务器端 relay 渠道**零改动**。
- **24/7**：凭据落盘 `wa-auth/`（扫码一次），断线自动重连，systemd
  `Restart=always` + `StartLimitIntervalSec=0`。
- **验证**：账号 `85260712923`，relay loop active；端到端实测（webhook → 建任务 →
  outbox → 投递 `85260712923@s.whatsapp.net` → SENT）全通。

## 二、微信 iLink — 出站故障根因确诊 + 扫码保活看板

- **症状**：能收不能发。`sendmessage` 返回 **`ret:-2 "prepare failed"`**（带不带
  `context_token` 都一样）；`getupdates` 收件方向正常（有 inbound、有 context_token）。
- **根因**：**不是频控、也不是腾讯全网故障** —— 是旧域名 `ilinkai.wechat.com`
  当前 **524 故障**（状态接口不可用）；适配器已回退到腾讯主域名
  `ilinkai.weixin.qq.com`（实测 `get_qrcode_status` 恢复正常返回 `wait`）。
- **重扫流程**：备份并删除 `/etc/facai-cloud/providers/wechat.env` 与
  `/opt/facai-cloud/data/providers/wechat.env` → 重启 → 控制台出二维码 →
  **手机扫两次**（公众号页点「开始扫一扫」再扫一次）→ 允许。
- **扫码保活看板**（本次新增，解决"每次都要重新扫"）：服务端 `wx_qr_keeper.py`
  始终维持一个有效二维码，**只有过期才换新**（避免换新让在扫的码失效），过期自动轮换、
  进程守护自动拉起、授权成功后自动停；本地镜像 + 自动刷新页面展示实时状态。
- **注意**：iLink bot 的 `bot_token` **只能**通过官方扫码链路签发；PC 微信的客户端
  会话凭证与 iLink 协议不通用，抓 PC 微信握手包无法换出 bot token、也不可重放。

## 三、收费端（USDT TRC20）持久链同步 — 本次新增

需求："收费端也要持久链同步，且不能分家对不上"。

- **单表** `chain_transfers`（txid 主键）：每笔进入冷钱包的 USDT-TRC20 转账
  只记一次，幂等。
- **单一入账路径** `Store.approve_recharge_and_credit(recharge_id, decided_by, txid)`：
  **管理端审核**与**链上自动核销**共用同一函数——核准与计入月度额度在同一事务里，
  状态机 `status != PENDING` 守卫保证**恰好一次**入账，杜绝两条路径各自记账导致的偏差。
- **守护** `facai/ops/tron_sync.py` —— systemd `facai-tron-sync.service`
  （`Restart=always`），每 30s 拉 TronGrid：
  1. 记录所有进入 `FACAI_USDT_TRC20_ADDRESS` 的 USDT 转账（幂等）；
  2. 附了 txid 的 PENDING 充值 → 该 txid 若为**到我们地址且金额≥请求**的真实转账 →
     自动核准入账；
  3. 未附 txid 的 PENDING 充值 → 仅当窗口内**恰好一笔**金额精确匹配 → 自动核准；
  4. 其余一律留 `UNMATCHED` 给人工，**绝不猜**。
- **对账** `GET /api/v1/billing/reconciliation`（仅管理员）：`approved_total`
  （账本）vs `matched_total`（链上已匹配）+ `delta`，并列出 `approved_without_chain`
  （无链上凭证的人工核准）与 `unmatched_transfers`（没人认领的到账）——所有对不上的行可见。
- **不改用户控制**：仅"已证实的到账"自动入账；其余仍需管理员审核。

## 四、运维要点（本轮）

- **VPS root 口令已轮换**：会话内传递、未落盘。旧值已失效；提醒更新密码管理器
  「ETH-bank运维系统」条目。
- **env 配置**：`FACAI_USDT_TRC20_ADDRESS` 在 `/etc/facai-cloud/facai.env`；
  `TRONGRID_API_KEY`（可选，提额）同理，缺失时用公共端点。
- **部署**：常规 `git archive` → SFTP → `/opt/facai-staging` → `deploy.sh update`；
  新 unit 落到 `/etc/systemd/system/` 后 `daemon-reload && enable --now`。
