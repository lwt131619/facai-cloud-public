"""Per-user monthly spending budget: pure gate helpers, no prices invented.

This is the pre-payment/limit half of the paid service — actual payment
collection stays out of scope. The budget is a hard gate on NEW dispatch:
once a user's month-to-date recorded USD spend has reached their monthly
limit, no further missions may be created, converted or enqueued until the
next calendar month or a raised limit. A limit <= 0 means unlimited and is
the state of every user without an explicit ``user_budgets`` row, so a fresh
instance never blocks anyone.

The gate compares two views of the same month and the larger one governs: the
USD the system has recorded in the ``costs`` table, and ``estimated_cost`` —
the operator's frozen ``PRICING`` rates applied to token counts the upstream
actually reported (a report priced at 0 in the ledger, e.g. from a gateway
that records only the split, still spends the budget). The one conversion that
does live here is ``priced_cost``/``estimated_cost``; an unreported count is
never invented.
"""

import os
from datetime import datetime, timezone

DEFAULT_LIMIT_ENV = "FACAI_DEFAULT_MONTHLY_LIMIT_USD"

BUDGET_EXCEEDED_PREFIX = "预算已用尽"

# 80% soft threshold: a reminder, never a gate. Crossing 80% of the monthly
# limit keeps every dispatch path ALLOWed; the text is a fixed constant so the
# channel reply, /billing/me and the audit all quote the same sentence.
SPEND_ALERT_THRESHOLD = 0.8
SPEND_ALERT_TEXT = "余额即将用尽，请及时充值"

# Operator price list (frozen 2026-10-04): USD per 1M tokens, input and output
# separately. One table serves the console and any estimate so two screens can
# never quote different rates.
PRICING = {"input_per_mtok_usd": 95.0, "output_per_mtok_usd": 218.0}


def priced_cost(input_tokens, output_tokens):
    """USD for a reported token split at ``PRICING``; None when neither side is.

    A NULL side means the upstream never reported it and contributes nothing;
    this function must not be handed a combined count dressed up as a split —
    ``estimated_cost`` owns that conservative path.
    """
    if input_tokens is None and output_tokens is None:
        return None
    return round(((input_tokens or 0) / 1_000_000) * PRICING["input_per_mtok_usd"]
                 + ((output_tokens or 0) / 1_000_000) * PRICING["output_per_mtok_usd"], 10)


def estimated_cost(spend):
    """Estimate the USD bill for one ``Store.user_spend_month`` summary.

    Rows that reported a split are priced per side. Rows that reported only a
    combined count cannot be split, so their whole count is charged at the
    OUTPUT rate — the conservative (never-cheaper) bound — and ``conservative``
    is true so the number is never read as the exact bill. Returns None when
    the month holds no token data at all.
    """
    input_tokens = int(spend.get("input_tokens") or 0)
    output_tokens = int(spend.get("output_tokens") or 0)
    total_only_tokens = int(spend.get("total_only_tokens") or 0)
    if not input_tokens and not output_tokens and not total_only_tokens:
        return None
    split_usd = priced_cost(input_tokens, output_tokens) or 0.0
    conservative_usd = (total_only_tokens / 1_000_000) * PRICING["output_per_mtok_usd"]
    return {"currency": "USD", "usd": round(split_usd + conservative_usd, 10),
            "conservative": conservative_usd > 0,
            "priced_input_tokens": input_tokens, "priced_output_tokens": output_tokens,
            "total_only_tokens": total_only_tokens,
            "basis": "按运营方定价：输入 $95 / 输出 $218 每 100 万 tokens"}


def default_monthly_limit_usd(environ=None):
    """The instance-wide fallback monthly limit (float, >= 0; 0 = unlimited).

    Applied only when a user has no explicit ``user_budgets`` row. Blank or
    malformed values degrade to 0 (unlimited) instead of guessing.
    """
    environ = os.environ if environ is None else environ
    try:
        return max(0.0, float(environ.get(DEFAULT_LIMIT_ENV, "0") or 0))
    except (TypeError, ValueError):
        return 0.0


def effective_monthly_limit(store, username, environ=None):
    """The limit that governs ``username``: their row, else the env default."""
    budget = store.get_user_budget(username)
    if budget is not None:
        return float(budget["monthly_limit_usd"])
    return default_monthly_limit_usd(environ)


