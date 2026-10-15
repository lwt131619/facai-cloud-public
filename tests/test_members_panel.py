"""Admin member console (会员分管面板): roster + pool orchestration.

The contract under test:

* the roster and every member action are operator-only — a registered tenant
  gets 403 on the list and on top-up/freeze/unfreeze against anyone (including
  themselves), and a client API key is refused at the middleware scope gate
  before any handler runs;
* the roster is one row per registered member with honest numbers: explicit
  budget row (None when never set), month-to-date USD spend attributed to the
  member's OWN missions only, live api-key count, and bound channels across
  both families (personal bots + hosted channels; UNBOUND does not count);
* top-up STACKS onto the existing balance (the recharge-approval semantics,
  never a replacement) and is the official entrance for the balance pool;
* freeze writes an explicit 0 row and the budget gate then refuses every NEW
  dispatch with 402; unfreeze requires the operator to state the restored
  amount and only works on a frozen member;
* the env-backed operator has no platform_users row, so member actions cannot
  target them (404).
"""

import base64

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app

pytestmark = pytest.mark.raw_budget

PASSWORD = "panel-pass-123"
ALICE_AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:alice-pass-123").decode()}
BOB_AUTH = {"Authorization": "Basic " + base64.b64encode(b"bob:bob-pass-1234").decode()}


def build(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.delenv("FACAI_DEFAULT_MONTHLY_LIMIT_USD", raising=False)
    app = create_app(tmp_path)
    # Register on a throwaway client: the register endpoint auto-logs the new
    # member in and sets THEIR session cookie, which would otherwise replace
    # the operator session on the admin client's cookie jar.
    bootstrap = TestClient(app)
    for username, password in (("alice", "alice-pass-123"), ("bob", "bob-pass-1234")):
        r = bootstrap.post("/api/v1/auth/register",
                           json={"username": username, "password": password})
        assert r.status_code in (201, 409), r.text
    client = TestClient(app)
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    # A cookie-free client for tenant calls: a session cookie outranks a Basic
    # header in the middleware, so tenant requests sharing the admin client
    # would silently act AS the operator.
    tenant = TestClient(app)
    return app, client, tenant


def seed_spend(app, owner, amount=0.6):
    """A mission owned by ``owner`` with one USD cost row in the current month."""
    mission = app.state.store.create("已花费任务", "原话", ["执行"], owner_id=owner)
    app.state.store.add_cost(mission["id"], "deepseek", "deepseek-flash", amount, 1200,
                             currency="USD")
    return mission


def by_name(rows, username):
    return next(row for row in rows if row["username"] == username)


# --- roster visibility & tenant isolation ---------------------------------------

def test_member_roster_and_actions_reject_tenants(tmp_path, monkeypatch):
    """列表与全部管理动作只属于管理员：租户 403，client API key 也进不来。"""
    app, client, tenant = build(tmp_path, monkeypatch)

    assert tenant.get("/api/v1/admin/members", headers=ALICE_AUTH).status_code == 403
    assert tenant.post("/api/v1/admin/members/alice/top-up",
                        headers=ALICE_AUTH, json={"amount_usd": 5}).status_code == 403
    assert tenant.post("/api/v1/admin/members/bob/top-up",
                        headers=ALICE_AUTH, json={"amount_usd": 5}).status_code == 403
    assert tenant.post("/api/v1/admin/members/bob/freeze", headers=ALICE_AUTH).status_code == 403
    assert tenant.post("/api/v1/admin/members/bob/unfreeze",
                        headers=ALICE_AUTH, json={"amount_usd": 1}).status_code == 403
    # Nothing leaked through the refused attempts.
    assert app.state.store.get_user_budget("alice") is None
    assert app.state.store.get_user_budget("bob") is None

    # A client API key is scoped away from /api/v1/admin/* by the middleware,
    # before any handler could look at it.
    issued = tenant.post("/api/v1/my/keys", headers=ALICE_AUTH)
    assert issued.status_code == 201, issued.text
    bearer = {"Authorization": "Bearer " + issued.json()["api_key"]}
    r = tenant.get("/api/v1/admin/members", headers=bearer)
    assert r.status_code == 403
    assert "无权访问" in r.json()["detail"]


def test_member_roster_shape_counts_and_order(tmp_path, monkeypatch):
    app, client, tenant = build(tmp_path, monkeypatch)
    store = app.state.store
    seed_spend(app, "alice", 0.6)
    seed_spend(app, "alice", 0.25)
    seed_spend(app, "bob", 1.5)

    # Live key counts; a revoked key must stop counting.
    store.create_api_key("k1", "alice", "a" * 64, "facai_aaaa")
    store.create_api_key("k2", "alice", "b" * 64, "facai_bbbb")
    store.revoke_api_key("alice", "k2")
    # Personal bot binding + hosted channel binding; a terminal UNBOUND row is
    # no longer a bound channel.
    store.upsert_channel_binding("alice", "telegram", "cipher-blob", "556677")
    store.transition_host_binding("alice", "wechat", "BINDING")
    store.transition_host_binding("alice", "whatsapp", "BINDING")
    store.transition_host_binding("alice", "whatsapp", "UNBOUND")

    r = client.get("/api/v1/admin/members")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["username"] for row in rows] == ["bob", "alice"]  # newest member first

    bob = by_name(rows, "bob")
    assert bob["spend_usd"] == pytest.approx(1.5)  # only bob's own missions
    assert bob["monthly_limit_usd"] is None        # no explicit budget row yet
    assert bob["api_keys"] == 0
    assert bob["channel_bindings"] == 0 and bob["host_bindings"] == 0 and bob["channels"] == 0
    assert bob["must_change_password"] is False
    assert bob["created_at"]

    alice = by_name(rows, "alice")
    assert alice["spend_usd"] == pytest.approx(0.85)
    assert alice["api_keys"] == 1
    assert alice["channel_bindings"] == 1
    assert alice["host_bindings"] == 1
    assert alice["channels"] == 2


