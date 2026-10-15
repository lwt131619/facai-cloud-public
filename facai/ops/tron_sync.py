"""Persistent TRON (TRC20 USDT) chain sync for the billing side.

Keeps the recharge ledger reconciled with the blockchain 24/7 — the same
"持久链" posture the messaging channels run under:

  * every incoming USDT transfer to ``FACAI_USDT_TRC20_ADDRESS`` is recorded
    once, idempotently by txid, into ``chain_transfers``;
  * a PENDING recharge whose attached txid is a verified on-chain transfer to
    our address of at least the requested amount is auto-approved and credited
    through the SAME store path the admin console uses — one source of truth,
    so the recharge row and the user budget can never drift apart;
  * a recharge that HAS a txid is settled only by that exact txid appearing
    on-chain — never by an unrelated same-amount transfer, however similar;
  * a PENDING recharge with no txid is auto-matched only when EXACTLY ONE
    incoming transfer of the exact amount landed inside its payment window;
  * every other incoming transfer stays ``UNMATCHED`` for the operator — the
    daemon never guesses who a payment belongs to.

Runs under systemd (``facai-tron-sync.service``, Restart=always). ``--once``
does a single pass (tests, or an ad-hoc ``systemctl start``).
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical TRON mainnet USDT-TRC20 contract.
USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
# Upstream read-only REST endpoint; TRON_API_BASE (REROOT_PLAYBOOK P10/F1)
# repoints it at a self-hosted java-tron node. Unset env keeps the public
# TronGrid default byte-for-byte, so behavior without configuration is unchanged.
# Read once at import: fetch_transfers (:146) and sync_once (:285) bind it as
# their default argument at module load and need no edits of their own.
DEFAULT_API = os.getenv("TRON_API_BASE", "https://api.trongrid.io")
AMOUNT_TOLERANCE = 0.01          # ignore sub-cent rounding
WINDOW_GRACE_SECONDS = 15 * 60   # a payment may land a little after expiry

# How far back to ingest: a fresh wallet must not replay years of history into
# ``chain_transfers``. Override with TRON_SYNC_LOOKBACK_DAYS; <=0 disables it.
DEFAULT_LOOKBACK_DAYS = 30
DAY_MS = 24 * 60 * 60 * 1000

# Rate-limit / upstream-error backoff. 429 and 5xx are retried with exponential
# backoff plus jitter (never immediately), capped so one bad pass cannot stall
# the 30s loop for long; a pass that still fails raises and the daemon's own
# try/except keeps the process alive.
RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 60.0


def data_dir():
    return Path(os.getenv("FACAI_DATA_DIR") or (REPO_ROOT / "data"))


def wallet():
    return (os.getenv("FACAI_USDT_TRC20_ADDRESS") or "").strip()


def lookback_days():
    """Ingest window in days; <=0 means "no limit" (backfill everything)."""
    raw = (os.getenv("TRON_SYNC_LOOKBACK_DAYS") or "").strip()
    if not raw:
        return DEFAULT_LOOKBACK_DAYS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS


def _now_ms():
    return int(time.time() * 1000)


def filter_recent(transfers, days=None, now_ms=None):
    """Keep only transfers whose block_timestamp is within the last ``days``.

    ``days <= 0`` disables the window (returns everything). Transfers with a
    missing/zero timestamp are dropped, since they cannot be shown to be recent.
    """
    if days is None:
        days = lookback_days()
    if days <= 0:
        return list(transfers)
    cutoff = (now_ms if now_ms is not None else _now_ms()) - days * DAY_MS
    kept = []
    for item in transfers:
        try:
            ts = int(item.get("block_timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts >= cutoff:
            kept.append(item)
    return kept


def _is_retryable_error(exc):
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        return False
    return code == 429 or 500 <= code < 600


def _backoff_delay(attempt, base=RETRY_BASE_SECONDS, cap=RETRY_MAX_SECONDS):
    """Exponential backoff with jitter, floored so a retry is never immediate."""
    raw = min(cap, base * (2 ** attempt))
    # Jitter in [0.5, 1.0) of the exponential step keeps retries from marching
    # in lockstep, while the floor guarantees a real pause between attempts.
    return min(cap, raw * random.uniform(0.5, 1.0))


def _request_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    key = (os.getenv("TRONGRID_API_KEY") or "").strip()
    if key:
        req.add_header("TRON-PRO-API-KEY", key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _request_with_backoff(url, timeout=20, attempts=RETRY_ATTEMPTS):
    """GET ``url``, backing off on 429/5xx; raise once attempts are exhausted."""
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return _request_json(url, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if not _is_retryable_error(exc):
                raise
            last = exc
            remaining = attempts - attempt - 1
            if remaining <= 0:
                break
            delay = _backoff_delay(attempt)
            print(f"tron-sync: TronGrid returned HTTP {exc.code}; "
                  f"retrying in {delay:.1f}s "
                  f"({attempt + 1}/{attempts})", flush=True)
            time.sleep(delay)
    raise last


def fetch_transfers(address, api=DEFAULT_API, limit=200, timeout=20):
    """Incoming TRC20 transfers to ``address`` (TronGrid, newest first).

    The lookback window is applied here, at the pull boundary, so both the
    server-side query (``min_timestamp``) and a local filter keep ancient
    history out of ``chain_transfers``.
    """
    days = lookback_days()
    url = f"{api}/v1/accounts/{address}/transactions/trc20?limit={int(limit)}&only_to=true"
    if days > 0:
        url += f"&min_timestamp={_now_ms() - days * DAY_MS}"
    payload = _request_with_backoff(url, timeout=timeout)
    return filter_recent(payload.get("data") or [], days=days)


def _amount(item):
    info = item.get("token_info") or {}
    try:
        decimals = int(info.get("decimals") or 6)
    except (TypeError, ValueError):
        decimals = 6
    try:
        return float(item.get("value") or 0) / (10 ** decimals)
    except (TypeError, ValueError):
        return 0.0


def _iso_to_ms(text):
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError, AttributeError):
        return 0


def record_transfers(store, address, transfers):
    """Insert new incoming USDT transfers; returns how many were new."""
    new = 0
    for item in transfers:
        txid = item.get("transaction_id")
        info = item.get("token_info") or {}
        if not txid:
            continue
        if item.get("type") and item.get("type") != "Transfer":
            continue
        # Positive proof of USDT only: a TRC20 event with a missing/other
        # contract address must never be booked as USDT (an unknown token
        # could otherwise exact-match a recharge and credit without real USDT).
        if info.get("address") != USDT_TRC20:
            continue
        if (item.get("to") or "") != address:
            continue
        if store.record_chain_transfer(txid, item.get("from") or "", address,
                                       _amount(item), info.get("symbol") or "USDT",
                                       int(item.get("block_timestamp") or 0), item):
            new += 1
    return new


def match_recharges(store, address):
    """Tie PENDING recharges to proven on-chain transfers; return approved ids."""
    approved = []
    # Close requests whose window plus settlement grace is over before the
    # scan: an expired request left PENDING occupies the oldest-first queue
    # head forever and keeps its txid hostage, and the window check below can
    # never accept it again. Only PENDING rows survive this call, so the scan
    # below skips EXPIRED ones by construction. Same grace as the window
    # binding here, so nothing expires while it could still auto-credit.
    store.expire_stale_recharges(WINDOW_GRACE_SECONDS)
    pending = store.recharges_by_status("PENDING", limit=500)
    if not pending:
        return approved
    unmatched = store.chain_transfers(status="UNMATCHED", limit=500)
    used = store.matched_transfer_txids()
    # Oldest order claims first: newest-first ordering let a freshly opened
    # same-amount order outrun the real payer's older order and steal the
    # transfer while the ledger still looked perfectly reconciled.
    pending.reverse()
    for recharge in pending:
        txid = (recharge.get("txid") or "").strip()
        amount = float(recharge["amount_usd"])
        if txid:
            transfer = store.chain_transfer(txid)
            # Time binding: the referenced transfer must have landed inside the
            # payment window. Without this, anyone can staple a well-known OLD
            # public txid (wallet history is public) onto a fresh request and
            # be auto-credited with money that predates the platform.
            in_window = False
            if transfer:
                ts = int(transfer.get("block_ts") or 0)
                # "First the order, then the payment": no grace below the
                # order's creation time. A negative grace allowed post-hoc
                # claiming — watch a public transfer land, open a same-amount
                # order within the grace window, staple the txid, get credited
                # with someone else's payment. Expiry grace above stays for
                # settlement lag.
                lo = _iso_to_ms(recharge["created_at"])
                hi = _iso_to_ms(recharge["expires_at"]) + WINDOW_GRACE_SECONDS * 1000
                in_window = lo <= ts <= hi
            if (transfer and in_window and txid not in used
                    and transfer["to_address"] == address
                    and float(transfer["amount_usd"]) + AMOUNT_TOLERANCE >= amount):
                if _approve(store, recharge["id"], txid):
                    approved.append(recharge["id"])
                    # One transfer settles one recharge: refresh the consumed
                    # set so no later recharge in this pass can reuse it.
                    used = store.matched_transfer_txids()
            # A claimed txid settles this recharge and nothing else: until that
            # exact transfer shows up verified on-chain, the recharge waits —
            # it must never fall through to amount-matching and be credited by
            # an unrelated same-amount payment.
            continue
        # No txid: match only an unambiguous exact-amount payment in window.
        # The ``unmatched`` snapshot goes stale the moment a credit lands, so
        # skip transfers the refreshed ``used`` set says are already consumed.
        lo = _iso_to_ms(recharge["created_at"])
        hi = _iso_to_ms(recharge["expires_at"]) + WINDOW_GRACE_SECONDS * 1000
        hits = [t for t in unmatched
                if t["txid"] not in used
                and abs(float(t["amount_usd"]) - amount) <= AMOUNT_TOLERANCE
                and lo <= int(t["block_ts"]) <= hi]
        if len(hits) == 1 and _approve(store, recharge["id"], hits[0]["txid"]):
            approved.append(recharge["id"])
            used = store.matched_transfer_txids()  # one transfer, one credit
    return approved


def _approve(store, recharge_id, txid):
    """Credit via the shared store path; True only when this call applied it."""
    try:
        store.approve_recharge_and_credit(recharge_id, "chain-sync", txid=txid)
        return True
    except KeyError:
        return False
    except ValueError:
        # Already decided between the read and the write, or the claimed
        # transfer was consumed by another recharge — nothing applied here.
        return False


def sync_once(store=None, api=DEFAULT_API):
    from ..store import Store
    store = store or Store(data_dir())
    address = wallet()
    if not address:
        return {"skipped": "FACAI_USDT_TRC20_ADDRESS not set"}
    transfers = fetch_transfers(address, api=api)
    new = record_transfers(store, address, transfers)
    approved = match_recharges(store, address)
    # Freshness markers: the console reads these so a stalled monitor is
    # visible at a glance (data is at most one interval + upstream latency old).
    from datetime import datetime, timezone
    store.set_sync_meta("chain_sync:last_round_at",
                        datetime.now(timezone.utc).isoformat())
    store.set_sync_meta("chain_sync:last_round_new", str(new))
    store.set_sync_meta("chain_sync:wallet", address)
    store.set_sync_meta("chain_sync:lookback_days", str(lookback_days()))
    return {"new_transfers": new, "auto_approved": approved,
            "scanned": len(transfers)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="FACAI TRON USDT chain sync")
    parser.add_argument("--once", action="store_true", help="single pass, then exit")
    parser.add_argument("--interval", type=int, default=30, help="seconds between passes")
    parser.add_argument("--reconcile", action="store_true",
                        help="print billing reconciliation JSON and exit")
    args = parser.parse_args(argv)

    from ..store import Store
    root = data_dir()
    store = Store(root)
    if args.reconcile:
        print(json.dumps(store.billing_reconciliation(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        print(json.dumps(sync_once(store), ensure_ascii=False))
        return 0
    print(f"tron-sync: watching {wallet() or '(no wallet!)'} every {args.interval}s", flush=True)
    while True:
        try:
            result = sync_once(store)
            if result.get("new_transfers") or result.get("auto_approved"):
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # never let a transient upstream error stop the loop
            print(f"tron-sync pass failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(5, args.interval))
    return 0


if __name__ == "__main__":
    sys.exit(main())
