# Deployment record — channel base layer, integer progress, deep-sea UI

Append-only log of verified production deployments. Each entry is written **after**
the post-deploy checks pass; no entry is written from inspection alone. No credential
value is ever recorded here.

## 2026-10-03 深夜 · WhatsApp relay bridge live, WeChat iLink outbound root-caused, on-chain billing sync

A late-night round on top of `HEAD` `692278b`. The WhatsApp relay bridge and the TronGrid
billing-sync daemon are deployed as systemd units on the VPS. The WeChat outbound fault was
root-caused; its recovery is a manual admin re-scan (not yet performed). No credential value
is recorded here.

### WhatsApp personal-account relay bridge — live (verified end-to-end)

- **What:** `bridge/whatsapp-relay/` in the repo — a standalone Node bridge cloned from
  AutoClaw's embedded Baileys **6.7.24** (the same version AutoClaw bundles in `main.cjs`).
  Deployed at `/opt/facai-whatsapp-relay`; systemd `facai-whatsapp-relay.service`
  (`Restart=always`, 24/7). Bound account `85260712923`; credentials persist in `wa-auth/`,
  reconnect is automatic.
- **Wiring (server-side relay channel unchanged):** inbound text → `X-Facai-Signature` HMAC →
  `POST /api/v1/channels/relay/webhook`; outbound replies queue in the durable
  `channel_messages` outbox → the bridge polls `POST /api/v1/channels/relay/send` → delivers →
  acks (`SENT`; undelivered stay `QUEUED`, at-least-once).
- **Verified:** e2e round trip passed — relay webhook → mission created → outbox → bridge
  delivery to `85260712923@s.whatsapp.net` → ack/`SENT`. Deploy/scan steps:
  `bridge/whatsapp-relay/README.md` (Node ≥18 on the server, one phone scan).

### WeChat iLink outbound fault — root-caused (recovery pending)

- **Symptom:** inbound works, outbound does not — `sendmessage` returns
  `ret:-2 "prepare failed"` (with or without `context_token`).
- **Root cause:** the old host `ilinkai.wechat.com` answers **524** (edge timeout), which also
  affects `getupdates` and `get_qrcode_status`. The adapter now falls back to / tolerates the
  primary Tencent host `ilinkai.weixin.qq.com` (the qrcode-status probe returns `wait` again).
  Neither rate-limiting nor a global Tencent outage.
- **Recovery (admin action, not yet done):** back up and delete
  `/etc/facai-cloud/providers/wechat.env` and `/opt/facai-cloud/data/providers/wechat.env`
  → restart → scan the console QR **twice** (tap 开始扫一扫 on the official page, then scan again)
  → allow.

### On-chain recharge sync (USDT-TRC20) — deployed

- **Single table** `chain_transfers` (txid primary key): each USDT-TRC20 transfer into the
  cold wallet is recorded exactly once (idempotent).
- **Single crediting path** `Store.approve_recharge_and_credit(recharge_id, decided_by, txid)`:
  admin review and automatic on-chain clearing share this one function, so approval and the
  monthly-credit write happen in one transaction; a `status != PENDING` guard makes it
  exactly-once — no double-bookkeeping between the two paths.
- **Daemon** `facai/ops/tron_sync.py` — systemd `facai-tron-sync.service` (`Restart=always`),
  scans TronGrid every 30s: records every USDT transfer to `FACAI_USDT_TRC20_ADDRESS`; a
  PENDING recharge carrying a txid is auto-cleared when that txid is a real transfer to our
  address for at least the requested amount; a PENDING recharge without a txid is auto-cleared
  only when exactly one in-window transfer matches the amount exactly; everything else stays
  `UNMATCHED` for a human — never guessed.
- **Reconciliation** `GET /api/v1/billing/reconciliation` (admin only): ledger `approved_total`
  vs on-chain `matched_total` + `delta`, plus `approved_without_chain` (manual approvals with
  no on-chain proof) and `unmatched_transfers` (unclaimed arrivals) — every mismatch visible.
- **Tests:** `tests/test_chain_sync.py`.

### Credential rotation

