"""n8n automation integration boundary for Facai Cloud.

Facai forwards mission and stage events to a configured n8n webhook as signed
JSON, and accepts authenticated callbacks from n8n that update a mission's
events and steps. Two properties are non-negotiable:

* **Honesty.** Credentials come from the process environment only (on the
  server, from the classified provider file
  ``/etc/facai-cloud/providers/n8n.env``). Without a webhook URL and a signing
  secret the integration reports ``CONFIG_REQUIRED``, sends nothing and never
  claims to be connected. The webhook URL is itself treated as a secret (its
  path typically contains an unguessable id) and is never returned or logged.
* **Authenticity.** Every outbound request body is signed with HMAC-SHA256 over
  ``<timestamp>.<body>`` and the signature is sent in ``X-Facai-Signature``
  together with ``X-Facai-Timestamp``. Inbound callbacks must carry a fresh
  timestamp and a valid signature, use an idempotent ``request_id`` and are
  attributed honestly when they report a retry or an error.

No secret value is ever printed, returned or written to the database.
"""

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone

import httpx

STATE_CONFIGURED = "CONFIGURED"
STATE_CONFIG_REQUIRED = "CONFIG_REQUIRED"

SIGNATURE_HEADER = "X-Facai-Signature"
TIMESTAMP_HEADER = "X-Facai-Timestamp"
REQUEST_ID_HEADER = "X-Facai-Request-Id"

# Upstream errors that are worth retrying. A terminal client error (auth,
# malformed request) is never retried and is attributed as such.
RETRYABLE_ERRORS = frozenset({"TIMEOUT", "TRANSPORT_ERROR", "RATE_LIMITED", "UPSTREAM_UNAVAILABLE"})

# Mission/stage events forwarded to n8n. High-volume model output lines
# (OUTPUT/ERROR) and the integration's own events (N8N_*) are deliberately
# excluded: the first would flood the workflow, the second would echo back.
STAGE_EVENT_TYPES = frozenset({
    "MISSION_CREATED", "PROMPT_UPDATED", "BINDING_CHANGED",
    "STEP_RUNNING", "STEP_COMPLETED", "STEP_FAILED",
    "SESSION_PREPARED", "SESSION_COMPLETED", "SESSION_FAILED",
    "OPENCODE_STARTED", "OPENCODE_COMPLETED", "OPENCODE_FAILED",
    "MODEL_COMPLETED", "MODEL_REFUSED", "MODEL_TIMEOUT", "MODEL_RATE_LIMITED",
    "MODEL_AUTH_FAILED", "MODEL_TRANSPORT_FAILED", "MODEL_TOOL_FAILED",
    "MODEL_RESOURCE_UNAVAILABLE", "MODEL_FAILED",
    "JOB_QUEUED", "JOB_STARTED", "JOB_COMPLETED", "JOB_FAILED", "JOB_RETRIED",
    "JOB_REQUEUED", "JOB_PAUSED", "JOB_RESUMED", "JOB_CANCELLED", "JOB_BLOCKED",
    "STEP_BLOCKED", "STEP_UNBLOCKED",
    "GIT_CHECKPOINT", "DISPATCH_QUEUED", "DISPATCH_DEFERRED",
})

