"""Channel message -> prompt conversion -> linked Mission -> dispatch queue.

The contract under test: the original words are never lost, conversion cannot
silently drop a message, dispatch is idempotent, a mission links back to its
source message, and auto-enqueue only happens for a genuinely valid binding.
"""

import base64
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from facai.api import create_app


def auth(password="test-pass"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()}


def build(tmp_path, monkeypatch, password="test-pass"):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", password)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    return app, TestClient(app)


def record(app, channel="telegram", text="帮我做一个可以看到进度的任务面板", external_id="update:5"):
    return app.state.store.record_channel_message(
        channel, "INBOUND", "RECEIVED", external_id=external_id,
        sender="42", recipient="9", text=text, payload={"update_id": 5})


def test_channel_message_dispatch_preserves_original_and_prompt(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    message_id = record(app)
    response = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                           headers=auth())
    assert response.status_code == 201
    mission = response.json()
    assert mission["original_input"] == "帮我做一个可以看到进度的任务面板"
    assert "来源渠道：telegram" in mission["professional_prompt"]
    assert mission["steps"][-1]["label"] == "交付说明"
    converted = [event for event in app.state.store.events(mission["id"])
                 if event["type"] == "PROMPT_CONVERTED"]
    assert converted and converted[0]["payload"]["basis"]
    recorded = app.state.store.channel_message(message_id)
    assert recorded["mission_id"] == mission["id"]
    assert recorded["status"] == "DISPATCHED"


def test_dispatch_preserves_original_whitespace_exactly(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    original = "  第一行\n\t第二行  "
    message_id = record(app, "telegram", original, "update:verbatim")
    mission = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                          headers=auth()).json()
    assert mission["original_input"] == original
    assert original in mission["professional_prompt"]


def test_dispatch_is_idempotent(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    message_id = record(app, "whatsapp", "测试", external_id="wamid:1")
    first = client.post(f"/api/v1/channels/whatsapp/messages/{message_id}/dispatch",
                        headers=auth()).json()
    second = client.post(f"/api/v1/channels/whatsapp/messages/{message_id}/dispatch",
                         headers=auth()).json()
    assert first["id"] == second["id"]
    assert len(app.state.store.list()) == 1


def test_conversion_failure_keeps_the_message(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    message_id = record(app, "telegram", "   ", external_id="update:blank")
    response = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                           headers=auth())
    assert response.status_code == 422
    recorded = app.state.store.channel_message(message_id)
    assert recorded is not None
    assert recorded["text"] == "   "
    assert recorded["status"] == "CONVERSION_FAILED"
    assert recorded["mission_id"] is None
    events = app.state.store.channel_events(message_id=message_id)
    assert [event["type"] for event in events] == ["CONVERSION_FAILED"]


def test_dispatch_auto_binds_and_enqueues_with_valid_binding(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    message_id = record(app)
    mission = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                          headers=auth()).json()
    assert mission["agent_id"] == "opencode"
    jobs = app.state.store.jobs(mission["id"])
    assert len(jobs) == 5 and all(job["status"] == "QUEUED" for job in jobs)
    types = [event["type"] for event in app.state.store.events(mission["id"])]
    assert "DISPATCH_QUEUED" in types and "DISPATCH_DEFERRED" not in types


def test_dispatch_defers_without_a_valid_binding(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    app.state.registries.catalogs["agents"].clear()
    app.state.registries.catalogs["models"].clear()
    app.state.registries.catalogs["runtimes"].clear()
    message_id = record(app)
    mission = client.post(f"/api/v1/channels/telegram/messages/{message_id}/dispatch",
                          headers=auth()).json()
    assert mission["agent_id"] is None
    assert app.state.store.jobs(mission["id"]) == []
    deferred = [event for event in app.state.store.events(mission["id"])
                if event["type"] == "DISPATCH_DEFERRED"]
    assert deferred and deferred[0]["payload"]["reason"] == "NO_VALID_BINDING"


def test_mission_links_back_to_its_source_message(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    message_id = record(app, "whatsapp", "原话要逐字保留", external_id="wamid:link")
    mission = client.post(f"/api/v1/channels/whatsapp/messages/{message_id}/dispatch",
                          headers=auth()).json()
    linked = client.get(f"/api/v1/missions/{mission['id']}/channel-messages",
                        headers=auth()).json()
    assert len(linked) == 1
    assert linked[0]["id"] == message_id
    assert linked[0]["text"] == "原话要逐字保留"


def test_inbox_is_authenticated_and_lists_inbound(tmp_path, monkeypatch):
    app, client = build(tmp_path, monkeypatch)
    record(app)
    assert client.get("/api/v1/inbox").status_code == 401
    body = client.get("/api/v1/inbox", headers=auth()).json()
    assert len(body) == 1 and body[0]["direction"] == "INBOUND"


def test_concurrent_duplicate_conversion_creates_exactly_one_mission(tmp_path, monkeypatch):
    """A racing duplicate delivery must yield one mission and one linked message.

    Every worker runs the real ``create_mission_from_message`` against the same
    inbound row, released together by a barrier so the check/insert windows
    overlap. No sleeps: the barrier alone forces the contention.
    """
    app, _client = build(tmp_path, monkeypatch)
    store = app.state.store
    original = "帮我做一个可以看到进度的任务面板"
    message_id = record(app, "telegram", original, external_id="update:race")
    conversion_payload = {
        "channel": "telegram", "message_id": message_id, "original_input": original,
        "professional_prompt": "professional", "basis": ["test"],
        "conversion_status": "CONVERTED",
    }

    workers = 8
    barrier = threading.Barrier(workers)

    def convert():
        barrier.wait()
        return store.create_mission_from_message(
            message_id, "telegram", title="并发去重", original_input=original,
            labels=["第一步", "交付说明"], professional_prompt="professional",
            conversion_payload=conversion_payload)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [future.result() for future in [pool.submit(convert) for _ in range(workers)]]

    mission_ids = {mission_id for mission_id, _created in results}
    assert len(mission_ids) == 1
    created_flags = [created for _mission_id, created in results]
    assert sum(created_flags) == 1
    assert len(store.list()) == 1

    linked = store.channel_message(message_id)
    assert linked["mission_id"] == mission_ids.pop()
    assert linked["status"] == "DISPATCHED"
    assert len(store.messages_for_mission(linked["mission_id"])) == 1
