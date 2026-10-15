"""Per-step model binding: validation at create, dispatch refs, honest attribution."""

import json
import subprocess

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.opencode import OpenCodeAdapter
from facai.prompting import MissionPlan
from facai.registries import Registries
from facai.scheduler import Scheduler
from facai.store import Store


def register_step_model(client, key="step-model", opencode_model="other/step-model"):
    response = client.put(f"/api/v1/registries/models/{key}", json={
        "name": key, "provider": "OtherProvider", "endpoint": "http://127.0.0.1:9/v1",
        "model": "step-model-name", "api_key_env": "STEP_KEY",
        "opencode_model": opencode_model})
    assert response.status_code == 200


def test_mission_accepts_registered_step_model_and_rejects_unknown(tmp_path):
    client = TestClient(create_app(tmp_path))
    register_step_model(client)

    ok = client.post("/api/v1/missions", json={
        "title": "绑定", "original_input": "input", "steps": [
            {"label": "默认"}, {"label": "专属", "model_id": "step-model"}]})
    assert ok.status_code == 201
    steps = ok.json()["steps"]
    assert steps[0]["model_id"] is None
    assert steps[1]["model_id"] == "step-model"
    assert client.get(f"/api/v1/missions/{ok.json()['id']}").json()["steps"][1]["model_id"] \
        == "step-model"

    bad = client.post("/api/v1/missions", json={
        "title": "未知模型", "original_input": "input",
        "steps": [{"label": "a", "model_id": "ghost"}]})
    assert bad.status_code == 422
    assert "ghost" in bad.json()["detail"]


def test_auto_dispatch_puts_step_model_ref_into_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    client = TestClient(create_app(tmp_path))
    register_step_model(client)
    plan = MissionPlan(
        title="分模型任务",
        professional="目标明确。\n用户原话：做两件事",
        steps=({"label": "默认阶段", "depends_on": []},
               {"label": "专属阶段", "depends_on": [], "model_id": "step-model"}),
        basis=("来源：测试",),
        source="deterministic")
    monkeypatch.setattr("facai.api.deterministic_plan", lambda text, channel="web": plan)

    body = client.post("/api/v1/missions/auto",
                       json={"original_input": "做两件事"}).json()
    assert body["dispatch"]["status"] == "QUEUED"
    steps = {step["label"]: step for step in body["steps"]}
    jobs = {job["step_id"]: job
            for job in client.get(f"/api/v1/missions/{body['id']}/jobs").json()}
    assert jobs[steps["默认阶段"]["id"]]["model_ref"] is None
    assert "本阶段由模型" not in jobs[steps["默认阶段"]["id"]]["prompt"]

    bound = jobs[steps["专属阶段"]["id"]]
    assert bound["model_ref"] == "other/step-model"
    assert "本次只执行阶段：专属阶段" in bound["prompt"]
    assert "本阶段由模型 step-model 执行。" in bound["prompt"]


class CapturingRunner:
    """Records every OpenCode command; never calls a model."""

    def __init__(self, stdout=""):
        self.commands = []
        self.stdout = stdout

    def __call__(self, command, cwd, timeout):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, self.stdout, "")


def test_scheduler_runs_step_with_bound_model_and_attributes_it(tmp_path):
    stdout = json.dumps({"type": "step_finish", "part": {
        "type": "text", "text": "ok", "cost": 0.01, "tokens": {"total": 42}}}) + "\n"
    runner = CapturingRunner(stdout=stdout)
    store = Store(tmp_path)
    registries = Registries(store)
    registries.register("models", "step-model", {
        "name": "step-model", "provider": "OtherProvider",
        "endpoint": "http://127.0.0.1:9/v1", "model": "step-model-name",
        "opencode_model": "other/step-model"})
    mission = store.create("绑定", "input", [
        {"label": "默认阶段"}, {"label": "专属阶段", "model_id": "step-model"}])
    store.bind(mission["id"], "opencode", "deepseek-flash")
    mission = store.get(mission["id"])
    default_step, bound_step = mission["steps"]
    store.enqueue_job(mission["id"], "task default", step_id=default_step["id"])
    store.enqueue_job(mission["id"], "task bound", step_id=bound_step["id"])

    scheduler = Scheduler(store, OpenCodeAdapter(binary="opencode", runner=runner),
                          registries, tmp_path, concurrency=1, worker_id="step-model-test")
    assert scheduler.run_pending() == 2
    assert all(job["status"] == "COMPLETED" for job in store.jobs())

    by_prompt = {command[-1]: command for command in runner.commands}
    default_command = by_prompt["task default"]
    bound_command = by_prompt["task bound"]
    assert default_command[default_command.index("--model") + 1] == "deepseek/deepseek-flash"
    assert bound_command[bound_command.index("--model") + 1] == "other/step-model"

    events = store.events(mission["id"])
    bound_done = next(event for event in events
                      if event["type"] == "OPENCODE_COMPLETED"
                      and event["step_id"] == bound_step["id"])
    assert bound_done["payload"]["provider"] == "OtherProvider"
    assert bound_done["payload"]["model_id"] == "step-model"
    default_done = next(event for event in events
                        if event["type"] == "OPENCODE_COMPLETED"
                        and event["step_id"] == default_step["id"])
    assert default_done["payload"]["provider"] == "DeepSeek"
    assert default_done["payload"]["model_id"] == "deepseek-flash"

    costs = {event["payload"]["model_id"]: event["payload"]
             for event in events if event["type"] == "COST_RECORDED"}
    assert set(costs) == {"deepseek-flash", "step-model"}
    assert costs["step-model"]["provider"] == "OtherProvider"
    assert costs["deepseek-flash"]["provider"] == "DeepSeek"