# --- pool orchestration: top-up / freeze / unfreeze ------------------------------

def test_top_up_stacks_never_replaces(tmp_path, monkeypatch):
    """叠加语义：两次 top-up 5 + 2.5 必须读 7.5（与充值核准同一叠加）。"""
    app, client, tenant = build(tmp_path, monkeypatch)

    r = client.post("/api/v1/admin/members/alice/top-up", json={"amount_usd": 5})
    assert r.status_code == 200, r.text
    assert r.json() == {"username": "alice", "added_usd": 5.0, "monthly_limit_usd": 5.0}
    r = client.post("/api/v1/admin/members/alice/top-up", json={"amount_usd": 2.5})
    assert r.json()["monthly_limit_usd"] == 7.5
    assert app.state.store.get_user_budget("alice")["monthly_limit_usd"] == 7.5

    # Validation: non-positive and non-numeric amounts are refused untouched.
    for bad in ({"amount_usd": 0}, {"amount_usd": -1}, {"amount_usd": "x"}, {}):
        assert client.post("/api/v1/admin/members/alice/top-up", json=bad).status_code == 422
    assert app.state.store.get_user_budget("alice")["monthly_limit_usd"] == 7.5
    # The env-backed operator is not a member row — member actions 404 on them.
    assert client.post("/api/v1/admin/members/admin/top-up",
                       json={"amount_usd": 5}).status_code == 404


def test_freeze_blocks_dispatch_and_unfreeze_restores(tmp_path, monkeypatch):
    """冻结 = 预算 0 → 该会员一切新派发 402；解冻按操作员指定额度恢复。"""
    app, client, tenant = build(tmp_path, monkeypatch)
    client.post("/api/v1/admin/members/alice/top-up", json={"amount_usd": 5})
    ok = tenant.post("/api/v1/missions/auto", headers=ALICE_AUTH,
                     json={"original_input": "有余额开工"})
    assert ok.status_code == 201, ok.text

    frozen = client.post("/api/v1/admin/members/alice/freeze")
    assert frozen.status_code == 200 and frozen.json()["monthly_limit_usd"] == 0.0
    blocked = tenant.post("/api/v1/missions/auto", headers=ALICE_AUTH,
                          json={"original_input": "冻结后再来一单"})
    assert blocked.status_code == 402
    assert "余额为 0" in blocked.json()["detail"]

    # Unfreeze states the restored amount explicitly (选简：不落 frozen_prev 列).
    assert client.post("/api/v1/admin/members/alice/unfreeze",
                       json={}).status_code == 422  # amount 必填
    early = client.post("/api/v1/admin/members/bob/unfreeze", json={"amount_usd": 3})
    assert early.status_code == 409  # bob was never frozen
    assert client.post("/api/v1/admin/members/bob/freeze").status_code == 200

    unfrozen = client.post("/api/v1/admin/members/alice/unfreeze", json={"amount_usd": 3})
    assert unfrozen.status_code == 200, unfrozen.text
    assert unfrozen.json() == {"username": "alice", "frozen": False, "monthly_limit_usd": 3.0}
    resumed = tenant.post("/api/v1/missions/auto", headers=ALICE_AUTH,
                          json={"original_input": "解冻后继续"})
    assert resumed.status_code == 201, resumed.text
    # bob's freeze is untouched by alice's unfreeze: per-member, not global.
    assert tenant.post("/api/v1/missions/auto", headers=BOB_AUTH,
                       json={"original_input": "bob 仍然冻结"}).status_code == 402


def test_freeze_survives_as_explicit_row_for_default_limit_worlds(tmp_path, monkeypatch):
    """冻结必须是显式 0 行：即使环境默认上限非 0，冻结依然 402。"""
    app, client, tenant = build(tmp_path, monkeypatch)
    monkeypatch.setenv("FACAI_DEFAULT_MONTHLY_LIMIT_USD", "50")
    assert client.post("/api/v1/admin/members/alice/freeze").status_code == 200
    blocked = tenant.post("/api/v1/missions/auto", headers=ALICE_AUTH,
                          json={"original_input": "默认上限也压不过显式冻结"})
    assert blocked.status_code == 402
