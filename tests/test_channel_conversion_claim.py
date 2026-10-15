"""Durable per-inbound-message conversion claim.

Duplicate webhook deliveries of the same message must not each invoke the paid
conversion model. The claim is a lease on the audit row: exactly one delivery
converts, duplicates get a truthful IN_PROGRESS answer, a crashed holder is
recovered after the lease expires, and no failure strands a message forever.
"""

import base64
import concurrent.futures
import hashlib
import hmac
import json
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.prompting import KNOWN_PHASES

PASSWORD = "claim-pass"
TEXT = "帮我整理发布步骤"
SECRET = "app-secret-value"


def auth():
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()}


def model_json(original=TEXT):
    return json.dumps({
        "title": "整理发布步骤",
        "professional_prompt": f"目标：整理发布步骤。\n用户原话：{original}",
        "phases": list(KNOWN_PHASES),
    }, ensure_ascii=False)


class Recorder:
    """Counts conversion model calls; never touches a real provider."""

    def __init__(self):
        self.calls = 0

    async     def __call__(self, model, messages, client=None, timeout=30):
        self.calls += 1
        return self._result()

    def _result(self):
        return {"status": "MODEL_COMPLETED", "label": "ok", "http_status": 200,
                "content": model_json(), "upstream": {}, "reason": None, "usage": None}


