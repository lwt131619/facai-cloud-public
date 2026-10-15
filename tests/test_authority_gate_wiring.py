"""Authority-gate wiring: the frozen no-default-open semantics hold on the
real execution chain (api sync path and scheduler jobs path share
``execute_mission_task``, so one insertion point must deny both).

Every test here resets the process-level engine: the singleton outlives a
single test, and a leftover OWNER_GATE mode or stray grant would leak into
unrelated suites running in the same process.
"""

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.authority import (
    FACAI_OWNER,
    GRANT_EXPIRED,
    GRANT_REQUIRED,
    MISSION_AUTONOMOUS,
    OWNER_GATE,
)
from facai.authority_gate import (
    GATE_BACKEND,
    MISSION_RUN,
    TTL_ENV,
    engine,
    reset_engine,
)
from facai.registries import Registries
from facai.scheduler import Scheduler
from facai.store import Store


@pytest.fixture(autouse=True)
def fresh_gate(monkeypatch):
    """Each test starts from the documented default: MISSION_AUTONOMOUS,
    empty engine, no mode/ttl overrides."""
    monkeypatch.delenv("FACAI_GATE_MODE", raising=False)
    monkeypatch.delenv(TTL_ENV, raising=False)
    reset_engine()
    yield
    reset_engine()


class StubAdapter:
    """Execution probe: records every invocation and returns one well-formed
    completed result. Never runs an external command."""

    def __init__(self):
        self.calls = []

    def execute(self, mission_id, root, prompt, model_ref=None, timeout=300,
                on_output=None, **kwargs):
        from pathlib import Path

        self.calls.append(mission_id)
        workspace = Path(root) / "workspaces" / mission_id
        workspace.mkdir(parents=True, exist_ok=True)
        return {"workspace": str(workspace), "status": "OPENCODE_COMPLETED",
                "exit_code": 0, "stdout": "ok", "stderr": ""}


def make_client(monkeypatch, tmp_path, adapter):
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    return TestClient(create_app(tmp_path, opencode_adapter=adapter)), adapter


def create_and_bind(client, title="授权接线"):
    mission = client.post("/api/v1/missions", json={
        "title": title, "original_input": f"要求：{title}", "steps": ["开发"]}).json()
    bound = client.put(f"/api/v1/missions/{mission['id']}/binding",
                       json={"agent_id": "opencode", "model_id": "deepseek-flash"})
    assert bound.status_code == 200, bound.text
    return mission


def mission_grant(mission_id):
    return [g for g in engine().grants.values()
            if g.action_key == f"mission:{mission_id}"]


def run_task(client, mission_id, prompt="干活"):
    return client.post(f"/api/v1/missions/{mission_id}/opencode/run",
                       json={"prompt": prompt})


def deny_events(store, mission_id):
    return [e for e in store.events(mission_id) if e["type"] == "MISSION_DENIED"]


# --- a) creation issues the mission grant; execution passes -----------------

