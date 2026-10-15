"""Message channel base layer: honesty, verification, parsing and send.

These tests prove that the channel layer never fabricates a connection: without
credentials it reports UNCONFIGURED, refuses to send and returns the missing
variable *names*; with credentials the WhatsApp Cloud API shape is verified
(webhook challenge + HMAC signature), inbound is normalized and outbound is
built against the Graph API. WeChat remains a reserved interface.
"""

import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.channels import ChannelConfigError, ChannelRegistry, OutboundReply
from facai.channels.telegram import TelegramProvider
from facai.channels.wechat import WeChatProvider
from facai.channels.whatsapp import WhatsAppCloudProvider

WHATSAPP_ENV = {
    "WHATSAPP_VERIFY_TOKEN": "verify-token-value",
    "WHATSAPP_APP_SECRET": "app-secret-value",
    "WHATSAPP_ACCESS_TOKEN": "access-token-value",
    "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
}


def clear_whatsapp(monkeypatch):
    for key in list(WHATSAPP_ENV) + ["WHATSAPP_GRAPH_VERSION"]:
        monkeypatch.delenv(key, raising=False)


def configure_whatsapp(monkeypatch):
    for key, value in WHATSAPP_ENV.items():
        monkeypatch.setenv(key, value)


def wa_payload(text="你好", sender="8613800000000", message_id="wamid.TEST", timestamp="1759250000"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "biz", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15550001111", "phone_number_id": "1234567890"},
            "contacts": [{"profile": {"name": "Owner"}, "wa_id": sender}],
            "messages": [{"from": sender, "id": message_id, "timestamp": timestamp,
                          "type": "text", "text": {"body": text}}],
        }}]}],
    }


def sign(body, secret=WHATSAPP_ENV["WHATSAPP_APP_SECRET"]):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_unconfigured_channel_reports_missing_names_not_values(monkeypatch):
    clear_whatsapp(monkeypatch)
    provider = WhatsAppCloudProvider()
    assert provider.configured() is False
    status = provider.status()
    assert status.state == "UNCONFIGURED"
    assert status.missing == list(provider.required_env)
    blob = json.dumps(status.to_dict(), ensure_ascii=False)
    for value in WHATSAPP_ENV.values():
        assert value not in blob


def test_wechat_adapter_is_wired_but_waits_for_qr_authorization(monkeypatch):
    clear_whatsapp(monkeypatch)
    provider = WeChatProvider()
    status = provider.status()
    assert status.implemented is True
    assert status.configured is False
    assert status.state == "UNCONFIGURED"
    assert status.send_ready is False
    with pytest.raises(ValueError):
        asyncio.run(WeChatProvider().send(OutboundReply(channel="wechat", recipient="1", text="hi")))


def test_whatsapp_webhook_challenge_requires_matching_token(monkeypatch):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    provider = WhatsAppCloudProvider()
    params = {"hub.mode": "subscribe", "hub.verify_token": WHATSAPP_ENV["WHATSAPP_VERIFY_TOKEN"],
              "hub.challenge": "challenge-123"}
    assert provider.verify_challenge(params) == "challenge-123"
    params["hub.verify_token"] = "wrong"
    assert provider.verify_challenge(params) is None
    assert provider.verify_challenge({"hub.mode": "subscribe", "hub.challenge": "x"}) is None


def test_whatsapp_signature_verification(monkeypatch):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    provider = WhatsAppCloudProvider()
    body = b'{"object":"whatsapp_business_account"}'
    assert provider.verify_signature(body, {"x-hub-signature-256": sign(body)}) is True
    assert provider.verify_signature(body, {"x-hub-signature-256": sign(body, "other")}) is False
    assert provider.verify_signature(body, {}) is False
    assert provider.verify_signature(body, {"x-hub-signature-256": "sha256="}) is False


def test_whatsapp_parse_normalizes_inbound():
    provider = WhatsAppCloudProvider()
    messages = provider.parse_inbound(wa_payload(text="你好，帮我看下任务"))
    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "whatsapp"
    assert message.sender == "8613800000000"
    assert message.recipient == "15550001111"
    assert message.kind == "text"
    assert message.text == "你好，帮我看下任务"
    assert message.external_id == "wamid.TEST"
    assert message.metadata["phone_number_id"] == "1234567890"
    assert message.metadata["profile_name"] == "Owner"
    assert provider.parse_inbound({"object": "page"}) == []


@pytest.mark.parametrize("missing_field", ["id", "from"])
def test_whatsapp_parse_requires_stable_message_identity(missing_field):
    payload = wa_payload()
    del payload["entry"][0]["changes"][0]["value"]["messages"][0][missing_field]
    assert WhatsAppCloudProvider().parse_inbound(payload) == []


