"""API-layer tests for GET /api/v1/billing/reconciliation.

The operator-only reconciliation view proves the money side adds up: the ledger
(approved recharges) against the chain (transfers matched to a recharge). These
tests exercise the HTTP surface -- the admin-only gate and the exact JSON shape
-- and drive real rows through the same store paths the console and the TRON
chain-sync daemon use, so an endpoint regression cannot hide behind a
store-level unit test that already passes.
"""

import base64

from fastapi.testclient import TestClient

from facai.api import create_app

ADDRESS = "TLRCZgWxGNDBw7pYJdomK1RsowSLsUvc8Z"
ADMIN_PASSWORD = "pay-pass"

RECONCILIATION_KEYS = {
    "approved_total",
    "approved_count",
    "matched_total",
    "matched_count",
    "delta",
    "approved_without_chain",
    "unmatched_transfers",
}


def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _write_headers(client, user, pw):
    """Authenticate a WRITE the way a browser does: session cookie + CSRF token.

    Basic credentials traverse every request the origin receives (including a
    cross-site form post), so the API accepts them for reads only.
    """
    login = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert login.status_code == 200, login.text
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    app = create_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/v1/auth/register", json={"username": "tenant1", "password": "tenant-pass"})
    assert r.status_code in (201, 409)
    client.cookies.clear()  # register doubles as login; tests below auth by Basic
    return client, app


def test_admin_gets_reconciliation_with_all_keys(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    r = client.get("/api/v1/billing/reconciliation", headers=_basic("admin", ADMIN_PASSWORD))
    assert r.status_code == 200
    body = r.json()
    assert RECONCILIATION_KEYS <= set(body)
    # A fresh instance has no ledger and no chain rows: both sides are empty and
    # the delta is exactly zero rather than a guessed value.
    assert body["approved_total"] == 0.0
    assert body["approved_count"] == 0
    assert body["matched_total"] == 0.0
    assert body["matched_count"] == 0
    assert body["delta"] == 0.0
    assert body["approved_without_chain"] == []
    assert body["unmatched_transfers"] == []


def test_tenant_is_forbidden(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    r = client.get("/api/v1/billing/reconciliation", headers=_basic("tenant1", "tenant-pass"))
    assert r.status_code == 403


def test_approved_recharge_counts_and_surfaces_without_chain(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    # A recharge opened by the tenant, approved by the admin with no on-chain
    # evidence: the ledger grows and the row must show up as unbacked.
    created = client.post("/api/v1/billing/recharge", json={"amount_usd": 250},
                          headers=_write_headers(client, "tenant1", "tenant-pass"))
    assert created.status_code == 201
    rid = created.json()["id"]
    client.cookies.clear()  # reads below authenticate by Basic

    before = client.get("/api/v1/billing/reconciliation",
                        headers=_basic("admin", ADMIN_PASSWORD)).json()
    approved = client.post(f"/api/v1/billing/recharges/{rid}/approve",
                           headers=_write_headers(client, "admin", ADMIN_PASSWORD))
    assert approved.status_code == 200
    client.cookies.clear()

    after = client.get("/api/v1/billing/reconciliation",
                       headers=_basic("admin", ADMIN_PASSWORD)).json()
    assert after["approved_total"] == before["approved_total"] + 250.0
    assert after["approved_count"] == before["approved_count"] + 1
    # No chain match exists for this approval, so it is exactly what the
    # "approved but not backed by the chain" list is for.
    ids = {row["id"] for row in after["approved_without_chain"]}
    assert rid in ids
    row = next(r for r in after["approved_without_chain"] if r["id"] == rid)
    assert row["amount_usd"] == 250.0
    assert row["txid"] in (None, "")


def test_unmatched_chain_transfer_is_surfaced(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    # Record an incoming on-chain transfer through the app's own store, the same
    # path the TRON daemon writes; nothing ties it to a recharge yet.
    assert app.state.store.record_chain_transfer(
        "tx-orphan", "TXsender", ADDRESS, 90.0, "USDT", 1_700_000_000_000, {}) is True

    body = client.get("/api/v1/billing/reconciliation",
                      headers=_basic("admin", ADMIN_PASSWORD)).json()
    txids = {t["txid"] for t in body["unmatched_transfers"]}
    assert "tx-orphan" in txids
    transfer = next(t for t in body["unmatched_transfers"] if t["txid"] == "tx-orphan")
    assert transfer["amount_usd"] == 90.0
    # An unmatched transfer has not been credited, so it never inflates the
    # "matched" side of the ledger.
    assert body["matched_total"] == 0.0
    assert body["matched_count"] == 0
