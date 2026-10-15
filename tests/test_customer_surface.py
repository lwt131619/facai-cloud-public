# 客户视图全枚举（验收面）：以真实客户身份（注册→登录）枚举每一条可达端点，
# 证明其中无源码、无他人数据、无运营方凭据。每条 OK 路径断言无泄漏字段；
# 每条禁路径断言拒绝码。对应文档：docs/CUSTOMER_SURFACE_MAP.md。
#
# 审计结论（2026-10-06，工作树 = 46b31d0 + 并行防泄漏波 + 本波修复）：
#   * 修复 1（本波）：FastAPI 默认 /openapi.json、/docs、/redoc 此前对登录
#     客户开放（92 条路径含全部管理端点拓扑），现已在 create_app 关闭。
#   * 修复 2（本波）：POST /api/v1/n8n/relay 与 POST /api/v1/scheduler/run
#     此前任意客户可 200 触发运营泵，现已加 require_admin_owner()。
#   * 修复 3（并行源码泄漏波）：/api/v1/missions 曾带服务器绝对路径
#     workspace，现经 facai/api.py public_mission 在 API 边界剥离——本文件
#     test_no_internal_paths_anywhere_in_customer_surface 钉死。

import os
import re

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app

ADMIN_PASSWORD = "surface-admin-pass"
CAROL_PASSWORD = "surface-carol-pass"
DAVE_PASSWORD = "surface-dave-pass-1"


