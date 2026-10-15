"""TRON USDT chain sync: persistent billing reconciliation.

The billing side must stay in step with the chain: an incoming USDT transfer is
recorded once, a PENDING recharge tied to a proven on-chain payment is credited
through the same store path as the admin console, and everything the daemon is
unsure about is left for the operator instead of guessed.
"""

import pytest

from facai.ops import tron_sync
from facai.store import Store

ADDRESS = "TLRCZgWxGNDBw7pYJdomK1RsowSLsUvc8Z"
USDT = tron_sync.USDT_TRC20


def _transfer(txid, amount, block_ms, frm="TXsender", to=ADDRESS):
    return {"transaction_id": txid, "type": "Transfer", "to": to, "from": frm,
            "value": str(int(round(amount * 1_000_000))),
            "block_timestamp": block_ms,
            "token_info": {"address": USDT, "symbol": "USDT", "decimals": 6}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    return Store(tmp_path)


def _run_sync(monkeypatch, store, transfers):
    monkeypatch.setattr(tron_sync, "fetch_transfers",
                        lambda address, api=tron_sync.DEFAULT_API, **kw: transfers)
    return tron_sync.sync_once(store)


def test_record_transfer_is_idempotent_by_txid(store):
    assert store.record_chain_transfer("t1", "TXa", ADDRESS, 250.0, "USDT", 1000, {}) is True
    assert store.record_chain_transfer("t1", "TXa", ADDRESS, 250.0, "USDT", 1000, {}) is False
    assert len(store.chain_transfers()) == 1


def test_approve_and_credit_happens_once(store):
    rc = store.create_recharge("client1", 250.0, 300)
    store.approve_recharge_and_credit(rc["id"], "admin")
    budget = store.get_user_budget("client1")
    assert budget["monthly_limit_usd"] == pytest.approx(250.0)
    # A second approval must not double-credit.
    with pytest.raises(ValueError):
        store.approve_recharge_and_credit(rc["id"], "admin")
    assert store.get_user_budget("client1")["monthly_limit_usd"] == pytest.approx(250.0)


def test_sync_auto_approves_verified_txid(store, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    rc = store.create_recharge("client1", 200.0, 300)
    store.attach_recharge_tx(rc["id"], "client1", "abc123")
    # Transfer lands inside the payment window (time binding: an old public
    # txid must never credit a fresh request).
    import time as _time
    now_ms = int(_time.time() * 1000)
    result = _run_sync(monkeypatch, store, [_transfer("abc123", 200.0, now_ms)])
    assert rc["id"] in result["auto_approved"]
    assert store.get_recharge(rc["id"])["status"] == "APPROVED"
    assert store.get_user_budget("client1")["monthly_limit_usd"] == pytest.approx(200.0)
    assert store.chain_transfer("abc123")["status"] == "MATCHED"


def test_sync_does_not_credit_underpaid_txid(store, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    rc = store.create_recharge("client1", 500.0, 300)
    store.attach_recharge_tx(rc["id"], "client1", "small")
    result = _run_sync(monkeypatch, store, [_transfer("small", 100.0, 1_700_000_000_000)])
    assert result["auto_approved"] == []
    assert store.get_recharge(rc["id"])["status"] == "PENDING"
    assert store.get_user_budget("client1") is None


def test_sync_matches_unique_amount_within_window(store, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    rc = store.create_recharge("client2", 300.0, 300)
    lo_ms = tron_sync._iso_to_ms(rc["created_at"]) + 1000
    result = _run_sync(monkeypatch, store, [_transfer("uniq", 300.0, lo_ms)])
    assert rc["id"] in result["auto_approved"]
    assert store.get_user_budget("client2")["monthly_limit_usd"] == pytest.approx(300.0)


def test_sync_leaves_ambiguous_payment_unmatched(store, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    rc = store.create_recharge("client3", 300.0, 300)
    lo_ms = tron_sync._iso_to_ms(rc["created_at"]) + 1000
    result = _run_sync(monkeypatch, store, [_transfer("a", 300.0, lo_ms), _transfer("b", 300.0, lo_ms + 10)])
    assert result["auto_approved"] == []
    assert store.get_recharge(rc["id"])["status"] == "PENDING"


def test_reconciliation_surfaces_rows_that_do_not_add_up(store, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    # A manual approval with no chain evidence.
    manual = store.create_recharge("client4", 150.0, 300)
    store.approve_recharge_and_credit(manual["id"], "admin")
    # An approval carrying a txid that never matched a real transfer.
    claimed = store.create_recharge("client4", 80.0, 300)
    store.attach_recharge_tx(claimed["id"], "client4", "ghost-txid")
    store.approve_recharge_and_credit(claimed["id"], "admin")
    # A chain payment nobody was tied to yet.
    store.record_chain_transfer("orphan", "TXx", ADDRESS, 90.0, "USDT", 1_700_000_000_000, {})
    report = store.billing_reconciliation()
    assert report["approved_total"] == pytest.approx(230.0)
    assert report["matched_total"] == pytest.approx(0.0)
    assert report["delta"] == pytest.approx(230.0)
    ids = {r["id"] for r in report["approved_without_chain"]}
    assert manual["id"] in ids and claimed["id"] in ids
    assert any(r["txid"] == "orphan" for r in report["unmatched_transfers"])


def test_ignored_transfer_exits_reconciliation_and_never_credits(store, monkeypatch):
    # 平台外入账：operator marks pre-platform wallet history IGNORED — it must
    # leave the unmatched queue, report as ignored, and never be credited.
    rc = store.create_recharge("client1", 500.0, 300)
    store.record_chain_transfer("old-tx", "TXold", ADDRESS, 500.0, "USDT", 1_000, {})
    assert store.mark_transfer_ignored("old-tx", note="pre-platform income") is True
    # guarded transition: IGNORED is terminal, a second mark is a no-op
    assert store.mark_transfer_ignored("old-tx") is False
    recon = store.billing_reconciliation()
    assert all(t["txid"] != "old-tx" for t in recon["unmatched_transfers"])
    assert recon["ignored_count"] == 1
    assert recon["ignored_total_usd"] == pytest.approx(500.0)
    # even an admin approval naming the ignored txid must not credit through it
    store.attach_recharge_tx(rc["id"], "client1", "old-tx")
    with pytest.raises(ValueError):
        store.approve_recharge_and_credit(rc["id"], "admin", txid="old-tx")
    assert store.get_recharge(rc["id"])["status"] == "PENDING"
    budget = store.get_user_budget("client1")
    assert budget is None or budget["monthly_limit_usd"] == pytest.approx(0.0)


def test_sync_round_persists_freshness_meta(store, monkeypatch):
    assert store.get_sync_meta("chain_sync:last_round_at") is None
    _run_sync(monkeypatch, store, [_transfer("meta-tx", 100.0, 1_700_000_000_000)])
    assert store.get_sync_meta("chain_sync:last_round_at")
    assert store.get_sync_meta("chain_sync:last_round_new") == "1"
    assert store.get_sync_meta("chain_sync:wallet") == ADDRESS
    recon = store.billing_reconciliation()
    assert recon["sync"]["last_round_at"] is not None
    assert recon["sync"]["last_round_new_transfers"] == 1
    assert recon["sync"]["wallet_address"] == ADDRESS


def test_expired_request_releases_txid_and_late_payment_credits_new_request(store, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    r1 = store.create_recharge("client1", 200.0, 300)
    store.attach_recharge_tx(r1["id"], "client1", "late-tx")

    # Window plus settlement grace pass with no payment: the sync pass itself
    # closes the request and frees the txid, and the expired row leaves the
    # PENDING scan queue instead of clogging its oldest-first head.
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE recharges SET expires_at=? WHERE id=?", (stale, r1["id"]))
    assert _run_sync(monkeypatch, store, [])["auto_approved"] == []
    expired = store.get_recharge(r1["id"])
    assert expired["status"] == "EXPIRED" and expired["txid"] is None
    assert all(r["id"] != r1["id"] for r in store.recharges_by_status("PENDING"))

    # The payer reopens and re-attaches the same payment to the new request.
    r2 = store.create_recharge("client1", 200.0, 300)
    store.attach_recharge_tx(r2["id"], "client1", "late-tx")

    # The transfer confirms late but inside the new request's window: it
    # settles r2 through the ordinary auto-credit path, and the expired r1
    # is never resurrected or credited.
    import time as _time
    now_ms = int(_time.time() * 1000)
    result = _run_sync(monkeypatch, store, [_transfer("late-tx", 200.0, now_ms)])
    assert r2["id"] in result["auto_approved"]
    assert store.get_recharge(r2["id"])["status"] == "APPROVED"
    assert store.get_recharge(r1["id"])["status"] == "EXPIRED"
    assert store.get_user_budget("client1")["monthly_limit_usd"] == pytest.approx(200.0)
    assert store.chain_transfer("late-tx")["status"] == "MATCHED"
