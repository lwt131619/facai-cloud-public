# n8n automation integration

Facai Cloud forwards mission and stage events to an n8n webhook as **signed JSON**
and accepts **signed, idempotent callbacks** from n8n that update a mission's
events and steps. Nothing is ever claimed as connected until both credentials
exist, and no secret (including the webhook URL) is written to the database, a
log or the console.

## Credentials

The integration reads two variables from the process environment. On the server
they live in the classified provider file
`/etc/facai-cloud/providers/n8n.env` (mode `0640`, `root:facai`), seeded from
`deploy/providers/n8n.env.example` by `deploy.sh install` / `update`:

| Name | Required | Purpose |
| --- | --- | --- |
| `N8N_WEBHOOK_URL` | yes | Production webhook URL that receives Facai events. Treated as a secret because its path usually contains an unguessable id; it is never returned or logged. |
| `N8N_SIGNING_SECRET` | yes | Shared HMAC-SHA256 key for outbound signing and inbound verification. |
| `N8N_TIMEOUT` | no | Request timeout in seconds (default `15`). |
| `N8N_MAX_ATTEMPTS` | no | Delivery attempts per event (default `3`). |
| `N8N_MAX_SKEW` | no | Accepted callback clock skew in seconds (default `300`). |

Until `N8N_WEBHOOK_URL` and `N8N_SIGNING_SECRET` are both present the integration
reports `CONFIG_REQUIRED`, sends nothing and the relay is a no-op.

## Outbound: mission/stage events

`N8nRelay` scans new mission events above a stored cursor, forwards the
allowlisted stage events once (deduplicated by `request_id`) and retries only
retryable failures. High-volume `OUTPUT`/`ERROR` lines and Facai's own `N8N_*`
events are deliberately excluded (the first would flood the workflow, the second
would echo back). The cursor is initialised at the current event head when the
app starts, so enabling n8n never replays history.

Each request is `POST`ed with:

- `X-Facai-Timestamp`: unix seconds;
- `X-Facai-Signature`: `sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>`;
- `X-Facai-Request-Id`: the idempotency key (`facai-evt-<event id>`).

The JSON body carries `request_id`, `source`, a compact `mission` snapshot
(`id`/`title`/`status`/`progress`), the `event` (id, sequence, type, step,
session, agent, `occurred_at`, payload), `callback_path` and `generated_at`.

Every outcome is attributed to the upstream: `SENT`, or `FAILED` with one of
`AUTH_FAILED`, `REJECTED`, `RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `TIMEOUT`,
`TRANSPORT_ERROR`. The retryable set (`TIMEOUT`, `TRANSPORT_ERROR`,
`RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`) is retried up to `N8N_MAX_ATTEMPTS`;
terminal errors are not retried. Recent deliveries and counts are shown in the
console and served by `GET /api/v1/n8n`.

n8n verifies the request with the same secret, e.g.:

```js
const crypto = require('crypto');
const expected = 'sha256=' + crypto
  .createHmac('sha256', $env.N8N_SIGNING_SECRET)
  .update(`${timestamp}.${rawBody}`)
  .digest('hex');
return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
```

## Inbound: signed callback

`POST /api/v1/n8n/callback` is public at the transport layer (no browser
session) but rejects anything without a fresh timestamp and a valid signature.
Body:

```json
{
  "request_id": "unique-idempotency-key",
  "mission_id": "<mission id>",
  "status": "SUCCEEDED | FAILED | RETRY",
  "attempt": 2,
  "error": {"code": "TIMEOUT", "message": "...", "retryable": true},
  "event": {"type": "N8N_STAGE_DONE", "payload": {}, "step_id": null},
  "step": {"step_id": "<id>", "status": "RUNNING"}
}
```

- `request_id` is required and enforced **idempotent**: a repeat returns
  `{"duplicate": true}` and applies nothing. Unknown/invalid missions, steps and
  step transitions are rejected and recorded with honest attribution.
- `event` and `step` are optional; when present the mission event is appended
  and/or the step transition is driven through the same validator the console
  uses.
- If the callback is unconfigured, the endpoint returns `503` with
  `CONFIG_REQUIRED` and the missing variable *names*.

Verification is **total**: a missing, malformed, truncated, non-hex or
non-ASCII signature/timestamp is rejected with `403` and applies nothing. It
never raises `500`. Only a well-formed `sha256=<64 hex>` signature over
`<timestamp>.<raw body>`, within the skew window, is accepted.

## Exact n8n setup

### 1. Give n8n the same secret

Set `N8N_SIGNING_SECRET` in n8n's environment (Docker `environment:` /
`--env-file`, or n8n's external env file) to **exactly** the value Facai stores
in `/etc/facai-cloud/providers/n8n.env`. If you read it from a Code node via
`process.env`, also set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`; otherwise pass it
through an n8n credential. Never paste the secret into the workflow JSON.

