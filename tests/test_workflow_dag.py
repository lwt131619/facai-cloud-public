import sqlite3

import pytest

from facai.scheduler import Scheduler
from facai.opencode import OpenCodeAdapter
from facai.registries import Registries
from facai.store import Store


class Runner:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [(0, "ok", "")])
        self.calls = []

    def __call__(self, command, cwd, timeout):
        self.calls.append((command, str(cwd)))
        returncode, stdout, stderr = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        import subprocess
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def make_scheduler(tmp_path, runner):
    store = Store(tmp_path)
    registry = Registries(store)
    store.registry_register("models", "mock", {
        "provider": "local", "endpoint": "http://localhost/v1", "model": "mock"})
    mission = store.create("DAG", "input", [
        {"label": "root"},
        {"label": "left", "depends_on": ["root"]},
        {"label": "right", "depends_on": ["root"]},
        {"label": "join", "depends_on": ["left", "right"]},
    ])
    store.bind(mission["id"], "opencode", "mock")
    adapter = OpenCodeAdapter(binary="opencode", runner=runner)
    scheduler = Scheduler(store, adapter, registry, tmp_path, concurrency=4, worker_id="dag-test")
    return store, mission, scheduler


def test_claim_waits_for_dependencies_and_runs_branch_join(tmp_path):
    runner = Runner()
    store, mission, scheduler = make_scheduler(tmp_path, runner)
    jobs = {}
    for step in mission["steps"]:
        jobs[step["label"]] = store.enqueue_job(mission["id"], step["label"], step_id=step["id"])

    assert scheduler.run_once() == [jobs["root"]["id"]]
    assert store.get_job(jobs["left"]["id"])["status"] == "QUEUED"
    assert store.get_job(jobs["right"]["id"])["status"] == "QUEUED"
    assert scheduler.run_pending() == 3
    assert all(store.get_job(job["id"])["status"] == "COMPLETED" for job in jobs.values())
    assert all(step["status"] == "COMPLETED" for step in store.get(mission["id"])["steps"])


def test_failed_or_cancelled_dependency_blocks_descendants(tmp_path):
    runner = Runner([(1, "", "boom")])
    store, mission, scheduler = make_scheduler(tmp_path, runner)
    root, left, right, join = mission["steps"]
    root_job = store.enqueue_job(mission["id"], "root", step_id=root["id"])
    left_job = store.enqueue_job(mission["id"], "left", step_id=left["id"])
    join_job = store.enqueue_job(mission["id"], "join", step_id=join["id"])
    scheduler.run_once()
    assert store.get_job(root_job["id"])["status"] == "FAILED"
    assert store.get_job(left_job["id"])["status"] == "BLOCKED"
    assert store.get_job(join_job["id"])["status"] == "BLOCKED"
    assert store.get(mission["id"])["status"] == "FAILED"

    second = store.create("Cancel", "input", ["one", "two"])
    one, two = second["steps"]
    first_job = store.enqueue_job(second["id"], "one", step_id=one["id"])
    dependent = store.enqueue_job(second["id"], "two", step_id=two["id"])
    store.cancel_job(first_job["id"])
    assert store.get_job(dependent["id"])["status"] == "BLOCKED"


def test_retry_reopens_blocked_dag_jobs_but_keeps_dependency_gate(tmp_path):
    runner = Runner([(1, "", "boom"), (0, "ok", "")])
    store, mission, scheduler = make_scheduler(tmp_path, runner)
    root, left, right, join = mission["steps"]
    root_job = store.enqueue_job(mission["id"], "root", step_id=root["id"], max_attempts=1)
    left_job = store.enqueue_job(mission["id"], "left", step_id=left["id"])
    scheduler.run_once()
    assert store.get_job(left_job["id"])["status"] == "BLOCKED"
    store.retry_job(root_job["id"])
    assert store.get_job(left_job["id"])["status"] == "QUEUED"
    assert scheduler.run_once() == [root_job["id"]]
    assert store.get_job(left_job["id"])["status"] == "QUEUED"
    assert scheduler.run_pending() == 1
    assert store.get_job(left_job["id"])["status"] == "COMPLETED"


def test_dag_validation_rejects_unknown_dependencies_and_cycles(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="Unknown step dependency"):
        store.create("Bad", "input", [{"label": "a", "depends_on": ["missing"]}])
    with pytest.raises(ValueError, match="DAG"):
        store.create("Cycle", "input", [
            {"label": "a", "depends_on": ["b"]},
            {"label": "b", "depends_on": ["a"]},
        ])


def test_mission_api_accepts_step_dependency_objects(tmp_path):
    from fastapi.testclient import TestClient
    from facai.api import create_app

    client = TestClient(create_app(tmp_path))
    response = client.post("/api/v1/missions", json={
        "title": "DAG", "original_input": "input", "steps": [
            {"label": "first"}, {"label": "parallel", "depends_on": ["first"]}]})
    assert response.status_code == 201
    steps = response.json()["steps"]
    assert steps[1]["depends_on"] == [steps[0]["id"]]


def test_existing_database_migration_chains_legacy_steps(tmp_path):
    database = tmp_path / "facai.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE missions (id TEXT PRIMARY KEY, title TEXT NOT NULL, original_input TEXT NOT NULL, status TEXT NOT NULL, workspace TEXT NOT NULL, agent_id TEXT, model_id TEXT, created_at TEXT NOT NULL, professional_prompt TEXT, skill_id TEXT, runtime_id TEXT)")
        db.execute("CREATE TABLE steps (id TEXT NOT NULL, mission_id TEXT NOT NULL, label TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'WAITING', PRIMARY KEY (mission_id,id))")
        db.execute("INSERT INTO missions VALUES ('m','old','input','WAITING','workspace',NULL,NULL,'now',NULL,NULL,NULL)")
        db.executemany("INSERT INTO steps VALUES (?,?,?,?,?)", [("s1", "m", "one", 0, "WAITING"), ("s2", "m", "two", 1, "WAITING")])
    store = Store(tmp_path)
    with store.connect() as db:
        deps = [row[0] for row in db.execute("SELECT depends_on FROM steps WHERE mission_id='m' ORDER BY position")]
    import json
    assert json.loads(deps[0]) == []
    assert json.loads(deps[1]) == ["s1"]
