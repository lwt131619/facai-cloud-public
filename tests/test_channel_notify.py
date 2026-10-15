"""Chat-channel progress notifications and mission acknowledgements."""

import base64
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.notify import NOTIFY_CHANNELS, ChannelNotifier
from facai.secretbox import decrypt
from facai.store import Store

PASSWORD = "notify-pass-123"


def _fake_registry(sends, configured=True):
    class FakeProvider:
        name = "telegram"

        def __init__(self):
            self.sends = sends

        def configured(self):
            return configured

        async def send(self, reply):
            self.sends.append((reply.channel, reply.recipient, reply.text))

    class FakeRegistry:
        def get(self, channel):
            return FakeProvider() if channel == "telegram" else None

    return FakeRegistry()


def _store_with_channel_mission(tmp_path):
    store = Store(tmp_path)
    mission = store.create("喝水工具", "帮我做一个喝水统计", ["开发", "测试"], owner_id="admin")
    store.record_channel_message("telegram", "INBOUND", "RECEIVED", external_id="tg-1",
                                 sender="10086", kind="text", text="帮我做一个喝水统计",
                                 mission_id=mission["id"])
    return store, mission


def test_relay_sends_step_progress_and_completion(tmp_path):
    store, mission = _store_with_channel_mission(tmp_path / "a")
    sends = []
    notifier = ChannelNotifier(store, _fake_registry(sends))
    notifier.run_pending()  # initialize the cursor at the current head

    step_id = mission["steps"][0]["id"]
    store.step_status(mission["id"], step_id, "RUNNING")
    store.step_status(mission["id"], step_id, "COMPLETED")

    assert notifier.run_pending() >= 1
    texts = [text for _, _, text in sends]
    assert any("阶段「开发」完成" in text for text in texts)

    # Completing every step emits the mission-done line exactly once.
    sends.clear()
    store.step_status(mission["id"], mission["steps"][1]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][1]["id"], "COMPLETED")
    notifier.run_pending()
    assert sum("全部完成" in text for text in texts + [t for _, _, t in sends]) >= 1


def test_relay_ignores_non_channel_missions_and_advances_cursor(tmp_path):
    store = Store(tmp_path / "b")
    mission = store.create("纯网页任务", "原话", ["开发"], owner_id="admin")
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")
    sends = []
    notifier = ChannelNotifier(store, _fake_registry(sends))
    assert notifier.run_pending() == 0
    assert notifier.run_pending() == 0  # cursor advanced; nothing re-sent
    assert sends == []


def test_relay_reports_failure_with_error_text(tmp_path):
    store, mission = _store_with_channel_mission(tmp_path / "c")
    sends = []
    notifier = ChannelNotifier(store, _fake_registry(sends))
    notifier.run_pending()  # initialize the cursor at the current head
    step_id = mission["steps"][0]["id"]
    store.step_status(mission["id"], step_id, "RUNNING")
    job = store.enqueue_job(mission["id"], "p", step_id=step_id)
    store.claim_job("w", 60)
    store.finish_job(job["id"], "FAILED", "opencode exploded")
    notifier.run_pending()
    assert any("失败" in text and "opencode exploded" in text for _, _, text in sends)


def test_unconfigured_provider_never_crashes_the_relay(tmp_path):
    store, mission = _store_with_channel_mission(tmp_path / "d")
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    notifier = ChannelNotifier(store, _fake_registry([], configured=False))
    notifier.run_pending()  # must not raise
    notifier.start(interval=0.05)
    notifier.stop()


def test_webhook_ack_sent_for_fresh_telegram_mission(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    app = create_app(tmp_path)

    sent = []

    class FakeProvider:
        name = "telegram"
        implemented = True
        required_env = ()

        def configured(self):
            return True

        def missing(self):
            return []

        def verify_signature(self, body, headers):
            return True

        def parse_inbound(self, payload):
            from facai.channels import InboundMessage
            message = payload.get("message") or {}
            return [InboundMessage(channel="telegram",
                                   external_id=f"update:{payload['update_id']}",
                                   sender="10086", text=message.get("text", ""),
                                   received_at="now", raw=message)]

        async def send(self, reply):
            sent.append(reply.text)

    app.state.channels.providers["telegram"] = FakeProvider()
    client = TestClient(app)
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})

    response = client.post("/api/v1/channels/telegram/webhook",
                           json={"update_id": 1, "message": {"text": "帮我做一个喝水小工具"}})
    assert response.status_code in {200, 503}, response.text
    if response.status_code == 503:  # deferred dispatch without binding: no ack
        assert not sent
        return
    assert any("已收到需求并创建任务" in text for text in sent), sent


