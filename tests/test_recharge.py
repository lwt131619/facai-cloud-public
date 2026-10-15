"""USDT (TRC20) recharge flow: request window, txid, admin approval credits.

Policy the operator set: one TRC20 cold-wallet address (from the
FACAI_USDT_TRC20_ADDRESS env, never in the repo), $100 minimum per recharge,
a 5-minute payment window per request, and admin approval that credits the
amount onto the user's explicit monthly budget.
"""

import base64
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.store import Store

ADDRESS = "TLRCZgWxGNDBw7pYJdomK1RsowSLsUvc8Z"  # the operator's published cold wallet
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:pay-pass").decode()}


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "pay-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    app = create_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/v1/auth/register", json={"username": "client9", "password": "tenant-pass"})
    assert r.status_code in (201, 409)
    client.cookies.clear()  # register doubles as login; tests below auth by Basic
    return client, app


def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _session_client(app, user, pw):
    """One identity per client, authenticated the way a browser writes.

    Basic credentials traverse every request the origin receives (including a
    cross-site form post), so the API accepts them for reads only; writes need
    a session cookie plus its CSRF token. A separate client per identity keeps
    the session cookie of one user from shadowing another's Basic header.
    """
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert login.status_code == 200, login.text
    return client, {"X-CSRF-Token": login.json()["csrf_token"]}


def test_config_publishes_address_and_policy(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    r = client.get("/api/v1/billing/recharge/config", headers=_basic("admin", "pay-pass"))
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": True, "network": "TRC20", "address": ADDRESS,
                    "min_usd": 100.0, "window_seconds": 300}


def test_recharge_enforces_minimum_and_window(monkeypatch, tmp_path):
    _client9, app = _client(monkeypatch, tmp_path)
    client, headers = _session_client(app, "admin", "pay-pass")
    r = client.post("/api/v1/billing/recharge", json={"amount_usd": 50}, headers=headers)
    assert r.status_code == 422 and "100" in r.json()["detail"]
    r = client.post("/api/v1/billing/recharge", json={"amount_usd": 250}, headers=headers)
    assert r.status_code == 201
    body = r.json()
    assert body["address"] == ADDRESS and body["network"] == "TRC20"
    assert body["expires_at"] > body["created_at"]