def budget_exceeded_reason(spend_usd, limit_usd):
    """The fixed Chinese refusal shown to the user and stored in audit events."""
    return f"{BUDGET_EXCEEDED_PREFIX}：本月已消耗 ${spend_usd:.2f} / 上限 ${limit_usd:.2f}"


def month_key(year_month=None):
    """The billing month (UTC ``YYYY-MM``) a check belongs to; now by default."""
    if year_month is not None:
        return year_month
    return datetime.now(timezone.utc).strftime("%Y-%m")


def month_spend_usd(spend):
    """The governing spend view: max(ledger, PRICING estimate) — the same
    number the hard gate closes on, so alert and refusal can never disagree."""
    estimate = estimated_cost(spend)
    return max(spend["cost_usd"], estimate["usd"] if estimate else 0.0)


def spend_alert(store, owner_id, year_month=None):
    """The 80% soft-threshold reminder for one owner's billing month, or None.

    Fires when the governing spend (``month_spend_usd``: the larger of the
    recorded ledger and the priced token estimate) sits at/above 80% of the
    effective limit but strictly below it — at 100% the hard gate refuses with
    its own message, so a reminder would be noise on top of a refusal. A limit
    <= 0 (no credited balance / frozen account) never alerts: there is no
    approaching threshold to approach.

    Exactly once per (owner, month): the ``user_alerts`` row IS the claim —
    under concurrent gate calls exactly one INSERT survives and every later
    call returns None. The reminder never gates anything; callers attach the
    returned dict's ``text`` to whatever reply they were already sending.
    """
    limit = effective_monthly_limit(store, owner_id)
    if limit <= 0:
        return None
    month = month_key(year_month)
    cost_usd = month_spend_usd(store.user_spend_month(owner_id))
    if not (limit * SPEND_ALERT_THRESHOLD <= cost_usd < limit):
        return None
    if not store.record_user_alert(owner_id, month):
        return None  # this billing month's reminder already went out
    return {"owner_id": owner_id, "month": month, "text": SPEND_ALERT_TEXT,
            "spend_usd": cost_usd, "limit_usd": limit,
            "threshold": SPEND_ALERT_THRESHOLD}


def budget_gate(owner_id, store, pricing_rate=None, bypass=False):
    """Dry-run gate result for a NEW dispatch by ``owner_id``.

    The gate compares the limit against the larger of the two tracked spend
    views: the recorded USD ledger and the ``PRICING``-priced estimate of the
    tokens the upstream reported. Taking the max keeps a month whose rows were
    recorded at amount 0 (token-split reports) from reading as free.
    ``pricing_rate`` is deliberately unused — the only token pricing lives in
    ``estimated_cost``.

    Balance-pool semantics (operator directive 2026-10-06): a customer whose
    balance is 0 or unset cannot start anything — recharge (admin approval
    stacking onto ``user_budgets``) is the only way in. ``bypass=True`` marks
    the operator's own account, which stays unlimited. Returns
    ``{"allowed", "reason", "spend", "limit", "alert"}``; ``spend`` is the
    owner's month-to-date usage summary from ``Store.user_spend_month``. On
    every ALLOW the 80% soft-threshold check (``spend_alert``) runs once and
    its reminder — or ``None`` — rides back as ``alert``; DENY results carry
    ``alert: None`` because a refusal already says everything there is to say.
    """
    spend = store.user_spend_month(owner_id)
    limit = effective_monthly_limit(store, owner_id)
    if bypass:
        return {"allowed": True, "reason": None, "spend": spend, "limit": limit,
                "alert": spend_alert(store, owner_id)}
    if limit <= 0:
        return {"allowed": False,
                "reason": "余额为 0，请先充值后再发起任务",
                "spend": spend, "limit": limit, "alert": None}
    cost_usd = month_spend_usd(spend)
    if cost_usd >= limit:
        return {"allowed": False,
                "reason": budget_exceeded_reason(cost_usd, limit),
                "spend": spend, "limit": limit, "alert": None}
    return {"allowed": True, "reason": None, "spend": spend, "limit": limit,
            "alert": spend_alert(store, owner_id)}