VPS root password rotated in this round (in-session only, not recorded anywhere); the prior
value is invalid.

### Session write-up

This round is also documented in `docs/SESSION_2026-10-03_WHATSAPP_WECHAT_BILLING.md`.

## 2026-10-03 · Overnight launch-readiness round (HEAD `3696b65`)

Three same-day deploys, each verified live. Local full suite grew 574 → **605 passed**;
shipped suite re-run on the deployed artifact from a clean directory.

### Client-supplied models (客户端自带模型) — the main §四 task, now live

- **What a client gets:** `POST /api/v1/my/models` registers their own OpenAI-compatible
  endpoint + API key (name/model/endpoint/key). The key is Fernet-encrypted at rest
  (`facai/secretbox.py`; master key in `FACAI_MASTER_KEY` or auto-created
  `data/master.key`, mode 0600 — never in the database), decrypted only in memory at
  use time, never returned (masked tail only), never logged, never on a command line.
  `GET /api/v1/my/models` (masked), `DELETE /api/v1/my/models/{id}` (also removes the
  registry entry), `POST /api/v1/my/models/{id}/test` (honest upstream probe with the
  stored credential, per-owner cooldown).
- **Their missions run on their model:** `default_binding` now prefers a
  client-registered model (`user_model_id`) over the shared defaults; the scheduler
  attaches the decrypted key in memory for one dispatch; execution materializes a
  project-level `opencode.json` in the mission workspace (provider `facai-user`,
  `apiKey: "{env:FACAI_USER_MODEL_KEY}"`) plus the key in the child process
  environment only, excludes the config from git (`.git/info/exclude`) and deletes it
  after the run — checkpoints can never capture the key.
