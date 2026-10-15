"""Distillation pipeline: DONE mission → APPROVED skill metadata."""
import pytest

from facai.distill import distill_mission, extract_recipe


class FakeStore:
    def get(self, mission_id):
        return self.mission

    def __init__(self, mission):
        self.mission = mission


def done_mission(**over):
    m = {
        "id": "m-123",
        "status": "DONE",
        "title": "竞品日报生成",
        "original_input": "抓取竞品价格并生成中文日报",
        "agent_id": "opencode",
        "model_id": "glm-flash",
        "runtime_id": "local",
        "steps": [{"id": "s1", "title": "抓取竞品数据"}, {"id": "s2", "title": "对比分析"},
                  {"id": "s3", "title": "成稿排版"}],
    }
    m.update(over)
    return m


def test_done_mission_yields_approved_recipe():
    r = extract_recipe(done_mission())
    assert r["review_status"] == "approved"          # distillation is an immediate capability admission
    assert r["steps"] == ["抓取竞品数据", "对比分析", "成稿排版"]
    assert r["source_url"] == "facai://missions/m-123"
    assert r["license"]  # registries require a license field


def test_non_done_or_stepless_missions_are_not_eligible():
    assert extract_recipe(done_mission(status="RUNNING")) is None
    assert extract_recipe(done_mission(steps=[])) is None


def test_long_fields_are_clipped():
    m = done_mission(title="x" * 300, original_input="y" * 900,
                     steps=[{"id": str(i), "title": "s" * 200} for i in range(30)])
    r = extract_recipe(m)
    assert len(r["description"]) <= 400
    assert len(r["steps"]) == 12 and all(len(s) <= 80 for s in r["steps"])


def test_distill_registers_approved_skill_and_is_idempotent():
    registered = {}

    class RegistryStore(FakeStore):
        def registry_register(self, kind, key, value, owner_id="admin"):
            registered[(kind, key)] = value

    store = RegistryStore(done_mission())
    first = distill_mission(store, "m-123", owner_id="admin")
    second = distill_mission(store, "m-123", owner_id="admin")
    assert first["key"] == second["key"]
    assert ("skills", first["key"]) in registered
    assert registered[("skills", first["key"])]["review_status"] == "approved"


def test_cjk_titles_fall_back_to_mission_id_keys():
    r = extract_recipe(done_mission())
    assert r["name"] == "m-m-123"  # ascii-empty CJK title -> mission-id key, no collisions
    r2 = extract_recipe(done_mission(title="Competitor Daily Report"))
    assert r2["name"] == "competitor-daily-report"


def test_distill_rejects_unfinished_mission():
    with pytest.raises(ValueError):
        distill_mission(FakeStore(done_mission(status="RUNNING")), "m-123", owner_id="admin")
