"""反代转售审计（2026-10-06）：把「客户把 FACAI 当上游反代转售给下家」的每条路径堵死或算清账。

合约：
1. key 链式铸造被堵死：API key 对 /my/keys 的铸造、列表、吊销一律 403，
   key 也不能当作账号口令经 Basic 认证升级——管理 key 只剩交互式会话一条路。
2. 每 owner 活跃（WAITING/RUNNING）mission 硬上限（FACAI_MAX_ACTIVE_MISSIONS_PER_OWNER，
   默认 10）：超限 402 中文文案，任务完成/取消即释放名额；上限按 owner 隔离。
3. mission 创建速率：每 owner 60 秒滑动窗口 ≤ 6（进程内存）：超速 429 中文文案，
   与 402 的「容量」语义文案互斥；/missions 与 /missions/auto 共享同一窗口。
4. 转售经济学：跨 owner 资源访问全清单（missions/jobs/artifacts/proof/
   audit-report）一律 404/403——转售者摸不到别人的任何资源，也看不到站台级台账。
5. 端到端：转售者烧自己的余额——client1 用 key 消费到 402 时，client2 的
   余额与台账分文不动，自己的 key 照常开工。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from facai.api import create_app

PASSWORDS = {"client1": "client-pass", "client2": "client-pass"}
MISSION = {"title": "转售任务", "original_input": "由 key 创建", "steps": ["规划", "开发"]}


def _build(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "ap-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    app = create_app(tmp_path)
    with TestClient(app) as setup:
        for username in PASSWORDS:
            response = setup.post("/api/v1/auth/register",
                                  json={"username": username, "password": PASSWORDS[username]})
            assert response.status_code in (201, 409), response.text
    return app


def _console(app, username):
    """一个持有该账号会话 cookie 与 CSRF token 的 TestClient（写请求自动带 CSRF）。"""
    client = TestClient(app)
    response = client.post("/api/v1/auth/login",
                           json={"username": username, "password": PASSWORDS[username]})
    assert response.status_code == 200, response.text
    client.csrf = response.json()["csrf_token"]
    client.headers.update({"X-CSRF-Token": client.csrf})
    return client


def _mint(console):
    response = console.post("/api/v1/my/keys", headers={"X-CSRF-Token": console.csrf})
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(api_key):
    return {"Authorization": f"Bearer {api_key}"}


# --- 1. key 不能铸造/管理 key -----------------------------------------------------


def test_api_key_cannot_mint_list_or_revoke_keys(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    created = _mint(console)
    headers = _bearer(created["api_key"])

    # 铸造、列表、吊销：key 全部 403，管理 key 只剩交互式会话一条路。
    assert console.post("/api/v1/my/keys", headers=headers).status_code == 403
    assert console.post("/api/v1/my/keys/", headers=headers).status_code == 403
    assert console.get("/api/v1/my/keys", headers=headers).status_code == 403
    assert console.delete(f"/api/v1/my/keys/{created['id']}",
                          headers=headers).status_code == 403
    # key 也不能当口令走 Basic 认证（key → 口令升级必须失败）；载体必须无
    # 会话 cookie，否则通过的是 cookie 而不是这把 key。
    import base64
    stranger = TestClient(app)
    basic_key = {"Authorization": "Basic " + base64.b64encode(
        f"client1:{created['api_key']}".encode()).decode()}
    assert stranger.get("/api/v1/my/keys", headers=basic_key).status_code == 401
    assert stranger.get("/api/v1/missions", headers=basic_key).status_code == 401

    # 三次拒绝零副作用：key 数量不变、原 key 仍在自己的 scope 内可用。
    listing = console.get("/api/v1/my/keys").json()
    assert [k["id"] for k in listing] == [created["id"]]
    assert console.get("/api/v1/missions", headers=headers).status_code == 200


# --- 2. 每 owner 活跃 mission 上限 -------------------------------------------------


def test_active_mission_cap_is_per_owner_402_with_chinese_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_MAX_ACTIVE_MISSIONS_PER_OWNER", "2")
    app = _build(monkeypatch, tmp_path)
    console1 = _console(app, "client1")
    console2 = _console(app, "client2")
    store = app.state.store

    first = console1.post("/api/v1/missions", json=MISSION)
    second = console1.post("/api/v1/missions", json=MISSION)
    assert first.status_code == 201 and second.status_code == 201, first.text

    third = console1.post("/api/v1/missions", json=MISSION)
    assert third.status_code == 402, third.text
    # 中文文案区分：容量上限，不是余额、也不是频率。
    assert "活跃任务已达上限" in third.json()["detail"]
    assert "2/2" in third.json()["detail"]
    assert "预算" not in third.json()["detail"] and "频繁" not in third.json()["detail"]
    # 402 不落库：拒绝的就是那个新任务。
    assert len(store.list("client1")) == 2

    # 上限按 owner 隔离：client1 打满，client2 自己的名额原封不动。
    assert console2.post("/api/v1/missions", json=MISSION).status_code == 201

    # 释放名额：取消第一个步骤（其余步骤被依赖传播自动 BLOCKED，任务转 FAILED
    # 终态），立刻能再建——限额锁容量，不把客户锁死在限额里。
    mission = first.json()
    head_step = mission["steps"][0]
    cancelled = console1.patch(
        f"/api/v1/missions/{mission['id']}/steps/{head_step['id']}",
        json={"status": "CANCELLED"})
    assert cancelled.status_code == 200, cancelled.text
    assert console1.post("/api/v1/missions", json=MISSION).status_code == 201


def test_default_active_cap_is_ten(monkeypatch, tmp_path):
    monkeypatch.delenv("FACAI_MAX_ACTIVE_MISSIONS_PER_OWNER", raising=False)
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    codes = []
    for i in range(10):
        response = console.post("/api/v1/missions",
                                json={**MISSION, "title": f"m{i}"})
        codes.append(response.status_code)
        if (i + 1) % 6 == 0:  # 清空速率窗口，只测容量上限这一件事
            app.state.mission_create_times["client1"].clear()
    assert codes == [201] * 10, codes
    eleventh = console.post("/api/v1/missions", json=MISSION)
    assert eleventh.status_code == 402
    assert "10/10" in eleventh.json()["detail"]


# --- 3. mission 创建速率（每 owner 60 秒滑窗 ≤ 6）----------------------------------


def test_creation_rate_limit_429_with_distinct_chinese_copy(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console1 = _console(app, "client1")
    console2 = _console(app, "client2")

    codes = [console1.post("/api/v1/missions", json=MISSION).status_code for _ in range(6)]
    assert codes == [201] * 6, codes
    seventh = console1.post("/api/v1/missions", json=MISSION)
    assert seventh.status_code == 429, seventh.text
    detail = seventh.json()["detail"]
    assert "任务创建过于频繁" in detail and "每分钟最多 6" in detail
    # 文案区分：429 是频率问题，绝不能长着 402 的「容量/预算」脸。
    assert "预算" not in detail and "已达上限" not in detail
    # 429 不落库。
    assert len(app.state.store.list("client1")) == 6

    # 每 owner 各自的窗口：client1 被限速，client2 立刻照常建。
    assert console2.post("/api/v1/missions", json=MISSION).status_code == 201


def test_auto_endpoint_shares_the_window_and_never_burns_a_plan_call(monkeypatch, tmp_path):
    """速率拒绝发生在（付费的）转换模型调用之前：超速者烧不到一分钱算力。"""
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")

    calls = []

    async def spy_complete(model, messages, client=None, timeout=30):
        calls.append(model)
        return {"status": "MODEL_COMPLETED", "label": "不应到达", "http_status": 200,
                "content": "hi", "upstream": None, "reason": None, "usage": None}
    monkeypatch.setattr("facai.api.complete", spy_complete)

    for _ in range(6):
        assert console.post("/api/v1/missions", json=MISSION).status_code == 201
    auto = console.post("/api/v1/missions/auto", json={"original_input": "第七个任务"})
    assert auto.status_code == 429
    assert "任务创建过于频繁" in auto.json()["detail"]
    assert calls == []  # 转换模型从未被调用


# --- 4. 跨 owner 资源访问全清单（key 承载——转售者的实际访问方式）-------------------


def _seed_client1_estate(app):
    """client1 名下的完整资产：任务、步骤、job、artifact、事件。"""
    store = app.state.store
    mission = store.create("客户甲的任务", "原始话", ["规划", "执行"], owner_id="client1")
    step_id = mission["steps"][0]["id"]
    job = store.enqueue_job(mission["id"], "执行提示词", step_id=step_id)
    workspace = Path(mission["workspace"])
    artifact_file = workspace / "交付物.txt"
    artifact_file.write_text("只有 client1 该看到的成果", encoding="utf-8")
    artifact_id = store.add_artifact(mission["id"], "交付物.txt", str(artifact_file))
    store.append(mission["id"], "NOTE", {"secret": "内部事件"})
    return mission, job, artifact_id


def test_cross_owner_key_gets_404_on_every_resource_and_403_on_station_internals(
        monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    mission, job, artifact_id = _seed_client1_estate(app)
    mission_id, step_id = mission["id"], mission["steps"][0]["id"]

    attacker = TestClient(app)  # 纯 key 载体：无 cookie、无会话，只有下发的 key
    attacker_key = _mint(_console(app, "client2"))["api_key"]
    headers = _bearer(attacker_key)

    # 转换模型点火探针：别人的任务绝不允许烧到一次模型调用。
    plan_calls = []

    async def spy_complete(model, messages, client=None, timeout=30):
        plan_calls.append(model)
        return {"status": "MODEL_COMPLETED", "label": "不应到达", "http_status": 200,
                "content": "hi", "upstream": None, "reason": None, "usage": None}
    monkeypatch.setattr("facai.api.complete", spy_complete)

    # 全清单：别人的资源一律 404（存在性都不泄露）。
    foreign_404 = [
        ("GET", f"/api/v1/missions/{mission_id}", None),
        ("PATCH", f"/api/v1/missions/{mission_id}/steps/{step_id}", {"status": "COMPLETED"}),
        ("GET", f"/api/v1/missions/{mission_id}/events", None),
        ("POST", f"/api/v1/missions/{mission_id}/events", {"type": "NOTE"}),
        ("GET", f"/api/v1/missions/{mission_id}/proof", None),
        ("GET", f"/api/v1/missions/{mission_id}/jobs", None),
        ("POST", f"/api/v1/missions/{mission_id}/jobs", {"prompt": "蹭算力"}),
        ("GET", f"/api/v1/missions/{mission_id}/artifacts/{artifact_id}", None),
        ("GET", f"/api/v1/missions/{mission_id}/diff", None),
        ("GET", f"/api/v1/missions/{mission_id}/channel-messages", None),
        ("GET", f"/api/v1/missions/{mission_id}/stream", None),
        ("PUT", f"/api/v1/missions/{mission_id}/binding", {"agent_id": "x"}),
        ("PUT", f"/api/v1/missions/{mission_id}/prompt", {"professional_prompt": "改"}),
        ("POST", f"/api/v1/missions/{mission_id}/opencode/prepare", None),
        ("POST", f"/api/v1/missions/{mission_id}/opencode/run", {"prompt": "跑"}),
        ("POST", f"/api/v1/missions/{mission_id}/model/complete",
         {"messages": [{"role": "user", "content": "hi"}]}),
        ("GET", f"/api/v1/jobs/{job['id']}", None),
        ("POST", f"/api/v1/jobs/{job['id']}/pause", None),
        ("POST", f"/api/v1/jobs/{job['id']}/resume", None),
        ("POST", f"/api/v1/jobs/{job['id']}/cancel", None),
        ("POST", f"/api/v1/jobs/{job['id']}/retry", None),
    ]
    for method, path, body in foreign_404:
        response = attacker.request(method, path, json=body, headers=headers)
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"

    # 列表型资源：别人的条目根本不出现在里面。
    assert attacker.get("/api/v1/missions", headers=headers).json() == []
    assert attacker.get("/api/v1/artifacts", headers=headers).json() == []

    # 站台级台账（含全站余额/对账）：key 越界 403，双重守卫的第一层。
    report = attacker.get("/api/v1/admin/audit-report", headers=headers)
    assert report.status_code == 403
    assert "该 API key 无权访问此接口" in report.json()["detail"]
    # 转换模型从未被别人的任务点火。
    assert plan_calls == []


def test_cross_owner_console_session_matches_key_refusals(monkeypatch, tmp_path):
    """交互式会话同样拿不到别人的资源：隔离在 owner 上，不在凭证形态上。"""
    app = _build(monkeypatch, tmp_path)
    mission, job, artifact_id = _seed_client1_estate(app)
    mission_id = mission["id"]

    attacker = _console(app, "client2")
    assert attacker.get(f"/api/v1/missions/{mission_id}/proof").status_code == 404
    assert attacker.get(f"/api/v1/missions/{mission_id}/artifacts/{artifact_id}"
                        ).status_code == 404
    assert attacker.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert attacker.get("/api/v1/admin/audit-report").status_code == 403
    assert "仅管理员可访问" in attacker.get("/api/v1/admin/audit-report").json()["detail"]

    # 对照组：资源对 owner 本人完好——404 是隔离，不是全员拒绝。
    owner = _console(app, "client1")
    assert owner.get(f"/api/v1/missions/{mission_id}/proof").status_code == 200
    assert owner.get(f"/api/v1/jobs/{job['id']}").status_code == 200
    artifact = owner.get(f"/api/v1/missions/{mission_id}/artifacts/{artifact_id}")
    assert artifact.status_code == 200
    assert "只有 client1 该看到的成果" in artifact.json()["content"]


# --- 5. 端到端：转售者烧自己的余额 ---------------------------------------------------


def test_reseller_burns_only_their_own_balance(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    store = app.state.store
    console1 = _console(app, "client1")
    console2 = _console(app, "client2")
    key1 = _mint(console1)["api_key"]
    key2 = _mint(console2)["api_key"]
    store.set_user_budget("client1", 10.0)   # 转售者甲：小余额
    store.set_user_budget("client2", 500.0)  # 正常客户乙：大余额

    # 甲用 key 干活并真实计费（$12 记在甲的台账上）。
    first = console1.post("/api/v1/missions", json=MISSION, headers=_bearer(key1))
    assert first.status_code == 201, first.text
    store.add_cost(first.json()["id"], "deepseek", "deepseek-flash", 12.0)

    # 甲的余额烧穿：key 建下一个任务被 402，文案是预算语义。
    blocked = console1.post("/api/v1/missions", json=MISSION, headers=_bearer(key1))
    assert blocked.status_code == 402
    assert "预算已用尽" in blocked.json()["detail"]

    # 乙的账目分文不动，key 照常开工：甲的破产不外溢。
    assert console2.post("/api/v1/missions", json=MISSION, headers=_bearer(key2)
                         ).status_code == 201
    me1 = console1.get("/api/v1/billing/me", headers=_bearer(key1)).json()
    me2 = console2.get("/api/v1/billing/me", headers=_bearer(key2)).json()
    assert me1["allowed"] is False and me1["spend"]["cost_usd"] == 12.0
    assert me2["allowed"] is True and me2["spend"]["cost_usd"] == 0.0
    # 乙的 key 也读不到甲的台账入口：转售批发对账无门。
    assert console2.get("/api/v1/admin/audit-report",
                        headers=_bearer(key2)).status_code == 403
