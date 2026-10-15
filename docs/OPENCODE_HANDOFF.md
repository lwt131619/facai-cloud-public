# OpenCode handoff: finish Facai Cloud

You are continuing an existing repository. The owner wants OpenCode with DeepSeek to do the remaining implementation so their Codex conversation usage is conserved. Work in this repository. Inspect the code and Git state first. Preserve existing data and commits. Make small verifiable changes, run the relevant tests, and commit each completed increment. Do not report a feature complete until it runs and is verified.

## Product goal

Build Facai Cloud as the owner's browser-based Mission Control for PC and mobile. The architecture baseline is in `docs/ARCHITECTURE.md` and the working start is in `README.md`. The original product discussion established these requirements:

1. Facai is the single mission coordinator. OpenCode is a coding/operations agent; models, skills and runtimes are separate registries and can be changed per mission.
2. Preserve the owner's original input verbatim. Show a professional prompt/structured intent separately and let the owner inspect it. Do not silently change the requested meaning.
3. Support simultaneous missions. Each mission has isolated workspace, workflow graph, agent/model sessions, event/output streams, artifacts, Git checkpoints, tests, deployment and cost records. No cross-mission data leakage.
4. The Mission Control web UI shows real per-mission progress, current workflow node, agent output, timeline, files/artifacts, diff, tests and deployment status. Never invent progress or success.
5. Attribute outcomes accurately: model completed/refused/timed out/rate limited/auth failed, agent failure, tool failure, resource unavailable. A model refusal is an upstream model result, not a Facai decision.
6. Model Gateway supports registered OpenAI-compatible endpoints, including DeepSeek, with provider/model transparency. The owner prefers inexpensive models for routine work and uses larger self-hosted GPU models only on demand. Show provider, runtime location and cost where available.
7. OpenCode is the development executor. Implement a real mission-scoped adapter with explicit session lifecycle and output capture. n8n and messaging are external integration layers, not Facai Core.
8. Add a durable scheduler with concurrency limits, priority, pause/resume/cancel/retry, worker leases and recovery. Extend the data plane to PostgreSQL/Redis when the current SQLite design stops meeting those requirements.
9. Add secure cloud access, HTTPS, backup/restore verification and then public deployment. Phone and desktop views should see the same cloud mission state.

## Current verified state

- New Git repository in this directory. The first commits established the API, SQLite mission/event records, basic web console, registry separation, model gateway and OpenCode workspace preparation.
- Admin HTTP Basic authentication is active when `FACAI_ADMIN_PASSWORD` is set. Do not expose plaintext HTTP publicly.
- Registries are now SQLite-backed; model credentials are referenced using `api_key_env` and raw keys are rejected by the API. Default `deepseek-flash` model points at DeepSeek's official OpenAI-compatible endpoint.
- `python -m pytest -q` passes locally. Re-run before doing new work.
- On VPS `77.221.148.118` (Ubuntu 22.04, host `aba-bank.ptr.network`), `facai-cloud.service` now runs the current HEAD code, bound only to `127.0.0.1:8765`; local checks return `health=200`, anonymous `401`, authenticated `200`, with `NRestarts=0`.
- The owner's domain is `whstapp.lol`. As of 2026-10-01 the authoritative DNS gate passes: all three NameSilo `dnsowl` nameservers answer exactly `77.221.148.118` for both the apex and `www`. TLS was issued against that authoritative state and public access was verified (see the progress log). Recheck with `deploy/check_public_access.*` after any change.
- A dedicated deployment key now authenticates `root@77.221.148.118` with `ssh -i` and `BatchMode=yes`. The service config was migrated from the legacy `/etc/facai-cloud.env` to `/etc/facai-cloud/facai.env`; existing secrets were preserved verbatim.
- OpenCode 1.18.32 is installed on this Windows host. The DeepSeek credential has been saved in OpenCode's provider store and a minimal `deepseek-flash` API request returned HTTP 200. Never print, commit, copy into prompts, or log the credential.
- An unrelated previous application on the VPS was removed at the owner's confirmed request. Do not restore or integrate it.

## Next implementation order

