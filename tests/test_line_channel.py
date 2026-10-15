"""LINE channel adapter: signature, honest send mapping, inbound parsing, 403s.

Mirrors tests/test_telegram.py + tests/test_channels.py conventions for the
fourth channel: the adapter never fabricates a connection (UNCONFIGURED without
credentials, missing variable *names* only) and never reports a delivery the
push API did not confirm with a concrete message id.
"""

import asyncio
import base64
import copy
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.channels import ChannelConfigError, OutboundReply
from facai.channels.line import LineProvider

ENV = {"LINE_CHANNEL_ACCESS_TOKEN": "line-access-token-value",
       "LINE_CHANNEL_SECRET": "line-channel-secret-value"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin").decode()}

EVENT = {"type": "message",
         "message": {"type": "text", "id": "14353710092000", "text": "你好，帮我看下任务"},
         "webhookEventId": "01FZ74AAA0SDMY41AAAAAAAAAA",
         "timestamp": 1625665242000,
         "source": {"type": "user", "userId": "U4af4980629"},
         "replyToken": "0f3778f0ad7ade497400000000000000",
         "mode": "active"}
PAYLOAD = {"destination": "Ubot0000000000000000000000000000", "events": [EVENT]}


def sign(body, secret=ENV["LINE_CHANNEL_SECRET"]):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def configure(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


# --- signature -------------------------------------------------------------

def test_signature_accepts_only_matching_base64_hmac(monkeypatch):
    configure(monkeypatch)
    provider = LineProvider()
    body = json.dumps(PAYLOAD).encode()
    assert provider.verify_signature(body, {"X-Line-Signature": sign(body)}) is True
    # Wrong secret, tampered body, missing header and unconfigured channel all
    # fail closed.
    assert provider.verify_signature(body, {"X-Line-Signature": sign(body, "other")}) is False
    assert provider.verify_signature(b'{"tampered":true}', {"X-Line-Signature": sign(body)}) is False
    assert provider.verify_signature(body, {}) is False
    assert LineProvider({}).verify_signature(body, {"X-Line-Signature": sign(body)}) is False


# --- honest configuration --------------------------------------------------

def test_unconfigured_reports_missing_names_never_values(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    provider = LineProvider({})
    status = provider.status()
    assert status.implemented is True
    assert status.state == "UNCONFIGURED"
    assert status.missing == list(LineProvider.required_env)
    assert status.send_ready is False
    blob = json.dumps(status.to_dict(), ensure_ascii=False)
    for value in ENV.values():
        assert value not in blob
    with pytest.raises(ChannelConfigError) as excinfo:
        asyncio.run(provider.send(OutboundReply(channel="line", recipient="U4af4980629", text="hi")))
    assert "LINE_CHANNEL_ACCESS_TOKEN" in excinfo.value.missing


# --- inbound parsing -------------------------------------------------------

def test_parse_inbound_normalizes_text_event():
    provider = LineProvider({})
    messages = provider.parse_inbound(PAYLOAD)
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "line"
    assert message.sender == "U4af4980629"
    assert message.recipient == "Ubot0000000000000000000000000000"
    assert message.kind == "text"
    assert message.text == "你好，帮我看下任务"
    assert message.external_id == "14353710092000"
    assert message.metadata["source_type"] == "user"
    assert message.metadata["webhook_event_id"] == "01FZ74AAA0SDMY41AAAAAAAAAA"
    # raw carries the inner message object, mirroring telegram/whatsapp.
    assert message.raw == EVENT["message"]


def test_parse_inbound_ignores_non_text_and_non_message_events():
    provider = LineProvider({})
    follow = {"destination": "Ubot", "events": [
        {"type": "follow", "replyToken": "t", "source": {"type": "user", "userId": "U1"},
         "timestamp": 1625665242000, "webhookEventId": "E1"},
        {"type": "message", "message": {"type": "sticker", "id": "9", "stickerId": "1",
                                        "packageId": "1"},
         "source": {"type": "user", "userId": "U1"}, "timestamp": 1625665242000},
        EVENT,
    ]}
    messages = provider.parse_inbound(follow)
    assert [m.external_id for m in messages] == ["14353710092000"]
    assert provider.parse_inbound({"destination": "Ubot", "events": "not-a-list"}) == []
    assert provider.parse_inbound("not-a-dict") == []
    assert provider.parse_inbound(None) == []


def test_parse_inbound_skips_events_without_stable_identity():
    provider = LineProvider({})
    # Deep copies only: EVENT/PAYLOAD are module-level shared fixtures, and a
    # shallow dict(EVENT) would mutate them for every later test.
    payload = copy.deepcopy(PAYLOAD)
    del payload["events"][0]["message"]["id"]
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    del payload["events"][0]["source"]["userId"]  # group events may lack userId
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["events"][0]["message"]["text"] = ""
    assert provider.parse_inbound(payload) == []


# --- outbound send ---------------------------------------------------------

def _send(monkeypatch, handler):
    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LineProvider().send(
                OutboundReply(channel="line", recipient="U4af4980629", text="收到",
                              reply_to=None), client)

    return asyncio.run(run())


@pytest.mark.parametrize("code,body,error,status,external_id", [
    (200, {"sentMessages": [{"id": "438905617975683156"}]}, None, "DELIVERED",
     "438905617975683156"),
    (401, {"message": "The request body has 2 errors", "details": []}, "AUTH_FAILED",
     "FAILED", None),
    (429, {"message": "Too Many Requests"}, "RATE_LIMITED", "FAILED", None),
    (400, {"message": "Invalid reply token"}, "UPSTREAM_REJECTED", "FAILED", None),
    (500, {}, "UPSTREAM_REJECTED", "FAILED", None),
    # A 2xx without a concrete sentMessages id is never fabricated as delivery.
    (200, {}, "UPSTREAM_REJECTED", "FAILED", None),
    (200, [], "INVALID_UPSTREAM_RESPONSE", "FAILED", None),
])
def test_send_maps_upstream_outcomes_honestly(monkeypatch, code, body, error, status, external_id):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(code, json=body)

    result = _send(monkeypatch, handler)
    assert seen["url"] == "https://api.line.me/v2/bot/message/push"
    assert seen["auth"] == f"Bearer {ENV['LINE_CHANNEL_ACCESS_TOKEN']}"
    assert seen["json"] == {"to": "U4af4980629", "messages": [{"type": "text", "text": "收到"}]}
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
    assert ENV["LINE_CHANNEL_ACCESS_TOKEN"] not in json.dumps(result)


def test_send_refuses_overlong_text_without_calling_upstream(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"sentMessages": [{"id": "1"}]})

    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await LineProvider().send(
                OutboundReply(channel="line", recipient="U1", text="x" * 5001), client)

    result = asyncio.run(run())
    assert result == {"channel": "line", "status": "FAILED", "http_status": None,
                      "external_id": None, "error": "TEXT_TOO_LONG"}
    assert calls == []


def test_send_validates_recipient_and_channel(monkeypatch):
    configure(monkeypatch)
    provider = LineProvider()
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="telegram", recipient="U1", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="line", recipient="  ", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="line", recipient="U1", text="")))