def test_payer_attaches_txid_and_admin_credits_budget(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    client.post("/api/v1/auth/register", json={"username": "payer1", "password": "payer-pass"})
    client.cookies.clear()
    payer, payer_headers = _session_client(app, "payer1", "payer-pass")
    admin, admin_headers = _session_client(app, "admin", "pay-pass")
    r = payer.post("/api/v1/billing/recharge", json={"amount_usd": 300}, headers=payer_headers)
    rid = r.json()["id"]
    # someone else cannot attach a txid to it
    assert admin.post(f"/api/v1/billing/recharges/{rid}/tx", json={"txid": "0x" + "a" * 40},
                      headers=admin_headers).status_code == 404
    att = payer.post(f"/api/v1/billing/recharges/{rid}/tx", json={"txid": "0x" + "b" * 40},
                     headers=payer_headers)
    assert att.status_code == 200 and att.json()["txid"].startswith("0x")
    # admin approves -> the amount stacks onto the payer's explicit monthly budget
    approved = admin.post(f"/api/v1/billing/recharges/{rid}/approve", headers=admin_headers)
    assert approved.status_code == 200
    assert approved.json()["monthly_limit_usd"] == 300.0
    # second recharge stacks on top instead of overwriting
    r2 = payer.post("/api/v1/billing/recharge", json={"amount_usd": 150},
                    headers=payer_headers).json()
    again = admin.post(f"/api/v1/billing/recharges/{r2['id']}/approve", headers=admin_headers)
    assert again.json()["monthly_limit_usd"] == 450.0
    # the budget gate now sees the explicit limit
    assert app.state.store.get_user_budget("payer1")["monthly_limit_usd"] == 450.0


def test_double_decision_is_rejected_and_tenant_isolation(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    client.post("/api/v1/auth/register", json={"username": "payer2", "password": "payer-pass"})
    client.cookies.clear()
    payer, payer_headers = _session_client(app, "payer2", "payer-pass")
    admin, admin_headers = _session_client(app, "admin", "pay-pass")
    rid = payer.post("/api/v1/billing/recharge", json={"amount_usd": 120},
                     headers=payer_headers).json()["id"]
    assert admin.post(f"/api/v1/billing/recharges/{rid}/approve",
                      headers=admin_headers).status_code == 200
    assert admin.post(f"/api/v1/billing/recharges/{rid}/reject",
                      headers=admin_headers).status_code == 409
    # another tenant cannot see or decide it
    assert payer.post(f"/api/v1/billing/recharges/{rid}/approve",
                      headers=payer_headers).status_code == 403


def test_channel_off_when_address_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "pay-pass")
    monkeypatch.delenv("FACAI_USDT_TRC20_ADDRESS", raising=False)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    app = create_app(tmp_path / "off")
    client, headers = _session_client(app, "admin", "pay-pass")
    r = client.get("/api/v1/billing/recharge/config", headers=_basic("admin", "pay-pass"))
    assert r.status_code == 200 and r.json()["enabled"] is False  # honest, and no console error
    r = client.post("/api/v1/billing/recharge", json={"amount_usd": 100}, headers=headers)
    assert r.status_code == 201 and r.json()["address"] == ""  # honest empty, never a fake address


def test_operator_internals_hidden_from_tenants(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    client.post("/api/v1/auth/register", json={"username": "client8", "password": "tenant-pass"})
    client.cookies.clear()
    for path in ("/api/v1/channels", "/api/v1/daily-progress",
                 "/api/v1/n8n", "/api/v1/ops/brief"):
        r = client.get(path, headers=_basic("client8", "tenant-pass"))
        assert r.status_code == 403, (path, r.status_code)
    # admin still passes; tenant inbox stays owner-scoped (their own rows only)
    r = client.get("/api/v1/channels", headers=_basic("admin", "pay-pass"))
    assert r.status_code == 200
    assert client.get("/api/v1/inbox", headers=_basic("client8", "tenant-pass")).json() == []


def test_recharge_chain_match_endpoint(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    user, user_headers = _session_client(app, "client9", "tenant-pass")
    admin_hdr = _basic("admin", "pay-pass")

    rc = user.post("/api/v1/billing/recharge", json={"amount_usd": 250},
                   headers=user_headers).json()
    rid = rc["id"]

    # no txid yet → chain_seen false
    r = user.get(f"/api/v1/billing/recharges/{rid}/match",
                 headers=_basic("client9", "tenant-pass"))
    assert r.status_code == 200
    body = r.json()
    assert body["txid_attached"] is False and body["chain_seen"] is False

    # another tenant can never see someone else's recharge
    client.post("/api/v1/auth/register", json={"username": "client10", "password": "tenant-pass"})
    client.cookies.clear()  # register doubles as login; the session cookie would shadow Basic
    other = client.get(f"/api/v1/billing/recharges/{rid}/match", headers=_basic("client10", "tenant-pass"))
    assert other.status_code in (403, 404)

    # txid attached but transfer not yet ingested → chain_seen false
    user.post(f"/api/v1/billing/recharges/{rid}/tx",
              json={"txid": "matchtest" + "0" * 48}, headers=user_headers)
    body = user.get(f"/api/v1/billing/recharges/{rid}/match",
                    headers=_basic("client9", "tenant-pass")).json()
    assert body["txid_attached"] is True and body["chain_seen"] is False

    # transfer lands on chain (sync ingests it) → chain_seen true with status
    store = app.state.store
    store.record_chain_transfer("matchtest" + "0" * 48, "TXpayer", ADDRESS, 250.0,
                                "USDT", 1_700_000_000_000, {})
    body = user.get(f"/api/v1/billing/recharges/{rid}/match",
                    headers=_basic("client9", "tenant-pass")).json()
    assert body["chain_seen"] is True and body["chain_status"] == "UNMATCHED"

    # admin can view any user's match status
    r = client.get(f"/api/v1/billing/recharges/{rid}/match", headers=admin_hdr)
    assert r.status_code == 200 and r.json()["chain_seen"] is True


def test_expired_recharge_releases_txid_and_a_new_request_can_claim_it(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    client.post("/api/v1/auth/register", json={"username": "payer3", "password": "payer-pass"})
    client.cookies.clear()
    payer, payer_headers = _session_client(app, "payer3", "payer-pass")
    store = app.state.store
    txid = "0x" + "c" * 40

    r1 = payer.post("/api/v1/billing/recharge", json={"amount_usd": 300},
                    headers=payer_headers).json()
    assert payer.post(f"/api/v1/billing/recharges/{r1['id']}/tx", json={"txid": txid},
                      headers=payer_headers).status_code == 200

    # The payment window plus its settlement grace pass with nothing on chain.
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE recharges SET expires_at=? WHERE id=?", (stale, r1["id"]))

    # The payer reopens the request and re-claims the same payment: the dead
    # request must close as EXPIRED and release the txid within this call.
    r2 = payer.post("/api/v1/billing/recharge", json={"amount_usd": 300},
                    headers=payer_headers).json()
    att = payer.post(f"/api/v1/billing/recharges/{r2['id']}/tx", json={"txid": txid},
                     headers=payer_headers)
    assert att.status_code == 200, att.text
    expired = store.get_recharge(r1["id"])
    assert expired["status"] == "EXPIRED" and expired["txid"] is None
    assert att.json()["txid"] == txid

    # One live claim per txid still holds: a third request gets 409.
    r3 = payer.post("/api/v1/billing/recharge", json={"amount_usd": 300},
                    headers=payer_headers).json()
    assert payer.post(f"/api/v1/billing/recharges/{r3['id']}/tx", json={"txid": txid},
                      headers=payer_headers).status_code == 409

    # The admin can audit the closed requests.
    listed = client.get("/api/v1/billing/recharges?status=EXPIRED",
                        headers=_basic("admin", "pay-pass"))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [r1["id"]]


def test_reconciliation_reports_ignored_and_sync(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    admin_hdr = _basic("admin", "pay-pass")
    store = app.state.store
    store.record_chain_transfer("ign-tx", "TXold", ADDRESS, 4000.0, "USDT", 1_000, {})
    assert store.mark_transfer_ignored("ign-tx", note="platform-external")
    store.set_sync_meta("chain_sync:last_round_at", "2026-10-04T00:00:00+00:00")
    store.set_sync_meta("chain_sync:last_round_new", "3")
    store.set_sync_meta("chain_sync:wallet", ADDRESS)
    store.set_sync_meta("chain_sync:lookback_days", "30")
    r = client.get("/api/v1/billing/reconciliation", headers=admin_hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["ignored_count"] == 1 and body["ignored_total_usd"] == 4000.0
    assert all(t["txid"] != "ign-tx" for t in body["unmatched_transfers"])
    assert body["sync"]["last_round_at"] == "2026-10-04T00:00:00+00:00"
    assert body["sync"]["last_round_new_transfers"] == 3
