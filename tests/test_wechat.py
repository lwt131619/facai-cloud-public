import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx

from facai.channels.base import InboundMessage, OutboundReply
from facai.channels.wechat import WeChatProvider, _qr_png_bytes


def test_qr_login_saves_credentials_only_to_private_provider_store(tmp_path):
    env = {}
    provider = WeChatProvider(env, state_dir=tmp_path / "providers")

    async def scenario():
        async def handler(request):
            if request.url.path.endswith("get_bot_qrcode"):
                assert request.url.params["bot_type"] == "3"
                return httpx.Response(200, json={"qrcode": "qr-secret", "qrcode_img_content": "https://weixin.qq.com/qr"})
            return httpx.Response(200, json={"status": "confirmed", "bot_token": "bot-secret",
                "ilink_user_id": "user-123", "baseurl": "https://ilinkai.weixin.qq.com"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            started = await provider.start_login(client)
            assert started["state"] == "WAITING_FOR_SCAN"
            assert started["qr_image"].startswith("data:image/png;base64,")
            png = base64.b64decode(started["qr_image"].split(",", 1)[1])
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            assert (await provider.poll_login(client)) == {"state": "AUTHORIZED", "account": "user-123"}

    asyncio.run(scenario())
    credential_file = tmp_path / "providers" / "wechat.env"
    assert credential_file.read_text() == (
        "WECHAT_BOT_TOKEN=bot-secret\nWECHAT_ILINK_USER_ID=user-123\n"
        "WECHAT_BASE_URL=https://ilinkai.weixin.qq.com\n")
    if os.name != "nt":
        assert credential_file.stat().st_mode & 0o777 == 0o600
    assert "bot-secret" not in json.dumps(provider.status().to_dict())


def test_qr_png_when_pil_image_backend_is_missing(monkeypatch):
    """Production 500: qrcode.make().save(format='PNG') imports qrcode.image.pil."""
    real_import = __import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "qrcode.image.pil" or (name == "qrcode.image" and fromlist and "pil" in fromlist):
            raise ImportError("No module named 'qrcode.image.pil'")
        if name in {"PIL", "PIL.Image"}:
            raise ImportError("No module named 'PIL'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded)
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "qrcode.image.pil", None)
    png = _qr_png_bytes("https://weixin.qq.com/qr", border=4)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.endswith(b"IEND\xaeB`\x82")

    provider = WeChatProvider({})

    async def handler(request):
        return httpx.Response(200, json={
            "qrcode": "qr-secret",
            "qrcode_img_content": "https://weixin.qq.com/qr",
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await provider.start_login(client)

    started = asyncio.run(scenario())
    rendered = base64.b64decode(started["qr_image"].removeprefix("data:image/png;base64,"))
    assert started["state"] == "WAITING_FOR_SCAN"
    assert rendered.startswith(b"\x89PNG\r\n\x1a\n")


def test_requirements_declare_qrcode_without_undeclared_pil_save():
    root = Path(__file__).resolve().parents[1]
    lines = [
        line.strip().lower()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    source = (root / "facai" / "channels" / "wechat.py").read_text(encoding="utf-8")
    assert any(line.startswith("qrcode>=") or line.startswith("qrcode==") for line in lines)
    assert not any(line.startswith("qrcode[pil]") or line.startswith("pillow") for line in lines)
    assert ".save(" not in source
    assert 'format="PNG"' not in source


def test_wechat_parses_only_text_and_preserves_reply_context():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "secret", "WECHAT_ILINK_USER_ID": "bot"})
    messages = provider.parse_inbound({"msgs": [
        {"message_id": 17, "from_user_id": "friend", "create_time_ms": 1720000000000,
         "context_token": "context-secret", "item_list": [
             {"type": 1, "text_item": {"text": "帮我查一下"}}, {"type": 3, "file_item": {}}]},
        {"message_id": 18, "from_user_id": "friend", "item_list": [{"type": 3}]},
    ]})
    assert len(messages) == 1
    assert messages[0].text == "帮我查一下"
    assert messages[0].metadata["context_token"] == "context-secret"


def test_wechat_parser_preserves_original_whitespace():
    original = "  第一行\n\t第二行  "
    provider = WeChatProvider({"WECHAT_ILINK_USER_ID": "bot"})
    messages = provider.parse_inbound({"msgs": [
        {"message_id": 19, "from_user_id": "friend", "item_list": [
            {"type": 1, "text_item": {"text": original}}]}
    ]})
    assert len(messages) == 1
    assert messages[0].text == original


def test_wechat_send_uses_bearer_and_context_token():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ret": 0, "message_id": "19"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await provider.send(OutboundReply(channel="wechat", recipient="friend", text="收到",
                metadata={"context_token": "context-secret"}), client)

    result = asyncio.run(scenario())
    assert result["status"] == "SENT"
    assert seen["auth"] == "Bearer bot-secret"
    assert seen["body"]["msg"]["context_token"] == "context-secret"


def test_wechat_login_rejects_untrusted_redirect_hosts():
    provider = WeChatProvider({})
    try:
        from facai.channels.wechat import _ilink_url
        _ilink_url("https://attacker.example")
    except ValueError as exc:
        assert str(exc) == "INVALID_ILINK_HOST"
    else:
        raise AssertionError("untrusted iLink host was accepted")


def test_wechat_login_rejects_non_object_qr_responses():
    provider = WeChatProvider({})
    for payload in ([], ["unexpected"], 7, "scalar", None):
        async def handler(request, payload=payload):
            return httpx.Response(200, json=payload)

        async def scenario(handler=handler):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await provider.start_login(client)

        result = asyncio.run(scenario())
        assert result == {"state": "FAILED", "error": "INVALID_QR_RESPONSE"}
        assert "qr-secret" not in json.dumps(result)


def test_wechat_poll_retries_non_object_status_responses(tmp_path):
    # A non-object status body is transient upstream noise (a gateway page), not
    # a terminal failure: the QR stays valid, so the flow must stay alive.
    for payload in ([], 7, "scalar", None):
        provider = WeChatProvider({}, state_dir=tmp_path / "providers")

        async def handler(request, payload=payload):
            if request.url.path.endswith("get_bot_qrcode"):
                return httpx.Response(200, json={"qrcode": "qr-secret",
                                                 "qrcode_img_content": "https://weixin.qq.com/qr"})
            return httpx.Response(200, json=payload)

        async def scenario(handler=handler):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await provider.start_login(client)
                return await provider.poll_login(client)

        result = asyncio.run(scenario())
        assert result == {"state": "RETRY", "error": "INVALID_LOGIN_RESPONSE"}
        assert "qr-secret" not in json.dumps(result)
        assert "bot_token" not in json.dumps(result)


def test_wechat_updates_reject_non_object_payloads():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})
    for payload in ([], ["unexpected"], 42, "scalar", None):
        async def handler(request, payload=payload):
            return httpx.Response(200, json=payload)

        async def scenario(handler=handler):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await provider.fetch_updates(client)

        result = asyncio.run(scenario())
        assert result == {"state": "FAILED", "error": "INVALID_UPSTREAM_RESPONSE", "messages": []}
        assert "bot-secret" not in json.dumps(result)


