"""End-to-end personal WeChat path against a mock iLink server.

The provider-level tests prove each call in isolation; this one drives the whole
operator-visible flow through the real app: start a QR login, poll it to
``AUTHORIZED``, let the background monitor pick up an inbound text, then reply
through the dedicated endpoint and confirm the reply carries the original
``context_token``. Only ``httpx.AsyncClient`` is redirected to a mock transport,
so the API, store, monitor and dispatch code under test are the real ones.
"""

import json
import os
import time

import httpx
from fastapi.testclient import TestClient

import facai.channels as channels_module
import facai.channels.wechat as wechat_module
from facai.api import create_app

INBOUND = {
    "message_id": 101,
    "from_user_id": "friend-1",
    "create_time_ms": 1720000000000,
    "context_token": "ctx-1",
    "item_list": [{"type": 1, "text_item": {"text": "帮我把部署步骤整理一下"}}],
}


class MockILink:
    """Smallest iLink server the app needs: login, one update, send."""

    def __init__(self):
        self.sent = []
        self.login_polls = 0
        self.updates_served = 0

    async def handler(self, request):
        path = request.url.path
        if path.endswith("get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr-1",
                                             "qrcode_img_content": "https://weixin.qq.com/qr"})
        if path.endswith("get_qrcode_status"):
            self.login_polls += 1
            return httpx.Response(200, json={"status": "confirmed", "bot_token": "bot-secret",
                                             "ilink_user_id": "bot-1",
                                             "baseurl": "https://ilinkai.weixin.qq.com"})
        if path.endswith("getupdates"):
            self.updates_served += 1
            payload = {"ret": 0, "get_updates_buf": "cursor-1", "msgs": [INBOUND]}
            if self.updates_served == 1:
                return httpx.Response(200, json=payload)
            return httpx.Response(200, json={"ret": 0, "msgs": []})
        if path.endswith("sendmessage"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ret": 0, "message_id": "out-1"})
        return httpx.Response(404)


def test_wechat_qr_login_receive_and_reply_end_to_end(tmp_path, monkeypatch):
    mock = MockILink()
    real_async_client = httpx.AsyncClient

    # The QR flow must not write credentials into the real process env.
    env_keys = ("WECHAT_BOT_TOKEN", "WECHAT_ILINK_USER_ID", "WECHAT_BASE_URL")
    env_before = {key: os.environ.get(key) for key in env_keys}

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=httpx.MockTransport(mock.handler), **kwargs)

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", factory)
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    # Give the app its own credential mapping instead of the shared os.environ:
    # the QR flow writes the token into the provider env, and that must never
    # leak into the real process environment or into later tests. A fresh dict
    # (instead of ChannelRegistry defaulting to os.environ) keeps it isolated.
    real_build_providers = channels_module.build_providers
    monkeypatch.setattr(
        channels_module, "build_providers",
        lambda env=None, state_dir=None: real_build_providers({}, state_dir=state_dir))
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_WECHAT_POLL_INTERVAL", "0.02")
    monkeypatch.setenv("FACAI_WECHAT_ERROR_BACKOFF", "0.02")

    app = create_app(tmp_path)
    with TestClient(app) as client:
        started = client.post("/api/v1/channels/wechat/login")
        assert started.status_code == 200
        assert started.json()["state"] == "WAITING_FOR_SCAN"
        assert started.json()["qr_image"].startswith("data:image/png;base64,")

        authorized = client.get("/api/v1/channels/wechat/login")
        assert authorized.status_code == 200
        assert authorized.json() == {"state": "AUTHORIZED", "account": "bot-1"}
        assert app.state.channels.get("wechat").configured() is True

        deadline = time.time() + 10
        inbound = []
        while time.time() < deadline:
            inbound = [row for row in app.state.store.channel_messages("wechat")
                       if row["direction"] == "INBOUND"]
            if (inbound and inbound[0]["mission_id"]
                    and app.state.store.jobs(inbound[0]["mission_id"])):
                break
            time.sleep(0.02)

        assert mock.login_polls == 1
        assert len(inbound) == 1
        message = inbound[0]
        assert message["external_id"] == "101"
        assert message["text"] == "帮我把部署步骤整理一下"
        assert message["sender"] == "friend-1"
        assert message["payload"]["context_token"] == "ctx-1"
        assert message["mission_id"]

        mission = app.state.store.get(message["mission_id"])
        assert mission["original_input"] == "帮我把部署步骤整理一下"
        assert mission["agent_id"] and mission["model_id"] and mission["runtime_id"]
        # The dispatcher enqueues the whole DAG asynchronously; wait for all
        # five instead of racing it.
        deadline = time.time() + 5
        jobs = app.state.store.jobs(mission["id"])
        while time.time() < deadline and len(jobs) < 5:
            time.sleep(0.05)
            jobs = app.state.store.jobs(mission["id"])
        assert len(jobs) == 5
        assert all(job["status"] == "QUEUED" for job in jobs)
        event_types = [event["type"] for event in app.state.store.events(mission["id"])]
        assert "MISSION_CREATED" in event_types
        assert "PROMPT_CONVERTED" in event_types
        assert "DISPATCH_QUEUED" in event_types

        inbox = client.get("/api/v1/inbox").json()
        assert any(row["external_id"] == "101" for row in inbox)

        reply = client.post(
            f"/api/v1/channels/wechat/messages/{message['id']}/reply",
            json={"text": "收到，正在整理"})
        assert reply.status_code == 200
        assert reply.json()["status"] == "SENT"

        # One manual reply plus optional chat receipts (mission ack / progress).
        manual = [m for m in mock.sent
                  if not any(k in json.dumps(m, ensure_ascii=False) for k in ("已收到需求", "阶段"))]
        assert len(manual) == 1
        sent_message = manual[0]["msg"]
        assert sent_message["to_user_id"] == "friend-1"
        assert sent_message["context_token"] == "ctx-1"
        assert sent_message["item_list"][0]["text_item"]["text"] == "收到，正在整理"

        # Two audited lines: the mission ack (monitor thread, may land a beat
        # later) and the manual reply. Wait briefly for the ack row.
        deadline = time.time() + 5
        ack_rows = []
        outbound = []
        while time.time() < deadline:
            outbound = [row for row in app.state.store.channel_messages("wechat")
                        if row["direction"] == "OUTBOUND"]
            ack_rows = [row for row in outbound if "已收到需求" in row["text"]]
            if ack_rows:
                break
            time.sleep(0.05)
        assert len(ack_rows) == 1 and ack_rows[0]["status"] == "SENT"
        manual_rows = [row for row in outbound if row["external_id"] == "out-1"
                       and "已收到需求" not in row["text"]]
        assert len(manual_rows) == 1 and manual_rows[0]["status"] == "SENT"
        # channel_messages lists rows newest-first, so outbound[0] is whichever
        # line landed last (the ack when the monitor thread is slow). Assert on
        # the manual reply row itself, which the filters above isolate.
        assert manual_rows[0]["payload"]["reply_to_message"] == message["id"]
        assert "bot-secret" not in json.dumps(outbound)

    assert {key: os.environ.get(key) for key in env_keys} == env_before


