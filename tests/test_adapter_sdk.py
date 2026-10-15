"""[8] Adapter SDK：§八 AgentAdapter/ProviderAdapter 契约形式化的行为证据。

覆盖（对应任务书四项验收 + 契约自检细则）：

a) register_adapter 契约自检：缺 capabilities 拒登记、拒绝路径必经 engine
   （假引擎断言 DENY 传透）、license 必填、plan 必产 ActionIR；
b) FacaiNativePlanner 经 broker 的 DENY 传透：DENY 是九态 Outcome 而非异常，
   引擎留痕、执行不发生；grant 形拒绝归一 GRANT_REQUIRED（execution.py 同规），
   非 grant 形拒绝归一 FACAI_DENIED；
c) OpenCodeSDKAdapter.propose 产出合法 ActionIR（冻结动作族 + 已注册后端，
   引擎可裁决），ALLOW 路径经既有 inner.execute 真实运行并按 §15 归一；
d) FacaiNativePlanner 以 L4_FACAI_NATIVE license=Proprietary 注册后
   native_coverage 上升——Native Capability Coverage 的第一个 L4 种子；
e) ProviderAdapter 契约出口 provider_outcome 一律经 normalize_backend_response
   归一化，runtime_checkable 契约对内置实现成立。

全部离线：conftest 的 hermetic_upstream 保证不触真实上游；OpenCode 路径用
注入 fake runner（与 tests/test_goose_adapter.py 同一手法），零真实 CLI。
"""

import pytest

from facai import broker as facai_broker
from facai.adapters import (
    FACAI_NATIVE_LICENSE,
    AdapterContractError,
    AgentAdapter,
    AgentCapability,
    FacaiNativePlanner,
    OpenCodeSDKAdapter,
    ProviderAdapter,
    provider_outcome,
    register_adapter,
)
from facai.authority import (
    DENY,
    FACAI_OWNER,
    ActionIR,
    AuthorizationEngine,
    ExecutionOutcome,
)
from facai.capabilities import L2_ADAPTED, L4_FACAI_NATIVE, CapabilityRegistry
from facai.opencode import OpenCodeAdapter
from facai.outcomes import (
    EXECUTION_FAILED,
    FACAI_DENIED,
    GRANT_REQUIRED,
    PROVIDER_REFUSAL,
    STATES,
    TIMEOUT,
)


# --- a) register_adapter 契约自检 ------------------------------------------------

class _EmptyCapabilityAdapter(FacaiNativePlanner):
    """除了 capabilities() 为空、其余全部合格——登记必须在能力行这里止步。"""

    name = "empty-agent"
    agent_id = "empty-agent"

    def capabilities(self):
        return []


class _ShadowAdapter(FacaiNativePlanner):
    """拒绝时私自短路、从不问引擎的坏适配器——自检必须抓住。"""

    name = "shadow-agent"
    agent_id = "shadow-agent"

    def execute_via_broker(self, broker, engine, action):
        # authority.ExecutionOutcome 的本地成功词汇是字面量 "EXECUTED"
        return ExecutionOutcome(state="EXECUTED")  # 伪造成功，engine.authorize 从未被调


class _RaisingAdapter(FacaiNativePlanner):
    """把 DENY 当异常外泄的坏适配器——违反『DENY 归一为 Outcome』。"""

    name = "raising-agent"
    agent_id = "raising-agent"

    def execute_via_broker(self, broker, engine, action):
        broker.request_capability(engine, self.agent_id, action.backend,
                                  action.action, resource=action.resource)
        raise RuntimeError("denied!")  # 异常逃逸 = 拒绝路径断裂


def test_register_adapter_rejects_empty_capabilities():
    with pytest.raises(AdapterContractError, match="capabilities"):
        register_adapter(CapabilityRegistry(), _EmptyCapabilityAdapter(), license="MIT")


def test_register_adapter_rejects_adapter_without_capabilities_method():
    class Bare:
        name = "bare"
        agent_id = "bare"

    with pytest.raises(AdapterContractError, match="capabilities"):
        register_adapter(CapabilityRegistry(), Bare(), license="MIT")