def test_wechat_send_rejects_non_object_payloads():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})
    for payload in ([], 7, "scalar", None):
        async def handler(request, payload=payload):
            return httpx.Response(200, json=payload)

        async def scenario(handler=handler):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await provider.send(
                    OutboundReply(channel="wechat", recipient="friend", text="hello"), client)

        result = asyncio.run(scenario())
        assert result["status"] == "FAILED"
        assert result["error"] == "INVALID_UPSTREAM_RESPONSE"
        assert "bot-secret" not in json.dumps(result)


def test_wechat_updates_and_send_handle_malformed_json():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})

    async def handler(request):
        return httpx.Response(200, content=b"{not valid json",
                               headers={"content-type": "application/json"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            updates = await provider.fetch_updates(client)
            sent = await provider.send(
                OutboundReply(channel="wechat", recipient="friend", text="hello"), client)
            return updates, sent

    updates, sent = asyncio.run(scenario())
    assert updates == {"state": "FAILED", "error": "INVALID_UPSTREAM_RESPONSE", "messages": []}
    assert sent["status"] == "FAILED"
    assert sent["error"] == "INVALID_UPSTREAM_RESPONSE"
    assert "bot-secret" not in json.dumps([updates, sent])


def test_wechat_long_poll_gateway_timeout_is_retryable():
    # A Cloudflare 524 (or 502/503/504) on getupdates means the long poll held
    # until the gateway gave up — normal when no message is pending. It must be
    # a retryable "wait", never a channel failure, or the monitor would flap.
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})

    for code in (502, 503, 504, 524):
        async def handler(request, code=code):
            return httpx.Response(code, text="")

        async def scenario(handler=handler):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await provider.fetch_updates(client)

        result = asyncio.run(scenario())
        assert result == {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}