def test_notify_channels_constant():
    assert "telegram" in NOTIFY_CHANNELS and "whatsapp" in NOTIFY_CHANNELS
    assert "wechat" in NOTIFY_CHANNELS  # iLink channel notifies like the rest
    # line rides after relay: before the station's official-account token
    # exists, the loop pushes through an owner binding or skips silently.
    assert "line" in NOTIFY_CHANNELS
    assert NOTIFY_CHANNELS.index("relay") < NOTIFY_CHANNELS.index("line")


# --- LINE progress push: owner binding before the station token exists ----------

LINE_TOKEN = "lt-owner-access-token-0123456789abcdef"
LINE_SECRET = "ls-owner-channel-secret-0123456789abcdef"
LINE_USER = "U0123456789abcdef0123456789abcdef"
CARL_AUTH = {"Authorization": "Basic " + base64.b64encode(b"carl:carl-pass-123").decode()}


def _line_app(monkeypatch, tmp_path, username="carl", password="carl-pass-123"):
    """An app whose station LINE credentials stay absent on purpose: only the
    owner's own binding can carry a push, so a passing test proves the binding
    path -- and the no-binding test proves the honest silent skip."""
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    # 凭证预检打桩（同款 MockTransport）：/my/channels/line 绑定现在落库前会
    # 真实调 LINE bot info 验证 access_token，默认放行；需要断言推送行为的
    # 测试随后经 _mock_line_http 覆盖式重打桩（后写胜出）。
    real_client = httpx.AsyncClient

    def preflight_handler(request):
        url = str(request.url)
        if url == "https://api.line.me/v2/bot/info":
            return httpx.Response(200, json={"displayName": "notify-test-bot"})
        raise AssertionError(f"unexpected upstream call in notify test: {url}")

    def mock_client(*args, **kwargs):
        # setdefault：_mock_line_http 随后覆盖式重打桩（后写胜出），这里不反抢。
        kwargs.setdefault("transport", httpx.MockTransport(preflight_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.line.httpx.AsyncClient", mock_client)
    app = create_app(tmp_path)
    with TestClient(app) as bootstrap:
        r = bootstrap.post("/api/v1/auth/register",
                           json={"username": username, "password": password})
        assert r.status_code in (201, 409), r.text
    return app, TestClient(app)


def _bind_line(client, headers, token=LINE_TOKEN, secret=LINE_SECRET, user=LINE_USER):
    return client.put("/api/v1/my/channels/line",
                      json={"access_token": token, "channel_secret": secret,
                            "user_id": user}, headers=headers)


def _line_mission_for(store, owner_id, text="帮我做一个喝水统计"):
    mission = store.create("客户任务", text, ["开发", "测试"], owner_id=owner_id)
    store.record_channel_message("line", "INBOUND", "RECEIVED", external_id=f"line-{owner_id}",
                                 sender=LINE_USER, kind="text", text=text,
                                 mission_id=mission["id"])
    return mission


def _mock_line_http(monkeypatch, calls):
    """Intercept the LINE push API the same way the telegram binding tests do."""
    real_client = httpx.AsyncClient

    def handler(request):
        calls.append({"url": str(request.url),
                      "auth": request.headers.get("authorization"),
                      "json": json.loads(request.content)})
        return httpx.Response(200, json={"sentMessages": [{"id": "line-push-1"}]})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.channels.line.httpx.AsyncClient", mock_client)


def test_line_binding_roundtrip_encrypted_and_never_echoed(monkeypatch, tmp_path):
    app, client = _line_app(monkeypatch, tmp_path)

    empty = client.get("/api/v1/my/channels", headers=CARL_AUTH)
    assert empty.status_code == 200
    assert empty.json()["line"] == {"bound": False, "chat_id": None, "created_at": None}

    r = _bind_line(client, CARL_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "line" and body["bound"] is True
    assert body["chat_id"] == LINE_USER and body["created_at"]
    assert LINE_TOKEN not in r.text and LINE_SECRET not in r.text  # never echoed

    listing = client.get("/api/v1/my/channels", headers=CARL_AUTH)
    assert listing.json()["line"]["bound"] is True
    assert listing.json()["line"]["chat_id"] == LINE_USER
    assert LINE_TOKEN not in listing.text and LINE_SECRET not in listing.text

    # The database never carries plaintext credentials: one ciphertext opens
    # into both values, and the binding row's chat_id holds the LINE userId.
    row = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT bot_token_encrypted, chat_id FROM channel_bindings"
        " WHERE channel='line'").fetchone()
    assert row and row[1] == LINE_USER
    assert json.loads(decrypt(tmp_path, row[0])) == {
        "access_token": LINE_TOKEN, "channel_secret": LINE_SECRET}

    # Malformed credentials are rejected and store nothing new.
    for payload in (
        {"access_token": "short", "channel_secret": LINE_SECRET, "user_id": LINE_USER},
        {"access_token": LINE_TOKEN, "channel_secret": "has spaces!", "user_id": LINE_USER},
        {"access_token": LINE_TOKEN, "channel_secret": LINE_SECRET, "user_id": "U123"},
    ):
        assert client.put("/api/v1/my/channels/line", json=payload,
                          headers=CARL_AUTH).status_code == 422, payload
    assert client.get("/api/v1/my/channels",
                      headers=CARL_AUTH).json()["line"]["bound"] is True

    # Unauthenticated traffic never touches the binding surface.
    assert client.put("/api/v1/my/channels/line",
                      json={"access_token": LINE_TOKEN, "channel_secret": LINE_SECRET,
                            "user_id": LINE_USER}).status_code == 401

    # Unbind; deleting again is a 404, never a silent second deletion.
    assert client.delete("/api/v1/my/channels/line",
                         headers=CARL_AUTH).json() == {"channel": "line", "bound": False}
    assert client.delete("/api/v1/my/channels/line", headers=CARL_AUTH).status_code == 404
    assert client.get("/api/v1/my/channels",
                      headers=CARL_AUTH).json()["line"]["bound"] is False


def test_bound_line_owner_gets_progress_via_their_own_token(monkeypatch, tmp_path):
    app, client = _line_app(monkeypatch, tmp_path)
    assert _bind_line(client, CARL_AUTH).status_code == 200

    store = app.state.store
    mission = _line_mission_for(store, "carl")
    notifier = app.state.notify
    notifier.run_pending()  # initialize the cursor at the current head
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")

    calls = []
    _mock_line_http(monkeypatch, calls)

    assert notifier.run_pending() >= 1
    assert calls, "the owner's bound LINE push endpoint must have been called"
    # Every push rode the LINE push API with the owner's own access token ...
    assert all(call["url"] == "https://api.line.me/v2/bot/message/push" for call in calls)
    assert all(call["auth"] == f"Bearer {LINE_TOKEN}" for call in calls)
    # ... targeted the bound userId (the push payload's ``to``) ...
    assert all(call["json"].get("to") == LINE_USER for call in calls)
    # ... and carried the Chinese progress line for the completed step.
    assert any("完成" in call["json"]["messages"][0]["text"] for call in calls)

    # The audit row is honest (owner, bound user, mission) and credential-free.
    audits = [m for m in store.channel_messages("line") if m["direction"] == "OUTBOUND"]
    assert any(m["owner_id"] == "carl" and m["recipient"] == LINE_USER
               and m["mission_id"] == mission["id"] and m["status"] == "DELIVERED"
               for m in audits)
    blob = json.dumps(audits, ensure_ascii=False)
    assert LINE_TOKEN not in blob and LINE_SECRET not in blob


def test_line_without_binding_and_without_station_token_is_silently_skipped(monkeypatch, tmp_path):
    app, client = _line_app(monkeypatch, tmp_path)  # carl never binds line

    store = app.state.store
    mission = _line_mission_for(store, "carl")
    notifier = app.state.notify
    notifier.run_pending()
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")

    calls = []
    _mock_line_http(monkeypatch, calls)

    # Both paths absent: the event is consumed, nothing is pushed anywhere,
    # the loop survives and the cursor advances (no retry flood).
    assert notifier.run_pending() >= 1
    assert calls == []
    assert not [m for m in store.channel_messages("line") if m["direction"] == "OUTBOUND"]
    assert notifier.run_pending() == 0


def test_line_station_token_serves_progress_when_owner_unbound(monkeypatch, tmp_path):
    app, client = _line_app(monkeypatch, tmp_path)  # no owner binding...
    # ...but the station's own LINE credentials are in place (providers read
    # os.environ live, so flipping them here is enough).
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "station-access-token-0123456789")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "station-channel-secret-0123456789")

    store = app.state.store
    mission = _line_mission_for(store, "carl")
    notifier = app.state.notify
    notifier.run_pending()
    store.step_status(mission["id"], mission["steps"][0]["id"], "RUNNING")
    store.step_status(mission["id"], mission["steps"][0]["id"], "COMPLETED")

    calls = []
    _mock_line_http(monkeypatch, calls)

    assert notifier.run_pending() >= 1
    assert calls, "the station channel must carry progress when the owner is unbound"
    assert all(call["url"] == "https://api.line.me/v2/bot/message/push" for call in calls)
    assert all(call["auth"] == "Bearer station-access-token-0123456789" for call in calls)
    # The station channel replies to the chat that asked for the work: the
    # inbound sender's userId.
    assert all(call["json"].get("to") == LINE_USER for call in calls)
