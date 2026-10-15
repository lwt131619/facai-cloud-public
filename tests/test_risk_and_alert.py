import pytest

pytestmark = pytest.mark.raw_budget

"""Risk annotation layer (pure labeling, never gating) + 80% soft threshold.

Two contracts under test:

- ``risk_level`` is display-only. ActionIR labels itself from the frozen
  action table (explicit pass wins), CapabilityGrant inherits the label of
  the ActionIR it was issued for (default low), and every audit decision
  record carries the label. NOTHING in the ALLOW/DENY path reads it: the
  decisions-versus-labels test below is the pin.
- The 80% soft threshold reminds, never gates. ``spend_alert`` fires exactly
  once per (owner, billing month) — the ``user_alerts`` row is the claim —
  when the governing spend (max of ledger and priced estimate) sits at or
  above 80% of the limit but strictly below it; budget_gate attaches it to
  every ALLOW, /billing/me surfaces it, and the channel ack reply appends it.
"""

import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.authority import (
    DENY,
    FACAI_OWNER,
    OWNER_GATE,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    ActionIR,
    AuthorizationEngine,
    risk_for_action,
)
from facai.billing import (
    SPEND_ALERT_TEXT,
    SPEND_ALERT_THRESHOLD,
    budget_gate,
    effective_monthly_limit,
    spend_alert,
)

PASSWORD = "risk-alert-pass-123"


