"""Automatic re-dispatch of deferred Telegram and WhatsApp messages after binding.

When an inbound message is persisted but dispatch is deferred because no
agent/model/runtime binding exists, adding the missing binding through the
registry or mission-binding API must automatically re-dispatch without
duplicating missions, jobs, or provider messages.
"""

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.store import Store

# ---- Telegram helpers -------------------------------------------------------

TG_ENV = {"TELEGRAM_BOT_TOKEN": "123456:test_token",
          "TELEGRAM_WEBHOOK_SECRET": "test-secret"}
TG_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin").decode()}

TG_UPDATE = {"update_id": 100, "message": {"message_id": 10, "date": 1700000000,
             "chat": {"id": -100, "type": "group"}, "from": {"id": 200, "is_bot": False},
             "text": "Telegram deferred message"}}


def _tg_app(tmp_path, monkeypatch):
    for key, value in TG_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    app = create_app(tmp_path)
    app.state.registries.catalogs["models"].clear()
    return app


# ---- WhatsApp helpers -------------------------------------------------------

WA_ENV = {"WHATSAPP_VERIFY_TOKEN": "verify-token-value",
          "WHATSAPP_APP_SECRET": "app-secret-value",
          "WHATSAPP_ACCESS_TOKEN": "access-token-value",
          "WHATSAPP_PHONE_NUMBER_ID": "1234567890"}


def _wa_app(tmp_path, monkeypatch):
    for key, value in WA_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "test-admin")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    app.state.registries.catalogs["models"].clear()
    return app


def _wa_payload(text="Hello", sender="8613800000000", message_id="wamid.TEST1", timestamp="1759250000"):
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


def _wa_sign(body, secret=WA_ENV["WHATSAPP_APP_SECRET"]):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---- Tests ------------------------------------------------------------------

class TestTelegramDeferredRedispatch:
    """Telegram inbound messages deferred for lack of binding are re-dispatched
    automatically once an admin registers a missing model/runtime."""

    def test_register_model_redispatches_deferred_telegram(self, tmp_path, monkeypatch):
        app = _tg_app(tmp_path, monkeypatch)
        client = TestClient(app)
        route = "/api/v1/channels/telegram/webhook"

        deferred = client.post(route, json=TG_UPDATE, headers=TG_HEADERS)
        assert deferred.status_code == 503

        store = app.state.store
        records = store.channel_messages("telegram")
        assert len(records) == 1
        assert records[0]["text"] == "Telegram deferred message"
        mission_id = store.channel_message(records[0]["id"])["mission_id"]
        assert mission_id
        assert store.jobs(mission_id) == []

        resp = client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        assert resp.status_code == 200

        assert len(store.jobs(mission_id)) == 5
        mission = store.get(mission_id)
        assert mission["original_input"] == "Telegram deferred message"

    def test_register_runtime_redispatches_deferred_telegram(self, tmp_path, monkeypatch):
        app = _tg_app(tmp_path, monkeypatch)
        client = TestClient(app)
        route = "/api/v1/channels/telegram/webhook"

        deferred = client.post(route, json=TG_UPDATE, headers=TG_HEADERS)
        assert deferred.status_code == 503

        store = app.state.store
        records = store.channel_messages("telegram")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]
        assert store.jobs(mission_id) == []

        client.put("/api/v1/registries/runtimes/gpu-eu", json={
            "name": "GPU", "kind": "gpu", "provider": "runpod",
            "location": "eu-west", "status": "ready"}, headers=ADMIN)
        assert store.jobs(mission_id) == []

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        assert len(store.jobs(mission_id)) == 5

    def test_redispatch_is_idempotent_no_duplicate_jobs(self, tmp_path, monkeypatch):
        app = _tg_app(tmp_path, monkeypatch)
        client = TestClient(app)
        route = "/api/v1/channels/telegram/webhook"

        client.post(route, json=TG_UPDATE, headers=TG_HEADERS)
        store = app.state.store
        records = store.channel_messages("telegram")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        client.put("/api/v1/registries/models/other", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "other"}, headers=ADMIN)

        assert len(store.jobs(mission_id)) == 5

    def test_bind_endpoint_triggers_redispatch(self, tmp_path, monkeypatch):
        """A Telegram mission deferred for lack of binding dispatches when bound."""
        app = _tg_app(tmp_path, monkeypatch)
        client = TestClient(app)
        store = app.state.store

        client.post("/api/v1/channels/telegram/webhook", json=TG_UPDATE, headers=TG_HEADERS)
        records = store.channel_messages("telegram")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]
        assert store.jobs(mission_id) == []

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        client.put(f"/api/v1/missions/{mission_id}/binding",
                   json={"agent_id": "opencode", "model_id": "local",
                         "runtime_id": "local"}, headers=ADMIN)
        assert len(store.jobs(mission_id)) == 5

    def test_original_text_preserved_after_redispatch(self, tmp_path, monkeypatch):
        app = _tg_app(tmp_path, monkeypatch)
        client = TestClient(app)
        route = "/api/v1/channels/telegram/webhook"

        client.post(route, json=TG_UPDATE, headers=TG_HEADERS)
        store = app.state.store
        records = store.channel_messages("telegram")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)

        mission = store.get(mission_id)
        assert mission["original_input"] == "Telegram deferred message"
        msg = store.channel_message(records[0]["id"])
        assert msg["text"] == "Telegram deferred message"


