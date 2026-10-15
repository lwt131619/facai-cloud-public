import asyncio
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.channels import OutboundReply
from facai.channels.telegram import TelegramProvider
from facai.store import Store

ENV = {"TELEGRAM_BOT_TOKEN": "123456:test_token", "TELEGRAM_WEBHOOK_SECRET": "test-secret"}
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin").decode()}
UPDATE = {"update_id": 45, "message": {"message_id": 9, "date": 1700000000,
          "chat": {"id": -987, "type": "group"}, "from": {"id": 123, "is_bot": False},
          "text": "你好"}}


def test_webhook_auth_validation_dedup_and_restart(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.put('/api/v1/registries/models/local', json={
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'example'},
        headers=ADMIN).status_code == 200
    route = "/api/v1/channels/telegram/webhook"
    assert client.get("/api/v1/channels").status_code == 401
    assert client.get(route).status_code == 405
    assert client.post(route, json=UPDATE).status_code == 403
    accepted = client.post(route, json=UPDATE, headers=HEADERS)
    assert accepted.status_code == 200
    assert accepted.json()["received"] == 1
    assert accepted.json()["dispatch"] == "QUEUED"
    duplicate = client.post(route, json=UPDATE, headers=HEADERS)
    assert duplicate.json()["received"] == 0
    assert duplicate.json()["results"][0]["duplicate"] is True
    assert duplicate.json()["results"][0]["status"] == "QUEUED"
    assert client.post(route, json=[], headers=HEADERS).status_code == 400
    no_message = client.post(route, json={"update_id": 46}, headers=HEADERS).json()
    assert no_message["received"] == 0 and no_message["dispatch"] == "NO_MESSAGES"
    assert client.post(route, content=b"x" * (1024 * 1024 + 1), headers=HEADERS).status_code == 413
    records = [row for row in app.state.store.channel_messages("telegram")
               if row["direction"] == "INBOUND"]
    assert len(records) == 1
    assert (records[0]["sender"], records[0]["recipient"], records[0]["text"]) == ("123", "-987", "你好")
    mission = app.state.store.channel_message(records[0]["id"])["mission_id"]
    # The dispatch ack is audited as an OUTBOUND line against the same mission.
    acks = [row for row in app.state.store.channel_messages("telegram")
            if row["direction"] == "OUTBOUND" and row["mission_id"] == mission]
    assert len(acks) == 1 and "已收到需求" in acks[0]["text"]
    assert mission
    again = TestClient(create_app(tmp_path))
    restarted_delivery = again.post(route, json=UPDATE, headers=HEADERS).json()
    assert restarted_delivery["received"] == 0
    assert restarted_delivery["dispatch"] == "QUEUED"
    assert restarted_delivery["results"][0]["mission_id"] == mission
    assert len(again.app.state.store.jobs(mission)) == 5
    mission_record = app.state.store.get(mission)
    assert mission_record["original_input"] == "你好"
    assert "来源渠道：telegram" in mission_record["professional_prompt"]
    assert len(app.state.store.jobs(mission)) == 5


def test_webhook_defers_honestly_and_duplicate_retries_after_binding(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    app = create_app(tmp_path)
    app.state.registries.catalogs["models"].clear()
    client = TestClient(app)
    route = "/api/v1/channels/telegram/webhook"
    deferred = client.post(route, json=UPDATE, headers=HEADERS)
    assert deferred.status_code == 503
    message = client.app.state.store.channel_messages("telegram")[0]
    assert message["text"] == "你好" and message["mission_id"] is not None
    assert client.app.state.store.jobs(message["mission_id"]) == []
    assert client.put('/api/v1/registries/models/local', json={
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1', 'model': 'example'},
        headers=ADMIN).status_code == 200
    retried = client.post(route, json=UPDATE, headers=HEADERS)
    assert retried.status_code == 200
    assert retried.json()["received"] == 0
    assert retried.json()["dispatch"] == "QUEUED"
    assert len(client.app.state.store.jobs(message["mission_id"])) == 5


def test_unconfigured_and_non_ascii_secret_fail_closed():
    provider = TelegramProvider({})
    assert provider.status().state == "UNCONFIGURED"
    assert not provider.verify_signature(b"{}", HEADERS)
    provider = TelegramProvider(ENV)
    assert not provider.verify_signature(b"{}", {"X-Telegram-Bot-Api-Secret-Token": "错误"})
    assert provider.verify_signature(b"{}", HEADERS)


def test_duplicate_insert_is_atomic_and_channel_scoped(tmp_path):
    store = Store(tmp_path)
    def insert(_):
        return store.record_channel_message("telegram", "INBOUND", "RECEIVED", external_id="update:1")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(result is not None for result in pool.map(insert, range(8))) == 1
    assert store.record_channel_message("whatsapp", "INBOUND", "RECEIVED", external_id="update:1")


@pytest.mark.parametrize("code,body,error", [
    (200, {"ok": True, "result": {"message_id": 72}}, None),
    (200, {"ok": True, "result": {}}, "UPSTREAM_REJECTED"),
    (401, {"ok": False, "error_code": 401}, "AUTH_FAILED"),
    (403, {"ok": False, "error_code": 403}, "RECIPIENT_FORBIDDEN"),
    (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 20}}, "RATE_LIMITED"),
    (302, {}, "UPSTREAM_REJECTED"),
    (200, [], "INVALID_UPSTREAM_RESPONSE"),
])
def test_send_results_and_secret_redaction(code, body, error, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    def handler(request):
        data = json.loads(request.content)
        assert data["chat_id"] == "-987"
        assert data["reply_parameters"] == {"message_id": 9}
        assert "parse_mode" not in data
        return httpx.Response(code, json=body)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text="hello", reply_to="9"), client)
    result = asyncio.run(run())
    assert result["error"] == error
    assert result["status"] == ("SENT" if error is None else "FAILED")
    assert ENV["TELEGRAM_BOT_TOKEN"] not in caplog.text + json.dumps(result)
    if code == 429:
        assert result["retry_after"] == 20


