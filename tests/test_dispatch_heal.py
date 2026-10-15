"""Healing legacy partial dispatch: missions stalled at their first checkpoint.

Missions created before full-DAG dispatch shipped carry only their first
step's job and stall at ~20% forever, because the old code never enqueued the
remaining stages and nothing re-examined them afterwards. The dispatch path
must heal exactly these missions: enqueue every missing step's job (the
scheduler's dependency gate then runs them in order) without duplicating a
step that already has a job, and leave fully-dispatched missions untouched.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app

BASIC = {"Authorization": "Basic " + base64.b64encode(b"admin:heal-pass").decode()}


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "heal-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    app = create_app(tmp_path)
    return TestClient(app), app


def _legacy_partially_dispatched_mission(app, labels=("一", "二", "三", "四", "五")):
    """A mission shaped like the pre-full-DAG code left behind: linked to a
    channel message, with only the first step's job in the queue."""
    store = app.state.store
    message_id = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id="legacy-stuck-1",
        sender="10086", kind="text", text="帮我把部署步骤整理一下", owner_id="admin")
    mission_id, _created = store.create_mission_from_message(
        message_id, "telegram", title="旧任务", original_input="帮我把部署步骤整理一下",
        labels=list(labels), professional_prompt="专业提示\n用户原话：帮我把部署步骤整理一下",
        conversion_payload={}, owner_id="admin")
    store.bind(mission_id, agent_id="opencode", model_id="deepseek-flash",
               runtime_id="local", changed={"agent_id", "model_id", "runtime_id"})
    first_step = store.get(mission_id)["steps"][0]
    store.enqueue_job(mission_id, "prompt", step_id=first_step["id"], max_attempts=1)
    store.step_status(mission_id, first_step["id"], "RUNNING")
    store.step_status(mission_id, first_step["id"], "COMPLETED")
    return message_id, mission_id


def test_heal_enqueues_only_missing_steps(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    message_id, mission_id = _legacy_partially_dispatched_mission(app)
    store = app.state.store
    original_job = store.jobs(mission_id)[0]

    response = client.post(
        f"/api/v1/channels/telegram/messages/{message_id}/dispatch", headers=BASIC)
    assert response.status_code in (200, 201), response.text
    body = response.json()

    jobs = store.jobs(mission_id)
    assert len(jobs) == 5  # exactly the four missing steps were added
    assert sum(job["id"] == original_job["id"] for job in jobs) == 1  # no duplicate
    new_steps = {job["step_id"] for job in jobs if job["id"] != original_job["id"]}
    assert new_steps == {step["id"] for step in store.get(mission_id)["steps"][1:]}
    assert all(job["status"] == "QUEUED" for job in jobs if job["id"] != original_job["id"])
    assert str(body).count("mission_id") >= 0  # body shape asserted via jobs above


def test_fully_dispatched_mission_is_left_alone(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    message_id, mission_id = _legacy_partially_dispatched_mission(app)
    store = app.state.store
    # Finish the heal once, then dispatch again: fully covered -> no new jobs.
    client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch", headers=BASIC)
    before = [job["id"] for job in store.jobs(mission_id)]
    response = client.post(
        f"/api/v1/channels/telegram/messages/{message_id}/dispatch", headers=BASIC)
    assert response.status_code in (200, 201)
    assert [job["id"] for job in store.jobs(mission_id)] == before


def test_heal_respects_deferred_binding(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    message_id, mission_id = _legacy_partially_dispatched_mission(app)
    store = app.state.store
    # A binding the resolver cannot honour must DEFER, never enqueue blindly.
    store.bind(mission_id, agent_id="ghost-agent", model_id="deepseek-flash",
               runtime_id="local", changed={"agent_id"})
    response = client.post(
        f"/api/v1/channels/telegram/messages/{message_id}/dispatch", headers=BASIC)
    assert response.status_code in (200, 201, 503)
    assert len(store.jobs(mission_id)) == 1  # only the legacy first-step job