def test_wechat_long_poll_returns_normalized_message_and_carries_cursor():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})
    seen = {}

    async def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "cursor-2", "msgs": [
            {"message_id": 22, "from_user_id": "friend", "item_list": [
                {"type": 1, "text_item": {"text": "开始工作"}}]}]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await provider.fetch_updates(client)
            assert first["get_updates_buf"] == "cursor-2"
            assert provider._updates_buf == ""
            provider.acknowledge_updates()
            second = await provider.fetch_updates(client)
            return first, second

    first, _ = asyncio.run(scenario())
    assert first["state"] == "OK"
    assert first["messages"][0].text == "开始工作"
    assert seen["body"]["get_updates_buf"] == "cursor-2"


def test_wechat_monitor_replays_poll_when_durable_inbox_write_fails(tmp_path, monkeypatch):
    import facai.api as api

    message = InboundMessage("wechat", "retry-before-store", "friend", "不要丢掉我", "now")

    class FakeWeChat:
        def __init__(self):
            self.calls = 0
            self.acknowledgements = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.acknowledgements == 0:
                return {"state": "OK", "messages": [message]}
            return {"state": "WAITING", "messages": []}

        def acknowledge_updates(self):
            self.acknowledgements += 1

    provider = FakeWeChat()
    app = _wechat_app(api, monkeypatch, provider, tmp_path)
    original_record = app.state.store.record_channel_message
    failures = {"remaining": 1}

    def fail_before_persist_once(*args, **kwargs):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("temporary local database failure")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(app.state.store, "record_channel_message", fail_before_persist_once)

    async def scenario():
        async with app.router.lifespan_context(app):
            for _ in range(500):
                messages = app.state.store.channel_messages("wechat")
                if (messages and messages[0]["mission_id"]
                        and provider.acknowledgements >= 1):
                    break
                await asyncio.sleep(0.02)
            assert len(messages) == 1
            assert messages[0]["text"] == "不要丢掉我"
            assert messages[0]["mission_id"]
            assert provider.calls >= 2
            assert provider.acknowledgements >= 1

    asyncio.run(scenario())


def test_wechat_monitor_isolates_bad_messages_and_does_not_record_orphan_conversion(
        tmp_path, monkeypatch):
    import facai.api as api

    class FakeWeChat:
        def __init__(self):
            self.calls = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.calls == 1:
                return {"state": "OK", "messages": [
                    InboundMessage("wechat", "empty", "friend", "   ", "now"),
                    InboundMessage("wechat", "unclaimed", "friend", "检查部署配置", "now"),
                    InboundMessage("wechat", "valid", "friend", "帮我整理发布步骤", "now"),
                ]}
            return {"state": "WAITING", "messages": []}

    provider = FakeWeChat()

    class FakeRegistry:
        def __init__(self, state_dir=None):
            pass

        def require(self, name):
            assert name == "wechat"
            return provider

    monkeypatch.setattr(api, "ChannelRegistry", FakeRegistry)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = api.create_app(tmp_path)

    original_create = app.state.store.create_mission_from_message

    def create_with_one_unclaimed(message_id, channel, **kwargs):
        if app.state.store.channel_message(message_id)["external_id"] == "unclaimed":
            return None, False
        return original_create(message_id, channel, **kwargs)

    monkeypatch.setattr(app.state.store, "create_mission_from_message", create_with_one_unclaimed)

    async def scenario():
        async with app.router.lifespan_context(app):
            for _ in range(200):
                messages = app.state.store.channel_messages(limit=10)
                by_external = {message["external_id"]: message for message in messages}
                if (set(by_external) == {"empty", "unclaimed", "valid"}
                        and by_external["empty"]["status"] == "CONVERSION_FAILED"
                        and by_external["unclaimed"]["status"] == "RETRY"
                        and by_external["valid"]["mission_id"]):
                    break
                await asyncio.sleep(0.02)
            messages = app.state.store.channel_messages(limit=10)
            by_external = {message["external_id"]: message for message in messages}
            assert set(by_external) == {"empty", "unclaimed", "valid"}
            assert by_external["empty"]["status"] == "CONVERSION_FAILED"
            assert by_external["unclaimed"]["status"] == "RETRY"
            assert by_external["valid"]["mission_id"]
            assert provider.calls >= 1

            empty_events = app.state.store.channel_events(
                message_id=by_external["empty"]["id"], channel="wechat")
            assert [event["type"] for event in empty_events] == ["CONVERSION_FAILED"]
            unclaimed_events = app.state.store.channel_events(
                message_id=by_external["unclaimed"]["id"], channel="wechat")
            assert [event["type"] for event in unclaimed_events] == ["MISSION_CREATION_FAILED"]
            valid_events = app.state.store.channel_events(
                message_id=by_external["valid"]["id"], channel="wechat")
            assert [event["type"] for event in valid_events] == ["PROMPT_CONVERTED"]

    asyncio.run(scenario())