def test_timeout_does_not_expose_token():
    def handler(request):
        raise httpx.ReadTimeout(str(request.url), request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(OutboundReply("telegram", "1", "hi"), client)
    result = asyncio.run(run())
    assert result["error"] == "TIMEOUT"
    assert ENV["TELEGRAM_BOT_TOKEN"] not in json.dumps(result)


def test_long_message_is_chunked_on_paragraph_boundaries_in_order():
    paragraphs = ["a" * 199 + "."] * 45  # 45 paragraphs of 200 chars.
    text = "\n\n".join(paragraphs)
    assert len(text) == 9088 and len(text) > 4096
    payloads = []
    def handler(request):
        data = json.loads(request.content)
        payloads.append(data)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(payloads)}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text=text, reply_to="9"), client)
    result = asyncio.run(run())
    assert result["status"] == "SENT"
    assert result["external_id"] == str(len(payloads))  # Last chunk's message_id.
    assert result["http_status"] == 200
    assert len(payloads) > 1
    assert all(len(payload["text"]) <= 4096 for payload in payloads)
    assert "\n\n".join(payload["text"] for payload in payloads) == text
    assert payloads[0]["reply_parameters"] == {"message_id": 9}
    assert all("reply_parameters" not in payload for payload in payloads[1:])


def test_overlong_text_fails_with_text_too_long_and_zero_sends():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text="x" * 50000), client)
    result = asyncio.run(run())
    assert result == {"channel": "telegram", "status": "FAILED", "http_status": None,
                      "external_id": None, "error": "TEXT_TOO_LONG"}
    assert calls == []


def test_rate_limit_with_short_retry_after_sleeps_once_then_succeeds(monkeypatch):
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr("facai.channels.telegram.asyncio.sleep", fake_sleep)
    attempts = []
    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(429, json={"ok": False, "error_code": 429,
                                             "parameters": {"retry_after": 2}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text="hello"), client)
    result = asyncio.run(run())
    assert result["status"] == "SENT"
    assert result["external_id"] == "5"
    assert len(attempts) == 2  # The same chunk retried exactly once.
    assert attempts[0]["text"] == attempts[1]["text"] == "hello"
    assert sleeps == [2]  # No real waiting happened.


def test_rate_limit_with_long_retry_after_fails_without_retry(monkeypatch):
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr("facai.channels.telegram.asyncio.sleep", fake_sleep)
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(429, json={"ok": False, "error_code": 429,
                                         "parameters": {"retry_after": 30}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text="hello"), client)
    result = asyncio.run(run())
    assert result["status"] == "FAILED"
    assert result["error"] == "RATE_LIMITED"
    assert result["retry_after"] == 30
    assert len(attempts) == 1  # No retry, no long blocking.
    assert sleeps == []


def test_mid_chunk_failure_reports_partial_sent_and_stops():
    payloads = []
    def handler(request):
        data = json.loads(request.content)
        payloads.append(data)
        if len(payloads) == 1:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})
        return httpx.Response(500, json={"ok": False, "error_code": 500})
    text = "\n\n".join(["a" * 199 + "."] * 45)  # Splits into 3 chunks.
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await TelegramProvider(ENV).send(
                OutboundReply(channel="telegram", recipient="-987", text=text), client)
    result = asyncio.run(run())
    assert result["status"] == "FAILED"
    assert result["error"] == "UPSTREAM_REJECTED"
    assert result["http_status"] == 500
    assert result["partial_sent"] == 1
    assert result["external_id"] is None
    assert len(payloads) == 2  # The third chunk was never attempted.
