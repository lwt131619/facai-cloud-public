import pytest
"""Goose (second agent CLI) adapter: per-mission agent selection end to end."""

import json

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.goose import GooseAdapter
from facai.opencode import OpenCodeAdapter

PASSWORD = "goose-pass-123"


def FakeGooseRunner(stdout="goose done", returncode=0):
    """Captures the command like the OpenCode fake runners do."""
    calls = []

    def runner(command, cwd, timeout, on_output=None):
        calls.append({"command": command, "cwd": str(cwd)})
        (cwd / "goose-out.txt").write_text("goose wrote this\n", encoding="utf-8")
        return type("CP", (), {"returncode": returncode, "stdout": stdout, "stderr": ""})()

    runner.calls = calls
    return runner


def _client(tmp_path, monkeypatch, goose_runner=None, opencode_runner=None):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    adapters = {
        "opencode": OpenCodeAdapter(binary="opencode", runner=opencode_runner),
        "goose": GooseAdapter(binary="goose", runner=goose_runner),
    }
    client = TestClient(create_app(tmp_path, opencode_adapter=adapters["opencode"],
                                   goose_adapter=adapters["goose"]))
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    return client


def test_goose_command_shape():
    adapter = GooseAdapter(binary="goose")
    command = adapter.command("写个脚本", "C:/ws/m1", model_ref="xai/grok-4.7")
    assert command[:2] == ["goose", "run"]
    assert "--dir" in command and "--text" in command
    assert command[command.index("--model") + 1] == "xai/grok-4.7"
    assert command[-1] == "写个脚本"
    assert command.index("--text") < command.index("写个脚本")


def test_goose_mission_runs_via_sync_endpoint_with_goose_attribution(tmp_path, monkeypatch):
    runner = FakeGooseRunner(stdout=json.dumps(
        {"type": "step_finish", "part": {"type": "text", "text": "ok",
                                         "cost": 0.01, "tokens": {"total": 77}}}) + "\n")
    client = _client(tmp_path, monkeypatch, goose_runner=runner)
    mission = client.post("/api/v1/missions", json={
        "title": "鹅任务", "original_input": "原话", "steps": ["开发"]}).json()
    client.put(f"/api/v1/missions/{mission['id']}/binding",
               json={"agent_id": "goose", "model_id": "deepseek-flash"})

    response = client.post(f"/api/v1/missions/{mission['id']}/opencode/run",
                           json={"prompt": "执行任务", "step_id": mission["steps"][0]["id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "OPENCODE_COMPLETED"
    assert body["agent_id"] == "goose"
    assert runner.calls[0]["command"][0] == "goose"

    detail = client.get(f"/api/v1/missions/{mission['id']}").json()
    assert detail["steps"][0]["status"] == "COMPLETED"
    events = client.get(f"/api/v1/missions/{mission['id']}/events").json()
    started = [e for e in events if e["type"] == "OPENCODE_STARTED"]
    assert started and started[0]["agent_id"] == "goose"
    cost = [e for e in events if e["type"] == "COST_RECORDED"]
    assert cost and cost[0]["payload"]["amount"] == pytest.approx(77 / 1_000_000 * 218)


def test_goose_mission_runs_through_scheduler(tmp_path, monkeypatch):
    runner = FakeGooseRunner()
    client = _client(tmp_path, monkeypatch, goose_runner=runner)
    mission = client.post("/api/v1/missions", json={
        "title": "鹅队列", "original_input": "原话", "steps": ["开发"]}).json()
    client.put(f"/api/v1/missions/{mission['id']}/binding",
               json={"agent_id": "goose", "model_id": "deepseek-flash"})
    response = client.post(f"/api/v1/missions/{mission['id']}/jobs",
                           json={"prompt": "队列任务", "step_id": mission["steps"][0]["id"]})
    assert response.status_code == 201, response.text

    executed = client.post("/api/v1/scheduler/run").json()["executed"]
    assert executed == 1
    jobs = client.get(f"/api/v1/missions/{mission['id']}/jobs").json()
    assert jobs[0]["status"] == "COMPLETED"
    assert runner.calls and runner.calls[0]["command"][0] == "goose"


def test_unknown_adapter_is_refused_at_enqueue(tmp_path, monkeypatch):
    """An agent whose registry entry names an unrunnable adapter is refused
    honestly before a job is created, never silently rerouted to OpenCode."""
    client = _client(tmp_path, monkeypatch, goose_runner=FakeGooseRunner())
    mission = client.post("/api/v1/missions", json={
        "title": "坏适配器", "original_input": "原话", "steps": ["开发"]}).json()
    client.put(f"/api/v1/registries/agents/ghost", json={"name": "Ghost", "adapter": "ghost"})
    client.put(f"/api/v1/missions/{mission['id']}/binding", json={"agent_id": "ghost"})
    response = client.post(f"/api/v1/missions/{mission['id']}/jobs",
                           json={"prompt": "x", "step_id": mission["steps"][0]["id"]})
    assert response.status_code == 409
    assert client.get(f"/api/v1/missions/{mission['id']}/jobs").json() == []


def test_scheduler_marks_unresolvable_adapter_failed(tmp_path, monkeypatch):
    """The scheduler path fails the job honestly when the resolver returns None."""
    from facai.registries import Registries
    from facai.scheduler import Scheduler
    from facai.store import Store

    store = Store(tmp_path / "s")
    mission = store.create("调度坏适配器", "原话", ["开发"], owner_id="admin")
    store.bind(mission["id"], agent_id="ghost")
    store.enqueue_job(mission["id"], "x", step_id=mission["steps"][0]["id"])
    scheduler = Scheduler(store, OpenCodeAdapter(binary="opencode"), Registries(store),
                          tmp_path / "s", adapter_resolver=lambda m: None)
    scheduler.run_pending()
    job = store.jobs(mission["id"])[0]
    assert job["status"] == "FAILED"
    assert "Unsupported agent adapter" in job["error"]
