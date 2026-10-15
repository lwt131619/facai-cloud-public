"""渠内审批（Grant×渠道相乘点）——彻底无审核口径（2026-10-07 操作员指令）。

契约现状：

* 派发时授权门对 grant 缺失/过期/耗尽**不再等待任何批准**：平台核即时补签
  mission grant（任务仍在库中即合法），落 ``GRANT_AUTO_RENEWED`` 审计事件后
  照常入队执行。渠道零打扰——没有审批提示、没有 15 分钟窗口、没有
  ``pending_approvals`` 行；
* ``request_channel_approval``/``handle_channel_approval`` 代码保留，但派发
  路径永不产生活动审批行；「批准」/「取消」拦截管线只在测试显式播种行时
  触发，其原契约照旧钉住：FIFO 消费一行、Owner 核签发 action 级 grant
  （uses=剩余步骤、TTL 86400s）、「取消」作废、过期行绝不签发；
* 隔离边界是 owner：行查找与消费都按消息归属者走，bob 的「批准」永远碰不到
  alice 的行；
* 无活动行的「批准」是普通文本，照旧走任务转换。

签发权永远在 Owner 核：引擎强制 ``issued_by=FACAI_OWNER``，任何回复都成不了
第二个授权根。
"""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.authority import FACAI_OWNER
from facai.authority_gate import GATE_BACKEND, MISSION_RUN, engine, reset_engine
from facai.registries import Registries
from facai.scheduler import Scheduler
from facai.store import Store

PASSWORD = "approve-pass-123"
PROMPT_MARK = "需要你的授权才能继续"


@pytest.fixture(autouse=True)
def fresh_gate(monkeypatch):
    """审批测试各自从干净的门开始：单例引擎跨测试存活，残留 grant 或模式
    覆盖会污染无关套件（test_authority_gate_wiring 同款纪律）。"""
    monkeypatch.delenv("FACAI_GATE_MODE", raising=False)
    monkeypatch.delenv("FACAI_MISSION_GRANT_TTL_SECONDS", raising=False)
    reset_engine()
    yield
    reset_engine()


class StubAdapter:
    """执行探针：记录每次调用，返回一个良构的完成结果，绝不跑外部命令。"""

    def __init__(self):
        self.calls = []

    def execute(self, mission_id, root, prompt, model_ref=None, timeout=300,
                on_output=None, **kwargs):
        from pathlib import Path

        self.calls.append(mission_id)
        workspace = Path(root) / "workspaces" / mission_id
        workspace.mkdir(parents=True, exist_ok=True)
        return {"workspace": str(workspace), "status": "OPENCODE_COMPLETED",
                "exit_code": 0, "stdout": "ok", "stderr": ""}


class FakeTelegram:
    """站内 telegram 替身：configured 即真，send 只进内存。"""

    name = "telegram"
    implemented = True
    required_env = ()

    def __init__(self, sends):
        self.sends = sends

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
        self.sends.append({"recipient": reply.recipient, "text": reply.text})
        return {"channel": self.name, "status": "SENT",
                "external_id": f"out-{len(self.sends)}"}


def build(tmp_path, monkeypatch):
    """Booted app + telegram 替身 + 已登录（CSRF）客户端。调度器关闭：入队的
    job 停在 QUEUED，由测试手动跑 Scheduler，时序完全可控。"""
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    sends = []
    stub = StubAdapter()
    app = create_app(tmp_path, opencode_adapter=stub)
    app.state.channels.providers["telegram"] = FakeTelegram(sends)
    client = TestClient(app)
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    return app, client, sends, stub


def channel_mission(app, external_id, title="部署整理", text="帮我把部署步骤整理一下",
                    owner_id="admin"):
    """Store 级建一条渠道来源任务（签发 mission grant），返回 (message_id, mission_id)。"""
    store = app.state.store
    message_id = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id=external_id,
        sender="10086", kind="text", text=text, owner_id=owner_id)
    mission_id, created = store.create_mission_from_message(
        message_id, "telegram", title=title, original_input=text,
        labels=["开发"], professional_prompt="专业提示", conversion_payload={},
        owner_id=owner_id)
    assert created is True
    return message_id, mission_id


def revoke_mission_grant(mission_id):
    """把创建时签发的 mission grant 撤销 —— 构造授权门 DENY 的现场。"""
    grant = next(g for g in engine().grants.values()
                 if g.action_key == f"mission:{mission_id}")
    assert engine().revoke_grant(grant.grant_id, revoked_by=FACAI_OWNER) is True
    return grant.grant_id


def mission_grant(mission_id):
    return next((g for g in engine().grants.values()
                 if g.action_key == f"mission:{mission_id}"), None)


