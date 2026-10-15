"""Slack channel adapter: native v0 HMAC signature, honest send, event parsing.

Mirrors tests/test_line_channel.py conventions for the seventh channel: the
adapter never fabricates a connection (UNCONFIGURED without credentials,
missing variable *names* only) and never reports a delivery the push API did
not confirm with ``ok: true`` plus a concrete message ``ts``. Signature
verification is Slack's own scheme (``v0:<hex hmac>`` over
``v0:<timestamp>:<body>``) with the documented five-minute replay window.
"""

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.channels import ChannelConfigError, OutboundReply
from facai.channels.slack import SlackProvider

ENV = {"SLACK_BOT_TOKEN": "slack-bot-token-value",
       "SLACK_SIGNING_SECRET": "slack-signing-secret-value"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin").decode()}

EVENT = {"type": "message",
         "text": "你好，帮我看下任务",
         "user": "U4SLACK0USER",
         "ts": "1769160000.000300",
         "channel": "C0SLACKCHAN"}
PAYLOAD = {"type": "event_callback", "team_id": "T0SLACKTEAM", "event": EVENT}


def sign(body, secret=ENV["SLACK_SIGNING_SECRET"], timestamp=None):
    ts = timestamp if timestamp is not None else int(time.time())
    base = f"v0:{ts}:".encode() + body
    return str(ts), "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def signed_headers(body, secret=ENV["SLACK_SIGNING_SECRET"], timestamp=None):
    ts, signature = sign(body, secret, timestamp)
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": signature}


def configure(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


# --- signature -------------------------------------------------------------

def test_signature_accepts_only_matching_v0_hmac(monkeypatch):
    configure(monkeypatch)
    provider = SlackProvider()
    body = json.dumps(PAYLOAD).encode()
    assert provider.verify_signature(body, signed_headers(body)) is True
    # Wrong secret, tampered body, missing headers and unconfigured channel all
    # fail closed.
    assert provider.verify_signature(body, signed_headers(body, "other")) is False
    assert provider.verify_signature(b'{"tampered":true}', signed_headers(body)) is False
    assert provider.verify_signature(body, {"X-Slack-Signature": "v0=abc"}) is False
    assert provider.verify_signature(body, {"X-Slack-Request-Timestamp": str(int(time.time()))}) is False
    assert SlackProvider({}).verify_signature(body, signed_headers(body)) is False


def test_signature_refuses_replayed_or_nonsense_timestamps(monkeypatch):
    configure(monkeypatch)
    provider = SlackProvider()
    body = json.dumps(PAYLOAD).encode()
    now = 1769160000
    fresh = signed_headers(body, timestamp=now)
    assert provider.verify_signature(body, fresh, now=now) is True
    # The documented 5-minute replay window: outside it the delivery is
    # refused even with a valid signature.
    assert provider.verify_signature(body, fresh, now=now + 301) is False
    assert provider.verify_signature(body, fresh, now=now - 301) is False
    assert provider.verify_signature(body, fresh, now=now + 300) is True
    # A non-numeric timestamp is a malformed delivery, not a clock skew.
    broken = {"X-Slack-Request-Timestamp": "yesterday",
              "X-Slack-Signature": "v0=" + "0" * 64}
    assert provider.verify_signature(body, broken, now=now) is False


# --- honest configuration --------------------------------------------------

def test_unconfigured_reports_missing_names_never_values(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    provider = SlackProvider({})
    status = provider.status()
    assert status.implemented is True
    assert status.state == "UNCONFIGURED"
    assert status.missing == list(SlackProvider.required_env)
    assert status.send_ready is False
    blob = json.dumps(status.to_dict(), ensure_ascii=False)
    for value in ENV.values():
        assert value not in blob
    with pytest.raises(ChannelConfigError) as excinfo:
        asyncio.run(provider.send(OutboundReply(channel="slack",
                                                recipient="C0SLACKCHAN", text="hi")))
    assert "SLACK_BOT_TOKEN" in excinfo.value.missing


# --- inbound parsing -------------------------------------------------------

def test_parse_inbound_normalizes_event_callback():
    provider = SlackProvider({})
    messages = provider.parse_inbound(PAYLOAD)
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "slack"
    assert message.sender == "U4SLACK0USER"
    assert message.recipient == "C0SLACKCHAN"
    assert message.kind == "text"
    assert message.text == "你好，帮我看下任务"
    assert message.external_id == "1769160000.000300"
    assert message.metadata["team_id"] == "T0SLACKTEAM"
    assert message.raw == EVENT


def test_parse_inbound_skips_bots_subtypes_and_identity_less_events():
    provider = SlackProvider({})
    payload = copy.deepcopy(PAYLOAD)
    payload["event"]["bot_id"] = "B0OURBOT"  # never self-reply loops
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["event"]["subtype"] = "message_changed"  # edits/deletes/joins
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["event"]["type"] = "app_mention"  # not a plain message event
    assert provider.parse_inbound(payload) == []
    for key in ("ts", "user", "channel"):
        payload = copy.deepcopy(PAYLOAD)
        del payload["event"][key]
        assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["event"]["text"] = ""
    assert provider.parse_inbound(payload) == []
    # Malformed containers are skipped, never raised.
    assert provider.parse_inbound({"type": "url_verification"}) == []
    assert provider.parse_inbound({"type": "event_callback", "event": "not-a-dict"}) == []
    assert provider.parse_inbound("not-a-dict") == []
    assert provider.parse_inbound(None) == []


# --- outbound send ---------------------------------------------------------

def _send(monkeypatch, handler, text="收到"):
    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await SlackProvider().send(
                OutboundReply(channel="slack", recipient="C0SLACKCHAN",
                              text=text, reply_to=None), client)

    return asyncio.run(run())


@pytest.mark.parametrize("code,body,error,status,external_id", [
    (200, {"ok": True, "channel": "C0SLACKCHAN", "ts": "1769160123.000500"},
     None, "DELIVERED", "1769160123.000500"),
    # Slack answers auth failures with ok:false on a 200 -- read the body,
    # not just the status code.
    (200, {"ok": False, "error": "invalid_auth"}, "AUTH_FAILED", "FAILED", None),
    (200, {"ok": False, "error": "account_inactive"}, "AUTH_FAILED", "FAILED", None),
    (401, {"ok": False, "error": "not_authed"}, "AUTH_FAILED", "FAILED", None),
    (200, {"ok": False, "error": "rate_limited"}, "RATE_LIMITED", "FAILED", None),
    (429, {"ok": False, "error": "rate_limited"}, "RATE_LIMITED", "FAILED", None),
    (200, {"ok": False, "error": "channel_not_found"}, "UPSTREAM_REJECTED", "FAILED", None),
    (500, {}, "UPSTREAM_REJECTED", "FAILED", None),
    # A 2xx without ok:true plus a concrete ts is never fabricated as delivery.
    (200, {"ok": True}, "UPSTREAM_REJECTED", "FAILED", None),
    (204, None, "INVALID_UPSTREAM_RESPONSE", "FAILED", None),
])
def test_send_maps_upstream_outcomes_honestly(monkeypatch, code, body, error, status, external_id):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(code, json=body if body is not None else None)

    result = _send(monkeypatch, handler)
    assert seen["url"] == "https://slack.com/api/chat.postMessage"
    assert seen["auth"] == f"Bearer {ENV['SLACK_BOT_TOKEN']}"
    assert seen["json"] == {"channel": "C0SLACKCHAN", "text": "收到"}
    assert result["status"] == status
    assert result["error"] == error
    assert result["http_status"] == code
    assert result["external_id"] == external_id
    # The adapter never echoes a secret back through a result payload.
    assert all(value not in json.dumps(result) for value in ENV.values())


def test_send_reports_timeout_and_transport_errors_without_secrets(monkeypatch):
    def timeout_handler(request):
        raise httpx.ReadTimeout(str(request.url), request=request)

    result = _send(monkeypatch, timeout_handler)
    assert result["status"] == "FAILED" and result["error"] == "TIMEOUT"
    assert result["http_status"] is None

    def transport_handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    result = _send(monkeypatch, transport_handler)
    assert result["status"] == "FAILED" and result["error"] == "TRANSPORT_ERROR"
    assert ENV["SLACK_BOT_TOKEN"] not in json.dumps(result)


def test_send_refuses_overlong_text_without_calling_upstream(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "1"})

    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await SlackProvider().send(
                OutboundReply(channel="slack", recipient="C0SLACKCHAN",
                              text="x" * 40001), client)

    result = asyncio.run(run())
    assert result == {"channel": "slack", "status": "FAILED", "http_status": None,
                      "external_id": None, "error": "TEXT_TOO_LONG"}
    assert calls == []


def test_send_validates_recipient_and_channel(monkeypatch):
    configure(monkeypatch)
    provider = SlackProvider()
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="telegram",
                                                recipient="C0SLACKCHAN", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="slack",
                                                recipient="  ", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="slack",
                                                recipient="C0SLACKCHAN", text="")))