def test_wechat_unbind_clears_credentials_and_restores_qr_login(tmp_path, monkeypatch):
    """Stale-credential escape hatch: unbind really clears the data-side
    credential file and the provider env keys, and POST /login then runs the
    real QR flow again instead of short-circuiting to AUTHORIZED."""
    mock = MockILink()
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=httpx.MockTransport(mock.handler), **kwargs)

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", factory)
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    real_build_providers = channels_module.build_providers
    monkeypatch.setattr(
        channels_module, "build_providers",
        lambda env=None, state_dir=None: real_build_providers({}, state_dir=state_dir))
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")

    app = create_app(tmp_path)
    credential_file = tmp_path / "providers" / "wechat.env"
    with TestClient(app) as client:
        # Authorize through the real QR flow first: only then does the login
        # endpoint short-circuit and unbind have anything to clear.
        started = client.post("/api/v1/channels/wechat/login")
        assert started.status_code == 200
        assert started.json()["state"] == "WAITING_FOR_SCAN"
        authorized = client.get("/api/v1/channels/wechat/login")
        assert authorized.json()["state"] == "AUTHORIZED"
        provider = app.state.channels.get("wechat")
        assert provider.configured() is True
        assert credential_file.is_file()

        unbound = client.post("/api/v1/channels/wechat/unbind")
        assert unbound.status_code == 200, unbound.text
        body = unbound.json()
        assert body["state"] == "UNBOUND"
        assert body["configured"] is False
        assert body["credential_file_removed"] is True
        # The honest operator note: the systemd /etc copy is outside this
        # endpoint's reach and must be cleaned by hand or it returns on restart.
        assert "/etc/facai-cloud/providers/wechat.env" in body["note"]

        assert provider.configured() is False
        assert credential_file.is_file() is False
        assert not provider.setting("WECHAT_BOT_TOKEN")
        assert not provider.setting("WECHAT_ILINK_USER_ID")
        assert not provider.setting("WECHAT_BASE_URL")
        assert provider.status().state == "UNCONFIGURED"
        # A restarted process (fresh provider, same state dir) must not come
        # back configured from the deleted file.
        assert wechat_module.WeChatProvider({}, state_dir=tmp_path / "providers").configured() is False
        unbind_events = [event for event in app.state.store.channel_events(channel="wechat")
                         if event["type"] == "CHANNEL_UNBOUND"]
        assert len(unbind_events) == 1
        assert unbind_events[0]["payload"]["account"] == "bot-1"

        # The short-circuit is gone: POST /login starts a real QR login again.
        restarted = client.post("/api/v1/channels/wechat/login")
        assert restarted.status_code == 200
        assert restarted.json()["state"] == "WAITING_FOR_SCAN"
        assert restarted.json()["qr_image"].startswith("data:image/png;base64,")
        reauthorized = client.get("/api/v1/channels/wechat/login")
        assert reauthorized.json() == {"state": "AUTHORIZED", "account": "bot-1"}
        assert provider.configured() is True
        assert credential_file.is_file()


