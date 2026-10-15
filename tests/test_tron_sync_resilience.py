"""TRON chain-sync resilience: lookback window, rate-limit backoff, reconcile CLI.

These cover the daemon hardening on top of the core reconciliation behaviour in
``test_chain_sync.py``: ancient history must not be ingested, a 429/5xx response
must be retried with backoff instead of killing the pass, and operators need a
manual reconciliation readout.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from facai.ops import tron_sync
from facai.store import Store

ADDRESS = "TLRCZgWxGNDBw7pYJdomK1RsowSLsUvc8Z"
USDT = tron_sync.USDT_TRC20
DAY_MS = 24 * 60 * 60 * 1000


def _transfer(txid, amount, block_ms, frm="TXsender", to=ADDRESS):
    return {"transaction_id": txid, "type": "Transfer", "to": to, "from": frm,
            "value": str(int(round(amount * 1_000_000))),
            "block_timestamp": block_ms,
            "token_info": {"address": USDT, "symbol": "USDT", "decimals": 6}}


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, transfers):
    """Make the real ``fetch_transfers`` see ``transfers`` from TronGrid."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse({"data": transfers})

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", fake_urlopen)
    return calls


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    monkeypatch.delenv("TRON_SYNC_LOOKBACK_DAYS", raising=False)
    return Store(tmp_path)


# --- lookback window -------------------------------------------------------

def test_lookback_filters_ancient_transfers(store, monkeypatch):
    now = int(time.time() * 1000)
    recent = _transfer("recent", 10.0, now - 60_000)
    ancient = _transfer("ancient", 99.0, now - 40 * DAY_MS)
    _serve(monkeypatch, [ancient, recent])

    result = tron_sync.sync_once(store)

    assert store.chain_transfer("recent") is not None
    assert store.chain_transfer("ancient") is None
    assert result["new_transfers"] == 1
    assert result["scanned"] == 1


def test_lookback_window_is_configurable(store, monkeypatch):
    now = int(time.time() * 1000)
    # 10 days old: inside the default 30-day window, outside a 5-day one.
    ten_days = _transfer("ten", 5.0, now - 10 * DAY_MS)
    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "5")
    _serve(monkeypatch, [ten_days])
    tron_sync.sync_once(store)
    assert store.chain_transfer("ten") is None

    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "30")
    _serve(monkeypatch, [ten_days])
    tron_sync.sync_once(store)
    assert store.chain_transfer("ten") is not None


def test_lookback_disabled_when_nonpositive(store, monkeypatch):
    now = int(time.time() * 1000)
    ancient = _transfer("old", 1.0, now - 900 * DAY_MS)
    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "0")
    _serve(monkeypatch, [ancient])
    tron_sync.sync_once(store)
    assert store.chain_transfer("old") is not None


def test_filter_recent_is_pure_and_keeps_boundary():
    now = 1_800_000_000_000  # arbitrary fixed clock
    items = [_transfer("edge", 1.0, now - 30 * DAY_MS),
             _transfer("behind", 1.0, now - 30 * DAY_MS - 1),
             _transfer("fresh", 1.0, now)]
    kept = {t["transaction_id"] for t in tron_sync.filter_recent(items, days=30, now_ms=now)}
    assert kept == {"edge", "fresh"}


# --- rate-limit / 5xx backoff ---------------------------------------------