### 2. Receiver workflow (Facai -> n8n)

Nodes: **Webhook -> Code (verify) -> Respond to Webhook**.

- **Webhook node**
  - HTTP Method: `POST`
  - Path: an unguessable path, e.g. `facai-events-<random>`
  - Authentication: `None` (the HMAC signature *is* the authentication)
  - Response Mode: `Using Respond to Webhook Node`
  - Options: enable **Raw Body** so the exact received bytes are preserved in
    the binary property `data`.
- **Code node** (Run Once for All Items):

```js
const crypto = require('crypto');
const secret = process.env.N8N_SIGNING_SECRET;
const raw = helpers.getBinaryDataBuffer(0, 'data'); // exact request bytes
const h = $input.first().json.headers || {};
const timestamp = String(h['x-facai-timestamp'] || '');
const supplied = String(h['x-facai-signature'] || '');
const requestId = String(h['x-facai-request-id'] || '');

const fail = (msg) => [{ json: { ok: false, error: msg } }];
if (!secret || !timestamp || !supplied || !requestId) {
  return fail('missing credentials/headers');
}
const skew = Math.abs(Date.now() / 1000 - Number(timestamp));
if (!Number.isFinite(skew) || skew > 300) return fail('stale timestamp');

const expected = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(`${timestamp}.`)
  .update(raw)
  .digest('hex');
const a = Buffer.from(expected, 'utf8');
const b = Buffer.from(supplied, 'utf8');
if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
  return fail('bad signature');
}

// Idempotency: the same X-Facai-Request-Id is a retry -> process once.
const seen = $getWorkflowStaticData('global');
if (seen[requestId]) return [{ json: { ok: true, duplicate: true, request_id: requestId } }];
seen[requestId] = true;

return [{ json: { ok: true, request_id: requestId, event: $input.first().json.body } }];
```

- **Respond to Webhook node**
  - Respond With: `JSON`
  - Response Code: `{{ $json.ok ? 200 : 403 }}`
  - Body: `{{ JSON.stringify($json) }}`

`$getWorkflowStaticData('global')` is bounded, in-memory static data. For
durable or high-volume deduplication, replace that block with a lookup keyed on
`request_id` in Redis/Postgres; the correctness requirement is only that the
same `request_id` is processed once.

### 3. Callback workflow (n8n -> Facai)

Nodes: **Code (build + sign) -> HTTP Request**.

- **Code node**:

```js
const crypto = require('crypto');
const secret = process.env.N8N_SIGNING_SECRET;
const body = JSON.stringify({
  request_id: $json.request_id,
  mission_id: $json.mission_id,
  status: 'SUCCEEDED',
  event: { type: 'N8N_STAGE_DONE', payload: { note: 'ok' } },
});
const timestamp = String(Math.floor(Date.now() / 1000));
const signature = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(`${timestamp}.${body}`)
  .digest('hex');
return [{ json: { body, headers: {
  'Content-Type': 'application/json',
  'X-Facai-Timestamp': timestamp,
  'X-Facai-Signature': signature,
} } }];
```

- **HTTP Request node**
  - Method: `POST`
  - URL: `https://<facai-host>/api/v1/n8n/callback`
  - Send Headers: enabled; add `Content-Type`, `X-Facai-Timestamp` and
    `X-Facai-Signature` from the Code node output.
  - Send Body: enabled; Body Content Type `Raw`; Body `={{ $json.body }}`.

`body` must be sent **byte-for-byte** as signed. Re-serializing it in an
intermediate node changes the bytes and breaks verification.

### 4. Verify the contract

- Use the same compact, sorted JSON Facai emits (the Code node above signs the
  exact string it sends).
- On the receiver, always enable **Raw Body**; a re-stringified JSON body can
  differ in escaping/spacing and will fail the HMAC.
- The callback URL is treated as a secret by Facai: keep it out of logs, and
  never return it from an API.

## Console

The `n8n 自动化集成` panel shows the honest configuration state
(`CONFIGURED` / `CONFIG_REQUIRED`), the missing variable names, delivery counts
and the most recent deliveries with their real status, attempt count, HTTP
status and attributed error. The webhook URL and signing secret are never
returned by the API.
