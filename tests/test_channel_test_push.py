"""绑定渠道「测试推送」: POST /api/v1/my/channels/{channel}/test.

Mirrors tests/test_customer_binding_discord_slack.py conventions: the upstream
is a httpx.MockTransport (no real network), the credential is the customer's
own preflighted binding, and every assertion is secret-free. The endpoint must
reuse the NOTIFY loop's exact trust path (notify.owner_bot), report DELIVERED
only for an upstream-confirmed 2xx, pass the adapter's stable error code
through on failure, and never echo the credential.
"""

import base64
import json

import httpx
from fastapi.testclient import TestClient

from facai.api import TEST_PUSH_TEXT, create_app

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
        if request.headers.get("authorization") == f"Bot {DISCORD_TOKEN}":
            return httpx.Response(200, json={"id": "1086597162345678904",
                                             "username": "customer-bot"})
        return httpx.Response(401, json={"message": "401: Unauthorized"})
    if url == "https://slack.com/api/auth.test":
        if request.headers.get("authorization") == f"Bearer {SLACK_TOKEN}":
            return httpx.Response(200, json={"ok": True, "team": "T1", "user": "bot"})
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    raise AssertionError(f"unexpected upstream call: {url}")


def _mock_preflight(monkeypatch):
    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        # setdefault: the send mocks installed later override this transport.
        kwargs.setdefault("transport", httpx.MockTransport(_preflight_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    # Hermetic: the background NOTIFY thread must not race the endpoint under
    # test (the endpoint builds its own provider via notify.owner_bot).
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
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


def _bind_discord(client):
    return client.put("/api/v1/my/channels/discord",
                      json={"bot_token": DISCORD_TOKEN, "chat_id": DISCORD_CHANNEL},
                      headers=CARL_AUTH)


def _bind_slack(client):
    return client.put("/api/v1/my/channels/slack",
                      json={"bot_token": SLACK_TOKEN, "chat_id": SLACK_CHANNEL},
                      headers=CARL_AUTH)


# --- auth, channel gate, unbound gate ----------------------------------------

def test_test_push_requires_a_session(monkeypatch, tmp_path):
    _, client = _app(monkeypatch, tmp_path)
    r = client.post("/api/v1/my/channels/telegram/test")
    assert r.status_code == 401, r.text


def test_test_push_404_for_non_bindable_channel(monkeypatch, tmp_path):
    _, client = _app(monkeypatch, tmp_path)
    for channel in ("whatsapp", "wechat", "sms"):
        r = client.post(f"/api/v1/my/channels/{channel}/test", headers=CARL_AUTH)
        assert r.status_code == 404, (channel, r.text)


def test_test_push_409_without_binding(monkeypatch, tmp_path):
    _, client = _app(monkeypatch, tmp_path)
    r = client.post("/api/v1/my/channels/telegram/test", headers=CARL_AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "该渠道尚未绑定，请先完成绑定"


# --- DELIVERED path -----------------------------------------------------------

def test_test_push_delivered_via_the_bound_bot(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert _bind_discord(client).status_code == 200

    calls = []
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append({"url": str(request.url),
                      "auth": request.headers.get("authorization"),
                      "json": json.loads(request.content)})
        # Concrete message id: the only shape the adapter reports DELIVERED for.
        return httpx.Response(200, json={"id": "discord-test-push-1"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)

    r = client.post("/api/v1/my/channels/discord/test", headers=CARL_AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"channel": "discord", "status": "DELIVERED",
                        "error": None}
    assert DISCORD_TOKEN not in r.text  # the credential never leaves the box

    # The push really went to the customer's own bot and their bound channel.
    assert len(calls) == 1
    assert calls[0]["url"] == \
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages"
    assert calls[0]["auth"] == f"Bot {DISCORD_TOKEN}"
    assert calls[0]["json"]["content"] == TEST_PUSH_TEXT

    # Same audit trail as every other outbound line (text only, never a token).
    audits = [m for m in app.state.store.channel_messages("discord")
              if m["direction"] == "OUTBOUND"]
    assert any(m["owner_id"] == "carl" and m["recipient"] == DISCORD_CHANNEL
               and m["status"] == "DELIVERED" and m["text"] == TEST_PUSH_TEXT
               for m in audits)
    assert DISCORD_TOKEN not in json.dumps(audits, ensure_ascii=False)


# --- FAILED path (honest, stable code, no fabricated delivery) ----------------

def test_test_push_failed_reports_the_adapter_error_code(monkeypatch, tmp_path):
    _, client = _app(monkeypatch, tmp_path)
    assert _bind_slack(client).status_code == 200

    real_client = httpx.AsyncClient

    def handler(request):
        # Slack auth rejection: 200 + ok:false with an auth error body.
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)

    r = client.post("/api/v1/my/channels/slack/test", headers=CARL_AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"channel": "slack", "status": "FAILED",
                        "error": "AUTH_FAILED"}
    assert SLACK_TOKEN not in r.text


def test_test_push_failed_on_upstream_401(monkeypatch, tmp_path):
    _, client = _app(monkeypatch, tmp_path)
    assert _bind_discord(client).status_code == 200

    real_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)

    r = client.post("/api/v1/my/channels/discord/test", headers=CARL_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FAILED"
    assert r.json()["error"] == "AUTH_FAILED"
    assert DISCORD_TOKEN not in r.text