def test_whatsapp_parse_ignores_non_message_changes_and_handles_bad_text_shape():
    provider = WhatsAppCloudProvider()
    payload = wa_payload()
    change = payload["entry"][0]["changes"][0]
    change["field"] = "message_template_status_update"
    assert provider.parse_inbound(payload) == []

    change["field"] = "messages"
    change["value"]["messages"][0]["text"] = "malformed but authenticated provider data"
    messages = provider.parse_inbound(payload)
    assert len(messages) == 1
    assert messages[0].external_id == "wamid.TEST"
    assert messages[0].text == ""
    assert messages[0].raw["text"] == "malformed but authenticated provider data"


@pytest.mark.parametrize("payload", [
    {"object": "whatsapp_business_account", "entry": "not-a-list"},
    {"object": "whatsapp_business_account", "entry": 42},
    {"object": "whatsapp_business_account", "entry": None},
    {"object": "whatsapp_business_account", "entry": [{"changes": "not-a-list"}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": None}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [None]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": ["string-change"]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": "not-a-dict"}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": None}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": "not-a-dict", "messages": []}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": None, "messages": []}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "contacts": "not-a-list", "messages": []}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "contacts": [None, "str", 42], "messages": []}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "contacts": [{"wa_id": "1", "profile": "not-a-dict"}], "messages": []}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "messages": "not-a-list"}}]}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "messages": [None, "str", 42]}}]}]},
    {"object": "whatsapp_business_account", "entry": [None, "str", 42]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {}, "messages": []}}, "not-a-dict"]}]},
    "not-a-dict",
    42,
    None,
])
def test_whatsapp_parse_inbound_skips_malformed_payloads(payload):
    provider = WhatsAppCloudProvider()
    result = provider.parse_inbound(payload)
    assert isinstance(result, list)
    assert all(isinstance(m, InboundMessage) for m in result)


def test_whatsapp_parse_inbound_mixed_valid_and_malformed_entries():
    provider = WhatsAppCloudProvider()
    valid_entry = {"id": "biz", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "1234567890"},
        "contacts": [{"profile": {"name": "Owner"}, "wa_id": "8613800000000"}],
        "messages": [{"from": "8613800000000", "id": "wamid.VALID", "timestamp": "1759250000",
                      "type": "text", "text": {"body": "有效消息"}}],
    }}]}
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            None,
            "bad",
            {"changes": "not-a-list"},
            {"changes": [None]},
            {"changes": [{"value": "not-a-dict"}]},
            valid_entry,
            {"changes": [{"value": {"metadata": "bad", "messages": []}}]},
        ],
    }
    messages = provider.parse_inbound(payload)
    assert len(messages) == 1
    assert messages[0].text == "有效消息"
    assert messages[0].external_id == "wamid.VALID"


def test_whatsapp_send_refuses_without_credentials(monkeypatch):
    clear_whatsapp(monkeypatch)
    provider = WhatsAppCloudProvider()
    with pytest.raises(ChannelConfigError) as excinfo:
        asyncio.run(provider.send(OutboundReply(channel="whatsapp", recipient="1", text="hi")))
    assert "WHATSAPP_ACCESS_TOKEN" in excinfo.value.missing


def test_whatsapp_send_builds_cloud_api_request(monkeypatch):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WhatsAppCloudProvider().send(
                OutboundReply(channel="whatsapp", recipient="8613800000000", text="收到"),
                client=client)

    result = asyncio.run(run())
    assert result["status"] == "SENT"
    assert result["external_id"] == "wamid.SENT"
    assert seen["url"] == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert seen["auth"] == f"Bearer {WHATSAPP_ENV['WHATSAPP_ACCESS_TOKEN']}"
    assert seen["json"]["to"] == "8613800000000"
    assert seen["json"]["text"]["body"] == "收到"
    # No reply target means no Cloud API threading context is invented.
    assert "context" not in seen["json"]


def _send(monkeypatch, handler=None, transport=None, reply_to=None):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)

    async def run():
        kwargs = {"transport": transport} if transport is not None else {
            "transport": httpx.MockTransport(handler)}
        async with httpx.AsyncClient(**kwargs) as client:
            return await WhatsAppCloudProvider().send(
                OutboundReply(channel="whatsapp", recipient="8613800000000", text="收到",
                              reply_to=reply_to),
                client=client)

    return asyncio.run(run())


