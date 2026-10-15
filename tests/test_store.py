"""Mission index reads: list()/get() record shape and the lookup indexes."""

import json
import os
import sqlite3

from facai import store as store_module
from facai.opencode import summarize_output
from facai.store import Store


def seed(root):
    """A store holding missions of every shape list()/get() must reproduce."""
    s = Store(root)
    first = s.create("任务一", "要求一", ["开发", "测试"], owner_id="admin")
    step_ids = [step["id"] for step in first["steps"]]
    s.step_status(first["id"], step_ids[0], "RUNNING")
    s.step_status(first["id"], step_ids[0], "COMPLETED")
    s.step_status(first["id"], step_ids[1], "RUNNING")
    s.append(first["id"], "GIT_CHECKPOINT", {"stat": "+2"}, step_id=step_ids[1])
    s.start_session(first["id"], "opencode", "deepseek-flash")
    s.add_artifact(first["id"], "app.py", f"{first['workspace']}/app.py")
    s.add_checkpoint(first["id"], "abc123", "first commit")
    s.add_cost(first["id"], "deepseek", "flash", 1.25, tokens=100)
    s.add_cost(first["id"], "deepseek", "flash", 0.75, tokens=50)
    second = s.create("任务二", "要求二", ["开发"], owner_id="client1")
    empty = s.create("空任务", "无步骤", [], owner_id="client1")
    return s, [first["id"], second["id"], empty["id"]]


def test_list_reproduces_get_for_every_mission(tmp_path):
    s, mission_ids = seed(tmp_path)
    records = s.list()
    assert {record["id"] for record in records} == set(mission_ids)
    assert [record["created_at"] for record in records] == sorted(
        (record["created_at"] for record in records), reverse=True)
    # The batched list must carry exactly what the per-mission read returns:
    # same fields, same values, same parsed dependencies and milestone progress.
    assert records == [s.get(record["id"]) for record in records]
    by_id = {record["id"]: record for record in records}
    first = by_id[mission_ids[0]]
    assert [step["progress"] for step in first["steps"]] == [100, 90]
    assert first["cost_total"] == 2.0
    assert first["checkpoints"][0]["commit_hash"] == "abc123"
    assert first["artifacts"][0]["name"] == "app.py"
    assert first["sessions"][0]["agent_id"] == "opencode"
    assert by_id[mission_ids[2]]["progress"] == 0
    # Owner-scoped lists filter identically and compare field for field.
    owned = s.list("client1")
    assert {record["id"] for record in owned} == {mission_ids[1], mission_ids[2]}
    assert owned == [s.get(record["id"]) for record in owned]
    assert s.list("nobody") == []


def test_list_opens_one_connection_whatever_the_mission_count(tmp_path, monkeypatch):
    s, _ = seed(tmp_path)
    connects = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        connects.append(args)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", counting_connect)
    assert len(s.list()) == 3
    # The console N+1 regression guard: one connection for the whole list,
    # never one per mission.
    assert len(connects) == 1


def test_lookup_indexes_are_recreated_on_a_legacy_database(tmp_path):
    s, _ = seed(tmp_path)
    database = s.database
    with sqlite3.connect(database) as db:
        for name in ("costs_by_mission", "jobs_by_mission",
                     "recharges_by_status", "channel_messages_by_owner"):
            db.execute(f"DROP INDEX {name}")
    Store(tmp_path)  # reopening replays the schema, as it does for a deployed file
    with sqlite3.connect(database) as db:
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"costs_by_mission", "jobs_by_mission",
                "recharges_by_status", "channel_messages_by_owner"} <= indexes
        plan = [row[-1] for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT COALESCE(SUM(amount), 0) FROM costs WHERE mission_id=?",
            ("x",))]
        assert any("costs_by_mission" in step for step in plan), plan


# A database from the generation before the tenant columns existed: no
# owner_id anywhere, no conversion lease columns, pre-split costs, pre-DAG
# steps, and none of the tables the newer SCHEMA adds. Every deployed file
# from any past generation must boot and upgrade in place on first contact
# with the current code -- never crash inside executescript(SCHEMA).
LEGACY_PRE_TENANT_SCHEMA = """
CREATE TABLE missions (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, original_input TEXT NOT NULL,
 status TEXT NOT NULL, workspace TEXT NOT NULL, agent_id TEXT, model_id TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE steps (
 id TEXT NOT NULL, mission_id TEXT NOT NULL REFERENCES missions(id),
 label TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'WAITING',
 PRIMARY KEY (mission_id, id)
);
CREATE TABLE events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 id TEXT NOT NULL UNIQUE, mission_id TEXT NOT NULL REFERENCES missions(id),
 step_id TEXT, session_id TEXT, agent_id TEXT, type TEXT NOT NULL,
 payload TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
 agent_id TEXT NOT NULL, model_id TEXT, status TEXT NOT NULL
);
CREATE TABLE artifacts (
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
 name TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE registries (
 kind TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY (kind, id)
);
CREATE TABLE checkpoints (
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
 commit_hash TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE costs (
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
 provider TEXT, model_id TEXT, amount REAL NOT NULL, tokens INTEGER,
 currency TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id),
 step_id TEXT, prompt TEXT NOT NULL, model_ref TEXT, status TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 1, timeout INTEGER NOT NULL DEFAULT 300,
 lease_owner TEXT, lease_expires_at TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE channel_messages (
 id TEXT PRIMARY KEY, channel TEXT NOT NULL, direction TEXT NOT NULL,
 external_id TEXT, sender TEXT, recipient TEXT, kind TEXT NOT NULL,
 text TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
 mission_id TEXT, created_at TEXT NOT NULL
);
"""


