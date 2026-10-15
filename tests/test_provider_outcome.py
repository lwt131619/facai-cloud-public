"""§15 Provider Refusal Normalization: the nine states are load-bearing.

What is frozen here:

- ``facai.outcomes`` maps every upstream shape the gateway classifies onto
  exactly the nine §15 states — never a catch-all MISSION_FAILED.
- PROVIDER_REFUSAL keeps ``mission_failed == False`` (§14: OWNER_APPROVED +
  PROVIDER_REFUSAL is a legal pair; THIS BACKEND DID NOT EXECUTE).
- Failure events carry ``outcome_state`` so the console can show the true
  state instead of an undifferentiated error.
- The owner-facing copy for a refusal never says the request could not be
  understood — it says the model stopped and another way is being tried.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import facai.outcomes as outcomes
from facai.api import create_app
from facai.authority import ExecutionOutcome
from facai.opencode import OpenCodeAdapter
from facai.outcomes import (
    BACKEND_UNAVAILABLE,
    CAPABILITY_MISSING,
    EXECUTION_FAILED,
    FACAI_DENIED,
    GRANT_REQUIRED,
    PROVIDER_REFUSAL,
    RESOURCE_UNAVAILABLE,
    STATES,
    SUCCESS,
    TIMEOUT,
    normalize_backend_response,
    normalize_local_decision,
    outcome_from_adapter_error,
    to_user_copy,
)


# --- 1) the nine-state table -------------------------------------------------

def test_states_is_exactly_the_frozen_nine():
    """§15 names all nine states; none may be missing, renamed or duplicated."""
    assert STATES == (
        SUCCESS, PROVIDER_REFUSAL, FACAI_DENIED, GRANT_REQUIRED,
        CAPABILITY_MISSING, RESOURCE_UNAVAILABLE, BACKEND_UNAVAILABLE,
        EXECUTION_FAILED, TIMEOUT,
    )
    assert len(set(STATES)) == 9


@pytest.mark.parametrize(("status", "body", "expected"), [
    # 429: rate limit is the provider declining THIS request, not a mission death.
    (429, {"error": {"message": "rate limit exceeded"}}, PROVIDER_REFUSAL),
    # 401/403: auth-type refusal, hint attached in the detail.
    (401, {"error": {"message": "invalid api key"}}, PROVIDER_REFUSAL),
    (403, {"error": {"message": "permission denied"}}, PROVIDER_REFUSAL),
    # Content refusals over 200.
    (200, {"choices": [{"message": {"refusal": "cannot help with that"},
                        "finish_reason": "stop"}]}, PROVIDER_REFUSAL),
    (200, {"choices": [{"message": {}, "finish_reason": "content_filter"}]},
     PROVIDER_REFUSAL),
    (200, {"stop_reason": "refusal"}, PROVIDER_REFUSAL),
    # Any other 4xx: the provider was reached and declined to execute.
    (400, {"error": {"message": "malformed"}}, PROVIDER_REFUSAL),
    (404, {"error": {"message": "unknown model"}}, PROVIDER_REFUSAL),
    # 504 and transport timeouts: TIMEOUT, not "backend unavailable".
    (504, {}, TIMEOUT),
    (None, {"error": "ReadTimeout"}, TIMEOUT),
    # 5xx and transport errors: the backend answered (or could not be
    # reached) and cannot serve right now.
    (500, {"error": {"message": "boom"}}, BACKEND_UNAVAILABLE),
    (502, {}, BACKEND_UNAVAILABLE),
    (503, {}, BACKEND_UNAVAILABLE),
    (507, {}, BACKEND_UNAVAILABLE),
    (None, {"error": "ConnectError"}, BACKEND_UNAVAILABLE),
    # Tool failure after the run started: execution failed, nobody refused.
    (200, {"choices": [{"message": {}, "finish_reason": "tool_error"}]},
     EXECUTION_FAILED),
    # A completed upstream answer.
    (200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
     SUCCESS),
    (200, {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"},
     SUCCESS),
])
def test_normalize_backend_response_maps_the_full_remote_table(status, body, expected):
    outcome = normalize_backend_response(status, body, backend="deepseek")
    assert outcome.state == expected
    # The backend name rides the audit detail; the state is the nine-state.
    assert "deepseek" in outcome.detail


def test_auth_refusal_carries_the_credential_hint():
    outcome = normalize_backend_response(401, {"error": {"message": "bad key"}},
                                         backend="kimi")
    assert outcome.state == PROVIDER_REFUSAL
    assert "API Key" in outcome.detail
    # §14: the refusal names this backend only — no grant/mission language.
    assert "kimi" in outcome.detail
    assert "grant" not in outcome.detail.lower()


def test_local_decisions_map_to_local_states_never_provider_refusal():
    """本地拒绝→FACAI_DENIED；缺 grant→GRANT_REQUIRED；都不是 provider 说的话。"""
    for reason in ("GRANT_REQUIRED", "GRANT_EXPIRED", "GRANT_EXHAUSTED"):
        assert normalize_local_decision(reason, backend="opencode").state == GRANT_REQUIRED
    assert normalize_local_decision("step_already_completed", backend="opencode").state \
        == FACAI_DENIED
    # 本地事实不得伪装成远端拒绝
    for reason in ("GRANT_REQUIRED", "step_already_completed"):
        assert normalize_local_decision(reason).state != PROVIDER_REFUSAL


def test_adapter_errors_classify_before_any_terminal_status():
    assert outcome_from_adapter_error(
        subprocess.TimeoutExpired("cmd", 5), backend="opencode").state == TIMEOUT
    assert outcome_from_adapter_error(
        ValueError("OpenCode binary is not installed"), backend="opencode").state \
        == CAPABILITY_MISSING
    assert outcome_from_adapter_error(
        FileNotFoundError("workspace missing"), backend="opencode").state \
        == RESOURCE_UNAVAILABLE
    assert outcome_from_adapter_error(
        RuntimeError("runner exploded"), backend="opencode").state == EXECUTION_FAILED


# --- 2) the §15 invariant: PROVIDER_REFUSAL is not a mission failure ---------

@pytest.mark.parametrize(("status", "body", "state"), [
    (429, {"error": {"message": "rate limit"}}, PROVIDER_REFUSAL),
    (401, {}, PROVIDER_REFUSAL),
    (403, {}, PROVIDER_REFUSAL),
    (400, {"error": {"message": "bad request"}}, PROVIDER_REFUSAL),
    (200, {"stop_reason": "refusal"}, PROVIDER_REFUSAL),
])
def test_provider_refusal_never_fails_the_mission(status, body, state):
    """§14/§15: PROVIDER_REFUSAL 只影响该 backend，不撤销 Owner 的 mission grant。"""
    outcome = normalize_backend_response(status, body, backend="glm")
    assert outcome.state == state
    assert outcome.mission_failed is False
    assert outcome.executed is False


def test_no_normalized_state_is_ever_a_mission_failure():
    """全部九态（经归一化函数可达的）一律 mission_failed==False。"""
    for status, body in [(200, {"choices": [{"message": {"content": "x"},
                                             "finish_reason": "stop"}]}),
                         (429, {}), (401, {}), (504, {}), (503, {}),
                         (None, {"error": "ConnectError"}),
                         (200, {"choices": [{"message": {}, "finish_reason": "tool_error"}]})]:
        outcome = normalize_backend_response(status, body)
        assert outcome.state in STATES
        assert outcome.mission_failed is False
    # The frozen wrapper agrees on the raw state too (verifier probe parity).
    assert ExecutionOutcome(state=PROVIDER_REFUSAL, detail="probe").mission_failed is False
    assert ExecutionOutcome(state=FACAI_DENIED, detail="probe").mission_failed is False
    assert ExecutionOutcome(state=GRANT_REQUIRED, detail="probe").mission_failed is False


# --- 3) failure events carry outcome_state -----------------------------------

class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr="", timeout=False, raise_exc=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, command, cwd, timeout):
        self.calls.append({"command": list(command), "cwd": str(cwd)})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.timeout:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


class BurstAdapter:
    """Adapter whose execute always raises the given exception."""

    def __init__(self, exc):
        self.exc = exc

    def execute(self, mission_id, root, prompt, model_ref=None, timeout=300, on_output=None):
        raise self.exc


def make_client(tmp_path, adapter):
    return TestClient(create_app(tmp_path, opencode_adapter=adapter))


def run_one(client, title):
    mission = client.post('/api/v1/missions', json={
        'title': title, 'original_input': f'要求：{title}', 'steps': ['开发', '测试']}).json()
    client.put(f"/api/v1/missions/{mission['id']}/binding",
               json={'agent_id': 'opencode', 'model_id': 'deepseek-flash'})
    client.post(f"/api/v1/missions/{mission['id']}/opencode/run",
                json={'prompt': 'task', 'step_id': mission['steps'][0]['id']})
    return mission['id']


def events_of(client, mission_id):
    return client.get(f"/api/v1/missions/{mission_id}/events").json()


def test_nonzero_exit_event_carries_execution_failed_state(tmp_path):
    """非零退出：后端真的跑过并退出非零——EXECUTION_FAILED，不是任何人的拒绝。"""
    adapter = OpenCodeAdapter(binary='opencode',
                              runner=FakeRunner(returncode=2, stderr='tests failed\n'))
    client = make_client(tmp_path, adapter)
    mission_id = run_one(client, '非零退出')
    failed = [e for e in events_of(client, mission_id) if e['type'] == 'OPENCODE_FAILED']
    assert len(failed) == 1
    # 现有键不动，只加键
    assert failed[0]['payload']['exit_code'] == 2
    assert failed[0]['payload']['outcome_state'] == EXECUTION_FAILED
    assert failed[0]['payload']['outcome_state'] in STATES


def test_timeout_event_carries_timeout_state(tmp_path):
    adapter = OpenCodeAdapter(binary='opencode', runner=FakeRunner(timeout=True))
    client = make_client(tmp_path, adapter)
    mission_id = run_one(client, '超时')
    timed_out = [e for e in events_of(client, mission_id) if e['type'] == 'OPENCODE_TIMEOUT']
    assert len(timed_out) == 1
    assert timed_out[0]['payload']['outcome_state'] == TIMEOUT
    # 归一化边界：超时事件不得被折叠成执行失败或拒绝
    assert timed_out[0]['payload']['outcome_state'] != EXECUTION_FAILED


def test_adapter_exception_event_carries_classified_state(tmp_path):
    """adapter 执行异常：异常被归类进九态后落事件，而非裸错误名。"""
    adapter = BurstAdapter(RuntimeError("runner exploded"))
    client = make_client(tmp_path, adapter)
    mission_id = run_one(client, '异常')
    failed = [e for e in events_of(client, mission_id) if e['type'] == 'OPENCODE_FAILED']
    assert len(failed) == 1
    assert failed[0]['payload']['error'] == 'RuntimeError'  # 既有键保留
    assert failed[0]['payload']['outcome_state'] == EXECUTION_FAILED


def test_mission_denied_event_carries_grant_required_state(tmp_path):
    """无审核模式（2026-10-07）下门自动补签、运行期不再拒绝；本钉保住的是
    事件→九态映射本身：GRANT_REQUIRED 永远不是 PROVIDER_REFUSAL，历史
    拒绝事件（存量数据/OWNER_GATE 模式）也不得伪装成后端说过的话。"""
    from facai.outcomes import normalize_local_decision

    state = normalize_local_decision("GRANT_REQUIRED", backend="facai-native").state
    assert state == GRANT_REQUIRED
    assert state != PROVIDER_REFUSAL


def test_terminal_success_event_carries_success_state(tmp_path):
    adapter = OpenCodeAdapter(binary='opencode', runner=FakeRunner(returncode=0, stdout='ok\n'))
    client = make_client(tmp_path, adapter)
    mission_id = run_one(client, '完成')
    completed = [e for e in events_of(client, mission_id)
                 if e['type'] == 'OPENCODE_COMPLETED']
    assert len(completed) == 1
    assert completed[0]['payload']['outcome_state'] == SUCCESS


def test_all_failure_event_states_survive_the_round_trip(tmp_path):
    """每个落事件的 outcome_state 都在九态表里，且能翻成用户文案。"""
    adapter = OpenCodeAdapter(binary='opencode',
                              runner=FakeRunner(returncode=1, stderr='boom\n'))
    client = make_client(tmp_path, adapter)
    mission_id = run_one(client, '往返')
    for event in events_of(client, mission_id):
        state = event['payload'].get('outcome_state')
        if state is not None:
            assert state in STATES
            assert to_user_copy(state)


# --- 4) the user copy: a refusal never reads as incomprehension --------------

BANNED = ("无法理解", "不能理解", "看不懂", "无法解析你的需求")


def test_refusal_copy_says_the_model_stopped_not_that_it_could_not_understand():
    copy = to_user_copy(PROVIDER_REFUSAL)
    assert copy == "当前使用的模型没有继续处理这一步，正在检查其他可用方式。"
    for banned in BANNED:
        assert banned not in copy


def test_user_copy_exists_for_all_nine_states_and_never_claims_incomprehension():
    for state in STATES:
        copy = to_user_copy(state)
        assert copy
        for banned in BANNED:
            assert banned not in copy, f"{state} 的用户文案出现了「{banned}」"


def test_user_copy_refuses_unknown_states():
    with pytest.raises(ValueError):
        to_user_copy("MISSION_FAILED")  # 恰恰是不允许存在的折叠态