def test_whatsapp_send_threads_a_reply_to_the_original_message(monkeypatch):
    seen = {}

    def handler(request):
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})

    result = _send(monkeypatch, handler=handler, reply_to="wamid.INBOUND.1")
    assert result["status"] == "SENT"
    assert seen["json"]["context"] == {"message_id": "wamid.INBOUND.1"}


@pytest.mark.parametrize("reply_to", ["", "   "])
def test_whatsapp_send_rejects_a_blank_reply_to(monkeypatch, reply_to):
    with pytest.raises(ValueError):
        _send(monkeypatch, handler=lambda request: httpx.Response(200, json={}),
              reply_to=reply_to)


def test_whatsapp_send_never_fabricates_success_on_malformed_2xx_body(monkeypatch):
    # A 2xx response is not, by itself, proof the message was accepted: only a
    # concrete upstream message id is. Anything short of that must stay FAILED.
    result = _send(monkeypatch, handler=lambda request: httpx.Response(200, json={}))
    assert result["status"] == "FAILED"
    assert result["http_status"] == 200
    assert result["external_id"] is None
    assert result["error"] == "UPSTREAM_REJECTED"


def test_whatsapp_send_never_fabricates_success_on_non_json_2xx_body(monkeypatch):
    result = _send(monkeypatch, handler=lambda request: httpx.Response(200, text="not json"))
    assert result["status"] == "FAILED"
    assert result["error"] == "INVALID_UPSTREAM_RESPONSE"


@pytest.mark.parametrize("body", [{"messages": "unexpected"}, {"messages": {"id": "x"}},
                                   {"messages": 42}, {"messages": [None]}])
def test_whatsapp_send_rejects_malformed_message_collection(monkeypatch, body):
    result = _send(monkeypatch, handler=lambda request: httpx.Response(200, json=body))
    assert result["status"] == "FAILED"
    assert result["external_id"] is None
    assert result["error"] == "UPSTREAM_REJECTED"


def test_whatsapp_send_categorizes_auth_failure(monkeypatch):
    body = {"error": {"message": "Error validating access token", "type": "OAuthException",
                       "code": 190}}
    result = _send(monkeypatch, handler=lambda request: httpx.Response(401, json=body))
    assert result["status"] == "FAILED"
    assert result["http_status"] == 401
    assert result["error"] == "AUTH_FAILED"
    assert "190" in result["detail"]
    for value in WHATSAPP_ENV.values():
        assert value not in json.dumps(result, ensure_ascii=False)


def test_whatsapp_send_categorizes_auth_failure_by_upstream_code_even_off_401(monkeypatch):
    # Meta sometimes returns the OAuth error code 190 on a 400 response; the
    # category must still be AUTH_FAILED so an operator knows to rotate the token.
    body = {"error": {"message": "Error validating access token", "code": 190}}
    result = _send(monkeypatch, handler=lambda request: httpx.Response(400, json=body))
    assert result["error"] == "AUTH_FAILED"


def test_whatsapp_send_categorizes_rate_limiting(monkeypatch):
    body = {"error": {"message": "Too many requests", "code": 80007}}
    result = _send(monkeypatch, handler=lambda request: httpx.Response(429, json=body))
    assert result["status"] == "FAILED"
    assert result["http_status"] == 429
    assert result["error"] == "RATE_LIMITED"


def test_whatsapp_send_categorizes_permission_denied(monkeypatch):
    body = {"error": {"message": "Permission denied", "code": 200}}
    result = _send(monkeypatch, handler=lambda request: httpx.Response(403, json=body))
    assert result["error"] == "PERMISSION_DENIED"


def test_whatsapp_send_categorizes_timeout_and_transport_errors(monkeypatch):
    def timeout_handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    timeout_result = _send(monkeypatch, handler=timeout_handler)
    assert timeout_result["status"] == "FAILED"
    assert timeout_result["http_status"] is None
    assert timeout_result["error"] == "TIMEOUT"

    def transport_handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    transport_result = _send(monkeypatch, handler=transport_handler)
    assert transport_result["status"] == "FAILED"
    assert transport_result["http_status"] is None
    assert transport_result["error"] == "TRANSPORT_ERROR"


