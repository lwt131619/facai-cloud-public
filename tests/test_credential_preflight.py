"""凭证预检：绑一次 token 前先向平台验证（签发权在 Owner 核）。

绑定端点（PUT /my/channels/telegram|line）在落库前用客户凭证真实调上游只读
接口——格式合法 ≠ token 有效。本文件用 httpx.MockTransport 打桩证明：

* telegram getMe 200 且 ok=true / line bot info 200 → 才绑定成功，且请求语义
  （GET、URL、Bearer 头、5s 超时）与上游接口一致；
* 401/404 等非成功应答 → 422「token 无效（… 返回 …）」且一个字节都不落库；
* 传输层异常（超时/断网）→ 422「无法验证 token（网络原因）」；
* 凭证永不出现在任何响应或错误里，channel_secret 根本不上路；
* FACAI_SKIP_CREDENTIAL_PREFLIGHT=1 整体跳过预检（离线测试/极端网络），
  连一个 AsyncClient 都不构造。
"""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app

PASSWORD = "preflight-pass"
CAROL_AUTH = {"Authorization": "Basic " + base64.b64encode(b"carol:carol-pass-123").decode()}

TG_TOKEN = "111222333:APreflightBotToken_abc123XYZ"
TG_CHAT = "556677"
LINE_TOKEN = "lt-preflight-access-token-0123456789abcdef"
LINE_SECRET = "ls-preflight-channel-secret-0123456789abcdef"
LINE_USER = "U0123456789abcdef0123456789abcdef"

TG_GETME = f"https://api.telegram.org/bot{TG_TOKEN}/getMe"
LINE_BOT_INFO = "https://api.line.me/v2/bot/info"


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    # 站级通道凭证一律摘除；skip 开关也复位——本文件默认预检必须真实发生。
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
                 "LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET",
                 "FACAI_SKIP_CREDENTIAL_PREFLIGHT"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(tmp_path)
    with TestClient(app) as bootstrap:
        r = bootstrap.post("/api/v1/auth/register",
                           json={"username": "carol", "password": "carol-pass-123"})
        assert r.status_code in (201, 409), r.text
    return app, TestClient(app)


def _mock_upstream(monkeypatch, handler):
    """同款打桩（httpx.MockTransport）：AsyncClient 一律走桩并记录请求语义。

    返回 (client_kwargs, requests)：前者记录每次 AsyncClient 构造参数（断言
    5s 超时），后者记录每个上游请求的 method/URL/Authorization。装桩位置是
    facai.api.httpx.AsyncClient——与既有绑定测试同一全局模块属性；这个文件里
    NOTIFY / 调度器 / n8n 旁路全部关闭，命中桩的只会是预检本身。
    """
    client_kwargs, requests = [], []
    real_client = httpx.AsyncClient

    def recording_handler(request):
        requests.append({"method": request.method, "url": str(request.url),
                         "auth": request.headers.get("authorization")})
        return handler(request)

    def mock_client(*args, **kwargs):
        client_kwargs.append(dict(kwargs))
        # setdefault：覆盖式重打桩的 wrapper（后写胜出）会先注入自己的
        # transport，这里不得反抢。
        kwargs.setdefault("transport", httpx.MockTransport(recording_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", mock_client)
    return client_kwargs, requests


def _bind_telegram(client, token=TG_TOKEN, chat=TG_CHAT):
    return client.put("/api/v1/my/channels/telegram",
                      json={"bot_token": token, "chat_id": chat}, headers=CAROL_AUTH)


def _bind_line(client, token=LINE_TOKEN, secret=LINE_SECRET, user=LINE_USER):
    return client.put("/api/v1/my/channels/line",
                      json={"access_token": token, "channel_secret": secret,
                            "user_id": user}, headers=CAROL_AUTH)


def _unbound(client, channel):
    return client.get("/api/v1/my/channels", headers=CAROL_AUTH).json()[channel]["bound"] is False


# --- telegram：getMe 预检 --------------------------------------------------------


def test_telegram_bind_only_after_getme_confirms(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"id": 111222333,
                                                                "username": "carol_bot"}})

    client_kwargs, requests = _mock_upstream(monkeypatch, handler)
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_telegram(client)
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "telegram" and r.json()["bound"] is True
    assert client.get("/api/v1/my/channels",
                      headers=CAROL_AUTH).json()["telegram"]["bound"] is True
    # 请求语义：GET getMe、不带 Authorization（token 只在 URL 里）、5s 超时。
    assert requests == [{"method": "GET", "url": TG_GETME, "auth": None}]
    assert client_kwargs and client_kwargs[0].get("timeout") == 5.0
    assert TG_TOKEN not in r.text  # 绑定响应也绝不回显 token


@pytest.mark.parametrize("status,body,reason", [
    (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, "Unauthorized"),
    (404, {"ok": False, "error_code": 404, "description": "Not Found"}, "Not Found"),
    # 200 但 ok≠true 也不算数：ok=true 是唯一的有效确认。
    (200, {"ok": False, "error_code": 400, "description": "bad request"},
     "bad request"),
])
def test_telegram_rejected_token_422_carries_upstream_reason(monkeypatch, tmp_path,
                                                             status, body, reason):
    _, requests = _mock_upstream(
        monkeypatch, lambda request: httpx.Response(status, json=body))
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_telegram(client)
    assert r.status_code == 422
    assert r.json()["detail"] == f"token 无效（Telegram 返回 {reason}）"
    assert TG_TOKEN not in r.text
    assert _unbound(client, "telegram")  # 一个字节都不落库
    assert requests == [{"method": "GET", "url": TG_GETME, "auth": None}]