1. Inspect the current Git state and run the tests. Verify no key is in Git files or logs before changing code.
2. Make the web console useful: mission creation, detail view, registry/model binding, real status transitions, live event timeline, responsive PC/mobile layout. Add meaningful end-to-end tests.
3. Implement mission-scoped worker execution and an OpenCode adapter. Start with one safe coding task in an isolated workspace, capture stdout/stderr/events, run tests, and confirm a second simultaneous mission stays isolated. Use the configured model and record its true upstream status.
4. Add scheduler, artifact and Git checkpoint tracking, recovery, cost ledger and proper failure states. Verify state survives process restart.
5. Add authentication/session management suitable for a public service, HTTPS, backup/restore test and production deployment. Keep the app on localhost until DNS, TLS and access control pass verification.
6. Add optional n8n, messaging, GPU worker and cloud/local handoff adapters incrementally. Each adapter must have a concrete integration test or reproducible manual acceptance check.

## Acceptance bar

- Start two missions; run steps independently; each list, event stream, workspace, session, artifact and progress view contains only its own mission data.
- A model refusal/timeout/rate-limit appears in that mission timeline with model/provider attribution and does not become a fabricated Facai refusal or mission success.
- Restart the server; missions, events, bindings and registry entries remain intact, while API keys are absent from the database and logs.
- OpenCode can complete a small legitimate development task in one mission and produce a code diff and test result visible in Mission Control.
- HTTPS works on the intended domain, unauthorized users cannot reach the API or console, and a verified backup can be restored.

Do not attempt to build the entire final vision in one unchecked patch. Continue until the next increment is complete, tested and committed; then proceed to the next. If an external account, DNS state or secret blocks a step, finish independent work and report the exact blocker.

## Progress log

Steps 1-4 of the implementation order (console, mission-scoped OpenCode execution, scheduler/checkpoints/cost/recovery, verified backup/restore) were completed and committed before this log. The remaining production increment was then implemented:

- **Session authentication** (`facai/auth.py`, commit `Add production session authentication...`): login/logout/session endpoints, `HttpOnly` `SameSite=Strict` session cookie, SHA-256-hashed token storage, per-session CSRF header on unsafe methods, per-address login throttling, and Basic auth retained for scripts. `tests/test_auth.py` covers login, CSRF, throttling, expiry, cookie flags and non-storage of raw tokens.
- **HTTPS and deployment plane** (`deploy/`, `docs/DEPLOYMENT.md`): nginx TLS/SSE reverse proxy, hardened systemd unit with `EnvironmentFile`, idempotent `deploy.sh` (`install`/`update`/`cert`/`status`/`logs`), and `check_public_access.sh` / `.ps1` gates that verify DNS, TCP, TLS and anonymous/authenticated HTTP. Application secrets stay in `/etc/facai-cloud/facai.env` and model credentials in the classified `/etc/facai-cloud/providers/*.env`; none are in code, Git, database or logs.
- **Backup restore drill** (`facai.backup drill`, commit `Add non-destructive backup restore drill...`): snapshots, verifies, restores into a temp directory and compares row counts without touching the live database.
- **Mobile verification** (`tests/test_mobile_ui.py`, commit `Verify and refine mobile console...`): real Chromium at 390x844 checks no horizontal overflow, vertical pipeline, >=40px tap targets, sticky header, ARIA live regions, and the full login flow.

`python -m pytest -q` passed 43 tests at that point.

### Product-core increment (committed after this log's first draft)

- **Upstream attribution** (`facai/gateway.py`, `tests/test_gateway_attribution.py`): the gateway now reports nine distinct outcomes (`MODEL_COMPLETED`, `MODEL_REFUSED`, `MODEL_TIMEOUT`, `MODEL_RATE_LIMITED`, `MODEL_AUTH_FAILED`, `MODEL_TRANSPORT_FAILED`, `MODEL_TOOL_FAILED`, `MODEL_RESOURCE_UNAVAILABLE`, `MODEL_FAILED`) with Chinese labels, a short reason and the raw upstream body, and token usage from the response.
- **Independent four-registry binding** (`facai/store.py`, `facai/api.py`, `tests/test_registry_binding.py`): missions gained `skill_id` and `runtime_id` with a column migration; `PATCH`-style updates bind any subset of agent/model/skill/runtime without clearing the others; unknown values are rejected before any write; runtimes carry `provider`/`location`.
- **Consistent visibility** (`facai/execution.py`, `facai/scheduler.py`, `facai/api.py`, `facai/static/index.html`, `tests/test_attribution_visibility.py`): every task event and API response carries the same `provider`/`model_id`/`model_ref`/`runtime_id`/`runtime_location`/`skill_id` plus `cost`/`tokens`, and the console binds and renders all four registries and shows the attribution line.
- **Reviewable prompts** (`facai/store.py`, `tests/test_prompt_review.py`): the original input remains byte-for-byte; each professional-prompt revision writes an audit event holding both the professional prompt and the exact original it was derived from.
- **Acceptance** (`tests/test_mission_isolation_e2e.py`): two missions run through the durable queue with different agent/model/skill/runtime bindings; their lists, events, sessions, artifacts, checkpoints, jobs, progress and cost are verified to contain only their own data, and each task carries its true provider/model/runtime/cost.