class BlockingRecorder(Recorder):
    """Holds the winning delivery inside conversion to expose the race window."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.proceed = threading.Event()

    async def __call__(self, model, messages, client=None, timeout=30):
        self.calls += 1
        self.entered.set()
        assert self.proceed.wait(timeout=15)
        return self._result()


def build(tmp_path, monkeypatch, recorder):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setattr("facai.api.complete", recorder)
    app = create_app(tmp_path)
    return app


def record(app, external_id="update:claim", text=TEXT):
    return app.state.store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id=external_id,
        sender="42", text=text, payload={"update_id": external_id})


# --- store-level primitive ----------------------------------------------------

def test_concurrent_duplicate_claims_allow_exactly_one_worker(tmp_path, monkeypatch):
    """Duplicate deliveries released together yield one claim and N rejections."""
    app = build(tmp_path, monkeypatch, Recorder())
    store = app.state.store
    message_id = record(app, "update:barrier")

    workers = 8
    barrier = threading.Barrier(workers)

    def claim():
        barrier.wait(timeout=15)
        return store.claim_channel_conversion(message_id, lease_seconds=300)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [future.result() for future in [pool.submit(claim) for _ in range(workers)]]

    winners = [result for result in results if result["claimed"]]
    losers = [result for result in results if not result["claimed"]]
    assert len(winners) == 1
    assert len(losers) == workers - 1
    assert {result["reason"] for result in losers} == {"IN_PROGRESS"}
    assert store.channel_message(message_id)["status"] == "CONVERTING"


def test_stale_conversion_claim_is_recovered_for_replay(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, Recorder())
    store = app.state.store
    message_id = record(app, "update:stale")

    # A live claim is never stolen by recovery, and can be released by its owner.
    claim = store.claim_channel_conversion(message_id, lease_seconds=300)
    assert claim["claimed"] is True
    assert store.recover_stale_channel_conversions() == 0
    assert store.release_channel_conversion(message_id, claim["owner"]) is True
    assert store.channel_message(message_id)["status"] == "RETRY"

    # An expired lease (crashed holder) is reset to the replayable pool ...
    assert store.claim_channel_conversion(message_id, lease_seconds=-1)["claimed"] is True
    assert store.recover_stale_channel_conversions() == 1
    recovered = store.channel_message(message_id)
    assert recovered["status"] == "RETRY"
    assert recovered["conversion_owner"] is None
    assert recovered["conversion_expires_at"] is None

    # ... and a fresh replay can claim it again.
    assert store.claim_channel_conversion(message_id, lease_seconds=300)["claimed"] is True


def test_recovery_never_touches_a_linked_mission(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, Recorder())
    store = app.state.store
    message_id = record(app, "update:linked")
    claim = store.claim_channel_conversion(message_id, lease_seconds=-1)
    assert claim["claimed"] is True
    mission_id, created = store.create_mission_from_message(
        message_id, "telegram", title="t", original_input=TEXT, labels=["第一步"],
        professional_prompt="p", conversion_payload={"channel": "telegram"})
    assert created is True
    assert store.recover_stale_channel_conversions() == 0
    linked = store.channel_message(message_id)
    assert linked["mission_id"] == mission_id
    assert linked["status"] == "DISPATCHED"


# --- end-to-end duplicate deliveries -----------------------------------------

def test_concurrent_duplicate_webhook_deliveries_call_model_once(tmp_path, monkeypatch):
    """Two identical webhook deliveries released together convert once.

    A barrier inside the claim forces both deliveries into the contention window.
    The real webhook endpoint, store and dispatch path run unchanged; only the
    requested model call is recorded.
    """
    recorder = BlockingRecorder()
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "access-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    app = build(tmp_path, monkeypatch, recorder)
    store = app.state.store
    original_claim = store.claim_channel_conversion
    barrier = threading.Barrier(2)

    def gated_claim(message_id, lease_seconds=300, owner=None):
        barrier.wait(timeout=15)
        return original_claim(message_id, lease_seconds, owner)

    store.claim_channel_conversion = gated_claim

    body = json.dumps(whatsapp_payload()).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": f"sha256={signature}"}

    server, thread, base = _serve(app)
    try:
        def deliver():
            request = urllib.request.Request(
                base + "/api/v1/channels/whatsapp/webhook", data=body, headers=headers,
                method="POST")
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(deliver) for _ in range(2)]
            # The winner is inside the conversion model call and still holds the
            # lease, so the duplicate must answer without converting.
            assert recorder.entered.wait(timeout=15)
            done, _pending = concurrent.futures.wait(
                futures, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
            assert len(done) == 1
            duplicate_future = done.pop()
            duplicate_status, duplicate_body = duplicate_future.result()
            assert duplicate_status == 200
            assert duplicate_body["results"][0]["status"] == "IN_PROGRESS"
            recorder.proceed.set()
            winner_future = next(f for f in futures if f is not duplicate_future)

        winner_status, winner_body = winner_future.result()
        assert winner_status == 200
        assert winner_body["results"][0]["status"] == "QUEUED"
        assert recorder.calls == 1
        assert len(store.list()) == 1
        mission = store.list()[0]
        assert len(store.jobs(mission["id"])) == 5
        # The ack path may append an OUTBOUND audit row for the same channel,
        # so address the inbound delivery by its external id instead of row
        # position (channel_messages sorts newest-first regardless of direction).
        linked = store.channel_message_by_external_id("whatsapp", "wamid.CLAIM")
        assert linked["mission_id"] == mission["id"]
        assert linked["status"] == "DISPATCHED"
        assert linked["conversion_owner"] is None
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_live_claim_returns_in_progress_without_a_model_call(tmp_path, monkeypatch):
    recorder = Recorder()
    app = build(tmp_path, monkeypatch, recorder)
    store = app.state.store
    message_id = record(app, "update:live")
    assert store.claim_channel_conversion(message_id, lease_seconds=300)["claimed"] is True

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/channels/telegram/messages/{message_id}/dispatch", headers=auth())

    assert response.status_code == 202
    assert response.json()["status"] == "IN_PROGRESS"
    assert recorder.calls == 0
    assert store.list() == []


def test_creation_error_releases_claim_and_replay_succeeds(tmp_path, monkeypatch):
    recorder = Recorder()
    app = build(tmp_path, monkeypatch, recorder)
    store = app.state.store
    message_id = record(app, "update:error")

    original_create = store.create_mission_from_message
    failures = {"count": 0}

    def fail_once(message_id, channel, **kwargs):
        if failures["count"] == 0:
            failures["count"] += 1
            raise RuntimeError("transient conversion write failure")
        return original_create(message_id, channel, **kwargs)

    monkeypatch.setattr(store, "create_mission_from_message", fail_once)
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                        headers=auth())
    assert first.status_code == 500
    stranded = store.channel_message(message_id)
    assert stranded["status"] == "RETRY"
    assert stranded["conversion_owner"] is None
    assert stranded["conversion_expires_at"] is None

    second = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                         headers=auth())
    assert second.status_code == 201
    assert failures["count"] == 1
    assert len(store.list()) == 1
    assert len(store.jobs(store.list()[0]["id"])) == 5
    # The abandoned conversion was retried from scratch; the failure only cost
    # one extra conversion, never a stranded message or a duplicate mission.
    assert recorder.calls == 2


# --- helpers ------------------------------------------------------------------

def whatsapp_payload():
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "biz", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": "8613800000000", "id": "wamid.CLAIM", "timestamp": "1",
                          "type": "text", "text": {"body": TEXT}}],
        }}]}],
    }


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve(app):
    import uvicorn

    base = f"http://127.0.0.1:{_free_port()}"
    config = uvicorn.Config(app, host="127.0.0.1", port=int(base.rsplit(":", 1)[1]),
                            log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        try:
            urllib.request.urlopen(base + "/api/v1/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.05)
    else:
        raise RuntimeError("uvicorn did not start")
    return server, thread, base