def _wechat_app(api, monkeypatch, provider, tmp_path):
    class FakeRegistry:
        def __init__(self, state_dir=None):
            pass

        def require(self, name):
            assert name == "wechat"
            return provider

    monkeypatch.setattr(api, "ChannelRegistry", FakeRegistry)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_WECHAT_POLL_INTERVAL", "0.02")
    monkeypatch.setenv("FACAI_WECHAT_ERROR_BACKOFF", "0.02")
    return api.create_app(tmp_path)


def test_wechat_monitor_continues_after_fetch_updates_error(tmp_path, monkeypatch):
    import facai.api as api

    class FakeWeChat:
        def __init__(self):
            self.calls = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient transport failure")
            if self.calls == 2:
                return {"state": "OK", "messages": [
                    InboundMessage("wechat", "after-error", "friend", "错误之后仍可接收", "now")]}
            return {"state": "WAITING", "messages": []}

    provider = FakeWeChat()
    app = _wechat_app(api, monkeypatch, provider, tmp_path)

    async def scenario():
        async with app.router.lifespan_context(app):
            for _ in range(500):
                if app.state.store.channel_messages("wechat"):
                    break
                await asyncio.sleep(0.02)
            messages = app.state.store.channel_messages("wechat")
            assert [message["external_id"] for message in messages] == ["after-error"]
            assert provider.calls >= 2

    asyncio.run(scenario())


def test_wechat_monitor_recovers_pending_after_creation_error(tmp_path, monkeypatch):
    import facai.api as api

    class FakeWeChat:
        def __init__(self):
            self.calls = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.calls == 1:
                return {"state": "OK", "messages": [
                    InboundMessage("wechat", "recover-me", "friend", "帮我把失败的创建重试", "now")]}
            return {"state": "WAITING", "messages": []}

    provider = FakeWeChat()
    app = _wechat_app(api, monkeypatch, provider, tmp_path)
    original_create = app.state.store.create_mission_from_message
    failures = {"count": 0}

    def create_that_fails_once(message_id, channel, **kwargs):
        if failures["count"] == 0:
            failures["count"] += 1
            raise RuntimeError("transient database error")
        return original_create(message_id, channel, **kwargs)

    monkeypatch.setattr(app.state.store, "create_mission_from_message", create_that_fails_once)

    async def scenario():
        async with app.router.lifespan_context(app):
            for _ in range(500):
                if app.state.store.list():
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            missions = app.state.store.list()
            messages = app.state.store.channel_messages("wechat")
            assert failures["count"] == 1
            assert len(missions) == 1
            assert len(messages) == 1
            assert messages[0]["mission_id"] == missions[0]["id"]
            events = app.state.store.channel_events(
                message_id=messages[0]["id"], channel="wechat")
            assert [event["type"] for event in events].count("PROCESSING_RETRY") == 1
            assert app.state.store.pending_inbound_messages("wechat") == []

    asyncio.run(scenario())


def test_wechat_monitor_uses_unified_pipeline_and_keeps_context_token(tmp_path, monkeypatch):
    import facai.api as api

    class FakeWeChat:
        def __init__(self):
            self.calls = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.calls == 1:
                return {"state": "OK", "messages": [
                    InboundMessage("wechat", "ctx-msg", "friend", "帮我整理发布步骤", "now",
                                   metadata={"context_token": "context-secret"}),
                    InboundMessage("wechat", "blank-msg", "friend", "   ", "now"),
                ]}
            return {"state": "WAITING", "messages": []}

    provider = FakeWeChat()
    app = _wechat_app(api, monkeypatch, provider, tmp_path)

    async def scenario():
        async with app.router.lifespan_context(app):
            for _ in range(500):
                if len(app.state.store.channel_messages("wechat")) >= 2:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
            by_external = {message["external_id"]: message
                           for message in app.state.store.channel_messages("wechat")}
            assert set(by_external) == {"ctx-msg", "blank-msg"}
            assert by_external["ctx-msg"]["payload"]["context_token"] == "context-secret"
            assert by_external["ctx-msg"]["mission_id"]
            assert by_external["blank-msg"]["status"] == "CONVERSION_FAILED"
            blank_events = app.state.store.channel_events(
                message_id=by_external["blank-msg"]["id"], channel="wechat")
            assert [event["type"] for event in blank_events] == ["CONVERSION_FAILED"]
            pending = app.state.store.pending_inbound_messages("wechat")
            assert pending == []

    asyncio.run(scenario())