`python -m pytest -q` now passes 69 tests (`43 + 26 new`).

### Production deployment increment (2026-10-01) — public access verified

The former blockers (DNS propagation and VPS access) are both resolved. A dedicated
`ssh -i` key authenticated `root@77.221.148.118` (`BatchMode=yes`), and the following
was done and verified:

1. **Backup (non-destructive).** Pre-deploy snapshot at
   `/var/backups/facai-cloud/predeploy-20261001T125322Z/` containing an online
   SQLite backup (`db/facai.sqlite3`, `integrity_check=ok`), the full
   `/var/lib/facai-cloud` tree, `/opt/facai-cloud` (excluding `.venv`),
   `/etc/facai-cloud.env` (verbatim, mode 0600), the systemd unit, nginx sites and
   a `recon.txt`. `SHA256SUMS` recorded. No secret value was printed or committed.
2. **Config migration.** `/etc/facai-cloud/facai.env` (0640 `root:facai`) was built
   from the legacy `/etc/facai-cloud.env`, preserving the existing
   `FACAI_ADMIN_PASSWORD` verbatim and adding only non-secret managed keys
   (`FACAI_DATA_DIR`, `FACAI_COOKIE_SECURE`, `FACAI_TRUST_PROXY`). Data moved to
   `/opt/facai-cloud/data` (the legacy DB copy kept in place at
   `/var/lib/facai-cloud`). `deploy.sh install`/`update` now perform this adoption
   automatically and refuse to run without the env file.
3. **Safe update.** `deploy.sh update` synced the managed code, reinstalled the
   systemd unit and proxy config, and restarted the service. Local checks:
   `health=200`, `/login=200`, anonymous `/api/v1/missions=401`,
   authenticated `=200`, `NRestarts=0`.
4. **TLS.** `deploy.sh cert` used the Let's Encrypt account email already present
   on the VPS and issued a certificate for `whstapp.lol` + `www.whstapp.lol`
   (expires 2026-12-30); nginx now listens on 443 and redirects plaintext HTTP.
   `cert` now refuses to enable public HTTPS unless `FACAI_ADMIN_PASSWORD` is set.
5. **Verification.** `deploy/check_public_access.sh whstapp.lol 77.221.148.118`
   exited 0: authoritative DNS, TCP 443, TLS, `health 200`, anonymous
   `401`, authenticated `200`. Over HTTPS the login flow returned a session
   (`login=200`), a mission was created with its 3-step pipeline, an event was
   appended (`201`), and the SSE endpoint streamed `text/event-stream` through
   nginx with `X-Accel-Buffering: no`.

Public access is therefore **LIVE and verified**. Any change must re-run
`deploy/check_public_access.*`; do not report availability from inspection alone.

### Mission-first hierarchy increment (2026-10-01) — deployed and browser-verified

Owner feedback: opening Mission Control showed the creation forms first while the
selected mission's pipeline required scrolling, and the header still read "phase 1".
The console was reorganised around the mission:

- **First-screen progress.** The selected mission's title, status, real overall
  percentage (completed steps only — never fabricated), a `role="progressbar"`
  with `aria-valuenow`, the current stage and the stage pipeline now render at the
  top of the detail column. If no `?mission=` is given the most recent mission is
  selected automatically.
- **Secondary, collapsible sections.** Mission creation, model/registry
  registration, the four-registry binding, the stage inspector, the original +
  professional prompt and artifacts/checkpoints/jobs live in `<details>` sections
  that start collapsed (all four registries stay independently bindable).
