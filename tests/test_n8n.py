"""n8n integration: honesty, HMAC signatures, idempotent callbacks, relay.

The integration must never fabricate a connection: without a webhook URL and a
signing secret it reports CONFIG_REQUIRED and sends nothing, and the webhook URL
(which is itself a secret) is never returned or logged. With credentials, every
outbound event is signed with HMAC-SHA256 and inbound callbacks are verified,
deduplicated by request_id and attributed honestly when they report a retry or
an error. The relay forwards mission/stage events and retries retryable
failures.
"""

import asyncio
import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.n8n import (
    N8nIntegration,
    N8nRelay,
    STAGE_EVENT_TYPES,
    STATE_CONFIG_REQUIRED,
)

N8N_ENV = {
    "N8N_WEBHOOK_URL": "https://n8n.example.com/webhook/facai-secret-path",
    "N8N_SIGNING_SECRET": "n8n-signing-secret-value",
}


def clear_n8n(monkeypatch):
    for key in list(N8N_ENV) + ["N8N_TIMEOUT", "N8N_MAX_ATTEMPTS", "N8N_MAX_SKEW",
                                "FACAI_N8N_RELAY_ENABLED"]:
        monkeypatch.delenv(key, raising=False)


def configure_n8n(monkeypatch):
    for key, value in N8N_ENV.items():
        monkeypatch.setenv(key, value)


def basic(password="n8n-pass"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()}


def signed_headers(integration, body, timestamp=None):
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    return {"X-Facai-Signature": f"sha256={integration.sign(body, stamp)}",
            "X-Facai-Timestamp": stamp}


def make_mission(client, title="n8n 任务"):
    return client.post("/api/v1/missions", json={
        "title": title, "original_input": "原话", "steps": ["规划", "开发"]}).json()


# --- configuration honesty ---------------------------------------------------

def test_unconfigured_reports_config_required_without_values(monkeypatch):
    clear_n8n(monkeypatch)
    integration = N8nIntegration()
    assert integration.configured() is False
    status = integration.status()
    assert status["state"] == STATE_CONFIG_REQUIRED
    assert status["send_ready"] is False
    assert set(status["missing"]) == {"N8N_WEBHOOK_URL", "N8N_SIGNING_SECRET"}
    blob = json.dumps(status, ensure_ascii=False)
    for value in N8N_ENV.values():
        assert value not in blob


def test_invalid_scheme_is_reported_not_claimed(monkeypatch):
    clear_n8n(monkeypatch)
    monkeypatch.setenv("N8N_WEBHOOK_URL", "n8n.example.com/webhook/x")
    monkeypatch.setenv("N8N_SIGNING_SECRET", "secret")
    integration = N8nIntegration()
    assert integration.configured() is False
    assert "N8N_WEBHOOK_URL" in integration.missing()


@pytest.mark.parametrize("url", ["http://", "https:///missing-host", "https://[::1"])
def test_malformed_http_url_is_not_reported_as_configured(monkeypatch, url):
    clear_n8n(monkeypatch)
    monkeypatch.setenv("N8N_WEBHOOK_URL", url)
    monkeypatch.setenv("N8N_SIGNING_SECRET", "secret")
    assert N8nIntegration().configured() is False


