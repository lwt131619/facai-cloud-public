"""Customer self-service binding for discord/slack: preflight, CRUD, notify ride.

Mirrors tests/test_channel_notify.py's LINE binding conventions for the two
new customer-bindable channels: the credential is really preflighted upstream
before anything is stored (format-valid ≠ working), never echoed, and the
owner's bound bot carries their progress exactly like telegram/line do.
"""

import asyncio
import base64
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app

PASSWORD = "bind-pass-123"
CARL_AUTH = {"Authorization": "Basic " + base64.b64encode(
    f"carl:{PASSWORD}".encode()).decode()}

DISCORD_TOKEN = ("MTA4NjU5NzE2MjM0NTY3ODkwNA.GxYzAb."
                 "abcdefghijklmnopqrstuvwxyz123456789")
DISCORD_CHANNEL = "987654321098765432"
SLACK_TOKEN = "xoxb-REDACTED-PUBLIC-SNAPSHOT-NOT-A-REAL-TOKEN"
SLACK_CHANNEL = "C0SLACKCHAN"


def _preflight_handler(request):
    url = str(request.url)
    if url == "https://discord.com/api/v10/users/@me":
        auth = request.headers.get("authorization", "")
        if auth == f"Bot {DISCORD_TOKEN}":
            return httpx.Response(200, json={"id": "1086597162345678904",
                                             "username": "customer-bot"})
        return httpx.Response(401, json={"message": "401: Unauthorized"})
    if url == "https://slack.com/api/auth.test":
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {SLACK_TOKEN}":
            return httpx.Response(200, json={"ok": True, "team": "T1", "user": "bot"})
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    raise AssertionError(f"unexpected upstream call: {url}")