def action_grants(mission_id):
    """针对该任务的一次性 action 级 grant（审批签发的形态）。"""
    return [g for g in engine().grants.values()
            if g.action_key == f"{GATE_BACKEND}:{MISSION_RUN}:{mission_id}"]


def deny_deferred(store, mission_id):
    return [e for e in store.events(mission_id)
            if e["type"] == "DISPATCH_DEFERRED"
            and e["payload"].get("reason") == "GRANT_REQUIRED"]


def auto_renewed(store, mission_id):
    return [e for e in store.events(mission_id)
            if e["type"] == "GRANT_AUTO_RENEWED"]


def post_webhook(client, update_id, text):
    return client.post("/api/v1/channels/telegram/webhook",
                       json={"update_id": update_id, "message": {"text": text}})


def dispatch_message(client, message_id):
    return client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch")


def prompt_sends(sends):
    return [s for s in sends if PROMPT_MARK in s["text"]]


def backdate_row_expired(store, approval_id):
    """把审批行的过期时间改到过去（绕过 15 分钟窗口，不 sleep）。"""
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE pending_approvals SET expires_at='2020-01-01T00:00:00+00:00'"
                   " WHERE id=?", (approval_id,))


# --- 1) GRANT_REQUIRED → 平台核即时补签，任务照常派发，渠道零打扰 ----------------