def test_store_boots_and_upgrades_a_pre_tenant_database(tmp_path):
    database = tmp_path / "facai.sqlite3"
    db = sqlite3.connect(database)
    db.executescript(LEGACY_PRE_TENANT_SCHEMA)
    db.execute("INSERT INTO missions (id, title, original_input, status, workspace,"
               " agent_id, model_id, created_at)"
               " VALUES ('m-legacy', '旧任务', '原话', 'COMPLETED', 'w',"
               " 'opencode', 'deepseek-flash', '2026-09-01T00:00:00+00:00')")
    db.execute("INSERT INTO steps (id, mission_id, label, position, status)"
               " VALUES ('s1', 'm-legacy', '第一步', 0, 'COMPLETED')")
    db.execute("INSERT INTO channel_messages (id, channel, direction, kind, text,"
               " status, payload, created_at)"
               " VALUES ('msg-legacy', 'telegram', 'INBOUND', 'text', '你好',"
               " 'RECEIVED', '{}', '2026-09-01T00:00:00+00:00')")
    db.commit()
    db.close()

    # First boot on the untouched legacy file: must upgrade, not crash with
    # "no such column: owner_id" from the owner index.
    s = Store(tmp_path)
    with sqlite3.connect(database) as db:
        mission_cols = {row[1] for row in db.execute("PRAGMA table_info(missions)")}
        message_cols = {row[1] for row in db.execute("PRAGMA table_info(channel_messages)")}
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        owner = db.execute("SELECT owner_id FROM channel_messages WHERE id='msg-legacy'").fetchone()[0]
    assert {"owner_id", "professional_prompt", "skill_id", "runtime_id"} <= mission_cols
    assert {"owner_id", "conversion_owner", "conversion_expires_at"} <= message_cols
    assert "channel_messages_by_owner" in indexes
    # Legacy rows adopt the configured legacy admin, and the record stays readable.
    assert owner == os.getenv("FACAI_ADMIN_USER", "admin")
    assert s.get("m-legacy")["owner_id"] == os.getenv("FACAI_ADMIN_USER", "admin")
    Store(tmp_path)  # second boot on the upgraded file stays idempotent


def test_add_cost_clamps_non_finite_reported_cost_to_zero(tmp_path):
    s = Store(tmp_path)
    mission = s.create("预算门", "原话", ["干活"])
    for bad in (float("nan"), float("inf"), float("-inf")):
        # A non-finite amount would lock the budget gate at 402 forever (inf)
        # or violate the ledger's NOT NULL column (NaN); it must land as zero
        # with the token count preserved for the operator.
        assert s.add_cost(mission["id"], "deepseek", "flash", bad, tokens=10)
    with sqlite3.connect(s.database) as db:
        rows = db.execute("SELECT amount, tokens FROM costs ORDER BY created_at").fetchall()
    assert [(amount, tokens) for amount, tokens in rows] == [(0.0, 10)] * 3
    # The month-spend read must stay JSON-encodable (no inf/NaN can leak in).
    spend = s.user_spend_month("admin")
    json.dumps(spend, allow_nan=False)
    assert spend["cost_usd"] == 0.0


def test_summarize_output_skips_non_finite_and_boolean_cost():
    stdout = "\n".join([
        json.dumps({"part": {"type": "text", "text": "第一步",
                             "cost": 0.5, "tokens": {"total": 120}}}),
        json.dumps({"part": {"cost": float("inf")}}),   # bare Infinity literal
        json.dumps({"part": {"cost": float("nan")}}),   # bare NaN literal
        json.dumps({"part": {"cost": True}}),           # bool is an int subtype
        "not-json at all",
    ])
    summary = summarize_output(stdout)
    assert summary == {"text": "第一步", "cost": 0.5, "tokens": 120, "event_lines": 4}


def test_ops_brief_counts_distinct_missions_per_owner(tmp_path):
    s = Store(tmp_path)
    mission = s.create("日报任务", "原话", ["干活"], owner_id="alice")
    for amount in (1.0, 2.0, 3.0):
        s.add_cost(mission["id"], "deepseek", "flash", amount, tokens=10)
    by_owner = {row["owner_id"]: row for row in s.ops_brief(hours=24)["by_owner"]}
    # One mission with three cost rows is one mission, not three.
    assert by_owner["alice"]["missions"] == 1
    assert by_owner["alice"]["cost_usd"] == 6.0