# --- HTTP surface ----------------------------------------------------------

def _admin_basic(password="test-admin"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()}


def silence_line_push(monkeypatch):
    """line 在 NOTIFY_CHANNELS 里之后，入站任务排队即触发「已收到」ack 推送。

    These webhook tests hold real-shaped station credentials but must never
    reach api.line.me; point the push at a local mock transport instead. No
    assertion below depends on the push outcome.
    """
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"sentMessages": [{"id": "test-push-1"}]})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.line.httpx.AsyncClient", mock_client)


def test_line_webhook_is_signed_dispatches_like_telegram(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    silence_line_push(monkeypatch)
    client = TestClient(app)
    assert client.put('/api/v1/registries/models/local', json={
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'example'},
        headers=ADMIN).status_code == 200
    route = "/api/v1/channels/line/webhook"
    body = json.dumps(PAYLOAD).encode()
    # Unsigned / wrongly signed deliveries are rejected before parsing.
    assert client.post(route, json=PAYLOAD).status_code == 403
    assert client.post(route, content=body,
                       headers={"X-Line-Signature": sign(body, "wrong")}).status_code == 403
    accepted = client.post(route, content=body, headers={"X-Line-Signature": sign(body)})
    assert accepted.status_code == 200
    result = accepted.json()
    assert result["received"] == 1
    assert result["dispatch"] == "QUEUED"
    records = [r for r in app.state.store.channel_messages("line")
               if r["direction"] == "INBOUND"]
    assert len(records) == 1
    assert (records[0]["sender"], records[0]["text"]) == ("U4af4980629", "你好，帮我看下任务")
    mission = app.state.store.get(records[0]["mission_id"])
    assert mission["original_input"] == "你好，帮我看下任务"
    assert "来源渠道：line" in mission["professional_prompt"]
    # Duplicate delivery (same message id) is deduped, not re-dispatched.
    duplicate = client.post(route, content=body, headers={"X-Line-Signature": sign(body)})
    assert duplicate.json()["received"] == 0
    assert duplicate.json()["results"][0]["duplicate"] is True


def test_line_status_api_lists_line_and_send_requires_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/channels").status_code == 401
    body = client.get("/api/v1/channels", headers=_admin_basic()).json()
    assert body["line"]["state"] == "UNCONFIGURED"
    assert body["line"]["required"] == list(LineProvider.required_env)
    # Unconfigured webhook and send honestly refuse with missing names only.
    response = client.post("/api/v1/channels/line/webhook", content=b"{}")
    assert response.status_code == 503
    assert response.json()["detail"]["missing"] == list(LineProvider.required_env)
    send = client.post("/api/v1/channels/line/send", headers=_admin_basic(),
                       json={"recipient": "U4af4980629", "text": "hi"})
    assert send.status_code == 503
    assert "line-channel-secret-value" not in send.text


def test_tenant_cannot_access_line_status_or_send(tmp_path, monkeypatch):
    """A self-registered tenant must stay out of the station's LINE sender."""
    configure(monkeypatch)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_USERS_JSON",
                       '{"admin":"admin-pass","shop":"shop-pass"}')
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    silence_line_push(monkeypatch)
    client = TestClient(create_app(tmp_path))
    login = client.post("/api/v1/auth/login", json={"username": "shop", "password": "shop-pass"})
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.get("/api/v1/channels/line").status_code == 403
    assert client.get("/api/v1/channels").status_code == 403
    send = client.post("/api/v1/channels/line/send", headers=csrf,
                       json={"recipient": "U4af4980629", "text": "hi"})
    assert send.status_code == 403
    assert client.post("/api/v1/channels/line/send", headers=csrf,
                       json={"recipient": "U4af4980629", "text": "hi"}).status_code == 403
    # The webhook itself stays public but signature-gated (provider checks it).
    body = json.dumps(PAYLOAD).encode()
    assert client.post("/api/v1/channels/line/webhook", content=body,
                       headers={"X-Line-Signature": sign(body)}).status_code == 200