def test_channel_status_api_requires_auth_and_lists_channels(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/channels").status_code == 401
    headers = {"Authorization": "Basic " + base64.b64encode(b"admin:channel-pass").decode()}
    body = client.get("/api/v1/channels", headers=headers).json()
    assert set(body) == {"whatsapp", "wechat", "telegram", "relay", "line",
                         "discord", "slack"}
    assert body["whatsapp"]["state"] == "UNCONFIGURED"
    assert body["wechat"]["state"] == "UNCONFIGURED"


def test_whatsapp_webhook_is_public_but_signed_and_audited(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)

    challenge = client.get("/api/v1/channels/whatsapp/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": WHATSAPP_ENV["WHATSAPP_VERIFY_TOKEN"],
        "hub.challenge": "abc123"})
    assert challenge.status_code == 200
    assert challenge.text == "abc123"

    body = json.dumps(wa_payload(text="审核一下")).encode()
    assert client.post("/api/v1/channels/whatsapp/webhook", content=body,
                       headers={"X-Hub-Signature-256": "sha256=deadbeef"}).status_code == 403
    accepted = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                           headers={"X-Hub-Signature-256": sign(body)})
    assert accepted.status_code == 200
    result = accepted.json()
    assert result["received"] == 1
    assert result["dispatch"] == "QUEUED"

    recorded = [r for r in app.state.store.channel_messages("whatsapp")
                if r["direction"] == "INBOUND"]
    assert len(recorded) == 1  # the ack is audited separately as OUTBOUND
    assert recorded[0]["text"] == "审核一下"
    assert recorded[0]["sender"] == "8613800000000"
    mission_id = app.state.store.channel_message(recorded[0]["id"])["mission_id"]
    assert mission_id
    mission = app.state.store.get(mission_id)
    assert mission["original_input"] == "审核一下"
    assert "来源渠道：whatsapp" in mission["professional_prompt"]


def test_webhook_returns_unconfigured_without_leaking(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/v1/channels/whatsapp/webhook")
    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["missing"] == list(WhatsAppCloudProvider.required_env)
    assert "app-secret-value" not in response.text


def test_channel_registry_builds_all_providers():
    registry = ChannelRegistry(env={})
    assert tuple(registry.channels) == ("whatsapp", "relay", "wechat", "telegram",
                                        "line", "discord", "slack")
    assert registry.status("whatsapp").state == "UNCONFIGURED"
    assert registry.status("line").state == "UNCONFIGURED"
    assert registry.status("discord").state == "UNCONFIGURED"
    assert registry.status("slack").state == "UNCONFIGURED"


def _basic(password="channel-pass"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()}


def test_channel_status_api_never_returns_secret_values(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    body = client.get("/api/v1/channels", headers=_basic())
    assert body.status_code == 200
    assert body.json()["whatsapp"]["state"] == "CONFIGURED"
    for value in WHATSAPP_ENV.values():
        assert value not in body.text

    single = client.get("/api/v1/channels/whatsapp", headers=_basic())
    assert single.status_code == 200
    assert single.json()["send_ready"] is True
    for value in WHATSAPP_ENV.values():
        assert value not in single.text


def test_channel_send_api_refuses_unconfigured_without_leaking(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    response = client.post("/api/v1/channels/whatsapp/send",
                           headers=_basic(),
                           json={"recipient": "8613800000000", "text": "你好"})
    assert response.status_code == 503
    assert response.json()["detail"]["missing"] == list(WhatsAppCloudProvider.required_env)
    for value in WHATSAPP_ENV.values():
        assert value not in response.text
    assert app.state.store.channel_messages("whatsapp") == []


def test_channel_send_api_records_outbound_through_provider(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    seen = {}

    async def fake_send(reply, client=None):
        seen["recipient"] = reply.recipient
        seen["text"] = reply.text
        seen["reply_to"] = reply.reply_to
        return {"channel": "whatsapp", "status": "SENT", "http_status": 200,
                "external_id": "wamid.OUT", "error": None}

    app.state.channels.get("whatsapp").send = fake_send
    client = TestClient(app)
    response = client.post("/api/v1/channels/whatsapp/send", headers=_basic(),
                           json={"recipient": "8613800000000", "text": "收到请回复",
                                 "reply_to": "wamid.INBOUND.1"})
    assert response.status_code == 200
    assert response.json()["status"] == "SENT"
    assert seen == {"recipient": "8613800000000", "text": "收到请回复",
                    "reply_to": "wamid.INBOUND.1"}

    recorded = app.state.store.channel_messages("whatsapp")
    assert len(recorded) == 1
    assert recorded[0]["direction"] == "OUTBOUND"
    assert recorded[0]["status"] == "SENT"
    assert recorded[0]["external_id"] == "wamid.OUT"


def test_wechat_endpoints_report_waiting_for_authorization(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/channels/wechat/webhook").status_code == 503
    assert client.post("/api/v1/channels/wechat/webhook", content=b"{}").status_code == 503
    send = client.post("/api/v1/channels/wechat/send", headers=_basic(),
                       json={"recipient": "1", "text": "hi"})
    assert send.status_code == 503


def test_whatsapp_webhook_full_dispatch_flow(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    body = json.dumps(wa_payload(text="帮我写个脚本")).encode()
    response = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                           headers={"X-Hub-Signature-256": sign(body)})
    assert response.status_code == 200
    result = response.json()
    assert result["received"] == 1
    assert result["dispatch"] == "QUEUED"
    assert result["conversion_failed"] == 0
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "QUEUED"
    records = [r for r in app.state.store.channel_messages("whatsapp") if r["direction"] == "INBOUND"]
    assert len(records) == 1
    assert records[0]["text"] == "帮我写个脚本"
    assert records[0]["sender"] == "8613800000000"
    message = app.state.store.channel_message(records[0]["id"])
    mission_id = message["mission_id"]
    assert mission_id
    mission = app.state.store.get(mission_id)
    assert mission["original_input"] == "帮我写个脚本"
    assert "来源渠道：whatsapp" in mission["professional_prompt"]
    assert len(app.state.store.jobs(mission_id)) == 5  # full step DAG enqueued
    channel_events = app.state.store.channel_events(
        message_id=records[0]["id"], channel="whatsapp")
    converted = [e for e in channel_events if e["type"] == "PROMPT_CONVERTED"]
    assert len(converted) == 1
    assert converted[0]["payload"]["original_input"] == "帮我写个脚本"
    assert "来源渠道：whatsapp" in converted[0]["payload"]["professional_prompt"]


def test_whatsapp_webhook_duplicate_reuses_mission_and_job(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    body = json.dumps(wa_payload(text="重复消息")).encode()
    first = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                        headers={"X-Hub-Signature-256": sign(body)})
    assert first.status_code == 200
    assert first.json()["received"] == 1
    assert first.json()["dispatch"] == "QUEUED"
    second = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                         headers={"X-Hub-Signature-256": sign(body)})
    assert second.status_code == 200
    assert second.json()["received"] == 0
    assert second.json()["dispatch"] == "QUEUED"
    assert second.json()["results"][0]["duplicate"] is True
    assert second.json()["results"][0]["status"] == "QUEUED"
    records = [r for r in app.state.store.channel_messages("whatsapp")
               if r["direction"] == "INBOUND"]
    assert len(records) == 1
    mission_id = app.state.store.channel_message(records[0]["id"])["mission_id"]
    assert mission_id
    assert len(app.state.store.jobs(mission_id)) == 5  # full step DAG enqueued


def test_whatsapp_status_only_webhook_does_not_create_work(monkeypatch, tmp_path):
    """Meta delivery/read receipts are acknowledged without becoming missions."""
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    app = create_app(tmp_path)
    client = TestClient(app)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "biz", "changes": [{
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550001111",
                             "phone_number_id": "1234567890"},
                "statuses": [{"id": "wamid.STATUS", "status": "delivered",
                              "recipient_id": "8613800000000", "timestamp": "1759250000"}],
            },
        }]}],
    }
    body = json.dumps(payload).encode()

    response = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                           headers={"X-Hub-Signature-256": sign(body)})

    assert response.status_code == 200
    assert response.json() == {
        "received": 0, "dispatch": "NO_MESSAGES", "conversion_failed": 0, "results": []}
    assert app.state.store.channel_messages("whatsapp") == []
    assert app.state.store.list() == []