def test_register_adapter_rejects_deny_path_that_bypasses_engine():
    with pytest.raises(AdapterContractError, match="引擎"):
        register_adapter(CapabilityRegistry(), _ShadowAdapter(), license="MIT")


def test_register_adapter_rejects_deny_raising_out_of_execute_via_broker():
    with pytest.raises(AdapterContractError, match="DENY"):
        register_adapter(CapabilityRegistry(), _RaisingAdapter(), license="MIT")


def test_register_adapter_requires_license():
    with pytest.raises(AdapterContractError, match="license"):
        register_adapter(CapabilityRegistry(), FacaiNativePlanner(), license="")
    with pytest.raises(AdapterContractError, match="license"):
        register_adapter(CapabilityRegistry(), FacaiNativePlanner(), license=None)


def test_register_adapter_rejects_plan_that_does_not_produce_actionir():
    class BadPlanner(FacaiNativePlanner):
        name = "bad-planner"
        agent_id = "bad-planner"

        def plan(self, prompt, context=None):
            return {"actor": "bad-planner"}  # dict 不是 ActionIR

    with pytest.raises(AdapterContractError, match="ActionIR"):
        register_adapter(CapabilityRegistry(), BadPlanner(), license="MIT")


def test_register_adapter_happy_path_registers_contract_checked_rows():
    reg = CapabilityRegistry()
    planner = FacaiNativePlanner()
    rows = register_adapter(reg, planner, license=FACAI_NATIVE_LICENSE,
                            level=L4_FACAI_NATIVE)
    assert [row.id for row in rows] == ["planning.native_single_step"]
    row = reg.get("planning.native_single_step")
    assert row.level == L4_FACAI_NATIVE
    assert row.license == "Proprietary"
    assert row.provider == "AgentProvider"
    assert row.source == "facai-native"  # 自有实现按 doctrine 署名
    assert row.name.startswith("Facai 自有单步规划")


# --- b) FacaiNativePlanner 经 broker 的 DENY 传透 --------------------------------

def test_facai_native_planner_deny_passthrough_is_outcome_never_exception():
    planner = FacaiNativePlanner()
    eng = AuthorizationEngine()  # 无任何 grant：无默认开放（authority.py 冻结语义）
    action = planner.plan("第一步", {"mission_id": "m-1"})
    assert isinstance(action, ActionIR)  # 单步提案：一个 prompt 恰好一个 ActionIR

    outcome = planner.execute_via_broker(facai_broker, eng, action)  # 不抛

    # grant 形拒绝按 §15 归一（facai/execution.py 对 Gate 裁决的同一套映射）
    assert outcome.state == GRANT_REQUIRED
    assert outcome.state in STATES
    assert not outcome.executed and not outcome.mission_failed
    # 拒绝在引擎留痕：裁决属于引擎，适配器只是没有执行。
    assert eng.decisions[-1]["decision"] == DENY
    assert eng.decisions[-1]["reason"] == GRANT_REQUIRED


def test_facai_native_planner_non_grant_deny_maps_to_facai_denied():
    planner = FacaiNativePlanner()
    eng = AuthorizationEngine()
    eng.issue_grant("facai-native-planner", "*", issued_by=FACAI_OWNER)
    # 未注册后端：引擎裁决 DENY（非 grant 形理由）→ FACAI_DENIED，而非异常。
    action = ActionIR(actor=planner.agent_id, backend="unregistered-backend",
                      action="provider.invoke", mission_id="m-2")

    outcome = planner.execute_via_broker(facai_broker, eng, action)

    assert outcome.state == FACAI_DENIED
    assert not outcome.executed
    assert eng.decisions[-1]["decision"] == DENY
    assert "not registered" in eng.decisions[-1]["reason"]