- **SSRF guard:** registration resolves DNS and refuses private/loopback/link-local
  targets (a client-chosen endpoint must not become a probe against the deployment's
  internals). Self-hosted LAN models opt in via `FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`
  (the Facai API's own loopback stays refused either way).
- **Live end-to-end (verified):** client1 registered `https://api.deepseek.com` with a
  key (masked `****7650` in every listing); SSRF probe `http://127.0.0.1:8765` → 422;
  client1's auto mission bound `user-3ae94f1c…` and all 5 steps executed with
  `provider: facai-user`, `model_ref: facai-user/deepseek-chat`, exit 0 — including
  two PARALLEL branches running cleanly under the new serialization — mission
  `COMPLETED 100%`; no plaintext key in events or the workspace; the project config
  was cleaned up after the run. The test registration was deleted afterwards.
- **Console:** new 我的模型（客户端自带模型与密钥）panel — register/list/test/delete.

### opencode execution serialization (fixes live `database is locked`)

OpenCode keeps one shared state store under HOME, so two concurrent `opencode run`
processes (parallel mission branches) killed each other with SQLite locks — observed
live during the relay acceptance. `OpenCodeAdapter` now serializes executions by
default (`FACAI_OPENCODE_SERIAL=0` opts out for hosts isolating state per run) and
accepts per-run `env` additions (used by the user-model credential injection).
Parallel branches keep their worktree isolation; they take turns on the CLI.

### Outbound chat audit

Mission acknowledgements and notifier progress lines are now audited into
`channel_messages` (`OUTBOUND`, text only) for every channel, closing the blind spot
where Telegram sends were invisible in the console audit.

### Channel status at press time

- **Telegram:** Telegram's own `getWebhookInfo` confirms the webhook correctly
  registered (0 pending); zero notify/ack send failures since the stall fix; the 7
  healed missions all completed and their completion lines went out.
- **WeChat:** Tencent's iLink upstream (`ilinkai.wechat.com/ilink/bot/getupdates`)
  kept answering `524` (edge timeout on long-polls) — degraded since 2026-10-02
  15:57 UTC, ~64 failures/10min at press time, with an occasional successful poll
  (one inbound got through at 16:19). The monitor retries honestly with backoff and
  the channel stays `CONFIGURED`. Morning action if still failing: re-scan the QR to
  re-bind (the only lever we hold; do NOT start a login casually — it resets the
  session).

### Verification

- Local full suite: **605 passed / 0 failed** (runs 2, 5, 6*, 7; run 3 exposed a
  nondeterministic order assertion in the new serial test — thread lock order is not
  deterministic, now compared as a multiset — and run 6 exposed the mobile layout
  test clicking a stage button that live output re-renders; it now force-clicks,
  4/4 stable afterwards. Run 6's serial flake was the same pre-fix file).
- Shipped suite on the deployed artifact (full archive copy incl. deploy/ + docs/):
  **586 passed / 18 skipped (Playwright absent on the server) / 1 failed** — the
  single failure is the pre-existing `test_checkpoint_initializes_nested_workspace…`
  environment artifact proven to fail against the pre-deploy backup code as well.
- Final deploy of HEAD `692278b`: `deploy.sh update` exit 0, service `active`,
  health `ready`.
- Live spot checks: unauthenticated `/api/v1/my/models` → 401; `/billing/me`
  anonymous → 401; relay webhook without signature → 403; 我的模型 panel renders in
  the console (form + empty state); mission list shows all repaired Telegram
  missions at 100%.

## 2026-10-03 — HEAD `fb71596` (relay channel for AutoClaw + stuck-mission heal)

Two same-day deploys: `686d8c4` (relay channel) then `fb71596` (partial-dispatch heal,
shipped after live acceptance exposed the stall). Both verified live.

### Relay channel (`facai/channels/relay.py`) — AutoClaw WhatsApp forwarding

- **Contract:** AutoClaw (operator's own bridge client driving a personal WhatsApp
  account — unofficial access, the operator's own risk; FACAI never touches WhatsApp)
  POSTs `{external_id, sender, text}` to `/api/v1/channels/relay/webhook`, signed with
  the n8n scheme (`X-Facai-Signature: sha256=<hex>` over `<timestamp>.<body>`,
  `X-Facai-Timestamp`, ±300s skew). Inbound walks the same pipeline as every
  dispatching channel (dedupe by external_id, conversion lease, verbatim original,
  full-DAG dispatch, operator ownership, chat ack). Outbound is a durable outbox:
  replies queue in `channel_messages` (`OUTBOUND/QUEUED`); AutoClaw polls
  `POST /api/v1/channels/relay/send` (same signature) and acks ids, which flip rows
  to `SENT` (at-least-once). Relay joined `NOTIFY_CHANNELS`, so ack + progress lines
  flow automatically.
- **Credential:** `RELAY_SHARED_SECRET` seeded into
  `/etc/facai-cloud/providers/relay.env` (0640 root:facai, generated at deploy); the
  same value was handed to the operator in-session for AutoClaw's configuration.
  Channel reported `CONFIGURED` / `missing: []` immediately after restart.
- **Tests:** 10 new (`tests/test_relay_channel.py`); full suite 584 passed before the
  heal fix, 587 after (3 new in `tests/test_dispatch_heal.py`).

### Live end-to-end acceptance (relay)

- Unsigned webhook → `403`; signed webhook → `200`, mission created, full 5-step DAG
  queued, owner `admin`, original text verbatim.
- Outbox delivered 11 messages across the poll/ack loop: mission ack, ▶️/✅ progress
  lines per stage (and the honest ❌ line for the one step that failed on first run —
  see the opencode lock note below).
- After retrying the failed step, mission `COMPLETED 100%` (5/5). Retry performed via
  the existing `/api/v1/jobs/{job_id}/retry` endpoint.

### Partial-dispatch heal (`fb71596`) — fixes "Telegram 不回复" stall

- **Root cause found live:** 7 Telegram missions created 2026-10-02 14:08–14:59 UTC
  (before full-DAG dispatch shipped in the 15:43 UTC deploy) carried only their first
  step's job and stalled at 20%; `ensure_channel_dispatch` treated any existing job as
  "already dispatched" and could never repair them, so no further progress — and no
  completion replies — was ever produced.
- **Fix:** `ensure_channel_dispatch` now heals: a mission whose steps are all covered
  by jobs is untouched; a partially-dispatched mission gets exactly its missing steps
  enqueued (never a duplicate of a covered step). 3 tests pin the contract.
- **Live heal:** re-dispatched all 7 stuck source messages via the idempotent
  `POST /api/v1/channels/{channel}/messages/{message_id}/dispatch` (all `201`);
  scheduler picked up the 28 backfilled jobs immediately.

### WeChat upstream outage (observed, not ours to fix)

`ilinkai.wechat.com/ilink/bot/getupdates` answered `524 Response Timeout by Origin
Server` repeatedly around 17:38–17:41 UTC on 2026-10-03, so personal-WeChat polling
received nothing during that window. The monitor retried honestly with backoff; the
channel status stayed `CONFIGURED`. If inbound WeChat silence persists after the
upstream recovers, the binding may need a fresh QR login — that was NOT required by
press time.

### opencode concurrency note (pre-existing, exposed by parallel DAG)

The relay e2e mission's first two stages ran in parallel and one `opencode` invocation
died with `database is locked` (opencode's own shared state store, not the mission
workspace; exit code 1, logged in the step's stderr artifact). A solo retry of the
same step succeeded. Pre-existing hazard amplified by parallel branches, unrelated to
the relay channel; candidate follow-up: serialize opencode invocations adapter-side or
isolate its state dir per run.

## 2026-10-03 — HEAD `bcbb616` (budget hard limits, parallel worktree isolation, billing panel)

- **Commit:** `bcbb616f36f9cbccd330cab32aba7e24a23a6c41` — *"Handoff correction: strip live
  credentials from tracked doc, record real deploy gap"*, whose parent `38fa461` carries the
  actual feature work (per-user monthly budget gate, parallel worktree isolation + merge gate,
  账单 console panel, WeChat ack registry compat). The handoff doc was corrected in the same
  deploy: it no longer contains any credential value.
- **Server:** `77.221.148.118`, app root `/opt/facai-cloud` (`deploy.sh update`; only
  `/opt/facai-cloud` was touched).
- **Method:** `git archive HEAD` → SFTP `/tmp/facai-head.tar.gz` → `/opt/facai-staging` →
  `bash ./deploy/deploy.sh update`. Deploy exited `0`; `facai-cloud.service` `active`.
  Note: `deploy.sh` syncs an explicit include list (`facai/`, `tests/`, `docs/`, `deploy/`,
  README, requirements, start-opencode.ps1), so `HANDOFF-NEXT.md` is never shipped to the server.
- **Pre-deploy backups (taken before any change):**
  - app code: `/var/backups/facai-cloud/app-20261002T164524Z.tar.gz`
  - configuration: `/var/backups/facai-cloud/env-20261002T164526Z.tar.gz` (`0600`)
  - database: `/var/backups/facai-cloud/db-predeploy-20261002T164411Z.sqlite3`
    (`integrity ok`, 14 missions, no `user_budgets` table yet)

### Verification (all passed)

| Check | Result |
| --- | --- |
| systemd / health | `facai-cloud.service` `active`; `GET /api/v1/health` → `200` `{"status":"ready","phase":2}` |
| Billing routes live | `/api/v1/billing/me`, `POST /api/v1/billing/check`, `/api/v1/billing/users/{username}/budget` all `401` anonymous and `200` with HTTP Basic; `/api/v1/pricing` → `200` |
| Budget table | production DB now has `user_budgets` (20 tables, was 19); mission/registry counts preserved (14 missions, skills 21, models 12, agents 7, runtimes 4) |
| Deployed code == HEAD | `facai/execution.py`, `api.py`, `billing.py`, `prompting.py`, `static/index.html` byte-identical to `HEAD` (CRLF aside); `billing.py` present, baseline hash changed |
| Parallel worktree code | deployed `execution.py` contains `WORKTREE_CREATED`/`WORKTREE_MERGED`; `checkpoint.py` `prepare_worktree`; `execution.py` `merge_worktree` |
| Shipped suite on the deployed artifact | 552 passed / 18 skipped (Playwright not installed on the server) / 4 failed — all 4 environment-caused, see below |
| Console billing panel | 我的账单（本月）panel renders for a logged-in operator with real values (本月消耗 US$0.0833 · Token 合计 427,639 · 任务数 12 · 月度上限 不限), matching `GET /api/v1/billing/me` |
| Channel owner attribution | all 10 channel-created missions (telegram + wechat) carry `owner_id='admin'`; `send_channel_ack` registry-compat path present in deployed `api.py` |
| Live workspace prerequisite | live mission workspaces are real git repos with checkpoint commits (`Facai checkpoint: 理解需求`) and no stale linked worktrees |

### The 4 test failures are environment-caused, not regressions

- `tests/test_channels.py` (3 tests) fail **only** when pytest runs from `/opt/facai-cloud`,
  because that directory is production and holds live provider state
  (`data/providers/wechat.env`) — the adapter correctly reports `CONFIGURED` where the test
  expects a clean-instance `UNCONFIGURED`, and the webhook returns `403` instead of `503`.
  The same deployed code from a clean directory passes `test_channels.py` (**58 passed**), and
  the pre-deploy code fails identically from `/opt/facai-cloud`.
- `tests/test_checkpoints_and_cost.py::test_checkpoint_initializes_nested_workspace_without_committing_parent`
  fails against the pre-deploy backup archive as well, so it predates this deployment; it is a
  server-environment artifact (git in a commit-less temp repo). Product impact: none —
  `checkpoint.py` passes identity explicitly (`-c user.name=Facai -c user.email=facai@localhost`),
  so production checkpoints never depend on a global git config.

Public HTTPS for `https://whstapp.lol` remained up; the operator console was verified through a
real browser session after the deploy.

### Credential rotation — completed 2026-10-03

The VPS root password and the administrator password (`FACAI_ADMIN_PASSWORD` in
`/etc/facai-cloud/facai.env`) were rotated as a separate, explicitly sequenced step
(the removal of the leaked values from the tracked handoff doc does not purge them
from local git history `38fa461`). No credential value is recorded here or anywhere
in the repository; the new values were handed to the operator in-session only.

- Root: changed via `chpasswd`; the new password was verified by opening a **fresh**
  SSH connection before any later step; the old password is confirmed rejected
  (`AuthenticationException`).
- Admin: `FACAI_ADMIN_PASSWORD` replaced via `sed` (previous file backed up to
  `/var/backups/facai-cloud/facai.env-pre-rotation-<ts>`, mode 0600), service
  restarted, health `200` + `active` re-verified. Login form path: new `200`,
  old `401`; HTTP Basic (script path): new `200`, old `401`; app-direct loopback
  checks agree. No nginx-level auth exists (no `auth_basic` in the site config),
  so these two paths are the complete credential surface.
- During verification, one transient was observed and resolved: immediately after
  `systemctl restart`, a Basic-auth check with the old password returned `200`
  once (the old worker process was still draining — the same slow-stop behavior
  noted in the 2026-10-02 entry); repeated checks and loopback tests confirm the
  old password is fully rejected after the restart settles.
- Unaffected credentials: provider keys under `/etc/facai-cloud/providers/*.env`,
  WeChat binding credentials, GLM key file, registered `platform_users` accounts
  (`Facaile7777`, `Wanbaolu131`, `client1` — their passwords were not touched).

## 2026-10-02 — HEAD `6275061`

- **Commit:** `62750610c0cfe0a9d65ecf917571797d00a10bbd`
  *"Cover channel send and reserved endpoints in API tests"* (includes the messaging
  channel base layer `facai/channels/`, honest whole-percent mission progress, and the
  deep-sea Facai Mission Control identity).
- **Server:** `77.221.148.118`, app root `/opt/facai-cloud` (Facai-only path; nothing
  else was touched).
- **Method:** source staged to `/root/facai-cloud-src`, then the existing
  `deploy/deploy.sh update` flow (scoped `rsync --delete`, venv refresh, classified
  provider env, systemd + nginx re-apply, service restart).
- **Pre-deploy backups (taken and verified before any change):**
  - config: `/var/backups/facai-cloud/config-predeploy-20261001T160227Z.tar.gz` (`0600`)
  - database: `/var/backups/facai-cloud/db-predeploy-20261001T160227Z.sqlite3`
    (online snapshot, `integrity ok`, 3 missions)
  - app code: `/var/backups/facai-cloud/app-20261001T160227Z.tar.gz`
- **Credential templates seeded (empty placeholders only, no values set):**
  `/etc/facai-cloud/providers/{telegram,wechat,whatsapp}.env` (`0640 root:facai`);
  the existing DeepSeek credential was preserved unchanged.

### Verification (all passed)

| Check | Result |
| --- | --- |
| systemd | `facai-cloud.service` `active` / `running`, `NRestarts=0` |
| HTTPS gate | `deploy/check_public_access.sh` exits 0 — authoritative dnsowl apex+www all `77.221.148.118`, TCP 443, TLS valid to 2026-12-30 |
| Login protection | anonymous `401`, HTTP Basic authenticated `200` (over HTTPS and loopback) |
| Health check | `GET /api/v1/health` → `200` |
| Channel status API | `GET /api/v1/channels` → WhatsApp `UNCONFIGURED`, WeChat/Telegram `UNIMPLEMENTED` (honest states, `configured=false`, no false "connected") |
| Database integrity | `PRAGMA integrity_check` → `ok`; 13 tables including the new empty `channel_messages`; mission/event/registry counts preserved (3/53/3) |

Public access verified for `https://whstapp.lol`.

### Rollback reference

Code: `deploy/deploy.sh rollback /var/backups/facai-cloud/app-20261001T160227Z.tar.gz`.
Config: restore `config-predeploy-20261001T160227Z.tar.gz` over `/` and restart.
Database: stop the service, `facai.backup restore --dest <db-predeploy-...sqlite3>`, restart.

## 2026-10-02 — daily progress, WeChat QR, tenant compatibility

- **Source:** workspace snapshot (not committed to Git during this deployment).
- **Changes deployed:** stdlib PNG rendering for personal-WeChat QR codes (removes
  the missing-Pillow 500); duplicate inbound-message conversion leases and retry
  recovery; owner-scoped registry/auth/mission compatibility; authenticated daily
  provider progress API and dashboard panel. Codex repaired the registry
  compatibility regression found during full-suite validation.
- **Tests:** full local suite **488 passed** (repeated after the final dashboard
  counter change); progress + WeChat suite **39 passed**; `git diff --check` clean.
- **Method:** staged managed source in `/root/facai-cloud-src`, then ran
  `bash ./deploy/deploy.sh update`.
- **Backups:** app code `/var/backups/facai-cloud/app-20261002T120222Z.tar.gz`;
  classified environment `/var/backups/facai-cloud/env-20261002T120224Z.tar.gz`
  (mode `0600`; credential values are not recorded here).

### Verification

| Check | Result |
| --- | --- |
| systemd | `facai-cloud.service` active/running after update |
| Local health | `GET /api/v1/health` → `200` |
| Authentication | anonymous `/api/v1/daily-progress` → `401` as expected |
| Public site | authentication gate returned `401` to unauthenticated curl; browser redirects to `/login` |
| WeChat | QR renderer is deployed; account scan/authorization was not performed by deployment |

The restart invalidated the browser's in-memory admin session; sign in again to
view the new progress panel. During the graceful stop, the old process exceeded
systemd's stop timeout and was killed before the new process started; the new
service reached active state and returned health `200` afterward.

## 2026-10-02 · Full-speed development round

**Commit range:** handoff baseline import → current HEAD (see `git log`).

Deployed and verified live on `https://whstapp.lol`:

- Auto mission: one plain-language sentence -> validated step DAG (model-planned,
  deterministic fallback) -> full-DAG dispatch with dependency gating.
- Self-service registration (max five accounts, PBKDF2 passwords, tenant isolation
  fixed: webhook traffic attributes to the operator account).
- Output-token pricing panel ($400/MTok default, env-tunable) and milestone step
  progress (1% start / 90% checkpoint / 100% done).
- Telegram live: webhook + ack + progress notifications (chunked sends, 429
  backoff, editMessageText live refresh of the progress message).
- WeChat live: binding completed via the official
  `@tencent-weixin/openclaw-weixin-cli` (iLink 2.4.9 semantics; binded_redirect
  treated as success; single-flight status polling). Credentials stored in
  `data/providers/wechat.env` (0600). A real inbound "1" became a mission and
  all five stages completed (100%).
- Goose second agent adapter (registry-selectable), skill SKILL.md import
  (parse-only, always pending, pipe-to-shell refused).

Verification evidence: local suite 551 passed before this round's additions;
each deploy followed by `systemctl is-active` + local `health 200` + public
HTTPS checks. Rollback: `deploy/deploy.sh rollback`.

## 2026-10-06 → 10-07（合并大条目：七通道家族 · 客户自助绑定 · 授权A案 · 快照备份 · 轮换与独立审计）

多窗口并行推进（共享树纪律：每流只 add 自己的文件；每日备份的 add -A 会扫入并行 WIP，
署名以 HANDOFF §二十一/§二十二 为准）。测试基线 1079 → **1140 全绿**。

- **七通道家族**（`b04b9ab`..`6e26c5f`）：Discord（桥 HMAC `X-Facai-Signature`，与
  whatsapp-relay 同信任模型）+ Slack（原生 v0 HMAC + 5 分钟重放窗）适配器上线；签名
  webhook → dedupe → 全 DAG 派发；NOTIFY/dispatch 家族扩容；37 新用例。生产 UNCONFIGURED
  待凭据（env 文件就位即活，无需改码）。生产 9/9 PASS。
- **客户自助绑定 discord/slack**（`52a5a3b`）：真实上游预检（Discord /users/@me、
  Slack auth.test 且读响应体 ok 门）、格式闸门、secretbox 加密落库永不回显；owner 进度走
  客户自己的 Bot（OWNER_BINDING_CHANNELS）；控制台/移动端表单。8 新用例。
- **渠内审批 A 方案**（`04eb2bf`，并行窗口交付）：一次批准管全程（uses=剩余步骤、TTL=任务
  当日额度）；本窗口补跑全量 1079 绿验证。
- **每日按日快照备份**（`facai-handoff/backup-github.sh` v2）：镜像覆盖 + 按日私密仓
  `facai-cloud-backup-YYYY-MM-DD`（回滚点）+ >10 天清理（无 delete_repo scope 时降级
  archive+登记）。首日快照已验证。9:30 自动化自动继承。
- **基建**：fail2ban 重装+启用（unit 曾缺失；auth.log 2.2 万次爆破；ignoreip=161.142.0.0/16；
  即刻封禁生效）；wa-auth 755→700+内部 600（桥在线无损）。
- **口令轮换（操作员批准）**：VPS root 与管理面板 FACAI_ADMIN_PASSWORD 均已换新，
  纯口令断言（`allow_agent=False, look_for_keys=False`——本机公钥已授权，不关密钥回退
  验证无效）新 ✓/旧 ✗。§十九/§二十一 泄露提交值全部作死。
- **独立产品审计**：生产公开面根源模式全扫零泄露；两处客户面卫生修复（管理重置占位符
  去真实账号名；relay 标签去内部工具名），生产残留 0。
- **说明书三方接入专章**（`f63f08b`）：/guide 第六节逐渠道绑定步骤（TG/Discord/Slack/
  LINE）+ 绑后四用法 + 422 速查表；授权章节更新 A 方案语义；静态面外链白名单纳入三家
  官方门户。生产实测含新章。
- **Agent 交付（Dify manifest 吸收）**（`6b29601`/`41f1f07`）：ingest 产出
  `facai.permissions/1` 权限节（六族全枚举、deny-by-default）→ 受限 CapabilityGrant
  模板映射；13 新用例。
- 验证证据：每轮部署 deploy-now/HEAD-only 部署 9/9 PASS（health 200 / manifest 200 /
  三服务 active）；绑定面、guide、showcase 生产逐项实测。回滚：`deploy/deploy.sh rollback`。
