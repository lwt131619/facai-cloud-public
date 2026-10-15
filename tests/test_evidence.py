import pytest
"""执行证据链 (evidence chain): build_proof 与 /proof 端点的契约。

Frozen here:

- 证据包只由持久层组装（事件流 + 表），不读进程内存里的授权引擎；
- 缺证据的节如实为空数组，步骤没落过 outcome_state 事件就是 None；
- ``verified`` 是严格合取：全部步骤终态 且 成本账本至少一行；
- MISSION_DENIED 落进 grants（拒签记录），BACKEND_ATTEMPT/MISSION_REROUTED
  落进 backends（按事件流顺序）。
"""

import json
import subprocess

from fastapi.testclient import TestClient

from facai.api import create_app
from facai.evidence import build_proof
from facai.opencode import OpenCodeAdapter
from facai.registries import Registries
from facai.router import BACKEND_ATTEMPT_EVENT, REROUTED_EVENT, reroute
from facai.store import Store


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, command, cwd, timeout):
        return subprocess.CompletedProcess(command, self.returncode,
                                           self.stdout, self.stderr)


def costed_stdout(cost=0.25, tokens=120):
    """One OpenCode --format json line carrying a real reported cost."""
    return json.dumps({"part": {"type": "text", "text": "done",
                                "cost": cost, "tokens": {"total": tokens}}}) + "\n"


def run_mission(tmp_path, runner, steps=("开发",)):
    """Create → bind → run one step; returns (client, mission_id)."""
    adapter = OpenCodeAdapter(binary="opencode", runner=runner)
    client = TestClient(create_app(tmp_path, opencode_adapter=adapter))
    mission = client.post("/api/v1/missions", json={
        "title": "证据", "original_input": "要求：证据", "steps": list(steps)}).json()
    mission_id = mission["id"]
    client.put(f"/api/v1/missions/{mission_id}/binding",
               json={"agent_id": "opencode", "model_id": "deepseek-flash"})
    client.post(f"/api/v1/missions/{mission_id}/opencode/run",
                json={"prompt": "task", "step_id": mission["steps"][0]["id"]})
    return client, mission_id


# --- 1) 成功执行：结构完整 + verified=True -----------------------------------

def test_successful_mission_proof_is_complete_and_verified(tmp_path):
    client, mission_id = run_mission(
        tmp_path, FakeRunner(returncode=0, stdout=costed_stdout()))
    proof = build_proof(client.app.state.store, mission_id)

    # 七节齐全，且 mission 头来自持久行
    assert set(proof) == {"mission", "grants", "backends", "steps", "costs",
                          "artifacts", "verified"}
    assert proof["mission"]["id"] == mission_id
    assert proof["mission"]["status"] == "COMPLETED"  # 唯一步骤完成 → 任务终态
    assert proof["mission"]["owner"] == "admin"
    assert isinstance(proof["mission"]["created"], str) and proof["mission"]["created"]

    # 步骤：终态 + 落过 §15 outcome_state（OPENCODE_COMPLETED -> SUCCESS）
    assert proof["steps"] == [{"id": proof["steps"][0]["id"], "state": "COMPLETED",
                               "outcome_state": "SUCCESS"}]

    # 成本行与产物都来自真实写入，不是占位
    assert len(proof["costs"]) == 1
    assert proof["costs"][0]["amount"] == pytest.approx(120 / 1_000_000 * 218)
    assert proof["costs"][0]["tokens"] == 120
    artifact_names = [artifact["name"] for artifact in proof["artifacts"]]
    assert any(name.startswith("opencode-") and name.endswith(".stdout.log")
               for name in artifact_names)

    # 没有拒签、没有后端切换：这两节如实为空
    assert proof["grants"] == []
    assert proof["backends"] == []
    assert proof["verified"] is True


def test_proof_endpoint_returns_same_bundle_with_owner_scoping(tmp_path):
    client, mission_id = run_mission(
        tmp_path, FakeRunner(returncode=0, stdout=costed_stdout()))
    via_api = client.get(f"/api/v1/missions/{mission_id}/proof")
    assert via_api.status_code == 200
    # 逆向克隆防护：API 边界剥离产物行里的服务器 path；除该裁剪外，
    # 响应与持久层组装的 bundle 逐字段一致（owner 隔离语义不变）。
    expected = build_proof(client.app.state.store, mission_id)
    expected["artifacts"] = [{k: v for k, v in a.items() if k != "path"}
                             for a in expected["artifacts"]]
    assert via_api.json() == expected
    # 不存在的 mission 与其他端点同样 404，不泄露存在性
    assert client.get("/api/v1/missions/nonexistent/proof").status_code == 404


# --- 2) MISSION_DENIED：grants 有拒签记录，verified=False --------------------