@pytest.mark.parametrize("url,scheme", [
    ("HTTP://n8n.example.com/webhook/facai-secret-path", "http"),
    ("HTTPS://n8n.example.com/webhook/facai-secret-path", "https"),
])
def test_uppercase_scheme_is_configured_and_deliverable(monkeypatch, url, scheme):
    """A valid http(s) URL must not be reported ready and then refused on send.

    missing()/configured() accept an upper-case scheme, so delivery has to agree
    with them; otherwise every event is attributed CONFIG_REQUIRED and silently
    dropped while the status endpoint still claims CONFIGURED.
    """
    clear_n8n(monkeypatch)
    monkeypatch.setenv("N8N_WEBHOOK_URL", url)
    monkeypatch.setenv("N8N_SIGNING_SECRET", "n8n-signing-secret-value")
    integration = N8nIntegration()
    assert integration.configured() is True
    status = integration.status()
    assert status["state"] == "CONFIGURED"
    assert status["send_ready"] is True
    assert status["webhook_scheme"] == scheme

    seen = {}

    def handler(request):
        seen["scheme"] = request.url.scheme
        return httpx.Response(200, json={"ok": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await integration.deliver({"request_id": "upper-1"}, client=client)

    assert asyncio.run(run())["status"] == "SENT"
    assert seen["scheme"] == scheme


# --- signatures --------------------------------------------------------------

def test_sign_and_verify_round_trip(monkeypatch):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()
    body = b'{"hello":"\xe4\xb8\x96\xe7\x95\x8c"}'
    headers = signed_headers(integration, body)
    assert integration.verify(body, headers) is True
    assert integration.verify(b'{"hello":"tampered"}', headers) is False
    assert integration.verify(body, {}) is False
    assert integration.verify(body, {"X-Facai-Signature": "sha256=deadbeef",
                                     "X-Facai-Timestamp": headers["X-Facai-Timestamp"]}) is False


def test_verify_rejects_stale_timestamp(monkeypatch):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()
    body = b'{"a":1}'
    old = str(int(time.time()) - 10_000)
    headers = signed_headers(integration, body, timestamp=old)
    assert integration.verify(body, headers, now=time.time()) is False


def test_unconfigured_verify_is_false(monkeypatch):
    clear_n8n(monkeypatch)
    assert N8nIntegration().verify(b"{}", {"X-Facai-Signature": "sha256=x",
                                           "X-Facai-Timestamp": str(int(time.time()))}) is False


@pytest.mark.parametrize("supplied", [
    "sha256=h\xe9llo",             # non-ASCII must not raise
    "sha256=deadbeef",             # wrong length
    "sha256=" + "z" * 64,          # right length, non-hex
    "md5=" + "a" * 64,             # wrong algorithm
    "deadbeef",                    # missing prefix
    b"sha256=" + b"a" * 64,        # bytes header value
    12345,                         # non-text header value
])
def test_verify_rejects_malformed_signature_without_raising(monkeypatch, supplied):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()
    headers = {"X-Facai-Signature": supplied,
               "X-Facai-Timestamp": str(int(time.time()))}
    assert integration.verify(b'{"a":1}', headers) is False


@pytest.mark.parametrize("timestamp", ["１７００００００００", b"1700000000", "not-a-time"])
def test_verify_rejects_malformed_timestamp_without_raising(monkeypatch, timestamp):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()
    headers = {"X-Facai-Signature": "sha256=" + "a" * 64,
               "X-Facai-Timestamp": timestamp}
    assert integration.verify(b'{"a":1}', headers) is False


# --- outbound delivery -------------------------------------------------------

def test_deliver_signs_request_and_never_leaks_secret(monkeypatch):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["sig"] = request.headers.get("x-facai-signature")
        seen["ts"] = request.headers.get("x-facai-timestamp")
        seen["request_id"] = request.headers.get("x-facai-request-id")
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await integration.deliver({"request_id": "req-1", "message": "世界"},
                                             client=client)

    result = asyncio.run(run())
    assert result["status"] == "SENT"
    assert seen["url"] == N8N_ENV["N8N_WEBHOOK_URL"]
    assert seen["request_id"] == "req-1"
    assert seen["sig"] == f"sha256={integration.sign(seen['body'], seen['ts'])}"
    assert integration.verify(seen["body"], {"X-Facai-Signature": seen["sig"],
                                             "X-Facai-Timestamp": seen["ts"]}) is True
    text = seen["body"].decode("utf-8")
    assert "世界" in text
    assert N8N_ENV["N8N_SIGNING_SECRET"] not in text
    assert N8N_ENV["N8N_WEBHOOK_URL"] not in text


def test_deliver_applies_configured_timeout_to_injected_client(monkeypatch):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("N8N_TIMEOUT", "7")
    integration = N8nIntegration()
    observed = {}

    def handler(request):
        observed.update(request.extensions["timeout"])
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await integration.deliver({"request_id": "timeout-test"}, client=client)

    assert asyncio.run(run())["status"] == "SENT"
    assert observed == {"connect": 7, "read": 7, "write": 7, "pool": 7}


@pytest.mark.parametrize("status,error,retryable", [
    (401, "AUTH_FAILED", False),
    (403, "AUTH_FAILED", False),
    (429, "RATE_LIMITED", True),
    (408, "TIMEOUT", True),
    (500, "UPSTREAM_UNAVAILABLE", True),
    (400, "REJECTED", False),
])
def test_deliver_attributes_http_errors(monkeypatch, status, error, retryable):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()

    def handler(request):
        return httpx.Response(status, json={})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await integration.deliver({"request_id": "r"}, client=client)

    result = asyncio.run(run())
    assert result["status"] == "FAILED"
    assert result["http_status"] == status
    assert result["error"] == error
    assert result["retryable"] is retryable


@pytest.mark.parametrize("exception,error", [
    (httpx.ConnectTimeout("slow"), "TIMEOUT"),
    (httpx.ConnectError("down"), "TRANSPORT_ERROR"),
])
def test_deliver_attributes_transport_errors(monkeypatch, exception, error):
    configure_n8n(monkeypatch)
    integration = N8nIntegration()

    def handler(request):
        raise exception

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await integration.deliver({"request_id": "r"}, client=client)

    result = asyncio.run(run())
    assert result["status"] == "FAILED"
    assert result["http_status"] is None
    assert result["error"] == error
    assert result["retryable"] is True


# --- status API --------------------------------------------------------------

def test_status_api_requires_auth_and_reports_config_required(monkeypatch, tmp_path):
    clear_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "n8n-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/n8n").status_code == 401
    response = client.get("/api/v1/n8n", headers=basic())
    body = response.json()
    assert body["state"] == STATE_CONFIG_REQUIRED
    assert set(body["missing"]) == {"N8N_WEBHOOK_URL", "N8N_SIGNING_SECRET"}
    assert body["recent"] == []
    for value in N8N_ENV.values():
        assert value not in response.text


def test_status_api_configured_never_leaks_url_or_secret(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "n8n-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/v1/n8n", headers=basic())
    body = response.json()
    assert body["state"] == "CONFIGURED"
    assert body["missing"] == []
    assert body["send_ready"] is True
    assert body["callback_ready"] is True
    assert body["webhook_configured"] is True
    assert body["webhook_scheme"] == "https"
    assert "n8n.example.com" not in response.text
    assert N8N_ENV["N8N_SIGNING_SECRET"] not in response.text


def test_relay_without_config_is_a_noop(monkeypatch, tmp_path):
    clear_n8n(monkeypatch)
    app = create_app(tmp_path)
    client = TestClient(app)
    result = client.post("/api/v1/n8n/relay").json()
    assert result["state"] == STATE_CONFIG_REQUIRED
    assert result["dispatched"] == 0
    assert app.state.store.n8n_deliveries() == []


def test_relay_delivers_with_uppercase_scheme_without_config_required(
        monkeypatch, tmp_path):
    """The relay must send whenever the same URL is reported as ready.

    Before the fix, an upper-case scheme passed configured() but made deliver()
    raise N8nConfigError, so the event was recorded as a non-retryable
    CONFIG_REQUIRED failure and permanently dropped.
    """
    clear_n8n(monkeypatch)
    monkeypatch.setenv("N8N_WEBHOOK_URL", "HTTPS://n8n.example.com/webhook/facai-secret-path")
    monkeypatch.setenv("N8N_SIGNING_SECRET", "n8n-signing-secret-value")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-facai-request-id"))
        return httpx.Response(200)

    app = create_app(tmp_path)
    app.state.n8n_relay.client_factory = (
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    make_mission(client)

    result = client.post("/api/v1/n8n/relay").json()
    assert result["state"] == "CONFIGURED"
    assert result["dispatched"] == 1
    delivery = client.get("/api/v1/n8n").json()["recent"][0]
    assert delivery["status"] == "SENT"
    assert delivery["error"] is None
    assert seen and seen[0].startswith("facai-evt-")


# --- relay + retry -----------------------------------------------------------

def test_relay_forwards_stage_events_with_signature(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    seen = []

    def handler(request):
        seen.append({"body": request.content,
                     "sig": request.headers.get("x-facai-signature"),
                     "ts": request.headers.get("x-facai-timestamp"),
                     "request_id": request.headers.get("x-facai-request-id")})
        return httpx.Response(200, json={"ok": True})

    app = create_app(tmp_path)
    app.state.n8n_relay.client_factory = (
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    mission = make_mission(client)

    result = client.post("/api/v1/n8n/relay").json()
    assert result["state"] == "CONFIGURED"
    assert result["dispatched"] == 1
    assert len(seen) == 1
    payload = json.loads(seen[0]["body"].decode("utf-8"))
    assert payload["event"]["type"] == "MISSION_CREATED"
    assert payload["mission"]["id"] == mission["id"]
    assert payload["request_id"] == f"facai-evt-{payload['event']['id']}"
    assert seen[0]["request_id"] == payload["request_id"]
    assert seen[0]["sig"] == f"sha256={app.state.n8n.sign(seen[0]['body'], seen[0]['ts'])}"

    listed = client.get("/api/v1/n8n").json()
    assert listed["state"] == "CONFIGURED"
    sent = [d for d in listed["recent"] if d["event_type"] == "MISSION_CREATED"]
    assert sent and sent[0]["status"] == "SENT"
    assert listed["summary"].get("SENT") == 1
    assert "n8n.example.com" not in json.dumps(listed)


def test_relay_retries_retryable_failure_then_succeeds(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] == 1 else 200, json={})

    app = create_app(tmp_path)
    app.state.n8n_relay.client_factory = (
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    make_mission(client)

    client.post("/api/v1/n8n/relay")
    first = client.get("/api/v1/n8n").json()["recent"][0]
    assert first["status"] == "FAILED"
    assert first["retryable"] is True
    assert first["attempts"] == 1

    client.post("/api/v1/n8n/relay")
    second = client.get("/api/v1/n8n").json()["recent"][0]
    assert second["status"] == "SENT"
    assert second["attempts"] == 2
    assert calls["n"] == 2


@pytest.mark.parametrize(("http_status", "retryable"), [(400, False), (500, True)])
def test_relay_does_not_exceed_retry_policy_when_cursor_write_retries(
        monkeypatch, tmp_path, http_status, retryable):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("N8N_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(http_status, json={"error": "mock failure"})

    app = create_app(tmp_path)
    app.state.n8n_relay.client_factory = (
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    make_mission(client)
    store = app.state.store
    original_set_cursor = store.n8n_set_cursor
    failures = {"remaining": 1}

    def fail_cursor_once(sequence):
        if sequence and failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("simulated cursor persistence interruption")
        return original_set_cursor(sequence)

    store.n8n_set_cursor = fail_cursor_once
    with pytest.raises(RuntimeError, match="cursor persistence"):
        app.state.n8n_relay.run_pending()
    store.n8n_set_cursor = original_set_cursor

    retried = app.state.n8n_relay.run_pending()
    delivery = store.n8n_deliveries()[0]
    assert retried["dispatched"] == 0
    assert delivery["status"] == "FAILED"
    assert delivery["attempts"] == 1
    assert delivery["retryable"] is retryable
    assert calls["n"] == 1


def test_stage_allowlist_excludes_own_events_and_output():
    assert "MISSION_CREATED" in STAGE_EVENT_TYPES
    assert "STEP_COMPLETED" in STAGE_EVENT_TYPES
    assert {"STEP_BLOCKED", "STEP_UNBLOCKED", "JOB_BLOCKED"} <= STAGE_EVENT_TYPES
    assert "OUTPUT" not in STAGE_EVENT_TYPES
    assert all(not event.startswith("N8N_") for event in STAGE_EVENT_TYPES)


def test_relay_forwards_dependency_block_and_recovery_events(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    forwarded = []

    def handler(request):
        forwarded.append(json.loads(request.content)["event"]["type"])
        return httpx.Response(200)

    app = create_app(tmp_path)
    app.state.n8n_relay.client_factory = (
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    mission = make_mission(client)
    step_id = mission["steps"][0]["id"]
    for event_type in ("STEP_BLOCKED", "STEP_UNBLOCKED", "JOB_BLOCKED"):
        app.state.store.append(mission["id"], event_type, {"status": event_type}, step_id=step_id)

    result = client.post("/api/v1/n8n/relay").json()
    assert result["dispatched"] == 4  # mission creation plus the three DAG events
    assert set(forwarded) == {"MISSION_CREATED", "STEP_BLOCKED", "STEP_UNBLOCKED", "JOB_BLOCKED"}


# --- callback ----------------------------------------------------------------

def test_callback_requires_valid_signature(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    payload = {"request_id": "cb-1", "mission_id": mission["id"], "status": "SUCCEEDED"}
    body = json.dumps(payload).encode()

    bad = client.post("/api/v1/n8n/callback", content=body,
                      headers={"X-Facai-Signature": "sha256=deadbeef",
                               "X-Facai-Timestamp": str(int(time.time()))})
    assert bad.status_code == 403
    assert app.state.store.n8n_callbacks() == []

    good = client.post("/api/v1/n8n/callback", content=body,
                       headers=signed_headers(app.state.n8n, body))
    assert good.status_code == 200


def test_callback_with_malformed_signature_is_rejected_not_500(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    mission = make_mission(client)
    body = json.dumps({"request_id": "cb-malformed", "mission_id": mission["id"],
                       "status": "SUCCEEDED"}).encode()
    response = client.post(
        "/api/v1/n8n/callback",
        content=body,
        headers=[
            (b"Content-Type", b"application/json"),
            (b"X-Facai-Signature", b"sha256=h\xe9llo"),
            (b"X-Facai-Timestamp", str(int(time.time())).encode()),
        ],
    )
    assert response.status_code == 403
    assert app.state.store.n8n_callbacks() == []


def test_callback_unconfigured_reports_config_required(monkeypatch, tmp_path):
    clear_n8n(monkeypatch)
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/v1/n8n/callback", content=b"{}")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["state"] == STATE_CONFIG_REQUIRED
    assert set(detail["missing"]) == {"N8N_WEBHOOK_URL", "N8N_SIGNING_SECRET"}


def test_callback_is_idempotent_by_request_id(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    payload = {"request_id": "cb-dup", "mission_id": mission["id"], "status": "SUCCEEDED",
               "event": {"type": "N8N_STAGE_DONE", "payload": {"note": "ok"}}}
    body = json.dumps(payload).encode()
    headers = signed_headers(app.state.n8n, body)

    first = client.post("/api/v1/n8n/callback", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/api/v1/n8n/callback", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True

    events = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    applied = [e for e in events if e["type"] == "N8N_STAGE_DONE"]
    assert len(applied) == 1
    assert app.state.store.n8n_callbacks()[0]["request_id"] == "cb-dup"


def test_callback_updates_a_step(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    step_id = mission["steps"][0]["id"]
    payload = {"request_id": "cb-step", "mission_id": mission["id"],
               "status": "SUCCEEDED",
               "step": {"step_id": step_id, "status": "RUNNING"}}
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/n8n/callback", content=body,
                           headers=signed_headers(app.state.n8n, body))
    assert response.status_code == 200
    assert response.json()["applied"]["step"] == {"step_id": step_id, "status": "RUNNING"}
    updated = client.get(f"/api/v1/missions/{mission['id']}").json()
    assert updated["steps"][0]["status"] == "RUNNING"


def test_callback_rejects_invalid_transition_and_records_it(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    step_id = mission["steps"][0]["id"]
    payload = {"request_id": "cb-bad-step", "mission_id": mission["id"],
               "status": "SUCCEEDED",
               "step": {"step_id": step_id, "status": "COMPLETED"}}
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/n8n/callback", content=body,
                           headers=signed_headers(app.state.n8n, body))
    assert response.status_code == 409
    recorded = app.state.store.n8n_callbacks()[0]
    assert recorded["request_id"] == "cb-bad-step"
    assert recorded["detail"].startswith("INVALID_TRANSITION")


def test_callback_unknown_mission_is_rejected(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    payload = {"request_id": "cb-missing", "mission_id": "does-not-exist",
               "status": "SUCCEEDED"}
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/n8n/callback", content=body,
                           headers=signed_headers(app.state.n8n, body))
    assert response.status_code == 404


def test_callback_records_retry_and_error_attribution(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    payload = {"request_id": "cb-retry", "mission_id": mission["id"], "status": "RETRY",
               "attempt": 2, "error": {"code": "TIMEOUT", "message": "上游慢", "retryable": True}}
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/n8n/callback", content=body,
                           headers=signed_headers(app.state.n8n, body))
    assert response.status_code == 200
    assert response.json()["retryable"] is True
    callback = app.state.store.n8n_callbacks()[0]
    assert callback["status"] == "RETRY"
    detail = json.loads(callback["detail"])
    assert detail["error"]["code"] == "TIMEOUT"
    assert detail["retry"] is None


def test_callback_rejects_unknown_status(monkeypatch, tmp_path):
    configure_n8n(monkeypatch)
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    client = TestClient(app)
    mission = make_mission(client)
    payload = {"request_id": "cb-status", "mission_id": mission["id"], "status": "WHATEVER"}
    body = json.dumps(payload).encode()
    response = client.post("/api/v1/n8n/callback", content=body,
                           headers=signed_headers(app.state.n8n, body))
    assert response.status_code == 422