def test_wechat_unbind_is_admin_only_and_idempotent(tmp_path, monkeypatch):
    """A self-registered tenant must not drop the operator's WeChat
    authorization (require_admin_owner); the admin call succeeds even when
    nothing is stored — an honest no-op, still UNBOUND."""
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "unbind-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        registered = client.post("/api/v1/auth/register",
                                 json={"username": "alice", "password": "alice-pass-1"})
        assert registered.status_code == 201, registered.text
        client.headers.update({"X-CSRF-Token": registered.json()["csrf_token"]})
        forbidden = client.post("/api/v1/channels/wechat/unbind")
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"] == "仅管理员可访问"
        # The refusal changed nothing: no unbind event was audited.
        assert [event for event in app.state.store.channel_events(channel="wechat")
                if event["type"] == "CHANNEL_UNBOUND"] == []

        admin = TestClient(app)
        logged = admin.post("/api/v1/auth/login",
                            json={"username": "admin", "password": "unbind-pass"})
        assert logged.status_code == 200, logged.text
        admin.headers.update({"X-CSRF-Token": logged.json()["csrf_token"]})
        unbound = admin.post("/api/v1/channels/wechat/unbind")
        assert unbound.status_code == 200, unbound.text
        body = unbound.json()
        assert body["state"] == "UNBOUND"
        assert body["configured"] is False
        assert body["credential_file_removed"] is False