CALLBACK_STATUSES = frozenset({"SUCCEEDED", "FAILED", "RETRY"})


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class N8nConfigError(ValueError):
    """Raised when the n8n integration is missing its credentials."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(
            "n8n is not configured; missing "
            + (", ".join(self.missing) or "credentials"))


def _int_env(env, key, default, low, high):
    try:
        value = int((env.get(key) or "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


class N8nIntegration:
    """Secret-free status plus outbound signing and inbound verification."""

    name = "n8n"
    required_env = ("N8N_WEBHOOK_URL", "N8N_SIGNING_SECRET")

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ

    def setting(self, key):
        return (self.env.get(key) or "").strip()

    def _webhook_scheme(self):
        """Normalized scheme of the webhook URL, or None when it is not http(s).

        This matches the validation in :meth:`missing`. Keeping one definition
        means a valid-but-uppercase scheme (``HTTPS://...``) is treated the same
        everywhere instead of being reported as configured and then refused at
        delivery time.
        """
        lowered = self.setting("N8N_WEBHOOK_URL").lower()
        if lowered.startswith("https://"):
            return "https"
        if lowered.startswith("http://"):
            return "http"
        return None

    @property
    def timeout_seconds(self):
        return _int_env(self.env, "N8N_TIMEOUT", 15, 1, 120)

    @property
    def max_attempts(self):
        return _int_env(self.env, "N8N_MAX_ATTEMPTS", 3, 1, 10)

    @property
    def max_skew_seconds(self):
        return _int_env(self.env, "N8N_MAX_SKEW", 300, 30, 3600)

    # --- configuration -----------------------------------------------------

    def missing(self):
        """Return missing/invalid credential *names*; never a value."""
        missing = []
        for name in self.required_env:
            if not self.setting(name):
                missing.append(name)
        url = self.setting("N8N_WEBHOOK_URL")
        if url and "N8N_WEBHOOK_URL" not in missing:
            try:
                parsed = httpx.URL(url)
                valid_url = parsed.scheme in {"http", "https"} and bool(parsed.host)
            except httpx.InvalidURL:
                valid_url = False
            if not valid_url:
                missing.append("N8N_WEBHOOK_URL")
        return missing

    def configured(self):
        return not self.missing()

    def callback_ready(self):
        return bool(self.setting("N8N_SIGNING_SECRET"))

    def require_configured(self):
        missing = self.missing()
        if missing:
            raise N8nConfigError(missing)

    def status(self):
        missing = self.missing()
        configured = not missing
        webhook = self.setting("N8N_WEBHOOK_URL")
        scheme = self._webhook_scheme()
        return {
            "integration": self.name,
            "state": STATE_CONFIGURED if configured else STATE_CONFIG_REQUIRED,
            "configured": configured,
            "required": list(self.required_env),
            "missing": missing,
            "send_ready": configured,
            "callback_ready": self.callback_ready(),
            "webhook_configured": bool(webhook),
            "webhook_scheme": scheme,
            "max_attempts": self.max_attempts,
            "allow_events": sorted(STAGE_EVENT_TYPES),
            "detail": ("已配置：事件投递与回调验签就绪" if configured
                       else "缺少 n8n Webhook URL 或签名密钥，安全返回 CONFIG_REQUIRED"),
        }

    # --- signatures --------------------------------------------------------

    def sign(self, body: bytes, timestamp) -> str:
        secret = self.setting("N8N_SIGNING_SECRET")
        message = str(timestamp).encode("ascii") + b"." + body
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    def verify(self, body: bytes, headers, now=None) -> bool:
        secret = self.setting("N8N_SIGNING_SECRET")
        if not secret:
            return False
        supplied = next((value for key, value in headers.items()
                         if key.lower() == SIGNATURE_HEADER.lower()), "")
        timestamp = next((value for key, value in headers.items()
                          if key.lower() == TIMESTAMP_HEADER.lower()), "")
        if not supplied or not timestamp:
            return False
        try:
            timestamp.encode("ascii")
            stamped_at = int(timestamp)
        except (AttributeError, TypeError, ValueError):
            return False
        reference = time.time() if now is None else now
        if abs(reference - stamped_at) > self.max_skew_seconds:
            return False
        try:
            prefix, _, digest = str(supplied).partition("=")
            if prefix != "sha256" or len(digest) != 64:
                return False
            expected = bytes.fromhex(self.sign(body, timestamp))
            received = bytes.fromhex(digest)
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(expected, received)

    # --- outbound delivery -------------------------------------------------

    @staticmethod
    def classify_http(status):
        if status in (401, 403):
            return "AUTH_FAILED"
        if status == 429:
            return "RATE_LIMITED"
        if status == 408:
            return "TIMEOUT"
        if status >= 500:
            return "UPSTREAM_UNAVAILABLE"
        if status >= 400:
            return "REJECTED"
        return "UPSTREAM_REJECTED"

    async def deliver(self, payload, client=None):
        """POST a signed JSON event to the configured n8n webhook.

        Returns the real outcome with the upstream attribution; it never
        fabricates a successful delivery.
        """
        self.require_configured()
        url = self.setting("N8N_WEBHOOK_URL")
        if self._webhook_scheme() is None:
            raise N8nConfigError(["N8N_WEBHOOK_URL"])
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: f"sha256={self.sign(body, timestamp)}",
            REQUEST_ID_HEADER: str(payload.get("request_id", "")),
        }
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False)
        result = {"status": "FAILED", "http_status": None, "error": None, "retryable": False}
        try:
            response = await client.post(url, content=body, headers=headers,
                                         follow_redirects=False, timeout=self.timeout_seconds)
            result["http_status"] = response.status_code
            if 200 <= response.status_code < 300:
                return {**result, "status": "SENT"}
            error = self.classify_http(response.status_code)
            return {**result, "error": error, "retryable": error in RETRYABLE_ERRORS}
        except httpx.TimeoutException:
            return {**result, "error": "TIMEOUT", "retryable": True}
        except httpx.InvalidURL:
            return {**result, "error": "INVALID_URL", "retryable": False}
        except httpx.RequestError:
            return {**result, "error": "TRANSPORT_ERROR", "retryable": True}
        finally:
            if own_client:
                await client.aclose()


class N8nRelay:
    """Durable relay from mission events to the n8n webhook.

    It scans new mission events above a stored cursor, forwards the allowlisted
    stage events once (deduplicated by ``request_id``) and retries retryable
    failures with the outcome attributed. It is a no-op while the integration is
    unconfigured, so the honest ``CONFIG_REQUIRED`` state is preserved.
    """

    def __init__(self, store, integration, batch=50, client_factory=None):
        self.store = store
        self.integration = integration
        self.batch = int(batch)
        self.client_factory = client_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def initialize_cursor(self):
        """Start from the current head so enabling n8n never floods old events."""
        if self.integration.configured() and self.store.n8n_cursor() is None:
            self.store.n8n_set_cursor(self.store.last_event_sequence())

    def _delivery_payload(self, event, mission):
        return {
            "request_id": f"facai-evt-{event['id']}",
            "source": "facai-cloud",
            "mission": {
                "id": mission.get("id"),
                "title": mission.get("title"),
                "status": mission.get("status"),
                "progress": mission.get("progress"),
            },
            "event": {
                "id": event["id"],
                "sequence": event["sequence"],
                "type": event["type"],
                "step_id": event["step_id"],
                "session_id": event["session_id"],
                "agent_id": event["agent_id"],
                "occurred_at": event["occurred_at"],
                "payload": event["payload"],
            },
            "callback_path": "/api/v1/n8n/callback",
            "generated_at": _now_iso(),
        }

    def _record(self, event):
        mission = self.store.get(event["mission_id"]) or {"id": event["mission_id"]}
        payload = self._delivery_payload(event, mission)
        return self.store.create_n8n_delivery(
            payload["request_id"], event["mission_id"], event["id"], event["type"],
            payload, max_attempts=self.integration.max_attempts)

    def _attempt(self, delivery, client=None):
        try:
            result = asyncio.run(self.integration.deliver(delivery["payload"], client=client))
        except N8nConfigError:
            result = {"status": "FAILED", "http_status": None,
                      "error": "CONFIG_REQUIRED", "retryable": False}
        return self.store.record_n8n_delivery_result(
            delivery["id"], result["status"], result.get("http_status"),
            result.get("error"), result.get("retryable", False))

    def run_pending(self):
        if not self.integration.configured():
            return {"dispatched": 0, "state": STATE_CONFIG_REQUIRED, "summary": {}}
        with self._lock:
            self.initialize_cursor()
            client = self.client_factory() if self.client_factory else None
            attempted = set()
            dispatched = 0
            try:
                cursor = self.store.n8n_cursor() or 0
                for event in self.store.n8n_events_after(cursor, self.batch):
                    cursor = event["sequence"]
                    if event["type"] in STAGE_EVENT_TYPES:
                        delivery = self._record(event)
                        # A crash can leave the event cursor behind after its
                        # delivery result was committed. Only brand-new rows
                        # are sent here; failed rows are handled by the bounded
                        # retry query below, which respects max_attempts.
                        if delivery["status"] == "PENDING":
                            self._attempt(delivery, client)
                            attempted.add(delivery["id"])
                            dispatched += 1
                    self.store.n8n_set_cursor(cursor)
                for delivery in self.store.n8n_retryable_deliveries(self.batch):
                    if delivery["id"] in attempted:
                        continue
                    self._attempt(delivery, client)
                    dispatched += 1
            finally:
                if client is not None:
                    try:
                        asyncio.run(client.aclose())
                    except Exception:
                        pass
            return {"dispatched": dispatched, "state": STATE_CONFIGURED,
                    "summary": self.store.n8n_delivery_summary()}

    # --- background thread -------------------------------------------------

    def start(self, interval=2.0):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, args=(interval,), daemon=True)
        self._thread.start()

    def _serve(self, interval):
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:
                pass
            self._stop.wait(interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