def test_facai_native_planner_allow_handshake_executes_through_broker():
    planner = FacaiNativePlanner()
    eng = AuthorizationEngine()
    eng.issue_grant("facai-native-planner", "facai-native:provider.invoke:",
                    issued_by=FACAI_OWNER)
    action = planner.propose("规划一步", {"model_ref": "deepseek/deepseek-flash"})

    outcome = planner.execute_via_broker(facai_broker, eng, action)

    assert eng.decisions[-1]["decision"] == "ALLOW"
    assert outcome.executed  # authority 词汇：本地单步计划真实产出
    assert "provider.invoke" in outcome.detail


# --- c) OpenCodeSDKAdapter.propose：合法 ActionIR --------------------------------

def _sdk_adapter(runner=None, models=None):
    inner = OpenCodeAdapter(binary="opencode-fake", runner=runner)
    return OpenCodeSDKAdapter(opencode=inner,
                              models=models if models is not None else {
                                  "deepseek-flash": {"opencode_model": "deepseek/deepseek-flash"},
                                  "glm-local": {"opencode_model": "facai-glm/glm-4.6"},
                              })


def test_opencode_sdk_capabilities_derive_from_opencode_model_domain():
    caps = _sdk_adapter().capabilities()
    names = [cap.name for cap in caps]
    # 基础能力一行 + 每台 opencode_model 一行；id 词法归一（"/"与"-"→"_"）
    assert names[0] == "agent.opencode.run"
    assert "model.opencode.deepseek_deepseek_flash" in names
    assert "model.opencode.facai_glm_glm_4_6" in names
    assert len(caps) == 3
    refs = [cap.description for cap in caps if cap.name.startswith("model.")]
    assert any("deepseek/deepseek-flash" in ref for ref in refs)  # 人读 ref 留在描述
    # 没有 opencode_model 的行不产生能力
    none_ref = _sdk_adapter(models={"plain": {"name": "no ref"}}).capabilities()
    assert [cap.name for cap in none_ref] == ["agent.opencode.run"]


def test_opencode_sdk_propose_produces_legal_action_ir(tmp_path):
    ws = tmp_path / "workspaces" / "m-1"
    ws.mkdir(parents=True)
    adapter = _sdk_adapter()

    ir = adapter.propose("写个脚本", {"workspace": str(ws), "mission_id": "m-1",
                                       "model_ref": "deepseek/deepseek-flash"})

    assert isinstance(ir, ActionIR)
    assert ir.action in facai_broker.CAPABILITY_ACTIONS   # 冻结动作族内
    assert ir.backend == "opencode"                       # 已注册后端（seed_backends）
    assert ir.actor == adapter.agent_id
    assert ir.resource == str(ws)
    # 合法性终判：引擎能裁决——补上 grant 即 ALLOW，提案不是无主词。
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "opencode:shell.exec:", issued_by=FACAI_OWNER)
    assert eng.authorize(ir)["decision"] == "ALLOW"
    # 提案建立在真实 command 构建上（opencode.py 零改动的包装保证）
    command = adapter._inner.command("写个脚本", str(ws), "deepseek/deepseek-flash")
    assert command[0] == "opencode-fake" and "写个脚本" in command


def test_opencode_sdk_propose_requires_workspace():
    with pytest.raises(AdapterContractError, match="workspace"):
        _sdk_adapter().propose("没有工作区", {})


