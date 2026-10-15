"""Self-healing pipeline: transient failures retry themselves, permanent ones don't.

A 24/7 station cannot page a human for every upstream hiccup. These tests pin
the contract: a TRANSIENT failure (upstream timeout, rate limit, SQLite
"database is locked") requeues the job with a not-before backoff stamp while
attempts remain, and the same job completes on its second attempt. Permanent
failures (auth, refusal) finish FAILED immediately, and the retry budget is
bounded by max_attempts.
"""

import pytest

from facai.api import create_app
from facai.scheduler import Scheduler
from facai.store import Store


class _ScriptedAdapter:
    """Fails N times with a transient error, then succeeds."""

    def __init__(self, failures, error_text="database is locked"):
        self.failures = failures
        self.error_text = error_text
        self.runs = 0

    def execute(self, mission, root, prompt, model_ref=None, timeout=300,
                on_output=None, **_kwargs):
        self.runs += 1
        if self.runs <= self.failures:
            return {"status": "OPENCODE_FAILED", "exit_code": 1,
                    "workspace": str(root / "workspaces" / mission),
                    "stdout": "", "stderr": self.error_text, "streamed": False}
        return {"status": "OPENCODE_COMPLETED", "exit_code": 0,
                "workspace": str(root / "workspaces" / mission),
                "stdout": "done", "stderr": "", "streamed": False}


class _FakeRegistries:
    """Minimal registry stand-in: one resolvable provider, nothing else."""

    def list(self, kind, owner_id="admin"):
        return {}

    def resolve(self, kind, key, owner_id="admin"):
        return {"provider": "DeepSeek"}


def _station(tmp_path, monkeypatch, failures, error_text, max_attempts=3, backoff_cap="0"):
    monkeypatch.setenv("FACAI_RETRY_BACKOFF_CAP", backoff_cap)  # no wait in tests
    store = Store(tmp_path)
    mission = store.create("自愈任务", "原话", ["干活"], owner_id="admin")
    store.bind(mission["id"], agent_id="opencode", model_id="deepseek-flash",
               runtime_id="local", changed={"agent_id"})
    adapter = _ScriptedAdapter(failures, error_text)
    scheduler = Scheduler(store, adapter, _FakeRegistries(), tmp_path)
    step = store.get(mission["id"])["steps"][0]
    job = store.enqueue_job(mission["id"], "prompt", step_id=step["id"],
                            max_attempts=max_attempts)
    return store, scheduler, adapter, job, mission


def test_transient_failure_requeues_then_completes(monkeypatch, tmp_path):
    store, scheduler, adapter, job, mission = _station(tmp_path, monkeypatch, 1, "database is locked")
    # Zero backoff: fail -> requeue -> retry all settle inside one drain.
    assert scheduler.run_pending() == 2
    assert store.get_job(job["id"])["status"] == "COMPLETED"
    assert adapter.runs == 2
    assert store.get(mission["id"])["progress"] == 100
    events = store.events(mission["id"])
    requeued = [e for e in events if e["type"] == "JOB_REQUEUED"]
    assert len(requeued) == 1 and "database is locked" in requeued[0]["payload"]["reason"]
    assert store.get_job(job["id"])["attempts"] == 2  # budget consumed, not reset


