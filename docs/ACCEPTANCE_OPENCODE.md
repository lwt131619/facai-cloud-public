# Live OpenCode execution acceptance (2026-10-01)

This records a **real, online** acceptance of the mission-scoped OpenCode adapter on
the VPS `77.221.148.118`, run against the public HTTPS API `https://whstapp.lol`.
Nothing here is simulated: the task was executed by the actual OpenCode CLI calling
the real DeepSeek endpoint, and every figure below was read back from the service.

## Environment installed

- OpenCode is a self-contained CLI; the host needed no Node/npm. The pinned release
  `anomalyco/opencode v1.18.32` for `linux-x64` was installed to `/usr/local/bin/opencode`
  by `deploy/install-opencode.sh`, which verifies the SHA-256
  (`3046e04…d6d43b` for `opencode-linux-x64.tar.gz`) before installing.
- The hardened `facai-cloud.service` now sets `PATH=/usr/local/bin:/usr/bin:/bin` and
  `HOME=/opt/facai-cloud/data/opencode/home` (inside the service's only writable path),
  so the adapter resolves `opencode` and the CLI can write its own store. The model
  credential is supplied only through the classified provider environment
  (`/etc/facai-cloud/providers/deepseek.env`, loaded by
  `EnvironmentFile=-/etc/facai-cloud/providers/*.env`); it is never placed on a
  command line, printed or logged.

## Target mission

The owner's acceptance mission `80587244520747ddb11f639e2746bce5`
("Facai 线上 OpenCode 执行验收") was bound to agent `opencode`, model
`deepseek-flash` (provider DeepSeek), runtime `local`, and a small safe coding task
was run through `POST /api/v1/missions/{id}/opencode/run`:

> Create `hello.py` containing `print('hello from facai')`, run it with `python3`,
> and report the output.

Observed (read back over HTTPS):

| Item | Value |
| --- | --- |
| Run status | `OPENCODE_COMPLETED`, exit code `0` |
| Attribution | provider `DeepSeek`, model `deepseek-flash`, runtime `local` |
| Cost / tokens | `0.00110635` USD / `7656` tokens for the final run; mission total `0.0021524` |
| Artifact | `opencode-125a7eaf96c94779b003265547369928.stdout.log` (raw stdout captured) |
| Git checkpoint | `9330b9db630a29f3c238f2cb11051802b7ab766e` in the mission workspace; `GIT_CHECKPOINT` event recorded |
| Workspace file | `hello.py` = `print('hello from facai')\n`, present in the target workspace only |
| Diff | available and shows the committed `hello.py` |
| Step / progress | the running step became `COMPLETED`; real progress advanced |

## Isolation control

A second, independent mission was bound to the same agent/model/runtime and was never
run. After the target run it still had **0 sessions, 0 artifacts, 0 checkpoints, cost 0**
and only `MISSION_CREATED` / `BINDING_CHANGED` events. Every event of each mission
carried its own `mission_id`; no output, artifact or checkpoint crossed the boundary.

## Upstream failure attribution (real)

A throwaway mission exercised `POST /api/v1/missions/{id}/model/complete` against
deliberately misconfigured registry entries. All three outcomes are genuine upstream
responses, not fabricated:

| Bound model | Result | Evidence |
| --- | --- | --- |
| missing `api_key_env` | `MODEL_AUTH_FAILED` | real HTTP `401` from `api.deepseek.com` |
| endpoint `http://10.255.255.1:81/v1` | `MODEL_TIMEOUT` | real connection timeout |
| endpoint `https://no-such-host.invalid/v1` | `MODEL_TRANSPORT_FAILED` | real DNS/transport error |

No credential was printed: the "bad auth" case used a deliberately missing environment
variable, so no real secret was involved. A model refusal is classified by the gateway
(`MODEL_REFUSED`) and covered by the recorded upstream-body unit tests; it was not
forced live.

## Product defect found and fixed

The first attempt pointed the run at a step that was already `COMPLETED`. The model ran
and a checkpoint/cost were written, but the final step transition (correctly) failed and
the endpoint returned `422`, leaving an orphaned run. `facai/execution.py` now refuses an
already-completed step **before** any model call, session or checkpoint
(`tests/test_opencode_execution.py::test_run_endpoint_refuses_an_already_completed_step`).

## Cleanup and evidence retained

The one-time missions and registry entries were removed after the run; the target
mission's own result is left in place. Retained evidence (on the VPS, mode `0700`):

- `/var/backups/facai-cloud/opencode-accept-final-20261001T133812Z/` — the two
  acceptance JSON files, `target-workspace-git.tar.gz` and
  `temp-run-workspace-git.tar.gz` (the real Git checkpoint history),
  `facai-post-accept.sqlite3` (`integrity_check=ok`), `data-tree-post-accept.tar.gz`
  and `SHA256SUMS`.
- Pre-change backup `/var/backups/facai-cloud/opencode-accept-20261001T132942Z/`.
- Public access re-verified after the change:
  `deploy/check_public_access.sh whstapp.lol 77.221.148.118` exits `0` (authoritative
  DNS, TCP 443, TLS, `health 200`, anonymous `401`); authenticated access was exercised
  by the acceptance itself.