def test_whatsapp_webhook_deferred_then_retry_after_binding(monkeypatch, tmp_path):
    clear_whatsapp(monkeypatch)
    configure_whatsapp(monkeypatch)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "channel-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    app.state.registries.catalogs["models"].clear()
    client = TestClient(app)
    body = json.dumps(wa_payload(text="待分发消息")).encode()
    deferred = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                           headers={"X-Hub-Signature-256": sign(body)})
    assert deferred.status_code == 503
    detail = deferred.json()["detail"]
    assert detail["dispatch"] == "DEFERRED"
    records = app.state.store.channel_messages("whatsapp")
    assert len(records) == 1
    assert records[0]["text"] == "待分发消息"
    mission_id = app.state.store.channel_message(records[0]["id"])["mission_id"]
    assert mission_id
    assert app.state.store.jobs(mission_id) == []
    app.state.registries.catalogs["models"]["deepseek-flash"] = {
        "name": "DeepSeek Flash", "provider": "DeepSeek",
        "endpoint": "https://api.deepseek.com", "model": "deepseek-flash",
    }
    retried = client.post("/api/v1/channels/whatsapp/webhook", content=body,
                          headers={"X-Hub-Signature-256": sign(body)})
    assert retried.status_code == 200
    assert retried.json()["dispatch"] == "QUEUED"
    assert len(app.state.store.jobs(mission_id)) == 5  # full step DAG enqueued