def test_grant_required_auto_renews_and_dispatches(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1001")
    revoke_mission_grant(mission_id)

    response = dispatch_message(client, message_id)
    assert response.status_code == 201, response.text  # 路由固定 201
    assert response.json()["status"] in ("WAITING", "RUNNING")

    # 补签即生效：mission grant 回来了，Owner 核签发、TTL 一个任务日。
    grant = mission_grant(mission_id)
    assert grant is not None and grant.issued_by == FACAI_OWNER
    assert (grant.expires_at - grant.issued_at).total_seconds() == pytest.approx(86400)

    # 任务照常入队，补签动作留痕（GRANT_AUTO_RENEWED，带拒绝原因）。
    assert len(store.jobs(mission_id)) == 1
    renewed = auto_renewed(store, mission_id)
    assert len(renewed) == 1
    assert renewed[0]["payload"]["reason"] == "GRANT_REQUIRED"
    assert "自动补签" in renewed[0]["payload"]["note"]

    # 渠道零打扰：无审批提示、无审批行、无 DEFERRED。
    assert prompt_sends(sends) == []
    assert store.pending_approvals_for_owner("admin") == []
    assert deny_deferred(store, mission_id) == []


def test_valid_grant_dispatch_never_prompts(tmp_path, monkeypatch):
    """守门不误伤：grant 完好时派发照旧入队，不产生审批行、不打扰客户。"""
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-2001")

    response = dispatch_message(client, message_id)
    assert response.status_code == 201, response.text  # 路由固定 201
    assert response.json()["status"] in ("WAITING", "RUNNING")
    assert len(store.jobs(mission_id)) == 1
    assert store.pending_approvals_for_owner("admin") == []
    assert prompt_sends(sends) == []
    assert auto_renewed(store, mission_id) == []


# --- 2) 监控重放幂等：不重复入队、不重复补签、永远零打扰 --------------------------

def test_replay_after_auto_renew_is_quiet_and_idempotent(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1002")
    revoke_mission_grant(mission_id)

    first = dispatch_message(client, message_id)
    assert first.status_code == 201
    assert len(store.jobs(mission_id)) == 1

    again = dispatch_message(client, message_id)
    assert again.status_code == 201
    # 步骤已被 job 全覆盖 → 派发早退，不重复入队（路由把内部 QUEUED 报为 WAITING）。
    assert again.json()["status"] in ("WAITING", "RUNNING", "QUEUED")

    assert len(store.jobs(mission_id)) == 1
    assert len(auto_renewed(store, mission_id)) == 1
    assert prompt_sends(sends) == []
    assert store.pending_approvals_for_owner("admin") == []


# --- 3) 播种审批行：回复「批准」→ 签发一次性 grant + 重派 + adapter 被调 ----------

def test_approval_reply_signs_grant_and_redispatches(tmp_path, monkeypatch):
    """审批行只会来自显式播种（store 直建）；拦截管线原契约照旧。"""
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1003")
    revoke_mission_grant(mission_id)
    store.create_pending_approval(mission_id, "admin", "telegram",
                                  message_id=message_id)

    reply = post_webhook(client, 3001, "批准")
    assert reply.status_code == 200, reply.text
    payload = reply.json()
    result = payload["results"][0]
    assert result["status"] == "APPROVAL_GRANTED"
    assert result["mission_id"] == mission_id

    # Owner 核签发的一次性 action 级 grant：uses>=1、TTL 一个任务日。
    grants = action_grants(mission_id)
    assert len(grants) == 1
    grant = grants[0]
    assert grant.issued_by == FACAI_OWNER and grant.uses_left >= 1
    assert (grant.expires_at - grant.issued_at).total_seconds() == pytest.approx(86400)

    # 重派落了地：一个 job 入队（单步任务），执行真正调到适配器。
    assert len(store.jobs(mission_id)) == 1
    scheduler = Scheduler(store, stub, Registries(store), tmp_path,
                          worker_id="approval-worker")
    scheduler.run_pending()
    job = store.jobs(mission_id)[0]
    assert job["status"] == "COMPLETED"
    assert store.get(mission_id)["steps"][0]["status"] == "COMPLETED"

    # 审批行已消费；审批/事件/回执三处留痕齐全。
    rows = store.pending_approvals_for_owner("admin")
    assert rows == []
    assert store.pending_approval(result["approval_id"])["status"] == "CONSUMED"
    assert [e for e in store.events(mission_id) if e["type"] == "APPROVAL_GRANTED"]
    inbound = store.channel_message_by_external_id("telegram", "update:3001", owner_id="admin")
    assert inbound["status"] == "APPROVAL_GRANTED"
    assert [e for e in store.channel_events(message_id=inbound["id"])
            if e["type"] == "APPROVAL_GRANTED"]
    assert any("已收到批准" in s["text"] for s in sends)


def test_dispatch_probe_never_burns_the_one_use(tmp_path, monkeypatch):
    """派发探针必须只读：批准 grant 在派发（ALLOW）后仍能撑起执行。"""
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1004")
    revoke_mission_grant(mission_id)
    store.create_pending_approval(mission_id, "admin", "telegram",
                                  message_id=message_id)
    assert post_webhook(client, 3002, "批准").status_code == 200

    grant = action_grants(mission_id)[0]
    # 探针若复用 authorize（会消费），这里的已用次数在派发后就会是 1，
    # 执行期直接 GRANT_EXHAUSTED —— 适配器一次都调不到。
    assert engine()._use_count.get(grant.grant_id, 0) == 0
    Scheduler(store, stub, Registries(store), tmp_path,
              worker_id="probe-worker").run_pending()
    assert engine()._use_count.get(grant.grant_id) == 1
    assert store.jobs(mission_id)[0]["status"] == "COMPLETED"


# --- 4) 回复「取消」→ 播种行作废；随后的派发自动补签执行 -------------------------

def test_cancel_reply_voids_request_and_dispatch_then_runs(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1005")
    revoke_mission_grant(mission_id)
    approval_id = store.create_pending_approval(mission_id, "admin", "telegram",
                                                message_id=message_id)["id"]

    reply = post_webhook(client, 3003, "取消")
    assert reply.status_code == 200, reply.text
    assert reply.json()["results"][0]["status"] == "APPROVAL_CANCELLED"

    assert store.pending_approval(approval_id)["status"] == "CANCELLED"
    assert store.pending_approvals_for_owner("admin") == []
    assert [e for e in store.events(mission_id) if e["type"] == "APPROVAL_CANCELLED"]
    inbound = store.channel_message_by_external_id("telegram", "update:3003", owner_id="admin")
    assert [e for e in store.channel_events(message_id=inbound["id"])
            if e["type"] == "APPROVAL_CANCELLED"]
    assert action_grants(mission_id) == []      # 取消绝不签发
    assert store.jobs(mission_id) == []         # 也绝不派发
    assert any("已取消" in s["text"] for s in sends)

    # 无审核：取消只作废播种行，不挡任务——派发自动补签并照常执行。
    assert dispatch_message(client, message_id).status_code == 201
    assert len(store.jobs(mission_id)) == 1
    assert len(auto_renewed(store, mission_id)) == 1
    assert prompt_sends(sends) == []
    assert store.pending_approvals_for_owner("admin") == []


# --- 5) 过期 → 不签发 ------------------------------------------------------------

def test_expired_approval_never_signs(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1006")
    revoke_mission_grant(mission_id)
    approval_id = store.create_pending_approval(mission_id, "admin", "telegram",
                                                message_id=message_id)["id"]
    backdate_row_expired(store, approval_id)

    # 读时判定：过期行对 owner 不可见、不可消费；清扫后状态如实落 EXPIRED。
    assert store.pending_approvals_for_owner("admin") == []
    assert store.consume_pending_approval(approval_id, "admin") is None
    assert store.expire_pending_approvals() == 1
    assert store.pending_approval(approval_id)["status"] == "EXPIRED"

    reply = post_webhook(client, 3004, "批准")
    assert reply.status_code == 200, reply.text
    assert action_grants(mission_id) == []                  # 未签发
    assert not [e for e in store.events(mission_id)
                if e["type"] == "APPROVAL_GRANTED"]
    assert store.jobs(mission_id) == []                     # 未重派
    # 无活动行的「批准」照旧走任务转换（普通文本语义），不信口丢弃。
    inbound = store.channel_message_by_external_id("telegram", "update:3004", owner_id="admin")
    assert inbound["mission_id"] and inbound["mission_id"] != mission_id


def test_store_expiry_window_and_sweep(tmp_path):
    """store 层：TTL 窗口内可见可消费，过窗即失效，清扫如实落 EXPIRED。"""
    store = Store(tmp_path)
    mission = store.create("窗口任务", "原话", ["执行"], owner_id="admin")
    row = store.create_pending_approval(mission["id"], "admin", "telegram",
                                        ttl_seconds=0.05)
    assert row["status"] == "PENDING"
    time.sleep(0.12)
    assert store.pending_approvals_for_owner("admin") == []
    assert store.consume_pending_approval(row["id"], "admin") is None
    assert store.expire_pending_approvals() == 1
    assert store.pending_approval(row["id"])["status"] == "EXPIRED"
    # 已终结的行不会被二次清扫，也不会复活。
    assert store.expire_pending_approvals() == 0


# --- 6) bob 批准不了 alice 的任务（owner 隔离） ----------------------------------

def test_bob_cannot_approve_alices_mission(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    for username, password in (("alice", "alice-pass-123"), ("bob", "bob-pass-1234")):
        assert client.post("/api/v1/auth/register",
                           json={"username": username, "password": password}).status_code in (201, 409)

    alice_message, alice_mission = channel_mission(app, "tg-alice", owner_id="alice")
    alice_row = store.create_pending_approval(alice_mission, "alice", "telegram",
                                              message_id=alice_message)
    revoke_mission_grant(alice_mission)

    # bob（经真实管线的其他 owner —— webhook 归属 admin，同理隔离）回复「批准」：
    # 自己名下没有活动行 → 走普通转换，alice 的行原封不动。
    reply = post_webhook(client, 3005, "批准")
    assert reply.status_code == 200, reply.text
    assert store.pending_approval(alice_row["id"])["status"] == "PENDING"
    assert action_grants(alice_mission) == []
    assert store.jobs(alice_mission) == []

    # store 边界：bob 的消费/作废都拿不到行；alice 自己可以。
    assert store.pending_approvals_for_owner("bob") == []
    assert store.consume_pending_approval(alice_row["id"], "bob") is None
    assert store.cancel_pending_approval(alice_row["id"], "bob") is None
    assert store.consume_pending_approval(alice_row["id"], "alice") is not None


# --- 7) 重复「批准」只消费一行 ----------------------------------------------------

def test_second_approval_within_window_consumes_nothing_more(tmp_path, monkeypatch):
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    message_id, mission_id = channel_mission(app, "tg-1007")
    revoke_mission_grant(mission_id)
    store.create_pending_approval(mission_id, "admin", "telegram",
                                  message_id=message_id)
    assert post_webhook(client, 3006, "批准").status_code == 200
    assert store.pending_approvals_for_owner("admin") == []  # 唯一一行已被消费
    assert len(action_grants(mission_id)) == 1

    # 第二次「批准」：没有活动行 → 不再消费、不再签发，照旧转普通任务。
    assert post_webhook(client, 3007, "批准").status_code == 200
    assert len(action_grants(mission_id)) == 1
    assert len([e for e in store.events(mission_id)
                if e["type"] == "APPROVAL_GRANTED"]) == 1


def test_one_reply_consumes_exactly_one_row(tmp_path, monkeypatch):
    """两条活动审批行（两个任务）：一次「批准」只消费最早一行、只签一个 grant。"""
    app, client, sends, stub = build(tmp_path, monkeypatch)
    store = app.state.store
    _, mission_a = channel_mission(app, "tg-a", title="任务甲")
    time.sleep(0.01)  # created_at 精度内保证先后
    _, mission_b = channel_mission(app, "tg-b", title="任务乙")
    revoke_mission_grant(mission_a)
    revoke_mission_grant(mission_b)
    row_a = store.create_pending_approval(mission_a, "admin", "telegram")
    row_b = store.create_pending_approval(mission_b, "admin", "telegram")

    assert post_webhook(client, 3008, "批准").status_code == 200

    statuses = {row["id"]: store.pending_approval(row["id"])["status"]
                for row in (row_a, row_b)}
    assert sorted(statuses.values()) == ["CONSUMED", "PENDING"]  # 只消费一行
    granted = [m for m in (mission_a, mission_b) if action_grants(m)]
    assert len(granted) == 1
    consumed_mission = (mission_a if statuses[row_a["id"]] == "CONSUMED" else mission_b)
    assert granted == [consumed_mission]
    assert store.jobs(consumed_mission)          # 被批准的任务重派了
    other = mission_b if consumed_mission == mission_a else mission_a
    assert store.jobs(other) == []               # 另一个仍在等
