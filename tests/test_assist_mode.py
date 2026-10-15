"""Visible assist sessions (协助模式): time-boxed, audited operator assistance.

The operator may reach a customer's mission-scoped resources only through an
ACTIVE assist session on that customer -- with their OWN session, never a
minted customer cookie. The customer sees WHO/WHY/UNTIL-WHEN on /auth/session
and their console banner, and every event written during the visit is stamped
``assist_by`` with the actual operator. Without an assist session (or after it
expires / is ended) the operator's access is the same 404 any stranger gets.
"""

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One app, an operator client and a customer client with one mission."""
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    app = create_app(tmp_path)
    admin, alice = TestClient(app), TestClient(app)
    assert admin.post("/api/v1/auth/login",
                      json={"username": "admin", "password": "correct-horse"}).status_code == 200
    # Registration logs the NEW account in on the calling client, so alice's
    # client ends the fixture holding alice's session, admin's holding the
    # operator's.
    reg = alice.post("/api/v1/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    assert reg.status_code == 201, reg.text

    def csrf(client):
        return client.get("/api/v1/auth/session").json()["csrf_token"]

    mission = alice.post("/api/v1/missions", headers={"X-CSRF-Token": csrf(alice)},
                         json={"title": "Alice 私有任务", "original_input": "alice private",
                               "steps": ["Build"]}).json()
    return {"app": app, "admin": admin, "alice": alice,
            "admin_csrf": csrf(admin), "alice_csrf": csrf(alice), "mission": mission}


def start_assist(admin, username="alice", reason="排查任务卡住"):
    return admin.post(f"/api/v1/admin/assist/{username}", json={"reason": reason},
                      headers={"X-CSRF-Token": admin.get("/api/v1/auth/session").json()["csrf_token"]})


def test_without_assist_admin_still_gets_404_on_customer_mission(world):
    """No assist session: the operator's reach is exactly a stranger's (404)."""
    admin, mission = world["admin"], world["mission"]
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 404
    assert admin.get(f"/api/v1/missions/{mission['id']}/events").status_code == 404
    assert admin.post(f"/api/v1/missions/{mission['id']}/events",
                      headers={"X-CSRF-Token": world["admin_csrf"]},
                      json={"type": "PROBE"}).status_code == 404
    # And the customer's session shows nobody is assisting.
    assert world["alice"].get("/api/v1/auth/session").json()["assist"] is None


def test_assist_grants_mission_access_and_stamps_events_with_assist_by(world):
    """发起后: 200 on the customer's mission, and every event the operator writes
    during the visit carries assist_by -- visible to the customer herself."""
    admin, alice, mission = world["admin"], world["alice"], world["mission"]

    started = start_assist(admin, reason="排查任务卡住")
    assert started.status_code == 201, started.text
    row = started.json()
    assert row["admin_username"] == "admin" and row["owner_username"] == "alice"
    assert row["reason"] == "排查任务卡住" and row["expires_at"] and row["ended_at"] is None

    # 客户侧知情（硬要求）: the assisted customer's session carries the record.
    assist = alice.get("/api/v1/auth/session").json()["assist"]
    assert assist == {"active": True, "admin": "admin", "reason": "排查任务卡住",
                      "expires_at": row["expires_at"]}
    # The operator's own session is not the assisted account: no banner there.
    assert admin.get("/api/v1/auth/session").json()["assist"] is None

    # The operator works through their OWN session; the owner check is widened.
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 200
    assert admin.get(f"/api/v1/missions/{mission['id']}/events").status_code == 200
    assert admin.post(f"/api/v1/missions/{mission['id']}/events",
                      headers={"X-CSRF-Token": world["admin_csrf"]},
                      json={"type": "ASSIST_NOTE", "payload": {"note": "帮你看看"}}
                      ).status_code == 201

    # Audit is customer-visible: the operator's event names the actual operator.
    events = alice.get(f"/api/v1/missions/{mission['id']}/events").json()
    stamped = [e for e in events if e["type"] == "ASSIST_NOTE"]
    assert len(stamped) == 1
    assert stamped[0]["payload"]["assist_by"] == {"admin": "admin", "reason": "排查任务卡住",
                                                  "expires_at": row["expires_at"]}

    # The customer's own writes stay their own -- no assist stamp on them.
    alice.post(f"/api/v1/missions/{mission['id']}/events",
               headers={"X-CSRF-Token": world["alice_csrf"]},
               json={"type": "OWNER_NOTE", "payload": {}})
    events = alice.get(f"/api/v1/missions/{mission['id']}/events").json()
    assert all("assist_by" not in e["payload"] for e in events if e["type"] == "OWNER_NOTE")

    # The widened check is mission-scoped: the list still shows only the
    # caller's own missions.
    assert [m["id"] for m in admin.get("/api/v1/missions").json()] != [mission["id"]]
    assert all(m["id"] != mission["id"] for m in admin.get("/api/v1/missions").json())


def test_assist_reason_is_required(world):
    """无事由不发车: missing, empty and blank-only reasons all 422."""
    admin = world["admin"]
    missing = admin.post("/api/v1/admin/assist/alice", json={},
                         headers={"X-CSRF-Token": world["admin_csrf"]})
    assert missing.status_code == 422
    empty = admin.post("/api/v1/admin/assist/alice", json={"reason": ""},
                       headers={"X-CSRF-Token": world["admin_csrf"]})
    assert empty.status_code == 422
    blank = admin.post("/api/v1/admin/assist/alice", json={"reason": "   "},
                       headers={"X-CSRF-Token": world["admin_csrf"]})
    assert blank.status_code == 422
    # Nothing was opened by the refused attempts.
    assert world["alice"].get("/api/v1/auth/session").json()["assist"] is None


def test_assist_target_and_caller_are_validated(world):
    """Unknown targets 404, self-assist 422, and a customer can never mint one."""
    admin, alice = world["admin"], world["alice"]
    assert admin.post("/api/v1/admin/assist/ghost",
                      json={"reason": "x"},
                      headers={"X-CSRF-Token": world["admin_csrf"]}).status_code == 404
    assert admin.post("/api/v1/admin/assist/admin",
                      json={"reason": "x"},
                      headers={"X-CSRF-Token": world["admin_csrf"]}).status_code == 422
    assert alice.post("/api/v1/admin/assist/alice",
                      json={"reason": "self service"},
                      headers={"X-CSRF-Token": world["alice_csrf"]}).status_code == 403
    assert alice.delete("/api/v1/admin/assist/alice",
                        headers={"X-CSRF-Token": world["alice_csrf"]}).status_code == 403


def test_second_active_assist_on_same_owner_conflicts(world):
    admin = world["admin"]
    assert start_assist(admin).status_code == 201
    assert start_assist(admin).status_code == 409


def test_assist_expires_automatically(world):
    """到期视为失效: judged on the clock at read time, no housekeeping needed."""
    admin, alice, mission, store = (world["admin"], world["alice"], world["mission"],
                                    world["app"].state.store)
    started = start_assist(admin)
    assert started.status_code == 201
    with store.connect() as db:
        db.execute("UPDATE assist_sessions SET expires_at='2000-01-01T00:00:00+00:00'")

    assert alice.get("/api/v1/auth/session").json()["assist"] is None
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 404
    assert admin.post(f"/api/v1/missions/{mission['id']}/events",
                      headers={"X-CSRF-Token": world["admin_csrf"]},
                      json={"type": "LATE_PROBE"}).status_code == 404

    # Housekeeping actually removes the dead rows.
    assert store.expire_assist_sessions() == 1
    assert store.active_assist_for_owner("alice") is None

    # And the freed owner can be assisted again.
    assert start_assist(admin).status_code == 201


def test_end_assist_early(world):
    """提前结束: access closes at once, the row stays queryable as audit."""
    admin, alice, mission, store = (world["admin"], world["alice"], world["mission"],
                                    world["app"].state.store)
    row = start_assist(admin).json()
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 200

    ended = admin.delete("/api/v1/admin/assist/alice",
                         headers={"X-CSRF-Token": world["admin_csrf"]})
    assert ended.status_code == 200
    assert ended.json() == {"ended": True, "username": "alice"}

    assert alice.get("/api/v1/auth/session").json()["assist"] is None
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 404

    # The ended session survives as an audit row, but is not active.
    with store.connect() as db:
        rows = db.execute("SELECT * FROM assist_sessions").fetchall()
    assert len(rows) == 1 and rows[0]["id"] == row["id"] and rows[0]["ended_at"] is not None
    assert store.active_assist_for_owner("alice") is None

    # Ending again (or when none is active) is an honest 404.
    assert admin.delete("/api/v1/admin/assist/alice",
                        headers={"X-CSRF-Token": world["admin_csrf"]}).status_code == 404