def test_opencode_sdk_allow_path_runs_inner_execute_and_normalizes_states(tmp_path):
    ws = tmp_path / "workspaces" / "m-9"
    ws.mkdir(parents=True)
    calls = []

    def runner(command, cwd, timeout, on_output=None):
        calls.append({"command": command, "cwd": str(cwd)})
        return type("CP", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    adapter = _sdk_adapter(runner=runner)
    eng = AuthorizationEngine()
    grant = eng.issue_grant("opencode", "opencode:shell.exec:", issued_by=FACAI_OWNER)
    action = adapter.propose("干活", {"workspace": str(ws), "mission_id": "m-9"})

    outcome = adapter.execute_via_broker(facai_broker, eng, action)

    assert eng.decisions[-1]["decision"] == "ALLOW"
    assert eng.decisions[-1]["grant_id"] == grant.grant_id
    # §15 冻结映射：OPENCODE_COMPLETED → SUCCESS（outcomes.TERMINAL_EVENT_STATES）
    assert outcome.state == "SUCCESS"
    assert calls and calls[0]["command"][0] == "opencode-fake"
    assert calls[0]["cwd"] == str(ws)


def test_opencode_sdk_failed_run_maps_to_execution_failed(tmp_path):
    ws = tmp_path / "workspaces" / "m-10"
    ws.mkdir(parents=True)

    def runner(command, cwd, timeout, on_output=None):
        return type("CP", (), {"returncode": 2, "stdout": "", "stderr": "boom"})()

    adapter = _sdk_adapter(runner=runner)
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "opencode:shell.exec:", issued_by=FACAI_OWNER)
    action = adapter.propose("会失败的一步", {"workspace": str(ws), "mission_id": "m-10"})

    outcome = adapter.execute_via_broker(facai_broker, eng, action)

    assert outcome.state == EXECUTION_FAILED  # 后端跑了且退出非零：执行失败，非拒绝


def test_opencode_sdk_registers_as_l2_rows_with_license():
    reg = CapabilityRegistry()
    rows = register_adapter(reg, _sdk_adapter(), license="Apache-2.0")
    assert rows and all(row.level == L2_ADAPTED for row in rows)
    assert all(row.license == "Apache-2.0" for row in rows)
    assert {row.id for row in rows} == {
        "agent.opencode.run",
        "model.opencode.deepseek_deepseek_flash",
        "model.opencode.facai_glm_glm_4_6",
    }


# --- d) L4 种子抬升 native_coverage ----------------------------------------------

def test_l4_seed_raises_native_coverage():
    reg = CapabilityRegistry()
    register_adapter(reg, _sdk_adapter(), license="Apache-2.0")
    before = reg.native_coverage()
    assert before["native"] == 0 and before["total"] > 0

    register_adapter(reg, FacaiNativePlanner(),
                     license=FACAI_NATIVE_LICENSE, level=L4_FACAI_NATIVE)

    after = reg.native_coverage()
    assert after["native"] == 1
    assert after["total"] == before["total"] + 1
    assert after["coverage"] > before["coverage"]


def test_l4_seed_without_license_registers_and_records():
    # 彻底无审核（2026-10-07）：SDK 登记门不再拒绝 UNKNOWN license 的 L4——
    # license 只是台账字段，能力照常入册。
    reg = CapabilityRegistry()
    register_adapter(reg, FacaiNativePlanner(), license="UNKNOWN", level=L4_FACAI_NATIVE)
    assert reg.get("planning.native_single_step").license == "UNKNOWN"


# --- e) ProviderAdapter 契约出口 ---------------------------------------------------

class _FakeProvider:
    """ProviderAdapter 契约的最小合规实现：complete 只做归一化出口这一件事。"""

    name = "fake-provider"

    def complete(self, model_ref, messages):
        return provider_outcome(200, {"choices": [{"message": {"content": "ok"}}]},
                                backend=self.name)


def test_provider_protocol_accepts_contract_shaped_implementations():
    assert isinstance(_FakeProvider(), ProviderAdapter)
    assert isinstance(FacaiNativePlanner(), AgentAdapter)
    assert isinstance(_sdk_adapter(), AgentAdapter)


def test_provider_outcome_normalizes_through_the_frozen_classifier():
    ok = provider_outcome(200, {"choices": [{"message": {"content": "hi"}}]}, "fake")
    assert ok.state == "SUCCESS" and ok.state in STATES
    refused = provider_outcome(429, {"error": {"message": "rate limited"}}, "fake")
    assert refused.state == PROVIDER_REFUSAL  # THIS BACKEND DID NOT EXECUTE
    timed_out = provider_outcome(None, {"error": "http timeout"}, "fake")
    assert timed_out.state == TIMEOUT