def _mock_preflight(monkeypatch):
    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        # setdefault: the push-ride mocks installed later override this
        # transport (last writer wins) -- never steal their delivery.
        kwargs.setdefault("transport", httpx.MockTransport(_preflight_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    _mock_preflight(monkeypatch)
    app = create_app(tmp_path)
    with TestClient(app) as bootstrap:
        r = bootstrap.post("/api/v1/auth/register",
                           json={"username": "carl", "password": PASSWORD})
        assert r.status_code in (201, 409), r.text
    return app, TestClient(app)


def _bind_discord(client, token=DISCORD_TOKEN, channel=DISCORD_CHANNEL):
    return client.put("/api/v1/my/channels/discord",
                      json={"bot_token": token, "chat_id": channel},
                      headers=CARL_AUTH)


def _bind_slack(client, token=SLACK_TOKEN, channel=SLACK_CHANNEL):
    return client.put("/api/v1/my/channels/slack",
                      json={"bot_token": token, "chat_id": channel},
                      headers=CARL_AUTH)


# --- discord binding roundtrip ---------------------------------------------

def test_discord_binding_roundtrip_preflighted_and_never_echoed(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)

    empty = client.get("/api/v1/my/channels", headers=CARL_AUTH)
    assert empty.status_code == 200
    assert empty.json()["discord"] == {"bound": False, "chat_id": None, "created_at": None}

    r = _bind_discord(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "discord" and body["bound"] is True
    assert body["chat_id"] == DISCORD_CHANNEL and body["created_at"]
    assert DISCORD_TOKEN not in r.text  # never echoed

    listing = client.get("/api/v1/my/channels", headers=CARL_AUTH)
    assert listing.json()["discord"]["bound"] is True
    assert listing.json()["discord"]["chat_id"] == DISCORD_CHANNEL
    assert DISCORD_TOKEN not in listing.text

    # The database carries only the ciphertext; the chat_id column holds the
    # channel snowflake.
    row = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT bot_token_encrypted, chat_id FROM channel_bindings"
        " WHERE channel='discord'").fetchone()
    assert row and row[1] == DISCORD_CHANNEL
    from facai import secretbox
    assert secretbox.decrypt(app.state.store.root, row[0]) == DISCORD_TOKEN

    # Unbind; deleting again is a 404, never a silent second deletion.
    assert client.delete("/api/v1/my/channels/discord",
                         headers=CARL_AUTH).json() == {"channel": "discord", "bound": False}
    assert client.delete("/api/v1/my/channels/discord",
                         headers=CARL_AUTH).status_code == 404


def test_discord_binding_rejects_failed_preflight_without_storing(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    r = _bind_discord(client, token="MTA4NjU5NzE2MjM0NTY3ODkwNA.GxYzAb."
                                  "WRONGwrongWRONGwrong123456789012345")
    assert r.status_code == 422, r.text
    assert "Discord 返回" in r.text
    assert client.get("/api/v1/my/channels",
                      headers=CARL_AUTH).json()["discord"]["bound"] is False
    # Unauthenticated traffic never touches the binding surface.
    anon = client.put("/api/v1/my/channels/discord",
                      json={"bot_token": DISCORD_TOKEN, "chat_id": DISCORD_CHANNEL})
    assert anon.status_code == 401


def test_discord_format_gates_reject_before_any_network(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    for payload in (
        {"bot_token": "short-token", "chat_id": DISCORD_CHANNEL},
        {"bot_token": DISCORD_TOKEN, "chat_id": "not-a-snowflake"},
        {"bot_token": DISCORD_TOKEN, "chat_id": "123"},
    ):
        r = client.put("/api/v1/my/channels/discord", json=payload, headers=CARL_AUTH)
        assert r.status_code == 422, payload


# --- slack binding roundtrip -------------------------------------------------

def test_slack_binding_roundtrip_reads_body_preflight(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)

    r = _bind_slack(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "slack" and body["bound"] is True
    assert body["chat_id"] == SLACK_CHANNEL
    assert SLACK_TOKEN not in r.text

    listing = client.get("/api/v1/my/channels", headers=CARL_AUTH)
    assert listing.json()["slack"]["bound"] is True
    assert SLACK_TOKEN not in listing.text

    row = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT bot_token_encrypted, chat_id FROM channel_bindings"
        " WHERE channel='slack'").fetchone()
    assert row and row[1] == SLACK_CHANNEL
    from facai import secretbox
    assert secretbox.decrypt(app.state.store.root, row[0]) == SLACK_TOKEN


def test_slack_binding_rejects_ok_false_preflight(monkeypatch, tmp_path):
    # Slack answers auth failures with a 200 + ok:false — the 422 must quote
    # the body's error code, proving the preflight read the body.
    app, client = _app(monkeypatch, tmp_path)
    r = _bind_slack(client, token="xoxb-REDACTED-PUBLIC-SNAPSHOT-NOT-A-REAL-TOKEN-1234567890123-WRONGwrongWRONG")
    assert r.status_code == 422, r.text
    assert "Slack 返回 invalid_auth" in r.text
    assert client.get("/api/v1/my/channels",
                      headers=CARL_AUTH).json()["slack"]["bound"] is False


def test_slack_format_gates_reject_before_any_network(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    for payload in (
        {"bot_token": "xoxp-not-a-bot-token-123456", "chat_id": SLACK_CHANNEL},
        {"bot_token": SLACK_TOKEN, "chat_id": "C"},
        {"bot_token": SLACK_TOKEN, "chat_id": "has space"},
    ):
        r = client.put("/api/v1/my/channels/slack", json=payload, headers=CARL_AUTH)
        assert r.status_code == 422, payload


# --- owner progress rides the bound bots ------------------------------------

def _channel_mission_for(store, owner_id, channel, sender, text="帮我做一个喝水统计"):
    mission = store.create("客户任务", text, ["开发", "测试"], owner_id=owner_id)
    store.record_channel_message(channel, "INBOUND", "RECEIVED",
                                 external_id=f"{channel}-{owner_id}",
                                 sender=sender, kind="text", text=text,
                                 mission_id=mission["id"])
    return mission


def test_bound_discord_owner_gets_progress_via_their_own_bot(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind_discord(client).status_code == 200

    store = app.state.store
    mission = _channel_mission_for(store, "carl", "discord", DISCORD_CHANNEL)
    notifier = app.state.notify
    notifier.run_pending()  # initialize the cursor at the current head
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")

    calls = []
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append({"url": str(request.url),
                      "auth": request.headers.get("authorization"),
                      "json": json.loads(request.content)})
        return httpx.Response(200, json={"id": "discord-push-1"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)

    assert notifier.run_pending() >= 1
    assert calls, "the owner's bound Discord push endpoint must have been called"
    assert all(call["url"] ==
               f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages"
               for call in calls)
    assert all(call["auth"] == f"Bot {DISCORD_TOKEN}" for call in calls)
    assert any("完成" in call["json"]["content"] for call in calls)

    audits = [m for m in store.channel_messages("discord") if m["direction"] == "OUTBOUND"]
    assert any(m["owner_id"] == "carl" and m["recipient"] == DISCORD_CHANNEL
               and m["mission_id"] == mission["id"] and m["status"] == "DELIVERED"
               for m in audits)
    blob = json.dumps(audits, ensure_ascii=False)
    assert DISCORD_TOKEN not in blob


def test_bound_slack_owner_gets_progress_via_their_own_bot(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind_slack(client).status_code == 200

    store = app.state.store
    mission = _channel_mission_for(store, "carl", "slack", SLACK_CHANNEL)
    notifier = app.state.notify
    notifier.run_pending()
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")

    calls = []
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append({"url": str(request.url),
                      "auth": request.headers.get("authorization"),
                      "json": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "ts": "1769160999.000100"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)

    assert notifier.run_pending() >= 1
    assert calls, "the owner's bound Slack push endpoint must have been called"
    assert all(call["url"] == "https://slack.com/api/chat.postMessage" for call in calls)
    assert all(call["auth"] == f"Bearer {SLACK_TOKEN}" for call in calls)
    assert all(call["json"].get("channel") == SLACK_CHANNEL for call in calls)
    assert any("完成" in call["json"]["text"] for call in calls)

    audits = [m for m in store.channel_messages("slack") if m["direction"] == "OUTBOUND"]
    assert any(m["owner_id"] == "carl" and m["recipient"] == SLACK_CHANNEL
               and m["status"] == "DELIVERED" for m in audits)
    blob = json.dumps(audits, ensure_ascii=False)
    assert SLACK_TOKEN not in blob