def test_429_is_retried_with_backoff_then_succeeds(monkeypatch):
    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "0")
    sleeps = []
    monkeypatch.setattr(tron_sync.time, "sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    def flaky(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return _FakeResponse({"data": [_transfer("ok", 1.0, int(time.time() * 1000))]})

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", flaky)

    result = tron_sync.fetch_transfers(ADDRESS)

    assert [t["transaction_id"] for t in result] == ["ok"]
    assert attempts["n"] == 3
    assert len(sleeps) == 2  # two backoffs before the successful third try
    assert all(s > 0 for s in sleeps)          # never an immediate retry
    assert all(s <= tron_sync.RETRY_MAX_SECONDS for s in sleeps)  # capped


def test_5xx_backoff_is_capped(monkeypatch):
    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "0")
    sleeps = []
    monkeypatch.setattr(tron_sync.time, "sleep", lambda s: sleeps.append(s))

    def always_500(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", always_500)

    with pytest.raises(urllib.error.HTTPError):
        tron_sync.fetch_transfers(ADDRESS)

    assert len(sleeps) == tron_sync.RETRY_ATTEMPTS - 1
    assert all(s <= tron_sync.RETRY_MAX_SECONDS for s in sleeps)


def test_non_retryable_status_raises_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(tron_sync.time, "sleep", lambda s: sleeps.append(s))

    def not_found(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", not_found)

    with pytest.raises(urllib.error.HTTPError):
        tron_sync._request_with_backoff("https://api.trongrid.io/nope")
    assert sleeps == []


def test_backoff_does_not_stop_the_daemon_loop(monkeypatch, tmp_path):
    """A pass that exhausts its retries must be swallowed by the loop's guard."""
    monkeypatch.setenv("FACAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    monkeypatch.setenv("TRON_SYNC_LOOKBACK_DAYS", "0")
    monkeypatch.setattr(tron_sync.time, "sleep", lambda s: None)

    def always_429(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", always_429)

    # The pass itself raises after retries...
    with pytest.raises(urllib.error.HTTPError):
        tron_sync.sync_once(Store(tmp_path))
    # ...but retries were attempted (not an immediate give-up).
    assert tron_sync.RETRY_ATTEMPTS > 1


# --- TRON_API_BASE env (P10) -----------------------------------------------

def _fresh_probe(monkeypatch, env_value=None):
    """Load tron_sync.py as a private module copy under the controlled env.

    ``DEFAULT_API`` is read once at import and then bound as the default
    argument of ``fetch_transfers``/``sync_once``, so the env hook can only be
    observed on a fresh load. A private copy leaves the shared module — and the
    already-bound defaults every other test relies on — untouched.
    """
    if env_value is None:
        monkeypatch.delenv("TRON_API_BASE", raising=False)
    else:
        monkeypatch.setenv("TRON_API_BASE", env_value)
    import importlib.util
    # Named under facai.ops so intra-package relative imports (`from ..store
    # import Store`) still resolve; module_from_spec never registers it in
    # sys.modules, so the real facai.ops.tron_sync stays the only shared copy.
    spec = importlib.util.spec_from_file_location(
        "facai.ops.tron_sync_env_probe", tron_sync.__file__)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe


def test_default_api_stays_trongrid_without_env(monkeypatch):
    probe = _fresh_probe(monkeypatch, env_value=None)
    assert probe.DEFAULT_API == "https://api.trongrid.io"


def test_tron_api_base_env_repoints_default_and_bound_arguments(monkeypatch):
    probe = _fresh_probe(monkeypatch, "http://127.0.0.1:9090")
    assert probe.DEFAULT_API == "http://127.0.0.1:9090"
    # fetch_transfers(address, api=DEFAULT_API, ...) / sync_once(store, api=DEFAULT_API):
    # both module-load bindings must follow the constant, not a stale literal.
    assert probe.fetch_transfers.__defaults__[0] == "http://127.0.0.1:9090"
    assert probe.sync_once.__defaults__[1] == "http://127.0.0.1:9090"


def test_tron_api_base_env_flows_into_the_real_request(store, monkeypatch):
    probe = _fresh_probe(monkeypatch, "http://127.0.0.1:9090")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse({"data": []})

    monkeypatch.setattr(tron_sync.urllib.request, "urlopen", fake_urlopen)

    result = probe.sync_once(store)

    assert result["scanned"] == 0
    assert calls and calls[0].startswith("http://127.0.0.1:9090/v1/accounts/")


# --- --reconcile CLI -------------------------------------------------------

def test_reconcile_cli_prints_billing_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FACAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)

    rc = tron_sync.main(["--reconcile"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) >= {"approved_total", "matched_total", "delta",
                           "approved_without_chain", "unmatched_transfers"}


def test_reconcile_cli_reflects_an_unmatched_transfer(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FACAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FACAI_USDT_TRC20_ADDRESS", ADDRESS)
    store = Store(tmp_path)
    store.record_chain_transfer("orphan", "TXx", ADDRESS, 42.0, "USDT",
                                int(time.time() * 1000), {})

    assert tron_sync.main(["--reconcile"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert any(r["txid"] == "orphan" for r in report["unmatched_transfers"])