def build(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.delenv("FACAI_DEFAULT_MONTHLY_LIMIT_USD", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    return app, client


def seed_spend(app, amount=0.0, owner="admin", tokens=0,
               input_tokens=None, output_tokens=None):
    """One mission owned by ``owner`` with one USD cost row this month."""
    mission = app.state.store.create("已花费任务", "原话", ["执行"], owner_id=owner)
    app.state.store.add_cost(mission["id"], "deepseek", "deepseek-flash",
                             amount, tokens, currency="USD",
                             input_tokens=input_tokens, output_tokens=output_tokens)
    return mission


# --- risk_level: the annotation table -------------------------------------------

@pytest.mark.parametrize("action,expected", [
    ("shell.exec", RISK_HIGH),
    ("docker.run", RISK_HIGH),
    ("filesystem.write", RISK_MEDIUM),
    ("network.*", RISK_MEDIUM),
    ("network.fetch", RISK_MEDIUM),      # family prefix, as in CAPABILITY_ACTIONS
    ("secret.request", RISK_MEDIUM),
    ("provider.invoke", RISK_LOW),
    ("mission.run", RISK_LOW),
    ("model.complete", RISK_LOW),        # unmapped actions stay at the low default
])
def test_action_ir_risk_level_defaults_to_the_frozen_mapping(action, expected):
    assert risk_for_action(action) == expected
    assert ActionIR(actor="opencode", backend="opencode",
                    action=action).risk_level == expected


def test_explicit_risk_level_wins_over_the_mapping():
    action = ActionIR(actor="opencode", backend="opencode", action="provider.invoke",
                      risk_level=RISK_HIGH)
    assert action.risk_level == RISK_HIGH
    # An explicitly empty/falsy label still falls back to the mapping.
    assert ActionIR(actor="opencode", backend="opencode",
                    action="shell.exec", risk_level=None).risk_level == RISK_HIGH


def test_grant_risk_defaults_low_and_inherits_from_action_ir():
    eng = AuthorizationEngine()
    plain = eng.issue_grant("opencode", "*", issued_by=FACAI_OWNER)
    assert plain.risk == RISK_LOW
    shell = eng.issue_grant(
        "opencode", "opencode:shell.exec", issued_by=FACAI_OWNER,
        action=ActionIR(actor="opencode", backend="opencode", action="shell.exec"))
    assert shell.risk == RISK_HIGH
    invoke = eng.issue_grant(
        "opencode", "facai-native:provider.invoke", issued_by=FACAI_OWNER,
        action=ActionIR(actor="facai-native-planner", backend="facai-native",
                        action="provider.invoke"))
    assert invoke.risk == RISK_LOW


def test_audit_decision_records_carry_risk_level_on_allow_and_deny():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    denied = eng.authorize(ActionIR(actor="opencode", backend="opencode",
                                    action="shell.exec"))
    assert denied["decision"] == DENY
    assert denied["risk_level"] == RISK_HIGH
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER)
    allowed = eng.authorize(ActionIR(actor="opencode", backend="opencode",
                                     action="shell.exec"))
    assert allowed["decision"] == "ALLOW"
    assert allowed["risk_level"] == RISK_HIGH
    assert eng.decisions[-1]["risk_level"] == RISK_HIGH
    mission_run = eng.authorize(ActionIR(actor="opencode", backend="facai-native",
                                         action="mission.run"))
    assert mission_run["risk_level"] == RISK_LOW


def test_risk_labels_never_change_any_decision():
    """The annotation is inert: identical grant matrices decide identically
    whether the labels come from the table or an explicit override."""
    actions = [
        ("opencode", "shell.exec", None),
        ("opencode", "shell.exec", RISK_LOW),      # deliberate mislabel: no effect
        ("facai-native", "provider.invoke", None),
        ("facai-native", "provider.invoke", RISK_HIGH),  # deliberate mislabel
        ("opencode", "docker.run", None),
    ]
    for labeled in (False, True):
        eng = AuthorizationEngine(mode=OWNER_GATE)
        for actor, verb, override in actions:
            kwargs = {"risk_level": override} if (labeled and override) else {}
            eng.authorize(ActionIR(actor=actor, backend=actor, action=verb, **kwargs))
        # Nothing granted: every action denies with the same reason.
        assert [d["decision"] for d in eng.decisions] == [DENY] * len(actions)
        assert all(d["reason"] == "GRANT_REQUIRED" for d in eng.decisions)
        # A subject-wide action grant allows exactly the matching family.
        eng.issue_grant("opencode", "opencode:", issued_by=FACAI_OWNER)
        verdicts = [eng.authorize(ActionIR(actor=actor, backend=actor,
                                           action=verb,
                                           **({"risk_level": override}
                                              if (labeled and override) else {})))
                    for actor, verb, override in actions]
        assert [v["decision"] for v in verdicts] == \
            ["ALLOW", "ALLOW", DENY, DENY, "ALLOW"]


# --- 80% soft threshold: billing layer ------------------------------------------

def test_spend_alert_fires_exactly_once_per_billing_month(tmp_path, monkeypatch):
    app, _client = build(tmp_path, monkeypatch)
    store = app.state.store
    store.set_user_budget("admin", 100)
    seed_spend(app, amount=85)

    first = spend_alert(store, "admin")
    assert first is not None
    assert first["text"] == "余额即将用尽，请及时充值"
    assert first["threshold"] == SPEND_ALERT_THRESHOLD == 0.8
    assert first["spend_usd"] == pytest.approx(85)
    assert first["limit_usd"] == 100
    # The store holds exactly one dedupe row for this billing month.
    with sqlite3.connect(store.database) as db:
        rows = db.execute("SELECT owner_id, month, sent_at FROM user_alerts"
                          " WHERE owner_id='admin'").fetchall()
    assert len(rows) == 1
    assert rows[0][2] is not None
    assert store.user_alert_sent("admin", first["month"]) is not None

    # The second call is the dedupe loser: no second reminder, no second row.
    assert spend_alert(store, "admin") is None
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM user_alerts").fetchone()[0] == 1


def test_spend_alert_threshold_and_hard_gate_boundary(tmp_path, monkeypatch):
    # Below 80%: silent, and nothing is recorded.
    app, _client = build(tmp_path / "below", monkeypatch)
    app.state.store.set_user_budget("admin", 100)
    seed_spend(app, amount=79)
    assert spend_alert(app.state.store, "admin") is None
    assert app.state.store.user_alert_sent("admin", _month()) is None

    # Exactly at 80%: fires (the threshold is inclusive).
    app2, _ = build(tmp_path / "at", monkeypatch)
    app2.state.store.set_user_budget("admin", 100)
    seed_spend(app2, amount=80)
    fired = spend_alert(app2.state.store, "admin")
    assert fired is not None and fired["spend_usd"] == pytest.approx(80)

    # At 100% the HARD gate refuses; the soft reminder must not double-speak.
    app3, _ = build(tmp_path / "over", monkeypatch)
    app3.state.store.set_user_budget("admin", 100)
    seed_spend(app3, amount=100)
    assert spend_alert(app3.state.store, "admin") is None

    # Limit 0 (no credited balance): nothing to approach, never alerts.
    app4, _ = build(tmp_path / "zero", monkeypatch)
    seed_spend(app4, amount=500)
    assert effective_monthly_limit(app4.state.store, "admin") == 0
    assert spend_alert(app4.state.store, "admin") is None


def test_spend_alert_governs_on_the_larger_of_ledger_and_estimate(tmp_path, monkeypatch):
    """A ledger recorded at 0 with a real token split still alerts: the priced
    estimate ($0.4M in × $95 + $0.2M out × $218 = $81.60) crosses 80% of $100."""
    app, _client = build(tmp_path, monkeypatch)
    store = app.state.store
    store.set_user_budget("admin", 100)
    seed_spend(app, amount=0, input_tokens=400_000, output_tokens=200_000)
    alert = spend_alert(store, "admin")
    assert alert is not None
    assert alert["spend_usd"] == pytest.approx(81.6)


def _month():
    from facai.billing import month_key
    return month_key()


# --- 80% soft threshold: gate, /billing/me and the channel reply -----------------

def test_budget_gate_attaches_alert_on_allow_only(tmp_path, monkeypatch):
    app, _client = build(tmp_path, monkeypatch)
    store = app.state.store
    store.set_user_budget("admin", 100)
    seed_spend(app, amount=85)

    gate = budget_gate("admin", store)
    assert gate["allowed"] is True
    assert gate["alert"]["text"] == SPEND_ALERT_TEXT
    # Second gate run: allowed, but the month's reminder is spent.
    assert budget_gate("admin", store)["alert"] is None

    # Cross the limit ($85 + $20 = $105): DENY carries the refusal, alert None.
    seed_spend(app, amount=20)
    denied = budget_gate("admin", store)
    assert denied["allowed"] is False
    assert "预算已用尽" in denied["reason"]
    assert denied["alert"] is None


def test_billing_me_carries_the_alert_once(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    app.state.store.set_user_budget("admin", 100)
    seed_spend(app, amount=90)

    me = client.get("/api/v1/billing/me").json()
    assert me["allowed"] is True
    assert me["alert"]["text"] == SPEND_ALERT_TEXT
    again = client.get("/api/v1/billing/me").json()
    assert again["alert"] is None


BASIC = {"Authorization": "Basic " + base64.b64encode(
    f"admin:{PASSWORD}".encode()).decode()}


def record_inbound(app, external_id, text="帮我做一个喝水小工具"):
    return app.state.store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id=external_id,
        sender="42", recipient="9", text=text, payload={"update_id": external_id})


class StubProvider:
    name, implemented = "telegram", True

    def __init__(self):
        self.replies = []

    def configured(self):
        return True

    async def send(self, reply):
        self.replies.append(reply)
        return {"status": "SENT", "external_id": "stub-1"}


def test_channel_ack_reply_appends_the_alert_once(tmp_path, monkeypatch):
    """The mission-creating ack carries the reminder inline on the FIRST
    crossing; the next mission's ack (same billing month) stays clean."""
    app, client = build(tmp_path, monkeypatch)
    store = app.state.store
    store.set_user_budget("admin", 100)
    seed_spend(app, amount=85)
    stub = StubProvider()
    app.state.channels.providers["telegram"] = stub

    first_id = record_inbound(app, "update:alert-1")
    first = client.post(f"/api/v1/channels/telegram/messages/{first_id}/dispatch",
                        headers=BASIC)
    assert first.status_code in (200, 201), first.text
    assert len(stub.replies) == 1
    ack = stub.replies[0].text
    assert "已收到需求并创建任务" in ack
    assert SPEND_ALERT_TEXT in ack  # attached to the reply, not a second message

    # Same billing month, another mission: no second reminder anywhere.
    second_id = record_inbound(app, "update:alert-2", text="再做一个记账工具")
    second = client.post(f"/api/v1/channels/telegram/messages/{second_id}/dispatch",
                         headers=BASIC)
    assert second.status_code in (200, 201), second.text
    assert len(stub.replies) == 2
    assert SPEND_ALERT_TEXT not in stub.replies[1].text
    assert "已收到需求并创建任务" in stub.replies[1].text


def test_channel_ack_without_threshold_stays_clean(tmp_path, monkeypatch):
    """Below 80% the ack is exactly what it always was — no alert line."""
    app, client = build(tmp_path, monkeypatch)
    store = app.state.store
    store.set_user_budget("admin", 100)
    seed_spend(app, amount=10)
    stub = StubProvider()
    app.state.channels.providers["telegram"] = stub

    message_id = record_inbound(app, "update:quiet")
    response = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                           headers=BASIC)
    assert response.status_code in (200, 201), response.text
    assert len(stub.replies) == 1
    assert SPEND_ALERT_TEXT not in stub.replies[0].text