def test_backoff_not_before_blocks_immediate_reclaim(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_RETRY_BACKOFF_CAP", "900")  # long backoff
    store, scheduler, _adapter, job, _mission = _station(
        tmp_path, monkeypatch, 1, "upstream timeout (504)", backoff_cap="900")
    scheduler.run_pending()
    requeued = store.get_job(job["id"])
    assert requeued["status"] == "QUEUED" and requeued["not_before"]
    assert scheduler.run_pending() == 0  # backoff window: nothing claimable
    # Once the stamp passes, the job is claimable again.
    import sqlite3
    db = sqlite3.connect(tmp_path / "facai.sqlite3")
    db.execute("UPDATE jobs SET not_before='2000-01-01T00:00:00+00:00' WHERE id=?", (job["id"],))
    db.commit()
    assert scheduler.run_pending() == 1


def test_permanent_failure_never_retries(monkeypatch, tmp_path):
    store, scheduler, adapter, job, _mission = _station(
        tmp_path, monkeypatch, 5, "upstream authentication failed (401)")
    scheduler.run_pending()
    finished = store.get_job(job["id"])
    assert finished["status"] == "FAILED"  # auth failures are honest, not retried
    assert adapter.runs == 1


def test_merge_conflict_requeues_for_solo_retry(monkeypatch, tmp_path):
    store, scheduler, adapter, job, mission = _station(
        tmp_path, monkeypatch, 1, "merge conflict: facai/abc-123")
    # Zero backoff: conflict -> requeue -> solo retry settle in one drain.
    assert scheduler.run_pending() == 2
    assert store.get_job(job["id"])["status"] == "COMPLETED"
    events = store.events(mission["id"])
    requeued = [e for e in events if e["type"] == "JOB_REQUEUED"]
    assert len(requeued) == 1 and "merge conflict" in requeued[0]["payload"]["reason"]
    assert adapter.runs == 2


def test_deploy_interruption_requeues_instead_of_failing(monkeypatch, tmp_path):
    # A redeploy/restart kills the agent subprocess mid-run. That must read as an
    # interruption and re-run, not burn the step to FAILED.
    store, scheduler, adapter, job, _mission = _station(
        tmp_path, monkeypatch, 1, "opencode run interrupted by service shutdown")
    assert scheduler.run_pending() == 2
    assert store.get_job(job["id"])["status"] == "COMPLETED"
    assert adapter.runs == 2


def test_retry_budget_is_bounded_by_max_attempts(monkeypatch, tmp_path):
    store, scheduler, adapter, job, _mission = _station(
        tmp_path, monkeypatch, 99, "provider resource unavailable", max_attempts=2)
    scheduler.run_pending()  # attempt 1 fails -> requeue
    scheduler.run_pending()  # attempt 2 fails -> budget exhausted -> FAILED
    assert store.get_job(job["id"])["status"] == "FAILED"
    assert adapter.runs == 2


def test_dispatch_enqueues_with_env_max_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_JOB_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("RELAY_SHARED_SECRET", "k")
    from fastapi.testclient import TestClient
    client = TestClient(create_app(tmp_path))
    import hashlib, hmac, json, time
    body = json.dumps({"external_id": "retry-1", "sender": "8613800000000",
                       "text": "帮我做一个喝水统计"}).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(b"k", ts.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    r = client.post("/api/v1/channels/relay/webhook", content=body, headers={
        "X-Facai-Signature": f"sha256={sig}", "X-Facai-Timestamp": ts,
        "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    mission_id = r.json()["results"][0]["mission_id"]
    client, app = client, None
    # jobs of the channel mission carry the self-healing budget
    from facai.store import Store
    jobs = Store(tmp_path).jobs(mission_id)
    assert jobs and all(j["max_attempts"] == 3 for j in jobs)


def test_ops_brief_aggregates_the_window(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "brief-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    from fastapi.testclient import TestClient
    app = create_app(tmp_path)
    client = TestClient(app)
    store = app.state.store
    mission = store.create("日报任务", "原话", ["干活"], owner_id="admin")
    step = mission["steps"][0]
    job = store.enqueue_job(mission["id"], "prompt", step_id=step["id"])
    store.claim_job("worker-brief", 300)
    store.finish_job(job["id"], "COMPLETED")
    store.add_cost(mission["id"], "DeepSeek", "deepseek-flash", 0.004, 1200)
    r = client.get("/api/v1/ops/brief", headers={
        "Authorization": "Basic YWRtaW46YnJpZWYtcGFzcw=="})
    assert r.status_code == 200
    brief = r.json()
    assert brief["missions_created"] == 1 and brief["jobs_completed"] >= 1
    assert brief["tokens"] == 1200 and abs(brief["cost_usd"] - 0.004) < 1e-9
    assert brief["stuck_running"] == 0 and brief["window_hours"] == 24
    # With the app password set, the ops report is never readable anonymously.
    assert client.get("/api/v1/ops/brief").status_code == 401
