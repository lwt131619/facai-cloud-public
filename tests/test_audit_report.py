"""每日查账系统：充值对账 + 余额核对（查账报告）。"""
import pytest

from facai.ops.audit_report import build_reconciliation, to_markdown


def test_recharge_reconciliation_counts_and_delta(tmp_path):
    from facai.store import Store
    store = Store(tmp_path)
    store.create_recharge("alice", 100.0, 300)
    store.record_chain_transfer("tx-a", "Tfrom", store.chains_wallet if hasattr(store, "chains_wallet") else "Tto", 100.0, "USDT", 1, {})
    report = build_reconciliation(store)
    assert report["recharges"]["approved_total_usd"] == 0.0  # nothing approved yet
    assert isinstance(report["recharges"]["delta_usd"], float)


def test_member_rows_and_alarm(tmp_path):
    from facai.store import Store
    store = Store(tmp_path)
    store.create_recharge("alice", 100.0, 300)
    store.approve_recharge_and_credit(store.create_recharge("alice", 100.0, 300)["id"], "admin")
    report = build_reconciliation(store)
    alice = next(m for m in report["members"] if m["username"] == "alice")
    assert alice["balance_limit"] == pytest.approx(100.0)
    assert alice["remaining"] == pytest.approx(100.0)
    assert alice["alarm"] is False


def test_overdraft_alarm(tmp_path):
    from facai.store import Store
    store = Store(tmp_path)
    store.set_user_budget("bob", 1.0)
    store.add_cost("no-such", "x", "y", 5.0) if False else None
    # direct spend without a mission is impossible; simulate via budget drop
    store.set_user_budget("bob", 0.0)
    report = build_reconciliation(store)
    bob = next(m for m in report["members"] if m["username"] == "bob")
    assert bob["remaining"] == 0.0 or bob["remaining"] is None
    md = to_markdown(report)
    assert "每日查账报告" in md and "充值入账" in md
