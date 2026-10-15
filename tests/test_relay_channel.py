"""AutoClaw relay channel: signed webhook in, durable outbox out.

The relay is the generic bridge for external forwarders: AutoClaw drives a
personal WhatsApp account (unofficial, operator's own risk) and relays
messages over signed HTTP. These tests pin the whole contract:

* the n8n signature scheme (sha256 over ``<timestamp>.<body>``, skew-bounded);
* the shared inbound pipeline (dedupe by external_id, verbatim original,
  full-DAG dispatch, operator ownership, chat ack);
* the outbox poll/ack loop that carries progress lines back to WhatsApp.
"""

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.channels.relay import RelayProvider

SECRET = "relay-test-secret"
HEADERS_BASE = {"Content-Type": "application/json"}


def _signed_headers(body: bytes, secret=SECRET, timestamp=None, omit=None):
    ts = str(timestamp if timestamp is not None else int(time.time()))
    message = ts.encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    headers = dict(HEADERS_BASE)
    if omit != "signature":
        headers["X-Facai-Signature"] = f"sha256={digest}"
    if omit != "timestamp":
        headers["X-Facai-Timestamp"] = ts
    return headers


def _client(monkeypatch, tmp_path, secret=SECRET):
    if secret is None:
        monkeypatch.delenv("RELAY_SHARED_SECRET", raising=False)
    else:
        monkeypatch.setenv("RELAY_SHARED_SECRET", secret)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    app = create_app(tmp_path)
    return TestClient(app), app


def _webhook(client, payload, secret=SECRET, timestamp=None, omit=None, raw=None):
    body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post("/api/v1/channels/relay/webhook", content=body,
                       headers=_signed_headers(body, secret=secret, timestamp=timestamp, omit=omit))


def _poll(client, payload=None, secret=SECRET):
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    return client.post("/api/v1/channels/relay/send", content=body,
                       headers=_signed_headers(body, secret=secret))


# --- provider unit: signature scheme -------------------------------------------


def test_relay_provider_signature_matches_n8n_scheme():
    provider = RelayProvider({"RELAY_SHARED_SECRET": SECRET})
    body = b'{"external_id":"w1"}'
    ts = 1_700_000_000
    expected = hmac.new(SECRET.encode("utf-8"), f"{ts}.".encode("ascii") + body,
                        hashlib.sha256).hexdigest()
    assert provider.sign(body, ts) == expected
    assert provider.verify_signature(body, {"X-Facai-Signature": f"sha256={expected}",
                                            "X-Facai-Timestamp": str(ts)}, now=ts)
    assert not provider.verify_signature(b"tampered", {"X-Facai-Signature": f"sha256={expected}",
                                                       "X-Facai-Timestamp": str(ts)}, now=ts)
    assert not provider.verify_signature(body, {"X-Facai-Signature": expected,
                                                "X-Facai-Timestamp": str(ts)}, now=ts)
    assert not provider.verify_signature(body, _signed_headers(body, timestamp=ts - 3600), now=ts)
    assert not provider.verify_signature(body, _signed_headers(body, omit="signature"), now=ts)
    assert not provider.verify_signature(body, _signed_headers(body, omit="timestamp"), now=ts)
    assert not RelayProvider({}).verify_signature(body, _signed_headers(body))


def test_relay_provider_status_is_honest(monkeypatch, tmp_path):
    monkeypatch.delenv("RELAY_SHARED_SECRET", raising=False)
    unconfigured = RelayProvider({})
    assert unconfigured.status().state == "UNCONFIGURED"
    assert unconfigured.status().missing == ["RELAY_SHARED_SECRET"]
    configured = RelayProvider({"RELAY_SHARED_SECRET": SECRET})
    assert configured.status().state == "CONFIGURED"
    assert configured.missing() == []


def test_relay_provider_parses_verbatim_and_rejects_garbage():
    provider = RelayProvider({"RELAY_SHARED_SECRET": SECRET})
    messages = provider.parse_inbound({"external_id": " w1 ", "sender": "8613800138000",
                                       "text": "  帮我部署一下  ", "recipient": "me"})
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "relay"
    assert message.external_id == "w1" and message.sender == "8613800138000"
    assert message.text == "  帮我部署一下  "  # verbatim; trimming is the caller's business
    for bad in ({}, {"external_id": "w1"}, {"sender": "s", "text": "t"},
                {"external_id": "w1", "sender": "s", "text": "   "},
                {"external_id": "w1", "sender": "s", "text": 42}):
        try:
            provider.parse_inbound(bad)
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass


# --- webhook: the shared inbound pipeline ---------------------------------------


def test_relay_webhook_requires_configuration_then_signature(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path / "a", secret=None)
    response = _webhook(client, {"external_id": "w1", "sender": "s", "text": "t"})
    assert response.status_code == 503  # honest UNCONFIGURED, never a silent drop

    client, _app = _client(monkeypatch, tmp_path / "b")
    body = json.dumps({"external_id": "w1", "sender": "s", "text": "t"}).encode("utf-8")
    assert client.post("/api/v1/channels/relay/webhook", content=body,
                       headers=_signed_headers(body, secret="wrong-secret")).status_code == 403
    stale = _webhook(client, {"external_id": "w1", "sender": "s", "text": "t"},
                     timestamp=int(time.time()) - 3600)
    assert stale.status_code == 403


