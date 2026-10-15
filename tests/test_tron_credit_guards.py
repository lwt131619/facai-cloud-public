"""Credit guards for the TRON recharge money path (audit HIGH fixes).

Three invariants that must never regress, each tied to a found bug:

* a recharge that claims a txid is settled only by that exact transfer
  appearing verified on-chain — an unrelated same-amount payment in its
  payment window must never auto-credit it;
* one chain transfer credits exactly one recharge — not twice through two
  same-amount recharges off a stale snapshot, and not again through the
  store path once the transfer is already MATCHED;
* an admin approval of a recharge carrying a user-submitted txid marks the
  matching chain transfer, so reconciliation reports no unbacked approval.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.ops import tron_sync
from facai.store import Store

ADDRESS = "TLRCZgWxGNDBw7pYJdomK1RsowSLsUvc8Z"
ADMIN_PASSWORD = "pay-pass"


def _transfer(txid, amount, block_ms, frm="TXsender", to=ADDRESS):
    return {"transaction_id": txid, "type": "Transfer", "to": to, "from": frm,
            "value": str(int(round(amount * 1_000_000))),
            "block_timestamp": block_ms,
            "token_info": {"address": tron_sync.USDT_TRC20, "symbol": "USDT",
                           "decimals": 6}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    return Store(tmp_path)


def _run_sync(monkeypatch, store, transfers):
    monkeypatch.setattr(tron_sync, "fetch_transfers",
                        lambda address, api=tron_sync.DEFAULT_API, **kw: transfers)
    return tron_sync.sync_once(store)


def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _api_client(monkeypatch, tmp_path):
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


# --- bug 1: a claimed txid is never settled by an unrelated same-amount ------

def test_pending_with_unverified_txid_waits_for_its_own_transfer(store, monkeypatch):
    rc = store.create_recharge("client1", 200.0, 300)
    store.attach_recharge_tx(rc["id"], "client1", "claimed-not-yet-onchain")
    # An unrelated stranger's transfer of the exact amount lands in-window.
    lo_ms = tron_sync._iso_to_ms(rc["created_at"]) + 1000
    result = _run_sync(monkeypatch, store, [_transfer("stranger", 200.0, lo_ms)])

    assert result["auto_approved"] == []
    assert store.get_recharge(rc["id"])["status"] == "PENDING"
    assert store.get_user_budget("client1") is None
    assert store.chain_transfer("stranger")["status"] == "UNMATCHED"


def test_claimed_txid_still_credits_once_its_own_transfer_verifies(store, monkeypatch):
    rc = store.create_recharge("client1", 200.0, 300)
    store.attach_recharge_tx(rc["id"], "client1", "mine")
    lo_ms = tron_sync._iso_to_ms(rc["created_at"]) + 1000
    _run_sync(monkeypatch, store, [_transfer("stranger", 200.0, lo_ms)])
    # The real payment shows up on the next pass: the claim settles then.
    result = _run_sync(monkeypatch, store, [_transfer("mine", 200.0, lo_ms)])

    assert result["auto_approved"] == [rc["id"]]
    assert store.get_recharge(rc["id"])["status"] == "APPROVED"
    assert store.chain_transfer("mine")["status"] == "MATCHED"


# --- bug 2a: one transfer, one credit, even off a stale snapshot --------------

def test_one_transfer_credits_exactly_one_of_two_same_amount_recharges(store, monkeypatch):
    store.create_recharge("client1", 300.0, 300)
    store.create_recharge("client2", 300.0, 300)
    first = store.recharges_by_status("PENDING")[0]
    lo_ms = tron_sync._iso_to_ms(first["created_at"]) + 1000
    result = _run_sync(monkeypatch, store, [_transfer("solo", 300.0, lo_ms)])

    assert len(result["auto_approved"]) == 1
    credited = result["auto_approved"][0]
    assert store.chain_transfer("solo")["status"] == "MATCHED"
    assert store.chain_transfer("solo")["matched_recharge_id"] == credited
    total = 0.0
    for user in ("client1", "client2"):
        budget = store.get_user_budget(user)
        total += budget["monthly_limit_usd"] if budget else 0.0
    assert total == pytest.approx(300.0)  # never 600


# --- bug 2b: the store transition itself refuses a double-apply ---------------

def test_store_guard_makes_double_apply_of_pre_matched_transfer_a_no_op(store):
    store.record_chain_transfer("dup", "TXa", ADDRESS, 300.0, "USDT",
                                1_700_000_000_000, {})
    a = store.create_recharge("client1", 300.0, 300)
    store.approve_recharge_and_credit(a["id"], "admin", txid="dup")
    assert store.chain_transfer("dup")["matched_recharge_id"] == a["id"]

    b = store.create_recharge("client2", 300.0, 300)
    with pytest.raises(ValueError):
        store.approve_recharge_and_credit(b["id"], "admin", txid="dup")

    assert store.get_recharge(b["id"])["status"] == "PENDING"
    assert store.get_user_budget("client2") is None
    assert store.chain_transfer("dup")["status"] == "MATCHED"
    assert store.chain_transfer("dup")["matched_recharge_id"] == a["id"]


def test_store_approval_with_uningested_txid_still_credits_admin_judgment(store):
    # A txid the chain sync has not ingested yet is not a double-apply: the
    # admin's judgment credits, exactly as before the guard existed.
    rc = store.create_recharge("client1", 300.0, 300)
    store.attach_recharge_tx(rc["id"], "client1", "not-yet-ingested")

    row = store.approve_recharge_and_credit(rc["id"], "admin", txid="not-yet-ingested")

    assert row["status"] == "APPROVED"
    assert store.get_user_budget("client1")["monthly_limit_usd"] == pytest.approx(300.0)


# --- bug 3: admin approval carries the user-submitted txid to the chain ------

def test_admin_approve_threads_user_txid_into_chain_match(monkeypatch, tmp_path):
    client, app = _api_client(monkeypatch, tmp_path)
    rid = client.post("/api/v1/billing/recharge", json={"amount_usd": 200},
                      headers=_basic("tenant1", "tenant-pass")).json()["id"]
    txid = "0x" + "c" * 40
    assert client.post(f"/api/v1/billing/recharges/{rid}/tx", json={"txid": txid},
                       headers=_basic("tenant1", "tenant-pass")).status_code == 200
    # The payment lands on-chain (the daemon records it) before the admin acts.
    assert app.state.store.record_chain_transfer(
        txid, "TXsender", ADDRESS, 200.0, "USDT", 1_700_000_000_000, {}) is True

    assert client.post(f"/api/v1/billing/recharges/{rid}/approve",
                       headers=_basic("admin", ADMIN_PASSWORD)).status_code == 200

    transfer = app.state.store.chain_transfer(txid)
    assert transfer["status"] == "MATCHED"
    assert transfer["matched_recharge_id"] == rid
    report = client.get("/api/v1/billing/reconciliation",
                        headers=_basic("admin", ADMIN_PASSWORD)).json()
    assert report["approved_without_chain"] == []
    assert report["matched_total"] == pytest.approx(200.0)


# --- minor: one txid backs at most one recharge -------------------------------

def test_attaching_same_txid_to_second_recharge_is_rejected(store):
    first = store.create_recharge("client1", 200.0, 300)
    assert store.attach_recharge_tx(first["id"], "client1", "0x" + "a" * 40)["txid"]
    second = store.create_recharge("client1", 150.0, 300)

    with pytest.raises(ValueError):
        store.attach_recharge_tx(second["id"], "client1", "0x" + "a" * 40)
    assert store.get_recharge(second["id"])["txid"] in (None, "")