- **Chinese, accessible, deep-linkable.** Status enums are shown in Chinese, the
  header subset is honest ("实时进度 · 当前阶段 · 真实流水线（无模拟进度）"), and
  `?mission=<id>` deep links are preserved through login via a validated local
  `next` parameter (no open redirect).
- **Tests.** `tests/test_console_layout.py` (real Chromium 1440x900 first-screen
  progress/current-stage/pipeline, collapsed sections, no overflow, deep-link
  login restore) and the rewritten `tests/test_mobile_ui.py` (390x844, no overflow,
  vertical pipeline, tap targets, sticky header, collapsible creation). Full suite:
  `78 passed`.
- **Deployment.** Pre-deploy backup
  `/var/backups/facai-cloud/predeploy-20261001T131544Z` (online SQLite backup,
  `integrity_check=ok`, data tree, env, unit, nginx, code snapshot, `SHA256SUMS`);
  managed code synced to `/opt/facai-cloud` only; `deploy.sh update` re-installed
  the unit/nginx and restarted. `check_public_access.sh whstapp.lol
  77.221.148.118` exits 0.
- **Real HTTPS browser acceptance.** A dedicated Chromium run against
  `https://whstapp.lol` passed 15/15 checks: desktop 1440x900 shows the real 33%
  progress bar, current stage and pipeline above the fold with collapsed
  creation/registry sections; 390x844 has no horizontal overflow; anonymous visits
  redirect to `/login`. The one-time session used for the check was revoked and its
  file deleted; no password, API key or env value was read into the transcript.

### Live OpenCode CLI increment (2026-10-01) — installed and verified end to end

The VPS had no Node/npm, so the coding agent was installed as a self-contained
binary rather than via npm:

- **Pinned installer** (`deploy/install-opencode.sh`, `deploy/deploy.sh opencode`):
  downloads `anomalyco/opencode v1.18.32` (`opencode-linux-x64.tar.gz`), verifies the
  SHA-256 `3046e04…d6d43b`, and installs to `/usr/local/bin/opencode`. `install`
  invokes it only when the binary is missing; `update` never reaches the network for
  it. `deploy/validate_config.sh` and `tests/test_deploy_config.py` assert the pin,
  the checksum check and the absence of any credential in the installer.
- **Service environment** (`deploy/systemd/facai-cloud.service`): sets
  `PATH=/usr/local/bin:/usr/bin:/bin` and
  `HOME=/opt/facai-cloud/data/opencode/home` (the only writable path under
  `ProtectSystem=strict`), with `XDG_*` under it. The adapter resolves `opencode`; the
  CLI authenticates from `DEEPSEEK_API_KEY` in the classified provider environment
  (`/etc/facai-cloud/providers/deepseek.env`, loaded by `EnvironmentFile`), never from a
  command line, stdout or log. `deploy.sh` creates the writable OpenCode HOME
  (`prepare_opencode_home`).
- **Real online run** in the owner's acceptance mission
  `80587244520747ddb11f639e2746bce5`: a small task (create and run `hello.py`) returned
  `OPENCODE_COMPLETED` (exit 0) with DeepSeek/`deepseek-flash`/`local` attribution,
  cost `0.00110635` and `7656` tokens for the final run (mission total `0.0021524`), a
  captured stdout artifact and Git checkpoint
  `9330b9db630a29f3c238f2cb11051802b7ab766e`. A second mission stayed isolated (zero
  sessions/artifacts/checkpoints/cost).
- **Real failure attribution**: `MODEL_AUTH_FAILED` (HTTP 401 from `api.deepseek.com`
  using a deliberately missing key env), `MODEL_TIMEOUT` and `MODEL_TRANSPORT_FAILED`
  were reproduced as genuine upstream results. No key was printed.
- **Defect fixed**: running an already-`COMPLETED` step used to execute the task and
  write an orphan checkpoint before failing the final transition; `facai/execution.py`
  now refuses it up front (regression test added). Suite: `82 passed`.
- **Deploy + cleanup**: `deploy.sh update` synced and restarted; the public gate
  `deploy/check_public_access.sh whstapp.lol 77.221.148.118` exits `0`. One-time test
  missions/registries were removed; the target mission's result and the evidence under
  `/var/backups/facai-cloud/opencode-accept-final-20261001T133812Z/` were kept. Full
  record: `docs/ACCEPTANCE_OPENCODE.md`.