def test_telegram_plain_non_json_answer_falls_back_to_http_status(monkeypatch, tmp_path):
    _mock_upstream(monkeypatch, lambda request: httpx.Response(503, text="unavailable"))
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_telegram(client)
    assert r.status_code == 422
    assert r.json()["detail"] == "token 无效（Telegram 返回 HTTP 503）"
    assert _unbound(client, "telegram")


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_telegram_network_failure_is_422_network_reason(monkeypatch, tmp_path, error):
    def handler(request):
        raise error("mock transport failure", request=request)

    _mock_upstream(monkeypatch, handler)
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_telegram(client)
    assert r.status_code == 422
    assert r.json()["detail"] == "无法验证 token（网络原因）"
    assert TG_TOKEN not in r.text
    assert _unbound(client, "telegram")  # 验证不了 ≠ 验证过：不落库


# --- line：/v2/bot/info 预检（同构语义）-------------------------------------------


def test_line_bind_only_after_bot_info_confirms(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, json={"userId": "Ubot", "displayName": "carol-bot"})

    client_kwargs, requests = _mock_upstream(monkeypatch, handler)
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_line(client)
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "line" and r.json()["bound"] is True
    # 请求语义：GET bot info、Bearer=access_token、5s 超时；
    # channel_secret 无法独立验证，必须根本不上路。
    assert requests == [{"method": "GET", "url": LINE_BOT_INFO,
                         "auth": f"Bearer {LINE_TOKEN}"}]
    assert client_kwargs and client_kwargs[0].get("timeout") == 5.0
    assert LINE_SECRET not in json.dumps(requests)  # secret 从未出现在请求里
    assert LINE_TOKEN not in r.text and LINE_SECRET not in r.text


def test_line_rejected_token_422_carries_upstream_reason(monkeypatch, tmp_path):
    body = {"message": "Authentication failed due to invalid authorization header"}
    _mock_upstream(monkeypatch, lambda request: httpx.Response(401, json=body))
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_line(client)
    assert r.status_code == 422
    assert r.json()["detail"] == f"token 无效（LINE 返回 {body['message']}）"
    assert LINE_TOKEN not in r.text and LINE_SECRET not in r.text
    assert _unbound(client, "line")


def test_line_non_json_answer_falls_back_to_http_status(monkeypatch, tmp_path):
    _mock_upstream(monkeypatch, lambda request: httpx.Response(500, text="oops"))
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_line(client)
    assert r.status_code == 422
    assert r.json()["detail"] == "token 无效（LINE 返回 HTTP 500）"
    assert _unbound(client, "line")


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_line_network_failure_is_422_network_reason(monkeypatch, tmp_path, error):
    def handler(request):
        raise error("mock transport failure", request=request)

    _mock_upstream(monkeypatch, handler)
    _app_, client = _app(monkeypatch, tmp_path)

    r = _bind_line(client)
    assert r.status_code == 422
    assert r.json()["detail"] == "无法验证 token（网络原因）"
    assert LINE_TOKEN not in r.text
    assert _unbound(client, "line")


# --- 预检失败绝不碰既有绑定 --------------------------------------------------------


def test_failed_preflight_never_touches_an_existing_binding(monkeypatch, tmp_path):
    _, requests = _mock_upstream(
        monkeypatch, lambda request: httpx.Response(
            200, json={"ok": True, "result": {"id": 111222333}}))
    _app_, client = _app(monkeypatch, tmp_path)
    assert _bind_telegram(client).status_code == 200

    # 覆盖式重打桩（monkeypatch 后写胜出）：网络断掉时的重绑必须整体失败。
    real_client = httpx.AsyncClient

    def dead_handler(request):
        raise httpx.ConnectError("network gone", request=request)

    def dead_client(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(dead_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("facai.api.httpx.AsyncClient", dead_client)

    r = _bind_telegram(client, token="111222333:ARotatedToken_newvalue99", chat="889900")
    assert r.status_code == 422
    assert r.json()["detail"] == "无法验证 token（网络原因）"
    # 旧绑定原封不动：chat_id 与凭据都不曾被半途轮换。
    listing = client.get("/api/v1/my/channels", headers=CAROL_AUTH).json()["telegram"]
    assert listing["bound"] is True and listing["chat_id"] == TG_CHAT
    assert requests == [{"method": "GET", "url": TG_GETME, "auth": None}]


# --- skip 开关 --------------------------------------------------------------------


def test_skip_env_var_bypasses_preflight_without_any_upstream_call(monkeypatch, tmp_path):
    def handler(request):  # 只要敢发起任何上游调用，测试当场炸出 500
        raise AssertionError(f"preflight must not call upstream when skipped: {request.url}")

    client_kwargs, requests = _mock_upstream(monkeypatch, handler)
    _app_, client = _app(monkeypatch, tmp_path)
    # _app 会把 skip 开关复位成默认（预检开启），所以开在 _app 之后。
    monkeypatch.setenv("FACAI_SKIP_CREDENTIAL_PREFLIGHT", "1")

    assert _bind_telegram(client).status_code == 200
    assert _bind_line(client).status_code == 200
    assert requests == [] and client_kwargs == []  # 连 AsyncClient 都没构造过
    listing = client.get("/api/v1/my/channels", headers=CAROL_AUTH).json()
    assert listing["telegram"]["bound"] is True and listing["telegram"]["chat_id"] == TG_CHAT
    assert listing["line"]["bound"] is True and listing["line"]["chat_id"] == LINE_USER


def test_other_skip_values_do_not_bypass(monkeypatch, tmp_path):
    _mock_upstream(monkeypatch, lambda request: httpx.Response(
        401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}))
    _app_, client = _app(monkeypatch, tmp_path)
    monkeypatch.setenv("FACAI_SKIP_CREDENTIAL_PREFLIGHT", "0")  # 只有字面 "1" 才跳过

    assert _bind_telegram(client).status_code == 422