def test_relay_webhook_creates_mission_with_full_dag_and_ack(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    payload = {"external_id": "wa-100", "sender": "8613800138000",
               "text": "帮我把部署步骤整理一下"}
    response = _webhook(client, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["dispatch"] == "QUEUED" and result["received"] == 1
    dispatch = result["results"][0]
    assert dispatch["status"] == "QUEUED" and dispatch["mission_id"]

    mission = app.state.store.get(dispatch["mission_id"])
    assert mission["original_input"] == "帮我把部署步骤整理一下"  # verbatim
    assert mission["owner_id"] == "admin"  # webhook traffic attributes to the operator
    assert mission["agent_id"] and mission["model_id"] and mission["runtime_id"]
    jobs = app.state.store.jobs(mission["id"])
    assert len(jobs) == len(mission["steps"]) == 5  # full DAG, dependency-gated
    assert all(job["status"] == "QUEUED" for job in jobs)

    # The chat acknowledgement waited in the durable outbox for AutoClaw.
    pending = app.state.store.channel_outbound_pending("relay")
    assert any("已收到需求" in row["text"] and row["recipient"] == "8613800138000"
               for row in pending)


def test_relay_duplicate_external_id_never_duplicates_mission(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    payload = {"external_id": "wa-dup", "sender": "8613800138000", "text": "同一个需求"}
    first = _webhook(client, payload).json()["results"][0]
    second = _webhook(client, payload).json()["results"][0]
    assert first["mission_id"] == second["mission_id"]
    assert second["duplicate"] is True
    missions = [m for m in app.state.store.list("admin") if m["original_input"] == "同一个需求"]
    assert len(missions) == 1


def test_relay_webhook_rejects_malformed_payload(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    assert _webhook(client, {"sender": "s", "text": "no external id"}).status_code == 400
    assert _webhook(client, None, raw=b"not json").status_code == 400


# --- outbox: the poll/ack loop back to WhatsApp ---------------------------------


def test_relay_send_poll_requires_signature_and_configuration(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path / "a", secret=None)
    assert _poll(client).status_code == 503
    client, _app = _client(monkeypatch, tmp_path / "b")
    assert _poll(client, secret="wrong").status_code == 403
    body = json.dumps({"ack_ids": []}).encode("utf-8")
    assert client.post("/api/v1/channels/relay/send", content=body,
                       headers={"Content-Type": "application/json"}).status_code == 403


def test_relay_send_poll_acks_and_drains_outbox(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    _webhook(client, {"external_id": "wa-poll", "sender": "8613800138000",
                      "text": "整理部署步骤"})

    first = _poll(client).json()
    assert first["status"] == "PENDING"
    assert any("已收到需求" in message["text"] for message in first["messages"])
    assert all(message["recipient"] == "8613800138000" for message in first["messages"])

    # Without an ack the messages are at-least-once: the same batch comes back.
    again = _poll(client).json()
    assert [m["id"] for m in again["messages"]] == [m["id"] for m in first["messages"]]

    # Ack everything: the outbox drains and stays drained.
    ids = [m["id"] for m in first["messages"]]
    drained = _poll(client, {"ack_ids": ids}).json()
    assert drained["acked"] == len(ids)
    assert drained["status"] == "EMPTY" and drained["messages"] == []
    assert _poll(client).json()["status"] == "EMPTY"

    # Acked rows are audited as SENT, never resurrected by a duplicate ack.
    rows = app.state.store.channel_messages("relay")
    assert all(row["status"] == "SENT" for row in rows if row["direction"] == "OUTBOUND")
    assert _poll(client, {"ack_ids": ids}).json()["acked"] == 0


def test_relay_progress_lines_flow_to_outbox(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    result = _webhook(client, {"external_id": "wa-progress", "sender": "8613800138000",
                               "text": "做一个喝水统计"}).json()["results"][0]
    mission_id = result["mission_id"]
    mission = app.state.store.get(mission_id)
    step_id = mission["steps"][0]["id"]

    # Initialize the notify cursor before the events, so they are still pending.
    app.state.notify.run_pending()
    app.state.store.step_status(mission_id, step_id, "RUNNING")
    app.state.store.step_status(mission_id, step_id, "COMPLETED")
    assert app.state.notify.run_pending() >= 1

    pending = _poll(client).json()
    texts = [m["text"] for m in pending["messages"]]
    assert any("阶段「" in text and "完成" in text for text in texts), texts


def test_relay_progress_lines_are_never_duplicated_in_outbox(monkeypatch, tmp_path):
    # The relay provider durably records its own outbound row; the notifier's
    # audit must not write a second one, or AutoClaw delivers every line twice.
    client, app = _client(monkeypatch, tmp_path)
    result = _webhook(client, {"external_id": "wa-dedupe", "sender": "8613800138000",
                               "text": "做一个站会提醒"}).json()["results"][0]
    mission_id = result["mission_id"]
    mission = app.state.store.get(mission_id)
    step_id = mission["steps"][0]["id"]

    app.state.notify.run_pending()
    app.state.store.step_status(mission_id, step_id, "RUNNING")
    app.state.store.step_status(mission_id, step_id, "COMPLETED")
    app.state.notify.run_pending()

    rows = [row for row in app.state.store.channel_messages("relay")
            if row["direction"] == "OUTBOUND"]
    texts = [row["text"] for row in rows]
    assert len(texts) == len(set(texts)), f"duplicated outbound rows: {texts}"