# --- HTTP surface ----------------------------------------------------------

def silence_slack_push(monkeypatch):
    """slack 在 NOTIFY_CHANNELS 里之后，入站任务排队即触发「已收到」ack 推送。

    These webhook tests hold real-shaped station credentials but must never
    reach slack.com; point the push at a local mock transport instead. No
    assertion below depends on the push outcome.
    """
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"ok": True, "ts": "test-push-1"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.slack.httpx.AsyncClient", mock_client)


def test_slack_webhook_is_signed_and_dispatches_like_telegram(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    silence_slack_push(monkeypatch)
    client = TestClient(app)
    assert client.put('/api/v1/registries/models/local', json={
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'example'},
        headers=ADMIN).status_code == 200
    route = "/api/v1/channels/slack/webhook"
    body = json.dumps(PAYLOAD).encode()
    # Unsigned / wrongly signed deliveries are rejected before parsing.
    assert client.post(route, json=PAYLOAD).status_code == 403
    assert client.post(route, content=body,
                       headers=signed_headers(body, "wrong")).status_code == 403
    accepted = client.post(route, content=body, headers=signed_headers(body))
    assert accepted.status_code == 200
    result = accepted.json()
    assert result["received"] == 1
    assert result["dispatch"] == "QUEUED"
    records = [r for r in app.state.store.channel_messages("slack")
               if r["direction"] == "INBOUND"]
    assert len(records) == 1
    assert (records[0]["sender"], records[0]["text"]) == ("U4SLACK0USER", "你好，帮我看下任务")
    mission = app.state.store.get(records[0]["mission_id"])
    assert mission["original_input"] == "你好，帮我看下任务"
    assert "来源渠道：slack" in mission["professional_prompt"]
    # Duplicate delivery (same ts) is deduped, not re-dispatched.
    duplicate = client.post(route, content=body, headers=signed_headers(body))
    assert duplicate.json()["received"] == 0
    assert duplicate.json()["results"][0]["duplicate"] is True


def test_slack_status_api_lists_slack_and_send_requires_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/channels").status_code == 401
    body = client.get("/api/v1/channels", headers=ADMIN).json()
    assert body["slack"]["state"] == "UNCONFIGURED"
    assert body["slack"]["required"] == list(SlackProvider.required_env)
    # Unconfigured webhook and send honestly refuse with missing names only.
    response = client.post("/api/v1/channels/slack/webhook", content=b"{}")
    assert response.status_code == 503
    assert response.json()["detail"]["missing"] == list(SlackProvider.required_env)
    send = client.post("/api/v1/channels/slack/send", headers=ADMIN,
                       json={"recipient": "C0SLACKCHAN", "text": "hi"})
    assert send.status_code == 503
    assert "slack-bot-token-value" not in send.text
