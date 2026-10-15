"""Customer-owned channel bindings: 绑一次 token = 云端 7×24.

A tenant binds the Telegram bot THEY created (their own BotFather bot, never
the station's) plus the chat that receives progress. Proven here:

* the binding API round-trips and the token is never echoed by any response;
* the token is stored only as secretbox ciphertext (never plaintext at rest);
* the NOTIFY loop prefers the owner's own bot for their missions — asserted at
  the httpx layer, where the customer's bot API URL and bound chat must appear;
* a failed customer-bot send is honestly audited as FAILED and never blocks
  the pipeline (the cursor advances either way);
* unbinding falls back to the station channel, whose behavior is unchanged;
* bindings are tenant-isolated: bob never sees or clears alice's binding, and
  alice's binding never captures admin-owned missions.
"""

import base64
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.secretbox import decrypt
from facai.store import Store

PASSWORD = "chan-pass"
ALICE_AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:alice-pass-123").decode()}
BOB_AUTH = {"Authorization": "Basic " + base64.b64encode(b"bob:bob-pass-1234").decode()}

ALICE_TOKEN = "111222333:AAAliceBotToken_abc123XYZ"
ALICE_CHAT = "556677"
BOB_TOKEN = "444555666:BBBobBotToken_def456UVW"
BOB_CHAT = "-1009988776655"


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    # The station telegram bot stays unconfigured: a customer-bound send must
    # not silently depend on it, and the station-fallback tests install their
    # own fake provider.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    # 凭证预检打桩（同款 MockTransport）：绑定端点现在落库前会真实调上游验证
    # 接口。默认让 getMe / LINE bot info 一律回答有效，本文件才能专注绑定语义；
    # 需要断言推送行为的测试随后用自己的 handler 覆盖式重打桩（后写胜出）。
    real_client = httpx.AsyncClient

    def preflight_handler(request):
        url = str(request.url)
        if url.startswith("https://api.telegram.org/bot") and url.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 111222333,
                                                                    "username": "binding_test_bot"}})
        if url == "https://api.line.me/v2/bot/info":
            return httpx.Response(200, json={"displayName": "binding-test-bot"})
        raise AssertionError(f"unexpected upstream call in tenant-channel test: {url}")

    def mock_client(*args, **kwargs):
        # setdefault：需要断言推送行为的测试随后覆盖式重打桩（后写胜出）——
        # 它们的 wrapper 会先注入自己的 transport，这里不得反抢。
        kwargs.setdefault("transport", httpx.MockTransport(preflight_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.telegram.httpx.AsyncClient", mock_client)
    app = create_app(tmp_path)
    with TestClient(app) as bootstrap:
        for username, password in (("alice", "alice-pass-123"), ("bob", "bob-pass-1234")):
            r = bootstrap.post("/api/v1/auth/register",
                               json={"username": username, "password": password})
            assert r.status_code in (201, 409), r.text
    return app, TestClient(app)


def _bind(client, token=ALICE_TOKEN, chat=ALICE_CHAT, headers=ALICE_AUTH):
    return client.put("/api/v1/my/channels/telegram",
                      json={"bot_token": token, "chat_id": chat}, headers=headers)


# --- binding API round-trip -----------------------------------------------------


def test_bind_list_unbind_roundtrip(monkeypatch, tmp_path):
    _app_, client = _app(monkeypatch, tmp_path)

    empty = client.get("/api/v1/my/channels", headers=ALICE_AUTH)
    assert empty.status_code == 200
    # line/discord/slack joined the customer-bindable family; none bound yet.
    assert empty.json() == {
        "telegram": {"bound": False, "chat_id": None, "created_at": None},
        "line": {"bound": False, "chat_id": None, "created_at": None},
        "discord": {"bound": False, "chat_id": None, "created_at": None},
        "slack": {"bound": False, "chat_id": None, "created_at": None},
    }

    r = _bind(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "telegram" and body["bound"] is True
    assert body["chat_id"] == ALICE_CHAT and body["created_at"]
    assert ALICE_TOKEN not in r.text  # the token is never echoed, not even at bind time

    listing = client.get("/api/v1/my/channels", headers=ALICE_AUTH).json()["telegram"]
    assert listing["bound"] is True and listing["chat_id"] == ALICE_CHAT

    # Re-binding rotates the credential in place and keeps the first-bind time.
    r2 = _bind(client, token="111222333:ARotatedToken_newvalue99", chat="889900")
    assert r2.status_code == 200
    rebound = client.get("/api/v1/my/channels", headers=ALICE_AUTH).json()["telegram"]
    assert rebound["chat_id"] == "889900"
    assert rebound["created_at"] == body["created_at"]

    # Unauthenticated traffic never touches the binding surface.
    assert client.get("/api/v1/my/channels").status_code == 401
    assert client.put("/api/v1/my/channels/telegram",
                      json={"bot_token": ALICE_TOKEN, "chat_id": ALICE_CHAT}).status_code == 401
    assert client.delete("/api/v1/my/channels/telegram").status_code == 401

    deleted = client.delete("/api/v1/my/channels/telegram", headers=ALICE_AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"channel": "telegram", "bound": False}
    assert client.get("/api/v1/my/channels",
                      headers=ALICE_AUTH).json()["telegram"]["bound"] is False
    # Deleting again is a 404, never a silent second deletion.
    assert client.delete("/api/v1/my/channels/telegram", headers=ALICE_AUTH).status_code == 404


@pytest.mark.parametrize("payload", [
    {"bot_token": "not-a-telegram-token", "chat_id": ALICE_CHAT},
    {"bot_token": "abc:def", "chat_id": ALICE_CHAT},  # bot id must be numeric
    {"bot_token": "111222333:has spaces", "chat_id": ALICE_CHAT},
    {"bot_token": ALICE_TOKEN, "chat_id": "not a chat"},
    {"bot_token": ALICE_TOKEN, "chat_id": "12.5"},
])
def test_invalid_bindings_are_rejected_and_store_nothing(monkeypatch, tmp_path, payload):
    _app_, client = _app(monkeypatch, tmp_path)
    r = client.put("/api/v1/my/channels/telegram", json=payload, headers=ALICE_AUTH)
    assert r.status_code == 422, r.text
    assert client.get("/api/v1/my/channels",
                      headers=ALICE_AUTH).json()["telegram"]["bound"] is False


def test_token_is_encrypted_at_rest_and_never_echoed(monkeypatch, tmp_path):
    _app_, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    listing = client.get("/api/v1/my/channels", headers=ALICE_AUTH)
    assert listing.status_code == 200
    assert ALICE_TOKEN not in listing.text

    # The database file never carries the plaintext token.
    for name in ("facai.sqlite3", "facai.sqlite3-wal"):
        page = tmp_path / name
        if page.exists():
            assert ALICE_TOKEN.encode() not in page.read_bytes()

    row = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT bot_token_encrypted FROM channel_bindings").fetchone()
    assert row and decrypt(tmp_path, row[0]) == ALICE_TOKEN  # ciphertext opens only in memory


# --- NOTIFY loop prefers the owner's own bot ------------------------------------


def _mission_for(store, owner_id, text="帮我做一个喝水统计"):
    mission = store.create("客户任务", text, ["开发", "测试"], owner_id=owner_id)
    store.record_channel_message("telegram", "INBOUND", "RECEIVED", external_id=f"tg-{owner_id}",
                                 sender="999888", kind="text", text=text,
                                 mission_id=mission["id"])
    return mission


def _run_step(store, mission):
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")


def test_progress_flows_through_the_customer_own_bot(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    store = app.state.store
    mission = _mission_for(store, "alice")
    notifier = app.state.notify
    notifier.run_pending()  # initialize the cursor at the current head
    _run_step(store, mission)

    calls = []

    def handler(request):
        calls.append({"url": str(request.url), "json": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.telegram.httpx.AsyncClient", mock_client)

    assert notifier.run_pending() >= 1
    assert calls, "the customer's own bot API must have been called"
    # Every call rode alice's token (never a station bot) ...
    assert all(call["url"].startswith(f"https://api.telegram.org/bot{ALICE_TOKEN}/")
               for call in calls), [c["url"] for c in calls]
    # ... and targeted her bound chat, not the inbound sender of the station bot.
    assert any(call["json"].get("chat_id") == ALICE_CHAT for call in calls)
    assert all(call["json"].get("chat_id") != "999888" for call in calls)

    # The audit row is honest (owner, bound chat, mission) and token-free.
    audits = [m for m in store.channel_messages("telegram") if m["direction"] == "OUTBOUND"]
    assert any(m["owner_id"] == "alice" and m["recipient"] == ALICE_CHAT
               and m["mission_id"] == mission["id"] and m["status"] == "SENT"
               for m in audits)
    assert all(ALICE_TOKEN not in m["text"] and ALICE_TOKEN not in json.dumps(m["payload"])
               for m in audits)


def test_customer_bot_failure_is_audited_and_never_blocks(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    store = app.state.store
    mission = _mission_for(store, "alice")
    notifier = app.state.notify
    notifier.run_pending()
    _run_step(store, mission)

    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        def handler(request):  # Bot API rejects the token outright
            return httpx.Response(401, json={"ok": False, "error_code": 401,
                                             "description": "Unauthorized"})
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.telegram.httpx.AsyncClient", mock_client)

    assert notifier.run_pending() >= 1  # no exception escapes; the loop survives
    audits = [m for m in store.channel_messages("telegram")
              if m["direction"] == "OUTBOUND" and m["owner_id"] == "alice"]
    assert audits and all(m["status"] == "FAILED" for m in audits)
    assert all(ALICE_TOKEN not in m["text"] for m in audits)
    assert notifier.run_pending() == 0  # cursor advanced: no retry flood


def test_customer_bot_crash_is_audited_via_the_failure_path(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    store = app.state.store
    mission = _mission_for(store, "alice")
    notifier = app.state.notify
    notifier.run_pending()
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")

    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        def handler(request):  # a hard transport crash, not a Bot API answer
            raise RuntimeError("boom")
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.telegram.httpx.AsyncClient", mock_client)

    assert notifier.run_pending() >= 1  # must not raise
    audits = [m for m in store.channel_messages("telegram")
              if m["direction"] == "OUTBOUND" and m["owner_id"] == "alice"]
    assert any(m["status"] == "FAILED" and m["recipient"] == ALICE_CHAT for m in audits)


# --- unbinding falls back to the station channel --------------------------------


class StationFakeProvider:
    """A configured stand-in for the station-level telegram provider."""

    name = "telegram"
    implemented = True
    records_outbound_itself = False

    def __init__(self):
        self.sends = []

    def configured(self):
        return True

    def missing(self):
        return []

    async def send(self, reply):
        self.sends.append((reply.recipient, reply.text))
        return {"channel": self.name, "status": "SENT", "http_status": 200,
                "external_id": "st-1", "error": None}


def test_unbound_owner_falls_back_to_the_station_channel(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200
    assert client.delete("/api/v1/my/channels/telegram", headers=ALICE_AUTH).status_code == 200

    station = StationFakeProvider()
    app.state.channels.providers["telegram"] = station

    store = app.state.store
    mission = _mission_for(store, "alice")
    notifier = app.state.notify
    notifier.run_pending()
    _run_step(store, mission)

    assert notifier.run_pending() >= 1
    # Back on the station bot, replying to the chat that asked for the work.
    assert any(recipient == "999888" and "完成" in text for recipient, text in station.sends)


def test_owner_binding_never_captures_other_owners_missions(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    station = StationFakeProvider()
    app.state.channels.providers["telegram"] = station

    store = app.state.store
    mission = _mission_for(store, "admin", text="站长自己的任务")
    notifier = app.state.notify
    notifier.run_pending()
    _run_step(store, mission)

    assert notifier.run_pending() >= 1
    assert any(recipient == "999888" for recipient, _ in station.sends)


# --- tenant isolation ------------------------------------------------------------


def test_bindings_are_tenant_isolated(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind(client).status_code == 200

    # bob sees no binding and cannot clear alice's.
    bob_view = client.get("/api/v1/my/channels", headers=BOB_AUTH).json()["telegram"]
    assert bob_view["bound"] is False and bob_view["chat_id"] is None
    assert client.delete("/api/v1/my/channels/telegram", headers=BOB_AUTH).status_code == 404

    # bob binds his own bot; alice's binding is untouched.
    assert _bind(client, token=BOB_TOKEN, chat=BOB_CHAT, headers=BOB_AUTH).status_code == 200
    alice_view = client.get("/api/v1/my/channels", headers=ALICE_AUTH).json()["telegram"]
    bob_view = client.get("/api/v1/my/channels", headers=BOB_AUTH).json()["telegram"]
    assert alice_view["chat_id"] == ALICE_CHAT
    assert bob_view["chat_id"] == BOB_CHAT
    assert ALICE_TOKEN not in client.get("/api/v1/my/channels", headers=BOB_AUTH).text

    rows = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT owner_id, channel, chat_id FROM channel_bindings ORDER BY owner_id").fetchall()
    assert rows == [("alice", "telegram", ALICE_CHAT), ("bob", "telegram", BOB_CHAT)]

    # alice's missions still ride alice's bot after bob's binding appeared.
    store = app.state.store
    mission = _mission_for(store, "alice")
    notifier = app.state.notify
    notifier.run_pending()
    _run_step(store, mission)

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.telegram.httpx.AsyncClient", mock_client)
    assert notifier.run_pending() >= 1
    assert calls and all(url.startswith(f"https://api.telegram.org/bot{ALICE_TOKEN}/")
                         for url in calls)


# --- store contract --------------------------------------------------------------


def test_store_binding_helpers_are_token_blind_and_owner_scoped(tmp_path):
    store = Store(tmp_path)
    assert store.channel_binding("alice", "telegram") is None
    assert store.channel_binding_token("alice", "telegram") is None
    assert store.delete_channel_binding("alice", "telegram") is False

    sealed = "gAAAAA-fake-ciphertext"
    binding = store.upsert_channel_binding("alice", "telegram", sealed, "556677")
    assert binding == {"owner_id": "alice", "channel": "telegram",
                       "chat_id": "556677", "created_at": binding["created_at"]}
    assert store.channel_binding_token("alice", "telegram") == sealed
    # Re-bind keeps the first-bind time and swaps credential + chat only.
    first_created = binding["created_at"]
    rebinding = store.upsert_channel_binding("alice", "telegram", "gAAAAA-rotated", "889900")
    assert rebinding["created_at"] == first_created
    assert rebinding["chat_id"] == "889900"
    assert store.channel_binding_token("alice", "telegram") == "gAAAAA-rotated"
    assert store.delete_channel_binding("alice", "telegram") is True
    assert store.channel_binding("alice", "telegram") is None
