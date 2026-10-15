"""Daily reconciliation report (查账系统): charges vs chain vs balances.

Every night the cloud worker (or an admin request) builds one report that
answers three questions in the operator's language:

1. 充值对账 — approved recharge total vs chain-matched total vs deltas.
2. 消费对账 — ledger cost vs priced estimate (a large gap means at-cost rows
   leaked past the pricing standard).
3. 余额核对 — per-customer: limit, month spend, remaining; negative remaining
   is an alarm, not a display bug.

Everything reads the persistent store; nothing is invented. The report is a
dict for the admin API and a markdown block for the nightly log.
"""
from __future__ import annotations

from ..billing import estimated_cost


def build_reconciliation(store) -> dict:
    recon = store.billing_reconciliation()
    # Members = everyone the money system knows: registered users, anyone with
    # a budget row, and anyone who ever opened a recharge. A recharge user who
    # never registered still has money in the system — they must be audited.
    usernames = set(store.platform_users())
    with store.connect() as db:
        usernames |= {r["u"] for r in db.execute(
            "SELECT DISTINCT username AS u FROM user_budgets UNION "
            "SELECT DISTINCT username AS u FROM recharges")}
    members = []
    for username in sorted(usernames):
        budget = store.get_user_budget(username)
        limit = float(budget["monthly_limit_usd"]) if budget else 0.0
        spend = store.user_spend_month(username)
        est = estimated_cost(spend)
        ledger = float(spend.get("cost_usd") or 0.0)
        priced = float(est["usd"]) if est else 0.0
        remaining = round(limit - max(ledger, priced), 6) if limit > 0 else None
        members.append({
            "username": username,
            "balance_limit": limit,
            "ledger_usd": round(ledger, 6),
            "priced_estimate_usd": round(priced, 6),
            "remaining": remaining,
            "alarm": remaining is not None and remaining < 0,
        })
    approved = float(recon.get("approved_total") or 0.0)
    matched = float(recon.get("matched_total") or 0.0)
    return {
        "recharges": {
            "approved_total_usd": approved,
            "chain_matched_total_usd": matched,
            "delta_usd": round(approved - matched, 6),
            "without_chain": recon.get("approved_without_chain") or [],
            "unmatched_transfers": len(recon.get("unmatched_transfers") or []),
            "ignored_count": recon.get("ignored_count"),
        },
        "members": members,
        "alarms": [m for m in members if m["alarm"]],
    }


def to_markdown(report: dict) -> str:
    r = report["recharges"]
    lines = [
        "# FACAI 每日查账报告",
        "",
        f"- 充值入账：${r['approved_total_usd']:.2f} · 链上匹配：${r['chain_matched_total_usd']:.2f}"
        f" · 差额：${r['delta_usd']:.2f}",
        f"- 无链上凭证的核准：{len(r['without_chain'])} 笔 · 未匹配转账：{r['unmatched_transfers']} 笔"
        f" · 已忽略（平台外）：{r['ignored_count']}",
        "",
        "| 客户 | 额度 | 账本 | 标准价估算 | 余额 | 告警 |",
        "|---|---|---|---|---|---|",
    ]
    for m in report["members"]:
        lines.append(
            f"| {m['username']} | {m['balance_limit']:.2f} | {m['ledger_usd']:.2f} | "
            f"{m['priced_estimate_usd']:.2f} | "
            f"{'—' if m['remaining'] is None else format(m['remaining'], '.2f')} | "
            f"{'⚠️ 透支' if m['alarm'] else ''} |")
    return chr(10).join(lines)
