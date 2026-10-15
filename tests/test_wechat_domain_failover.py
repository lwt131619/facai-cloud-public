"""Official-domain failover for the WeChat iLink adapter.

On 2026-10-03 stored credentials pinned ``https://ilinkai.wechat.com``, which
answered 524 on every getupdates long poll and timed out on
get_qrcode_status, while ``https://ilinkai.weixin.qq.com`` stayed healthy. Both
hosts are official and serve the identical protocol, so after sustained
gateway/transport trouble the adapter tries the other official host and adopts
it on success — without ever fabricating a success or leaking the token.
"""

import asyncio
import json

import httpx

from facai.channels.base import OutboundReply
from facai.channels.wechat import (
    ALTERNATE_BASE_URL,
    DEFAULT_BASE_URL,
    WeChatProvider,
)

DEAD = "https://ilinkai.wechat.com"
HEALTHY = "https://ilinkai.weixin.qq.com"


def test_updates_fail_over_to_the_other_official_domain_and_persist(tmp_path):
    # Start on the dead host the incident pinned in stored credentials.
    state_dir = tmp_path / "providers"
    state_dir.mkdir(parents=True)
    creds = state_dir / "wechat.env"
    creds.write_text("WECHAT_BOT_TOKEN=bot-secret\n"
                     "WECHAT_ILINK_USER_ID=bot-1\n"
                     f"WECHAT_BASE_URL={DEAD}\n", encoding="utf-8")
    provider = WeChatProvider({}, state_dir=state_dir)
    assert provider._base_url == DEAD

    seen_hosts = []

    async def handler(request):
        seen_hosts.append(request.url.host)
        if request.url.host == "ilinkai.wechat.com":
            return httpx.Response(524, text="gateway timeout")
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "cursor-1", "msgs": []})

    async def scenario():
        results = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(3):
                results.append(await provider.fetch_updates(client))
        return results

    results = asyncio.run(scenario())
    # First two are honest, retryable gateway failures on the dead host.
    assert results[0] == {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}
    assert results[1] == {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}
    # The third reaches the threshold, tries the healthy host and succeeds.
    assert results[2]["state"] == "OK"
    assert seen_hosts[2:] == ["ilinkai.wechat.com", "ilinkai.weixin.qq.com"]
    # The healthy host is adopted and persisted for later requests.
    assert provider._base_url == HEALTHY
    assert creds.read_text(encoding="utf-8").endswith(f"WECHAT_BASE_URL={HEALTHY}\n")
    assert "bot-secret" not in json.dumps(results)


def test_login_status_poll_fails_over_to_the_other_official_domain(tmp_path):
    provider = WeChatProvider({}, state_dir=tmp_path / "providers")

    async def handler(request):
        host = request.url.host
        if request.url.path.endswith("get_bot_qrcode"):
            assert host == "ilinkai.weixin.qq.com"
            return httpx.Response(200, json={"qrcode": "qr-1",
                                             "qrcode_img_content": "https://weixin.qq.com/qr"})
        if request.url.path.endswith("get_qrcode_status"):
            if host == "ilinkai.weixin.qq.com":
                raise httpx.ConnectError("dead host")
            return httpx.Response(200, json={"status": "confirmed", "bot_token": "bot-secret",
                                             "ilink_user_id": "bot-1"})
        return httpx.Response(404)

    async def scenario():
        results = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await provider.start_login(client)
            for _ in range(3):
                results.append(await provider.poll_login(client))
        return results

    results = asyncio.run(scenario())
    # Polling the dead host stays a retryable transport error, never terminal.
    assert results[0] == {"state": "RETRY", "error": "TRANSPORT_ERROR"}
    assert results[1] == {"state": "RETRY", "error": "TRANSPORT_ERROR"}
    # Third poll fails over to the other official host and authorizes.
    assert results[2] == {"state": "AUTHORIZED", "account": "bot-1"}
    assert provider._base_url == ALTERNATE_BASE_URL
    assert "bot-secret" not in json.dumps(results[:2])


def test_a_single_gateway_blip_does_not_switch_domains():
    # A lone 503/524 (a normal long-poll timeout) must not flap the base URL.
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="")
        return httpx.Response(200, json={"ret": 0, "msgs": []})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await provider.fetch_updates(client)
            second = await provider.fetch_updates(client)
            return first, second

    first, second = asyncio.run(scenario())
    assert first == {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}
    assert second["state"] == "OK"
    assert provider._base_url == DEFAULT_BASE_URL


def test_no_fabricated_success_when_both_official_hosts_fail():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot"})

    async def handler(request):
        return httpx.Response(502, text="")

    async def scenario():
        results = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(4):
                results.append(await provider.fetch_updates(client))
        return results

    results = asyncio.run(scenario())
    assert all(row == {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}
               for row in results)
    assert provider._base_url == DEFAULT_BASE_URL
    assert "bot-secret" not in json.dumps(results)


def test_send_adopts_the_other_official_domain_after_repeated_5xx():
    provider = WeChatProvider({"WECHAT_BOT_TOKEN": "bot-secret", "WECHAT_ILINK_USER_ID": "bot",
                               "WECHAT_BASE_URL": DEAD})
    assert provider._base_url == DEAD

    async def handler(request):
        if request.url.host == "ilinkai.wechat.com":
            return httpx.Response(503, text="")
        return httpx.Response(200, json={"ret": 0, "message_id": "out-1"})

    async def scenario():
        outcomes = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(3):
                try:
                    outcomes.append(await provider.send(
                        OutboundReply(channel="wechat", recipient="friend", text="hi"), client))
                except httpx.HTTPStatusError:
                    outcomes.append("HTTPStatusError")
        return outcomes

    outcomes = asyncio.run(scenario())
    assert outcomes[0] == "HTTPStatusError" and outcomes[1] == "HTTPStatusError"
    assert outcomes[2]["status"] == "SENT"
    assert provider._base_url == HEALTHY