def test_denied_mission_proof_carries_the_deny_record(tmp_path):
    """无审核模式下门自动补签、不再产生拒绝；但证据链映射必须仍能如实承载
    历史拒绝事件（存量数据/OWNER_GATE 模式），绝不伪装成后端说过的话。"""
    client, mission_id = run_mission(
        tmp_path, FakeRunner(returncode=0, stdout=costed_stdout()),
        steps=("开发", "测试"))
    store = client.app.state.store
    step_id = store.get(mission_id)["steps"][1]["id"]
    # 直接落一行历史拒绝事件（门的旧产物）；映射层必须如实承载。
    store.append(mission_id, "MISSION_DENIED",
                 {"reason": "GRANT_REQUIRED", "mode": "MISSION_AUTONOMOUS",
                  "backend": "facai-native", "action": "mission.run",
                  "message": "任务缺少有效的 mission grant，已拒绝执行且未调用适配器",
                  "outcome_state": "GRANT_REQUIRED"},
                 step_id, None, "opencode")

    proof = build_proof(store, mission_id)
    assert len(proof["grants"]) == 1
    denial = proof["grants"][0]
    assert denial["type"] == "MISSION_DENIED"
    assert denial["payload"]["reason"] == "GRANT_REQUIRED"
    # 拒签本身是持久事实：该步骤的 §15 结果如实记为 GRANT_REQUIRED，
    # 绝不伪装成后端说过的话（PROVIDER_REFUSAL）
    assert denial["payload"]["outcome_state"] == "GRANT_REQUIRED"
    # 拒签后该步骤仍未执行：WAITING 非终态，证据链不完整
    assert {"id": denial["step_id"], "state": "WAITING",
            "outcome_state": "GRANT_REQUIRED"} in proof["steps"]
    assert proof["verified"] is False


# --- 3) 无证据：各节如实为空，绝不为对称而编造 -------------------------------

def test_untouched_mission_proof_is_honestly_empty(tmp_path):
    store = Store(tmp_path)
    mission_id = store.create("空证据", "要求：空证据", ["开发"])["id"]
    proof = build_proof(store, mission_id)
    assert proof["grants"] == []
    assert proof["backends"] == []
    assert proof["costs"] == []
    assert proof["artifacts"] == []
    assert proof["steps"] == [{"id": proof["steps"][0]["id"], "state": "WAITING",
                               "outcome_state": None}]
    assert proof["verified"] is False


def test_zero_step_mission_is_never_verified_without_cost_evidence(tmp_path):
    store = Store(tmp_path)
    mission_id = store.create("无步骤", "要求：无步骤", [])["id"]
    proof = build_proof(store, mission_id)
    assert proof["steps"] == []
    # 空步骤被视为"没有可核对的执行"：没有成本行就不得 verified
    assert proof["verified"] is False


def test_missing_mission_raises_keyerror(tmp_path):
    store = Store(tmp_path)
    try:
        build_proof(store, "nonexistent")
    except KeyError as exc:
        assert "nonexistent" in str(exc)
    else:
        raise AssertionError("missing mission must raise KeyError")


# --- 4) 后端序列：BACKEND_ATTEMPT 与 MISSION_REROUTED 按事件流顺序 -----------

def test_reroute_leaves_an_ordered_backend_trail(tmp_path):
    store = Store(tmp_path)
    registries = Registries(store)
    registries.register("models", "deepseek-flash",
                        {"name": "deepseek", "provider": "deepseek", "model": "flash"})
    registries.register("models", "glm-local",
                        {"name": "glm", "provider": "zhipu", "model": "local"})
    mission = store.create("被路由", "要求：被路由", ["开发"], owner_id="admin")
    job = store.enqueue_job(mission["id"], "task")
    # 路由发生在真实路径上：任务先被认领（RUNNING），后端超时后才 reroute
    claimed = store.claim_job("worker-evidence", lease_seconds=3600)
    assert claimed["id"] == job["id"]

    outcome = reroute(store, registries, claimed, mission, "TIMEOUT",
                      "deepseek-flash", error="timeout")
    assert outcome is not None  # 真实路由发生，不是测试自摆事件

    proof = build_proof(store, mission["id"])
    assert [event["type"] for event in proof["backends"]] == \
        [BACKEND_ATTEMPT_EVENT, REROUTED_EVENT]
    attempt, rerouted = proof["backends"]
    assert attempt["payload"]["backend"] == "deepseek-flash"
    assert attempt["payload"]["state"] == "TIMEOUT"
    assert rerouted["payload"]["from"] == "deepseek-flash"
    assert rerouted["payload"]["to"] == "glm-local"
    # 序列可回事件流逐条核对（sequence 单调）
    sequences = [event["sequence"] for event in proof["backends"]]
    assert sequences == sorted(sequences)