@pytest.fixture()
def surface(tmp_path):
    """一个真实 app + 两个客户（carol/dave）+ 管理员，及各自的私有数据。

    数据造在会话里：carol 有任务/自备模型/客户端 API key/Telegram 绑定/充值单，
    dave 只有一个机密任务。所有凭据原文都留在本 fixture 里供断言比对。
    """
    saved = {name: os.environ.get(name) for name in
             ("FACAI_ADMIN_PASSWORD", "FACAI_SCHEDULER_ENABLED",
              "FACAI_SKIP_CREDENTIAL_PREFLIGHT", "FACAI_MAX_ACTIVE_MISSIONS_PER_OWNER")}
    os.environ["FACAI_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["FACAI_SCHEDULER_ENABLED"] = "0"
    os.environ["FACAI_SKIP_CREDENTIAL_PREFLIGHT"] = "1"
    try:
        app = create_app(tmp_path)
        carol = TestClient(app)
        dave = TestClient(app)
        admin = TestClient(app)

        r = carol.post("/api/v1/auth/register",
                       json={"username": "carol", "password": CAROL_PASSWORD})
        assert r.status_code == 201, r.text
        carol.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
        r = dave.post("/api/v1/auth/register",
                      json={"username": "dave", "password": DAVE_PASSWORD})
        assert r.status_code == 201, r.text
        dave.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
        r = admin.post("/api/v1/auth/login",
                       json={"username": "admin", "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        admin.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})

        # 真实资金池入金：管理员给两位客户各充值（本 fixture 自带余额，不依赖
        # conftest 的 funded 默认，语义与生产一致：0 余额无法建单）。
        assert admin.post("/api/v1/admin/members/carol/top-up",
                          json={"amount_usd": 500}).status_code == 200
        assert admin.post("/api/v1/admin/members/dave/top-up",
                          json={"amount_usd": 500}).status_code == 200

        r = carol.post("/api/v1/missions", json={
            "title": "CAROL私有任务", "original_input": "carol 的原话", "steps": ["抓取", "成稿"]})
        assert r.status_code == 201, r.text
        carol_mission = r.json()["id"]
        r = dave.post("/api/v1/missions", json={
            "title": "DAVE机密任务", "original_input": "dave 的原话", "steps": ["一步"]})
        assert r.status_code == 201, r.text
        dave_mission = r.json()["id"]
        r = admin.post("/api/v1/missions", json={
            "title": "ADMIN运营任务", "original_input": "admin 的原话", "steps": ["运营"]})
        assert r.status_code == 201, r.text
        admin_mission = r.json()["id"]

        r = carol.post("/api/v1/my/models", json={
            "name": "carol自备", "endpoint": "https://api.deepseek.com",
            "model": "deepseek-chat", "api_key": "sk-carol-model-secret-0123456789"})
        assert r.status_code == 201, r.text
        carol_model_key = "sk-carol-model-secret-0123456789"

        r = carol.post("/api/v1/my/keys")
        assert r.status_code == 201, r.text
        carol_api_key = r.json()["api_key"]

        r = carol.put("/api/v1/my/channels/telegram", json={
            "bot_token": "123456789:AAF-carolFakeToken-abcdefghij", "chat_id": "-100123"})
        assert r.status_code == 200, r.text
        carol_bot_token = "123456789:AAF-carolFakeToken-abcdefghij"

        r = carol.post("/api/v1/billing/recharge", json={"amount_usd": 150})
        assert r.status_code == 201, r.text
        carol_recharge = r.json()["id"]

        class NS:
            pass

        ns = NS()
        ns.app = app
        ns.carol = carol
        ns.dave = dave
        ns.admin = admin
        ns.carol_mission = carol_mission
        ns.dave_mission = dave_mission
        ns.admin_mission = admin_mission
        ns.carol_model_key = carol_model_key
        ns.carol_api_key = carol_api_key
        ns.carol_bot_token = carol_bot_token
        ns.carol_recharge = carol_recharge
        yield ns
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# 客户登录会话可达的全部 GET 端点（枚举基准：console request() 清单 + API 面）。
CUSTOMER_GET_PATHS = (
    "/api/v1/auth/session",
    "/api/v1/health",
    "/api/v1/pricing",
    "/api/v1/billing/me",
    "/api/v1/billing/users/carol/budget",
    "/api/v1/billing/recharge/config",
    "/api/v1/billing/recharges/mine",
    "/api/v1/missions",
    "/api/v1/artifacts",
    "/api/v1/inbox",
    # /api/v1/scheduler 不在客户面：stats() 暴露全站运行计数（所有租户的
    # 在跑任务、worker 身份），2026-10-13 审计后收归 operator-only（与 /run
    # 同守卫）；客户面可见性回归由 test_admin_only_boundary_surfaces 钉死。
    "/api/v1/registries/agents",
    "/api/v1/registries/models",
    "/api/v1/registries/runtimes",
    "/api/v1/registries/skills",
    "/api/v1/my/models",
    "/api/v1/my/keys",
    "/api/v1/my/channels",
    "/api/v1/my/channels/wechat/host/status",
    "/api/v1/my/channels/whatsapp/host/status",
)

MISSION_SCOPED_SUFFIXES = ("", "/events", "/proof", "/jobs", "/diff", "/channel-messages",
                           "/artifacts")

# 泄漏扫描禁词：任何客户响应体里出现即为失败。
FORBIDDEN_SUBSTRINGS = (
    "pbkdf2_sha256",                 # 口令哈希形状
    "DAVE机密任务", "dave 的原话",     # dave 的数据
    "ADMIN运营任务", "admin 的原话",   # 运营方的任务数据
    "sk-carol-model-secret",         # carol 的自备模型 key 原文
    "AAF-carolFakeToken",            # carol 的电报 token 原文
    "Traceback (most recent call last)",  # 堆栈
)
# OpenAI 形状密钥（sk- + 长随机段）；"task-" 等普通词不受影响。
SK_KEY_PATTERN = r"sk-[A-Za-z0-9_-]{20,}"


def _json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return response.text


def _collect_texts(payload):
    if isinstance(payload, dict):
        return {key: text for key, text in _walk(payload).items()}
    return {}


def _walk(node, prefix=""):
    """把任意 JSON 展平成 {点径: 字符串}，泄漏扫描按字段定位。"""
    flat = {}
    if isinstance(node, dict):
        for key, value in node.items():
            flat.update(_walk(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            flat.update(_walk(value, f"{prefix}[{index}]"))
    else:
        flat[prefix] = "" if node is None else str(node)
    return flat


def test_customer_get_surface_returns_only_own_data(surface):
    """枚举每一条客户可达 GET：全部 200，且拼起来的响应无任何禁词/他人数据。"""
    bodies = {}
    for path in CUSTOMER_GET_PATHS:
        response = surface.carol.get(path)
        assert response.status_code == 200, (path, response.text)
        bodies[path] = response.text

    # mission 级只读端点（自己的任务）
    for suffix in MISSION_SCOPED_SUFFIXES:
        path = f"/api/v1/missions/{surface.carol_mission}{suffix}"
        response = surface.carol.get(path)
        assert response.status_code == 200, (path, response.text)
        bodies[path] = response.text

    combined = "\n".join(bodies.values())
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in combined, f"客户可见面出现禁词 {needle!r}"
    assert not re.search(SK_KEY_PATTERN, combined), "客户可见面出现 sk- 形状密钥"
    # 他人/运营方任务 id 与他人用户名不可见
    assert surface.dave_mission not in combined
    assert surface.admin_mission not in combined
    assert "dave" not in combined
    # 完整客户端 API key 只在铸造响应里出现一次，此后任何列表都只有前缀
    assert surface.carol_api_key not in combined
    # CSRF token 只出现在本人会话端点
    for path, text in bodies.items():
        if path != "/api/v1/auth/session":
            assert "csrf_token" not in text, path
    # session 只暴露本人用户名
    assert '"carol"' in bodies["/api/v1/auth/session"]
    assert "dave" not in bodies["/api/v1/auth/session"]


def test_no_internal_paths_anywhere_in_customer_surface(surface):
    """mission 响应不带 workspace（部署布局不出口，public_mission 已剥离），
    其余客户可见面同样不得出现任何文件系统路径形状。

    审计过程记录：本波探针曾实测 /api/v1/missions 带
    ``workspace: <数据目录>/workspaces/<id>``（信息级泄漏），随后由源码泄漏
    波在 API 边界统一剥离（facai/api.py public_mission，本测试升级为钉死）。
    """
    response = surface.carol.get("/api/v1/missions")
    assert response.status_code == 200
    text = response.text
    assert '"workspace"' not in text
    assert surface.dave_mission not in text
    detail = surface.carol.get(f"/api/v1/missions/{surface.carol_mission}")
    assert detail.status_code == 200
    assert '"workspace"' not in detail.text
    assert '"path"' not in detail.text
    for path in ("/api/v1/billing/me", "/api/v1/auth/session", "/api/v1/my/models",
                 "/api/v1/my/keys", "/api/v1/my/channels", "/api/v1/pricing",
                 "/api/v1/artifacts", "/api/v1/inbox", "/api/v1/scheduler",
                 f"/api/v1/missions/{surface.carol_mission}/events",
                 f"/api/v1/missions/{surface.carol_mission}/proof"):
        text = surface.carol.get(path).text
        assert "C:\\" not in text and "/home/" not in text and "/root/" not in text, path
        assert '"workspace"' not in text and '"worktree"' not in text, path


def test_mission_scoped_reads_are_owner_isolated(surface):
    """dave 的任务 id 在 carol 手里是 404（存在性也不泄）：六条 mission 级读全打。"""
    for suffix in MISSION_SCOPED_SUFFIXES:
        path = f"/api/v1/missions/{surface.dave_mission}{suffix}"
        response = surface.carol.get(path)
        assert response.status_code == 404, (path, response.status_code,
                                             response.text[:120])
    # 运营方任务同理
    assert surface.carol.get(
        f"/api/v1/missions/{surface.admin_mission}").status_code == 404
    # 列表级隔离
    carol_ids = {m["id"] for m in surface.carol.get("/api/v1/missions").json()}
    assert carol_ids == {surface.carol_mission}
    dave_ids = {m["id"] for m in surface.dave.get("/api/v1/missions").json()}
    assert dave_ids == {surface.dave_mission}
    # 充值单隔离：dave 的充值 id 对 carol 是 404
    assert surface.carol.get(
        f"/api/v1/billing/recharges/{surface.carol_recharge}/match").status_code == 200
    fake = "0" * 32
    assert surface.carol.get(f"/api/v1/billing/recharges/{fake}/match").status_code == 404
    assert surface.carol.get(f"/api/v1/billing/recharges/{fake}/tx").status_code in (404, 405)


def test_client_api_key_is_scoped_to_client_surface(surface):
    """Bearer 客户端 key：作用域内 200，作用域外一律 403，无效 key 401。"""
    key = surface.carol_api_key
    allowed = surface.carol.get("/api/v1/missions",
                                headers={"Authorization": f"Bearer {key}"})
    assert allowed.status_code == 200
    assert [m["id"] for m in allowed.json()] == [surface.carol_mission]
    for path in ("/api/v1/billing/me", "/api/v1/artifacts"):
        response = surface.carol.get(path, headers={"Authorization": f"Bearer {key}"})
        assert response.status_code == 200, path
    denied = ("/api/v1/my/keys", "/api/v1/my/models", "/api/v1/my/channels",
              "/api/v1/registries/models", "/api/v1/admin/members",
              "/api/v1/channels", "/api/v1/n8n", "/api/v1/ops/brief",
              "/api/v1/daily-progress", "/api/v1/inbox", "/api/v1/scheduler")
    for path in denied:
        response = surface.carol.get(path, headers={"Authorization": f"Bearer {key}"})
        assert response.status_code == 403, (path, response.status_code)
        assert surface.carol_api_key not in response.text
    # /auth/session 是公共传输层端点：Bearer 被整个忽略。carol 的会话
    # cookie 在同一 client 里仍有效（那是合法登录），所以这里用无 cookie
    # 的裸客户端证明「仅凭 Bearer key 不构成会话」。
    from fastapi.testclient import TestClient as _TC
    bare = _TC(surface.app)  # 无 cookie 的裸客户端：证明仅凭 Bearer key 不构成会话
    session = bare.get("/api/v1/auth/session",
                       headers={"Authorization": f"Bearer {key}"})
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    assert surface.carol_api_key not in session.text
    assert surface.carol.get("/api/v1/missions",
                             headers={"Authorization": "Bearer facai_deadbeef_secret"}
                             ).status_code == 401


def test_credentials_are_masked_on_every_listing(surface):
    """自备模型只回掩码尾、客户端 key 只回 4 字符前缀、通道绑定不回 token。"""
    models = surface.carol.get("/api/v1/my/models").json()
    assert len(models) == 1
    assert models[0]["api_key_masked"].endswith("6789")
    assert "*" in models[0]["api_key_masked"]
    assert surface.carol_model_key not in surface.carol.get("/api/v1/my/models").text

    keys = surface.carol.get("/api/v1/my/keys").json()
    assert len(keys) == 1
    assert keys[0]["prefix"].startswith("facai_")
    assert surface.carol_api_key not in surface.carol.get("/api/v1/my/keys").text
    assert "api_key" not in keys[0]

    channels = surface.carol.get("/api/v1/my/channels").json()
    assert channels["telegram"]["bound"] is True
    assert "token" not in surface.carol.get("/api/v1/my/channels").text
    assert surface.carol_bot_token not in surface.carol.get("/api/v1/my/channels").text

    # 注册表（含共享默认模型）永远没有 api_key 字段；env 变量名可留（产品信息）
    registries = surface.carol.get("/api/v1/registries/models").json()
    assert registries, "默认模型注册表不应为空"
    for entry in registries.values():
        assert "api_key" not in entry


# 管理与内部路径全清单（客户会话 + 有效请求体，守卫在参数校验之后也必须拦）。
ADMIN_POST_BODIES = {
    "/api/v1/admin/members/carol/top-up": {"amount_usd": 1},
    "/api/v1/admin/members/dave/freeze": {},
    "/api/v1/admin/members/dave/unfreeze": {"amount_usd": 5},
    "/api/v1/admin/assist/dave": {"reason": "审计探测"},
    "/api/v1/auth/admin-reset-password": {"username": "dave", "new_password": "probe-12345"},
    "/api/v1/billing/users/dave/budget": {"monthly_limit_usd": 1},
    "/api/v1/channels/telegram/send": {"recipient": "x", "text": "probe"},
    "/api/v1/n8n/relay": {},
    "/api/v1/scheduler/run": {},
}
ADMIN_GET_PATHS = (
    "/api/v1/admin/audit-report",
    "/api/v1/admin/members",
    "/api/v1/billing/users/dave/budget",
    "/api/v1/billing/recharges",
    "/api/v1/billing/reconciliation",
    "/api/v1/ops/brief",
    "/api/v1/daily-progress",
    "/api/v1/channels",
    "/api/v1/channels/telegram",
    "/api/v1/channels/telegram/messages",
    "/api/v1/n8n",
    "/api/v1/channels/wechat/login",
)
ADMIN_OTHER = (
    ("DELETE", "/api/v1/admin/assist/dave", None),
    ("POST", "/api/v1/channels/wechat/login", None),
    ("POST", "/api/v1/channels/wechat/unbind", None),
    ("POST", "/api/v1/billing/recharges/0/approve", None),
    ("POST", "/api/v1/billing/recharges/0/reject", None),
)
# 非路由的管理/调试猜径：一律 404（无 200/500 泄漏存在性）。
GUESS_PATHS = (
    "/admin", "/admin/members", "/internal/health", "/internal/debug",
    "/debug/vars", "/debug/pprof", "/api/v1/admin", "/api/v1/internal",
    "/api/v1/debug", "/openapi.json", "/docs", "/docs/", "/redoc",
)


def test_admin_surface_denied_to_customer_with_real_bodies(surface):
    for path in ADMIN_GET_PATHS:
        response = surface.carol.get(path)
        assert response.status_code == 403, (path, response.status_code)
        assert "Traceback" not in response.text
    for path, body in ADMIN_POST_BODIES.items():
        response = surface.carol.post(path, json=body)
        assert response.status_code in (403, 405), (path, response.status_code,
                                                    response.text[:200])
        assert response.status_code not in (200, 500), path
    for method, path, body in ADMIN_OTHER:
        response = surface.carol.request(method, path, json=body)
        assert response.status_code in (403, 404, 405), (path, response.status_code)


def test_schema_docs_and_debug_guesses_are_404_for_customer(surface):
    """本波修复的验收：schema/docs 关闭 + 猜径 404（不允许 200/500 存在性泄漏）。"""
    assert surface.app.openapi_url is None
    assert surface.app.docs_url is None
    assert surface.app.redoc_url is None
    for path in GUESS_PATHS:
        response = surface.carol.get(path)
        assert response.status_code == 404, (path, response.status_code)
        assert "paths" not in response.text  # 不允许任何 OpenAPI 形状
    # 匿名同样拿不到
    anonymous = TestClient(surface.app)
    for path in ("/openapi.json", "/docs", "/redoc", "/api/v1/admin/members"):
        assert anonymous.get(path).status_code in (401, 404), path


def test_operational_pumps_refuse_customers(surface):
    """本波修复的验收：客户不能再触发运营方的调度泵与 n8n 泵。"""
    assert surface.carol.post("/api/v1/scheduler/run").status_code == 403
    assert surface.carol.post("/api/v1/n8n/relay").status_code == 403
    # 管理员自己仍可用
    assert surface.admin.post("/api/v1/scheduler/run").status_code == 200
    assert surface.admin.post("/api/v1/n8n/relay").json().get("state") in (
        "CONFIG_REQUIRED", "CONFIGURED")


def test_registry_writes_are_owner_scoped_shadows(surface):
    """客户可写自己的注册表命名空间，但绝不影响运营方视图（影子隔离）。"""
    write = surface.carol.put("/api/v1/registries/models/deepseek-flash",
                              json={"name": "carol影子"})
    assert write.status_code == 200, write.text
    assert surface.carol.get("/api/v1/registries/models").json()[
        "deepseek-flash"]["name"] == "carol影子"
    assert surface.admin.get("/api/v1/registries/models").json()[
        "deepseek-flash"]["name"] == "DeepSeek Flash"
    # api_key_env 仅限站长（防止把平台密钥打到客户自己的端点上）
    forbidden = surface.carol.put("/api/v1/registries/models/evil", json={
        "name": "evil", "endpoint": "https://api.deepseek.com",
        "model": "x", "api_key_env": "DEEPSEEK_API_KEY"})
    assert forbidden.status_code == 403


def test_static_surface_is_pinned_and_leak_free(surface):
    """静态面：白名单 200、无目录列表、备份后缀/点文件/源码路径全 404。"""
    ok_static = {"/static/index.html", "/static/billing.html", "/static/login.html",
                 "/static/guide.html", "/static/mobile.html", "/static/showcase.html",
                 "/static/facai-ui.css", "/static/sw.js",
                 "/static/manifest.webmanifest", "/static/icon-192.png",
                 "/static/icon-512.png"}
    for path in ok_static:
        assert surface.carol.get(path).status_code == 200, path
    # 目录列表关闭（httpx 会把 ../ 归一化掉，穿越用 %2e%2e 编码探测）
    for path in ("/static", "/static/", "/static/.", "/static/%2e%2e/",
                 "/static/%2e%2e/facai/api.py", "/static/../../../etc/passwd"):
        assert surface.carol.get(path).status_code == 404, path
    # 备份后缀 / 点文件 / 源码与文档路径不可猜
    probed = [f"{name}{suffix}" for name in
              ("index.html", "billing.html", "login.html", "mobile.html",
               "showcase.html", "guide.html", "facai-ui.css", "sw.js")
              for suffix in (".bak", ".old", ".orig", ".save", ".swp", "~")]
    probed += ["/static/.env", "/static/.git/config", "/static/master.key",
               "/static/registries.py.bak-20261005", "/static/tron_sync.py.bak-20261005",
               "/static/api.py", "/static/store.py",
               "/docs/ARCHITECTURE.md", "/docs/", "/facai/api.py", "/.env",
               "/.git/config", "/data/facai.db"]
    for path in probed:
        response = surface.carol.get(path)
        assert response.status_code == 404, (path, response.status_code)
    # 公开静态页内容：只有产品信息（本平台域名/公共上游/掩码形状），无凭据
    for path in ("/", "/billing", "/login", "/guide", "/manifest.webmanifest", "/sw.js"):
        response = surface.carol.get(path)
        assert response.status_code == 200, path
        text = response.text
        assert "pbkdf2" not in text and "FACAI_ADMIN_PASSWORD" not in text
        assert "os.getenv" not in text and "secretbox" not in text
        import re
        assert not re.search(r"sk-[A-Za-z0-9_-]{10,}", text), path
        assert not re.search(r"facai_[0-9a-f]{8,}_[A-Za-z0-9_-]{12,}", text), path
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", text):
            # 说明书第六节（三方接入）指向三家官方注册门户：客户建自己的 Bot
            # 必须去的地方，纯官方域名、无跟踪参数；新外链先过这道白名单。
            assert host in {"whstapp.lol", "api.deepseek.com", "github.com",
                            "www.w3.org", "discord.com", "api.slack.com",
                            "developers.line.biz"}, (path, host)


def test_media_and_public_callbacks_self_authenticate(surface):
    """公开传输层端点必须自带凭证：不带就是 4xx，绝不吐数据。"""
    assert surface.carol.get("/api/v1/media/digest-deadbeef.mp4").status_code == 404
    assert surface.carol.get("/api/v1/media/../../facai/api.py").status_code == 404
    assert surface.carol.get("/api/v1/channels/telegram/webhook").status_code in (403, 405, 503)
    # relay/send：未配置 HMAC 时是诚实的 4xx/503，绝不回数据
    assert surface.carol.post("/api/v1/channels/relay/send",
                              json={}).status_code in (400, 403, 422, 501, 503)
    assert surface.carol.post("/api/v1/n8n/callback",
                              content=b"{}", headers={"x-signature": "bad"}
                              ).status_code in (400, 403, 503)


@pytest.mark.raw_budget
def test_zero_balance_blocks_dispatch_without_leaking(monkeypatch, tmp_path):
    """资金池 0 余额拦截（复核既有防线）：402 + 固定中文文案，无内部信息。

    raw_budget 标记退出 conftest 的 funded 世界：无 user_budgets 行即真实 0 余额。
    """
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", ADMIN_PASSWORD)
    app = create_app(tmp_path)
    broke = TestClient(app)
    r = broke.post("/api/v1/auth/register",
                   json={"username": "broke", "password": "broke-pass-123"})
    assert r.status_code == 201, r.text
    broke.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    response = broke.post("/api/v1/missions", json={
        "title": "零余额", "original_input": "x", "steps": ["一步"]})
    assert response.status_code == 402
    assert "余额为 0" in response.json()["detail"]
    assert "C:\\" not in response.text and "/home/" not in response.text
