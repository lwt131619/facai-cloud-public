"""Discord channel adapter: bridge-HMAC signature, honest send, inbound parsing.

Mirrors tests/test_line_channel.py conventions for the sixth channel: the
adapter never fabricates a connection (UNCONFIGURED without credentials,
missing variable *names* only) and never reports a delivery the push API did
not confirm with a concrete message id. Inbound verification is the relay-
bridge HMAC convention (X-Facai-Signature), not native Ed25519 -- see the
adapter docstring for the trust model.
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
from facai.channels.discord import DiscordProvider

ENV = {"DISCORD_BOT_TOKEN": "discord-bot-token-value",
       "DISCORD_WEBHOOK_SECRET": "discord-webhook-secret-value"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin").decode()}

MESSAGE = {"id": "1192345678901234567",
           "content": "你好，帮我看下任务",
           "channel_id": "987654321098765432",
           "author": {"id": "112233445566778899", "username": "customer", "bot": False},
           "guild_id": "555000111222333444",
           "timestamp": "2026-10-06T15:00:00.000000+00:00"}
PAYLOAD = {"messages": [MESSAGE]}


def sign(body, secret=ENV["DISCORD_WEBHOOK_SECRET"]):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def configure(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


# --- signature -------------------------------------------------------------

def test_signature_accepts_only_matching_hex_hmac(monkeypatch):
    configure(monkeypatch)
    provider = DiscordProvider()
    body = json.dumps(PAYLOAD).encode()
    assert provider.verify_signature(body, {"X-Facai-Signature": sign(body)}) is True
    # Wrong secret, tampered body, missing header and unconfigured channel all
    # fail closed.
    assert provider.verify_signature(body, {"X-Facai-Signature": sign(body, "other")}) is False
    assert provider.verify_signature(b'{"tampered":true}', {"X-Facai-Signature": sign(body)}) is False
    assert provider.verify_signature(body, {}) is False
    assert DiscordProvider({}).verify_signature(body, {"X-Facai-Signature": sign(body)}) is False


# --- honest configuration --------------------------------------------------

def test_unconfigured_reports_missing_names_never_values(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_SECRET", raising=False)
    provider = DiscordProvider({})
    status = provider.status()
    assert status.implemented is True
    assert status.state == "UNCONFIGURED"
    assert status.missing == list(DiscordProvider.required_env)
    assert status.send_ready is False
    blob = json.dumps(status.to_dict(), ensure_ascii=False)
    for value in ENV.values():
        assert value not in blob
    with pytest.raises(ChannelConfigError) as excinfo:
        asyncio.run(provider.send(OutboundReply(channel="discord",
                                                recipient="987654321098765432", text="hi")))
    assert "DISCORD_BOT_TOKEN" in excinfo.value.missing


# --- inbound parsing -------------------------------------------------------

def test_parse_inbound_normalizes_bridge_message():
    provider = DiscordProvider({})
    messages = provider.parse_inbound(PAYLOAD)
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "discord"
    assert message.sender == "112233445566778899"
    assert message.recipient == "987654321098765432"
    assert message.kind == "text"
    assert message.text == "你好，帮我看下任务"
    assert message.external_id == "1192345678901234567"
    assert message.metadata["guild_id"] == "555000111222333444"
    assert message.received_at == MESSAGE["timestamp"]
    # raw carries the inner message object, mirroring telegram/whatsapp/line.
    assert message.raw == MESSAGE


def test_parse_inbound_skips_bots_and_entries_without_stable_identity():
    provider = DiscordProvider({})
    payload = copy.deepcopy(PAYLOAD)
    payload["messages"][0]["author"]["bot"] = True  # never self-reply loops
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    del payload["messages"][0]["id"]
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["messages"][0]["author"] = {"username": "no-id"}
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    payload["messages"][0]["content"] = ""
    assert provider.parse_inbound(payload) == []
    payload = copy.deepcopy(PAYLOAD)
    del payload["messages"][0]["channel_id"]
    assert provider.parse_inbound(payload) == []
    # Malformed containers are skipped, never raised: one bad object must not
    # 400 the whole delivery.
    assert provider.parse_inbound({"messages": "not-a-list"}) == []
    assert provider.parse_inbound("not-a-dict") == []
    assert provider.parse_inbound(None) == []


# --- outbound send ---------------------------------------------------------

def _send(monkeypatch, handler, text="收到"):
    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DiscordProvider().send(
                OutboundReply(channel="discord", recipient="987654321098765432",
                              text=text, reply_to=None), client)

    return asyncio.run(run())


@pytest.mark.parametrize("code,body,error,status,external_id", [
    (200, {"id": "1199887766554433221"}, None, "DELIVERED", "1199887766554433221"),
    (401, {"message": "401: Unauthorized"}, "AUTH_FAILED", "FAILED", None),
    (403, {"message": "Missing Permissions"}, "AUTH_FAILED", "FAILED", None),
    (429, {"message": "You are being rate limited."}, "RATE_LIMITED", "FAILED", None),
    (400, {"message": "Unknown Channel"}, "UPSTREAM_REJECTED", "FAILED", None),
    (500, {}, "UPSTREAM_REJECTED", "FAILED", None),
    # A 2xx without a concrete message id is never fabricated as delivery.
    (200, {}, "UPSTREAM_REJECTED", "FAILED", None),
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
    assert seen["url"] == "https://discord.com/api/v10/channels/987654321098765432/messages"
    assert seen["auth"] == f"Bot {ENV['DISCORD_BOT_TOKEN']}"
    assert seen["json"] == {"content": "收到"}
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
    assert ENV["DISCORD_BOT_TOKEN"] not in json.dumps(result)


def test_send_refuses_overlong_text_without_calling_upstream(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "1"})

    configure(monkeypatch)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DiscordProvider().send(
                OutboundReply(channel="discord", recipient="987654321098765432",
                              text="x" * 2001), client)

    result = asyncio.run(run())
    assert result == {"channel": "discord", "status": "FAILED", "http_status": None,
                      "external_id": None, "error": "TEXT_TOO_LONG"}
    assert calls == []


def test_send_validates_recipient_and_channel(monkeypatch):
    configure(monkeypatch)
    provider = DiscordProvider()
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="telegram",
                                                recipient="987654321", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="discord",
                                                recipient="  ", text="hi")))
    with pytest.raises(ValueError):
        asyncio.run(provider.send(OutboundReply(channel="discord",
                                                recipient="987654321", text="")))


# --- HTTP surface ----------------------------------------------------------

def silence_discord_push(monkeypatch):
    """discord 在 NOTIFY_CHANNELS 里之后，入站任务排队即触发「已收到」ack 推送。

    These webhook tests hold real-shaped station credentials but must never
    reach discord.com; point the push at a local mock transport instead. No
    assertion below depends on the push outcome.
    """
    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"id": "test-push-1"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.discord.httpx.AsyncClient", mock_client)


def test_discord_webhook_is_signed_and_dispatches_like_telegram(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    silence_discord_push(monkeypatch)
    client = TestClient(app)
    assert client.put('/api/v1/registries/models/local', json={
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'example'},
        headers=ADMIN).status_code == 200
    route = "/api/v1/channels/discord/webhook"
    body = json.dumps(PAYLOAD).encode()
    # Unsigned / wrongly signed deliveries are rejected before parsing.
    assert client.post(route, json=PAYLOAD).status_code == 403
    assert client.post(route, content=body,
                       headers={"X-Facai-Signature": sign(body, "wrong")}).status_code == 403
    accepted = client.post(route, content=body, headers={"X-Facai-Signature": sign(body)})
    assert accepted.status_code == 200
    result = accepted.json()
    assert result["received"] == 1
    assert result["dispatch"] == "QUEUED"
    records = [r for r in app.state.store.channel_messages("discord")
               if r["direction"] == "INBOUND"]
    assert len(records) == 1
    assert (records[0]["sender"], records[0]["text"]) == ("112233445566778899", "你好，帮我看下任务")
    mission = app.state.store.get(records[0]["mission_id"])
    assert mission["original_input"] == "你好，帮我看下任务"
    assert "来源渠道：discord" in mission["professional_prompt"]
    # Duplicate delivery (same message id) is deduped, not re-dispatched.
    duplicate = client.post(route, content=body, headers={"X-Facai-Signature": sign(body)})
    assert duplicate.json()["received"] == 0
    assert duplicate.json()["results"][0]["duplicate"] is True


def test_discord_status_api_lists_discord_and_send_requires_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/channels").status_code == 401
    body = client.get("/api/v1/channels", headers=ADMIN).json()
    assert body["discord"]["state"] == "UNCONFIGURED"
    assert body["discord"]["required"] == list(DiscordProvider.required_env)
    # Unconfigured webhook and send honestly refuse with missing names only.
    response = client.post("/api/v1/channels/discord/webhook", content=b"{}")
    assert response.status_code == 503
    assert response.json()["detail"]["missing"] == list(DiscordProvider.required_env)
    send = client.post("/api/v1/channels/discord/send", headers=ADMIN,
                       json={"recipient": "987654321098765432", "text": "hi"})
    assert send.status_code == 503
    assert "discord-bot-token-value" not in send.text