class TestWhatsAppDeferredRedispatch:
    """WhatsApp inbound messages deferred for lack of binding are re-dispatched
    automatically once an admin registers a missing model/runtime."""

    def test_register_model_redispatches_deferred_whatsapp(self, tmp_path, monkeypatch):
        app = _wa_app(tmp_path, monkeypatch)
        client = TestClient(app)
        body = json.dumps(_wa_payload(text="WhatsApp deferred msg")).encode()
        route = "/api/v1/channels/whatsapp/webhook"

        deferred = client.post(route, content=body,
                               headers={"X-Hub-Signature-256": _wa_sign(body)})
        assert deferred.status_code == 503

        store = app.state.store
        records = store.channel_messages("whatsapp")
        assert len(records) == 1
        assert records[0]["text"] == "WhatsApp deferred msg"
        mission_id = store.channel_message(records[0]["id"])["mission_id"]
        assert mission_id
        assert store.jobs(mission_id) == []

        resp = client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        assert resp.status_code == 200

        assert len(store.jobs(mission_id)) == 5
        mission = store.get(mission_id)
        assert mission["original_input"] == "WhatsApp deferred msg"

    def test_redispatch_is_idempotent_no_duplicate_jobs(self, tmp_path, monkeypatch):
        app = _wa_app(tmp_path, monkeypatch)
        client = TestClient(app)
        body = json.dumps(_wa_payload(text="idempotent check")).encode()
        route = "/api/v1/channels/whatsapp/webhook"

        client.post(route, content=body,
                    headers={"X-Hub-Signature-256": _wa_sign(body)})
        store = app.state.store
        records = store.channel_messages("whatsapp")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        client.put("/api/v1/registries/models/other", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "other"}, headers=ADMIN)

        assert len(store.jobs(mission_id)) == 5

    def test_bind_endpoint_triggers_whatsapp_redispatch(self, tmp_path, monkeypatch):
        """A WhatsApp-deferred mission is dispatched when bound via the API."""
        app = _wa_app(tmp_path, monkeypatch)
        client = TestClient(app)
        store = app.state.store

        body = json.dumps(_wa_payload(text="WA deferred")).encode()
        client.post("/api/v1/channels/whatsapp/webhook", content=body,
                    headers={"X-Hub-Signature-256": _wa_sign(body)})
        records = store.channel_messages("whatsapp")
        mission_id = store.channel_message(records[0]["id"])["mission_id"]
        assert store.jobs(mission_id) == []

        client.put("/api/v1/registries/models/local", json={
            "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
            "model": "example"}, headers=ADMIN)
        client.put(f"/api/v1/missions/{mission_id}/binding",
                   json={"agent_id": "opencode", "model_id": "local",
                         "runtime_id": "local"}, headers=ADMIN)
        assert len(store.jobs(mission_id)) == 5


class TestStoreDeferredChannelMessages:
    """Direct tests for Store.deferred_channel_messages."""

    def test_finds_deferred_messages(self, tmp_path):
        store = Store(tmp_path)
        msg_id = store.record_channel_message(
            "telegram", "INBOUND", "RECEIVED", external_id="ext:1",
            sender="100", kind="text", text="hello")
        mission_id, _ = store.create_mission_from_message(
            msg_id, "telegram", title="Test", original_input="hello",
            labels=["step1"], professional_prompt="pro hello",
            conversion_payload={"channel": "telegram"})
        result = store.deferred_channel_messages(("telegram",))
        assert len(result) == 1
        assert result[0]["id"] == msg_id
        assert result[0]["mission_id"] == mission_id

    def test_excludes_dispatched_missions(self, tmp_path):
        store = Store(tmp_path)
        msg_id = store.record_channel_message(
            "telegram", "INBOUND", "RECEIVED", external_id="ext:2",
            sender="100", kind="text", text="world")
        mission_id, _ = store.create_mission_from_message(
            msg_id, "telegram", title="Test", original_input="world",
            labels=["step1"], professional_prompt="pro world",
            conversion_payload={"channel": "telegram"})
        store.bind(mission_id, agent_id="opencode", model_id="deepseek-flash",
                   runtime_id="local", changed={"agent_id", "model_id", "runtime_id"})
        store.enqueue_job(mission_id, "pro world")
        result = store.deferred_channel_messages(("telegram",))
        assert len(result) == 0

    def test_channel_scoped(self, tmp_path):
        store = Store(tmp_path)
        msg_tg = store.record_channel_message(
            "telegram", "INBOUND", "RECEIVED", external_id="ext:tg",
            sender="100", kind="text", text="tg msg")
        store.create_mission_from_message(
            msg_tg, "telegram", title="TG", original_input="tg msg",
            labels=["s1"], professional_prompt="p", conversion_payload={"channel": "telegram"})
        msg_wa = store.record_channel_message(
            "whatsapp", "INBOUND", "RECEIVED", external_id="ext:wa",
            sender="200", kind="text", text="wa msg")
        store.create_mission_from_message(
            msg_wa, "whatsapp", title="WA", original_input="wa msg",
            labels=["s1"], professional_prompt="p", conversion_payload={"channel": "whatsapp"})
        result = store.deferred_channel_messages(("telegram",))
        assert len(result) == 1
        assert result[0]["channel"] == "telegram"
        result = store.deferred_channel_messages(("telegram", "whatsapp"))
        assert len(result) == 2