def test_wechat_retries_deferred_dispatch_after_binding_is_added(tmp_path, monkeypatch):
    import facai.api as api

    class FakeWeChat:
        def __init__(self):
            self.calls = 0

        def configured(self):
            return True

        async def fetch_updates(self):
            self.calls += 1
            if self.calls == 1:
                return {"state": "OK", "messages": [
                    InboundMessage("wechat", "deferred-binding", "friend", "稍后派发", "now")
                ]}
            return {"state": "WAITING", "messages": []}

    provider = FakeWeChat()
    app = _wechat_app(api, monkeypatch, provider, tmp_path)
    app.state.registries.catalogs["models"].clear()

    async def scenario():
        async with app.router.lifespan_context(app):
            message = None
            for _ in range(500):
                rows = app.state.store.channel_messages("wechat")
                if rows and rows[0]["mission_id"]:
                    message = rows[0]
                    break
                await asyncio.sleep(0.02)
            assert message is not None
            mission_id = message["mission_id"]
            assert app.state.store.jobs(mission_id) == []
            app.state.registries.register("models", "local", {
                "provider": "local", "endpoint": "http://127.0.0.1:11434/v1",
                "model": "example"})
            for _ in range(500):
                jobs = app.state.store.jobs(mission_id)
                if len(jobs) == 5:  # full step DAG
                    break
                await asyncio.sleep(0.02)
            assert len(jobs) == 5
            assert all(job["status"] == "QUEUED" for job in jobs)
            assert provider.calls >= 1
            event_types = [event["type"] for event in app.state.store.events(mission_id)]
            assert event_types.count("DISPATCH_QUEUED") == 5  # one per DAG step

    asyncio.run(scenario())


def test_wechat_qr_credentials_survive_a_restart(tmp_path):
    """The docs promise the QR token is restored from the provider file.

    A restart constructs a fresh provider; it must reload the token/user/base
    URL written during authorization so polling and sending still work.
    """
    state_dir = tmp_path / "providers"
    first = WeChatProvider({}, state_dir=state_dir)

    async def scenario():
        async def handler(request):
            if request.url.path.endswith("get_bot_qrcode"):
                return httpx.Response(200, json={"qrcode": "qr-1",
                                                 "qrcode_img_content": "https://weixin.qq.com/qr"})
            return httpx.Response(200, json={"status": "confirmed", "bot_token": "bot-secret",
                                             "ilink_user_id": "bot-1",
                                             "baseurl": "https://ilinkai.weixin.qq.com"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await first.start_login(client)
            return await first.poll_login(client)

    assert asyncio.run(scenario()) == {"state": "AUTHORIZED", "account": "bot-1"}

    restarted = WeChatProvider({}, state_dir=state_dir)
    assert restarted.configured() is True
    assert restarted.setting("WECHAT_BOT_TOKEN") == "bot-secret"
    assert restarted.setting("WECHAT_ILINK_USER_ID") == "bot-1"
    assert restarted._base_url == "https://ilinkai.weixin.qq.com"
    assert restarted.status().state == "CONFIGURED"


def test_poll_login_single_flight_and_transient_retry():
    """Concurrent pollers must not both hit upstream, and transport errors
    during status polling must be retryable, never terminal FAILED."""
    import asyncio
    provider = WeChatProvider({})
    long_poll_started = asyncio.Event()

    async def handler(request):
        if request.url.path.endswith("get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr-1",
                                             "qrcode_img_content": "https://weixin.qq.com/qr"})
        if long_poll_started.is_set():
            raise httpx.ConnectError("second concurrent poll rejected")
        long_poll_started.set()
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"status": "wait"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await provider.start_login(client)
            first, second = await asyncio.gather(
                provider.poll_login(client), provider.poll_login(client))
            return first, second

    first, second = asyncio.run(scenario())
    assert first == {"state": "wait"}  # the in-flight long-poll result
    assert second == {"state": "wait"}  # optimistic no-new-info from the lock


def test_poll_login_transport_error_is_retryable():
    import asyncio
    provider = WeChatProvider({})

    async def handler(request):
        if request.url.path.endswith("get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr-1",
                                             "qrcode_img_content": "https://weixin.qq.com/qr"})
        raise httpx.ConnectError("network blip")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await provider.start_login(client)
            return await provider.poll_login(client)

    result = asyncio.run(scenario())
    assert result == {"state": "RETRY", "error": "TRANSPORT_ERROR"}
