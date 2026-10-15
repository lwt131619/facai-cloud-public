"""Auto mission: one plain sentence becomes a dispatched, reviewable step DAG."""

import json

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.prompting import deterministic_plan, parse_plan

PASSWORD = "auto-pass-123"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    client = TestClient(create_app(tmp_path))
    session = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"]})
    return client


def test_deterministic_plan_is_linear_five_phases():
    plan = deterministic_plan("帮我做一个面板")
    assert [step["label"] for step in plan.steps] == ["理解需求", "规划方案", "执行工作", "验证结果", "交付说明"]
    assert plan.steps[0]["depends_on"] == []
    assert plan.steps[2]["depends_on"] == ["规划方案"]
    assert "帮我做一个面板" in plan.professional
    assert plan.source == "deterministic"


def test_parse_plan_accepts_dag_and_rejects_bad_dependencies():
    original = "做一个网站"
    content = json.dumps({
        "title": "做一个网站",
        "professional_prompt": f"专业任务。\n用户原话：{original}",
        "steps": [
            {"label": "后端接口", "depends_on": []},
            {"label": "前端页面", "depends_on": []},
            {"label": "联调测试", "depends_on": ["后端接口", "前端页面"]},
        ],
    })
    parsed = parse_plan(content, original, "web")
    assert [step["label"] for step in parsed["steps"]] == ["后端接口", "前端页面", "联调测试"]
    assert parsed["steps"][2]["depends_on"] == ["后端接口", "前端页面"]

    forward = json.dumps({
        "title": "x", "professional_prompt": f"用户原话：{original}",
        "steps": [{"label": "a", "depends_on": ["b"]}, {"label": "b", "depends_on": []}]})
    with pytest.raises(ValueError):
        parse_plan(forward, original, "web")

    duplicate = json.dumps({
        "title": "x", "professional_prompt": f"用户原话：{original}",
        "steps": [{"label": "a", "depends_on": []}, {"label": "a", "depends_on": []}]})
    with pytest.raises(ValueError):
        parse_plan(duplicate, original, "web")


def test_auto_mission_falls_back_and_enqueues_whole_dag(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    mission = client.post("/api/v1/missions/auto",
                          json={"original_input": "帮我开发一个库存管理小程序"}).json()
    assert mission["original_input"] == "帮我开发一个库存管理小程序"
    labels = [step["label"] for step in mission["steps"]]
    assert labels == ["理解需求", "规划方案", "执行工作", "验证结果", "交付说明"]
    assert mission["professional_prompt"] and "帮我开发一个库存管理小程序" in mission["professional_prompt"]
    conv = [e for e in client.get(f"/api/v1/missions/{mission['id']}/events").json()
            if e["type"] == "PROMPT_CONVERTED"][0]
    assert conv["payload"]["plan_source"] == "deterministic"
    dispatch = mission["dispatch"]
    assert dispatch["status"] == "QUEUED"
    assert len(dispatch["job_ids"]) == 5
    jobs = client.get(f"/api/v1/missions/{mission['id']}/jobs").json()
    assert len(jobs) == 5
    prompts = {job["step_id"]: job["prompt"] for job in jobs}
    step_by_label = {step["label"]: step["id"] for step in mission["steps"]}
    assert "本次只执行阶段：执行工作" in prompts[step_by_label["执行工作"]]


def test_auto_mission_uses_valid_model_plan(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    plan_json = json.dumps({
        "title": "双分支任务",
        "professional_prompt": "目标明确。\n用户原话：做两件事",
        "steps": [
            {"label": "写文档", "depends_on": []},
            {"label": "写代码", "depends_on": []},
            {"label": "合并检查", "depends_on": ["写文档", "写代码"]},
        ]})

    async def fake_complete(model, messages, client=None, timeout=30):
        return {"status": "MODEL_COMPLETED", "label": "上游模型已完成", "http_status": 200,
                "content": plan_json, "upstream": None, "reason": None,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    monkeypatch.setattr("facai.api.complete", fake_complete)
    mission = client.post("/api/v1/missions/auto", json={"original_input": "做两件事"}).json()
    labels = [step["label"] for step in mission["steps"]]
    assert labels == ["写文档", "写代码", "合并检查"]
    conv = [e for e in client.get(f"/api/v1/missions/{mission['id']}/events").json()
            if e["type"] == "PROMPT_CONVERTED"][0]
    assert conv["payload"]["plan_source"] == "model"
    assert conv["payload"]["usage"]["total_tokens"] == 30


def test_auto_mission_falls_back_when_model_plan_is_invalid(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    async def bad_complete(model, messages, client=None, timeout=30):
        return {"status": "MODEL_COMPLETED", "label": "上游模型已完成", "http_status": 200,
                "content": json.dumps({"title": "x", "professional_prompt": "不含原话",
                                       "steps": [{"label": "a", "depends_on": []}]}),
                "upstream": None, "reason": None, "usage": None}

    monkeypatch.setattr("facai.api.complete", bad_complete)
    mission = client.post("/api/v1/missions/auto", json={"original_input": "原话在这里"}).json()
    assert [step["label"] for step in mission["steps"]] == \
        ["理解需求", "规划方案", "执行工作", "验证结果", "交付说明"]


def test_pricing_endpoint_reports_owner_rate(tmp_path, monkeypatch):
    monkeypatch.delenv("FACAI_PRICE_OUTPUT_USD_PER_MTOK", raising=False)
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/v1/pricing").json()
    # One table everywhere: /pricing quotes the frozen billing.PRICING rates,
    # the same numbers /billing/me and the budget gate's estimate use.
    assert body["output_usd_per_mtok"] == 218
    assert body["input_per_mtok_usd"] == 95
    assert body["currency"] == "USD"
    me = client.get("/api/v1/billing/me").json()
    assert body["output_usd_per_mtok"] == me["pricing"]["output_per_mtok_usd"]
    assert body["input_per_mtok_usd"] == me["pricing"]["input_per_mtok_usd"]

    # The env override still moves the displayed output rate.
    monkeypatch.setenv("FACAI_PRICE_OUTPUT_USD_PER_MTOK", "250")
    assert client.get("/api/v1/pricing").json()["output_usd_per_mtok"] == 250
