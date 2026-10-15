# Facai Cloud architecture baseline

This document records the target agreed in the shared Facai discussion and distinguishes that target from what the current code actually does.

## Product boundary

Facai is the owner's cloud Mission Control. It accepts the owner's original words, preserves them, creates a structured mission, chooses registered agents/models/skills/runtimes, records actual execution, and presents one consistent timeline across devices. OpenCode is a coding agent; a model is a separate reasoning endpoint; n8n is an optional external automation bus. A GPU host is a runtime resource and its provider, location, model, uptime and cost must be visible to the owner.

## Mission record

The mission is the isolation key for:

- original input and any derived professional prompt;
- workflow graph, steps and progress;
- workspace and Git checkpoints;
- agent sessions and model bindings;
- event stream and output stream;
- artifacts, tests, deployment records and cost ledger.

Every event must carry `mission_id`, event type and time. Step/session/agent identifiers must be present when applicable. A mission detail view must never return another mission's data. Progress must derive from that mission's workflow, not from a timer or generated percentage.

## Components and contracts

| Component | Responsibility | Current state |
| --- | --- | --- |
| Mission API and store | Create/list missions, isolate workspaces, steps, sessions, events | Initial implementation |
| Live Mission Control | Mission list, pipeline and event timeline | Responsive pipeline, stage inspector, timeline, artifacts, checkpoints and cost |
| Agent Registry | Describe agent adapters and capabilities | SQLite-backed; a mission binds its own agent independently |
| Model Registry | Describe provider, endpoint and model independently of agents | SQLite-backed; provider/endpoint/model/api_key_env only, bound per mission |
| Skill Registry | Catalog optional capabilities | SQLite-backed; bound per mission independently of agent/model |
| Runtime Registry | Catalog execution locations and resource limits | SQLite-backed; entries carry provider/location; bound per mission independently |
| OpenCode Adapter | Bind a coding session to one mission workspace and model | Execution, output capture and Git checkpoints implemented and verified live over HTTPS on the VPS (pinned CLI, real cost/tokens, two-mission isolation) |
| Model Gateway | Call a bound endpoint and attribute upstream outcomes | Calls OpenAI-compatible endpoints and Anthropic Claude Messages API; attributes completed, refused, timeout, rate-limited, authentication, transport, tool, resource-unavailable and generic upstream-failure outcomes with the raw upstream body retained |
| Resource Scheduler | Concurrency, priority, pause/resume, leases and recovery | Durable SQLite queue with leases, priority, pause/resume/cancel/retry and expiry recovery; up to two workers by default, with only one active job per mission workspace |
| Worker Runtime | Isolated execution and per-mission output/artifacts | Mission-scoped OpenCode execution; stdout/stderr stream into mission events with redaction and a bounded line buffer; artifacts and Git checkpoints are retained |
| Cost ledger | Record tokens and upstream cost per mission | Implemented from OpenCode-reported usage |
| Prompt Inspector | Original text, professional prompt and execution instruction trace | Original input preserved byte-for-byte; the professional prompt is stored separately and every change records an audit event holding both the professional prompt and the exact original it was derived from |
| Admin authentication | Session login, CSRF, throttle, optional Basic | Session cookies (hashed), CSRF and per-address throttling implemented; Basic retained for scripts |
| Messaging Channels | Provider/adapter abstraction, unified inbound/outbound models, channel health and config validation | WhatsApp Cloud webhook/send adapter and personal WeChat iLink QR/text adapter implemented; Telegram currently has the shared interface but no working provider. Real delivery still requires valid channel credentials/authorization (`docs/CHANNELS.md`) |
| n8n integration | External triggers and automation flows | Signed event relay and idempotent callback implemented; still requires a real n8n webhook and signing secret to deliver events (`docs/N8N.md`) |
| Cloud/local handoff | Status sync plus explicit file/Git synchronization | Planned |
| Deployment plane | Build, test, release and domain verification | Reverse proxy, hardened systemd unit and scoped deploy/verify scripts exist; the current local deployment key cannot access the VPS, so new local commits are not automatically live |

## Outcome attribution

The gateway records the selected provider/model/runtime and upstream result. Every terminal outcome is one of `MODEL_COMPLETED`, `MODEL_REFUSED`, `MODEL_TIMEOUT`, `MODEL_RATE_LIMITED`, `MODEL_AUTH_FAILED`, `MODEL_TRANSPORT_FAILED`, `MODEL_TOOL_FAILED`, `MODEL_RESOURCE_UNAVAILABLE` or `MODEL_FAILED`, each with a human-readable label and, when available, the raw upstream error body. A provider refusal, timeout, rate limit, authentication, transport, tool or resource failure is presented as the upstream component's outcome, never rewritten as a Facai decision. A mission is complete only when its own acceptance checks have passed; a successful model response alone is not mission completion.

Task events carry a consistent attribution block — `agent_id`, `provider`, `model_id`, `model_ref`, `runtime_id`, `runtime_kind`, `runtime_provider`, `runtime_location`, `skill_id` — plus `cost` and `tokens` on completion, and the console renders the same fields. The four registries (agent, model, skill, runtime) bind independently per mission, so changing one never clears another and a mission detail view never exposes another mission's bindings.

## Deployment baseline

The current preview uses SQLite and an authenticated HTTP service bound to `127.0.0.1` on the VPS. It is not yet the complete cloud product. Verified database backup/restore and a non-destructive restore drill exist, session authentication with CSRF and login throttling is implemented, and nginx/systemd/deploy scripts with a DNS-TLS-HTTP verification gate are in `deploy/` and `docs/DEPLOYMENT.md`.

Public access is verified, not assumed. As of 2026-10-01 the authoritative A records for `whstapp.lol` and `www.whstapp.lol` resolve to the VPS (`77.221.148.118`) on every `dnsowl` nameserver, TLS is issued, and `deploy/check_public_access.*` exits 0 (DNS, TCP 443, TLS, `health 200`, anonymous `401`). Credentials are classified by service: app login/runtime config stays in `/etc/facai-cloud/facai.env` while each model credential lives in `/etc/facai-cloud/providers/<service>.env` (both `0640 root:facai`), and the systemd unit loads both. The OpenCode adapter runs a pinned, checksum-verified self-contained CLI installed to `/usr/local/bin/opencode`, with the service PATH/HOME (writable, under the data directory) supplied by the systemd unit; a real online task produced a code diff, a Git checkpoint and recorded cost. See `docs/ACCEPTANCE_OPENCODE.md`. Do not claim public availability without re-running the gate.