def test_creation_issues_grant_and_execution_passes(monkeypatch, tmp_path):
    client, adapter = make_client(monkeypatch, tmp_path, StubAdapter())
    mission = create_and_bind(client)

    grants = mission_grant(mission["id"])
    assert len(grants) == 1
    grant = grants[0]
    assert grant.issued_by == FACAI_OWNER
    # Missions are unbound at creation, so the grant admits the actor the
    # mission is later bound to.
    assert grant.subject == "*"
    assert engine().mode == MISSION_AUTONOMOUS
    # The TTL must outlive the largest single-task budget (the API ceiling is
    # one hour) plus retries and backoff.
    assert (grant.expires_at - grant.issued_at).total_seconds() >= 3600

    response = run_task(client, mission["id"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "OPENCODE_COMPLETED"
    assert adapter.calls == [mission["id"]]
    assert deny_events(client.app.state.store, mission["id"]) == []


def test_mission_grant_ttl_is_env_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv(TTL_ENV, "120")
    reset_engine()
    client, _ = make_client(monkeypatch, tmp_path, StubAdapter())
    mission = create_and_bind(client)
    grant = mission_grant(mission["id"])[0]
    # issued_at and expires_at come from two separate now() calls, so the
    # delta carries microsecond jitter — assert within a second, not exactly.
    assert 119 <= (grant.expires_at - grant.issued_at).total_seconds() <= 121


def test_unknown_gate_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("FACAI_GATE_MODE", "SOMETYPOMEANWHILE")
    reset_engine()
    with pytest.raises(ValueError):
        engine()


# --- b) revocation: no more denial — the gate re-signs and runs (无审核) -----

def test_revoked_grant_auto_renews_and_reruns(monkeypatch, tmp_path):
    client, adapter = make_client(monkeypatch, tmp_path, StubAdapter())
    mission = create_and_bind(client)
    grant = mission_grant(mission["id"])[0]

    assert engine().revoke_grant(grant.grant_id, revoked_by=FACAI_OWNER) is True
    response = run_task(client, mission["id"])

    # 彻底无审核（2026-10-07）：门不再拒绝——平台核即时补签，执行照常跑通。
    assert response.status_code == 200
    assert len(adapter.calls) == 1
    store = client.app.state.store
    renewed = [e for e in store.events(mission["id"])
               if e["type"] == "GRANT_AUTO_RENEWED"]
    assert len(renewed) == 1
    payload = renewed[0]["payload"]
    assert payload["reason"] == GRANT_REQUIRED
    assert "自动补签" in payload["note"]
    assert not [e for e in store.events(mission["id"])
                if e["type"] == "MISSION_DENIED"]
    fresh = mission_grant(mission["id"])
    assert fresh and fresh[0].grant_id != grant.grant_id


def test_scheduler_job_auto_renews_without_grant(monkeypatch, tmp_path):
    """The jobs path shares execute_mission_task, so auto-renewal lands there
    too: grant re-signed in place, job COMPLETED, GRANT_AUTO_RENEWED on the
    event stream, adapter actually ran."""
    store = Store(tmp_path)
    adapter = StubAdapter()
    scheduler = Scheduler(store, adapter, Registries(store), tmp_path, worker_id="gate-worker")
    mission = store.create("无授权任务", "原话", ["开发"])
    store.bind(mission["id"], "opencode", "deepseek-flash")
    grant = mission_grant(mission["id"])[0]
    engine().revoke_grant(grant.grant_id, revoked_by=FACAI_OWNER)
    store.enqueue_job(mission["id"], "prompt", step_id=mission["steps"][0]["id"],
                      max_attempts=3)

    scheduler.run_pending()

    job = store.jobs(mission["id"])[0]
    assert job["status"] == "COMPLETED"
    assert adapter.calls == [mission["id"]]
    events = store.events(mission["id"])
    renewed = [e for e in events if e["type"] == "GRANT_AUTO_RENEWED"]
    assert len(renewed) == 1 and renewed[0]["payload"]["reason"] == GRANT_REQUIRED
    assert not [e for e in events if e["type"] == "MISSION_DENIED"]


# --- c) expiry: rerun auto-renews with the specific reason -------------------

def test_expired_grant_auto_renews_with_expired_reason(monkeypatch, tmp_path):
    client, adapter = make_client(monkeypatch, tmp_path, StubAdapter())
    mission = create_and_bind(client)
    grant = mission_grant(mission["id"])[0]
    # Rewind past expiry without sleeping (frozen dataclass, same trick the
    # authority-core tests use).
    object.__setattr__(grant, "expires_at", grant.issued_at)

    response = run_task(client, mission["id"])

    assert response.status_code == 200
    assert len(adapter.calls) == 1
    renewed = [e for e in client.app.state.store.events(mission["id"])
               if e["type"] == "GRANT_AUTO_RENEWED"]
    assert len(renewed) == 1
    assert renewed[0]["payload"]["reason"] == GRANT_EXPIRED


# --- d) OWNER_GATE: a mission grant alone never satisfies the gate ----------

def test_owner_gate_mode_requires_action_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_GATE_MODE", OWNER_GATE)
    reset_engine()
    client, adapter = make_client(monkeypatch, tmp_path, StubAdapter())
    mission = create_and_bind(client)

    # The creation-time mission grant exists, but in OWNER_GATE it is exactly
    # what this mode refuses to accept.
    assert len(mission_grant(mission["id"])) == 1
    response = run_task(client, mission["id"])
    assert response.status_code == 422
    assert adapter.calls == []
    payload = deny_events(client.app.state.store, mission["id"])[0]["payload"]
    assert payload["reason"] == GRANT_REQUIRED
    assert payload["mode"] == OWNER_GATE

    # The operator signs an action-level grant; the same mission now runs.
    # The audited action key names its target mission
    # (facai-native:mission.run:<mission_id>), so an operator grant covers the
    # action family through the documented prefix form.
    engine().issue_grant("opencode", f"{GATE_BACKEND}:{MISSION_RUN}:",
                         issued_by=FACAI_OWNER, ttl_seconds=600)
    allowed = run_task(client, mission["id"])
    assert allowed.status_code == 200, allowed.text
    assert adapter.calls == [mission["id"]]


# --- channel-created missions carry the grant too ---------------------------

def test_channel_created_mission_receives_grant(tmp_path):
    store = Store(tmp_path)
    message_id = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id="gate-1",
        sender="10086", kind="text", text="整理部署步骤", owner_id="admin")
    mission_id, created = store.create_mission_from_message(
        message_id, "telegram", title="渠道任务", original_input="整理部署步骤",
        labels=["开发"], professional_prompt="专业提示", conversion_payload={},
        owner_id="admin")
    assert created is True
    assert len(mission_grant(mission_id)) == 1


def test_losing_concurrent_dispatch_does_not_double_issue(tmp_path):
    store = Store(tmp_path)
    message_id = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id="gate-2",
        sender="10086", kind="text", text="整理部署步骤", owner_id="admin")
    first_id, first = store.create_mission_from_message(
        message_id, "telegram", title="渠道任务", original_input="整理部署步骤",
        labels=["开发"], professional_prompt="专业提示", conversion_payload={},
        owner_id="admin")
    second_id, second = store.create_mission_from_message(
        message_id, "telegram", title="渠道任务", original_input="整理部署步骤",
        labels=["开发"], professional_prompt="专业提示", conversion_payload={},
        owner_id="admin")
    assert (first, second) == (True, False) and second_id == first_id
    assert len(mission_grant(first_id)) == 1


def test_grants_survive_a_restart_via_store_persistence(monkeypatch, tmp_path):
    """P0-7 slice: issuance writes through to the store; a fresh engine
    attached to the same store reloads the grant, so a service restart stops
    manufacturing MISSION_DENIED for legitimately authorized work."""
    from facai import authority_gate as gate
    from facai.store import Store

    store = Store(tmp_path)
    monkeypatch.delenv("FACAI_GATE_MODE", raising=False)  # default: mission grants rule
    reset_engine()
    gate.attach_store(store)
    gate.issue_mission_grant("m-persist", actor="opencode", ttl_seconds=600)

    # Simulate a restart: brand-new engine, same store.
    reset_engine()
    gate.attach_store(store)
    verdict = gate.authorize_for("m-persist", actor="opencode")
    assert verdict["decision"] == "ALLOW"

    # Revocation deletes the row, so the grant never resurrects.
    grant = next(iter(gate.engine().grants.values()))
    gate.engine().revoke_grant(grant.grant_id, revoked_by="FACAI_OWNER")
    reset_engine()
    gate.attach_store(store)
    assert gate.authorize_for("m-persist", actor="opencode")["decision"] == "DENY"
