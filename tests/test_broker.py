"""Capability Broker + Secret Broker (batches [3]+[4]).

Covers, per the merge-batch contract:

a) a request with no grant is refused with CapabilityDenied before anything
   is dispatched, and the engine still records the refusal;
b) mission grant + capability grant yield a scoped capability whose ttl is
   the grant's remaining life, with the ALLOW decision on the engine record;
c) secret.request resolves a secretbox-encrypted token for this execution
   only — wrong grant gets nothing, the plaintext never reaches the engine
   decision log;
d) inject_scoped_env is whitelist-based: sensitive os.environ keys never
   enter the child env, granted keys do;
e) the OpenCode child env is whitelisted and the user-model injection chain
   still works through the real ``_run`` subprocess path (the existing
   execution suites remain the behavior guards).
"""

import json
import sys

import pytest

from facai.authority import (
    DENY,
    FACAI_OWNER,
    GRANT_EXHAUSTED,
    GRANT_REQUIRED,
    AuthorizationEngine,
)
from facai.broker import (
    CAPABILITY_ACTIONS,
    CapabilityDenied,
    inject_scoped_env,
    request_capability,
    resolve_secret,
    system_env_base,
)
from facai.opencode import OpenCodeAdapter
from facai.secretbox import encrypt


# --- the frozen action family -------------------------------------------------

def test_capability_action_family_is_frozen():
    assert CAPABILITY_ACTIONS == ("shell.exec", "filesystem.write", "network.*",
                                  "docker.run", "secret.request", "provider.invoke")


# --- a) no grant → CapabilityDenied --------------------------------------------

def test_request_without_grant_is_denied_with_reason():
    eng = AuthorizationEngine()  # MISSION_AUTONOMOUS: no default-open
    with pytest.raises(CapabilityDenied) as excinfo:
        request_capability(eng, "opencode", "opencode", "shell.exec",
                           resource="/workspace/x", mission_id="m-1")
    assert excinfo.value.reason == GRANT_REQUIRED
    assert excinfo.value.verdict["decision"] == DENY
    # 拒绝同样在引擎留痕：裁决属于引擎，broker 只是不派发。
    assert eng.decisions[-1]["decision"] == DENY
    assert eng.decisions[-1]["reason"] == GRANT_REQUIRED


def test_unknown_action_is_refused_without_reaching_the_engine():
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "*", issued_by=FACAI_OWNER)
    with pytest.raises(CapabilityDenied) as excinfo:
        request_capability(eng, "opencode", "opencode", "model.complete")
    assert excinfo.value.reason == "UNKNOWN_ACTION"
    assert eng.decisions == []  # 未列入动作族的动作根本不进入裁决


def test_expired_capability_grant_denies_with_expired_reason():
    from datetime import timedelta

    eng = AuthorizationEngine()
    grant = eng.issue_grant("opencode", "opencode:shell.exec",
                            issued_by=FACAI_OWNER, ttl_seconds=60)
    object.__setattr__(grant, "expires_at", grant.issued_at - timedelta(seconds=1))
    with pytest.raises(CapabilityDenied) as excinfo:
        request_capability(eng, "opencode", "opencode", "shell.exec")
    assert excinfo.value.reason == "GRANT_EXPIRED"


# --- b) mission + capability grants → scoped capability, engine record ---------

def test_mission_and_capability_grants_yield_scoped_capability():
    eng = AuthorizationEngine()
    mission_grant = eng.issue_grant("opencode", "mission:m-1",
                                    issued_by=FACAI_OWNER, ttl_seconds=600)
    cap_grant = eng.issue_grant("opencode", "opencode:shell.exec:",
                                issued_by=FACAI_OWNER, ttl_seconds=300)

    before = len(eng.decisions)
    outcome = request_capability(eng, "opencode", "opencode", "shell.exec",
                                 resource="/workspace/x", mission_id="m-1",
                                 scope={"purpose": "build"})

    assert outcome["capability"]["action"] == "shell.exec"
    assert outcome["capability"]["backend"] == "opencode"
    assert outcome["capability"]["resource"] == "/workspace/x"
    assert outcome["capability"]["subject"] == "opencode"
    assert outcome["scope"] == {"purpose": "build"}
    assert isinstance(outcome["ttl"], int) and 0 < outcome["ttl"] <= 600
    # engine 留痕：本次请求恰好多出一条 ALLOW 决策。
    assert len(eng.decisions) == before + 1
    record = eng.decisions[-1]
    assert record["decision"] == "ALLOW"
    assert record["action"] == "opencode:shell.exec:/workspace/x"
    # 引擎决定由哪张 grant 承载（mission 或 action grant，冻结语义）；
    # broker 只引用裁决结果，不挑选 grant。
    assert record["grant_id"] in (mission_grant.grant_id, cap_grant.grant_id)
    assert outcome["capability"]["grant_id"] == record["grant_id"]


def test_capability_ttl_follows_only_the_backing_grant():
    eng = AuthorizationEngine()
    grant = eng.issue_grant("opencode", "opencode:filesystem.write:",
                            issued_by=FACAI_OWNER, ttl_seconds=90)
    outcome = request_capability(eng, "opencode", "opencode", "filesystem.write",
                                 resource="/workspace/x")
    assert 0 < outcome["ttl"] <= 90
    assert outcome["capability"]["grant_id"] == grant.grant_id


def test_use_limited_grant_exhausts_through_the_broker():
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "opencode:docker.run",
                    issued_by=FACAI_OWNER, uses=1)
    first = request_capability(eng, "opencode", "opencode", "docker.run")
    assert first["capability"]["action"] == "docker.run"
    with pytest.raises(CapabilityDenied) as excinfo:
        request_capability(eng, "opencode", "opencode", "docker.run")
    assert excinfo.value.reason == GRANT_EXHAUSTED


def test_unlimited_grant_reports_none_ttl():
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "opencode:network.*:", issued_by=FACAI_OWNER)
    outcome = request_capability(eng, "opencode", "opencode", "network.*",
                                 resource="https://api.example.com")
    assert outcome["ttl"] is None


# --- c) secret.request: secretbox in, plaintext for this execution only --------

def _engine_with_secret_grant(subject="opencode"):
    eng = AuthorizationEngine()
    eng.issue_grant(subject, "facai-native:secret.request:",
                    issued_by=FACAI_OWNER, ttl_seconds=300)
    return eng


def test_secret_request_resolves_secretbox_value_in_memory(tmp_path):
    eng = _engine_with_secret_grant()
    token = encrypt(tmp_path, "sk-live-value")
    before = len(eng.decisions)

    outcome = request_capability(eng, "opencode", "facai-native", "secret.request",
                                 resource="FACAI_USER_MODEL_KEY",
                                 scope={"root": tmp_path, "token": token})

    assert outcome["denied"] is False
    assert outcome["secret"] == {"granted": True, "reason": "",
                                 "env_name": "FACAI_USER_MODEL_KEY",
                                 "value": "sk-live-value"}
    assert outcome["capability"]["action"] == "secret.request"
    # 传输用键不回显：token/root 不随 scope 扩散。
    assert "token" not in outcome["scope"] and "root" not in outcome["scope"]
    # 引擎留痕且明文绝不进决策日志。
    assert len(eng.decisions) == before + 1
    assert eng.decisions[-1]["decision"] == "ALLOW"
    assert "sk-live-value" not in json.dumps(eng.decisions)


def test_secret_request_with_wrong_grant_yields_no_value(tmp_path):
    eng = _engine_with_secret_grant("opencode")
    token = encrypt(tmp_path, "sk-live-value")
    with pytest.raises(CapabilityDenied) as excinfo:
        request_capability(eng, "goose", "facai-native", "secret.request",
                           resource="FACAI_USER_MODEL_KEY",
                           scope={"root": tmp_path, "token": token})
    assert excinfo.value.reason == GRANT_REQUIRED
    # 拒绝路径从未解密：值不进异常、不进任何决策记录。
    assert "sk-live-value" not in str(excinfo.value)
    assert "sk-live-value" not in json.dumps(eng.decisions)


def test_resolve_secret_denies_without_value_when_scope_lacks_token(tmp_path):
    outcome = resolve_secret(tmp_path, "FACAI_X", {})
    assert outcome["granted"] is False
    assert outcome["reason"] == "NO_SECRET_IN_SCOPE"
    assert outcome["value"] is None


def test_resolve_secret_denies_without_value_on_decrypt_failure(tmp_path):
    outcome = resolve_secret(tmp_path, "FACAI_X", {"token": "not-a-fernet-token"})
    assert outcome["granted"] is False
    assert outcome["reason"] == "DECRYPT_FAILED"
    assert outcome["value"] is None


def test_secret_request_operational_failure_returns_deny_not_value(tmp_path):
    """An ALLOW verdict with an undecryptable token yields deny — never a value."""
    eng = _engine_with_secret_grant()
    outcome = request_capability(eng, "opencode", "facai-native", "secret.request",
                                 resource="FACAI_X", scope={"root": tmp_path})
    assert outcome["denied"] is True
    assert outcome["capability"] is None
    assert outcome["secret"]["value"] is None


# --- d) inject_scoped_env: whitelist base + explicit grants ---------------------

def test_inject_scoped_env_is_whitelist_based(monkeypatch):
    monkeypatch.setenv("FACAI_BROKER_LEAK_PROBE", "should-not-travel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    base = system_env_base()
    assert "FACAI_BROKER_LEAK_PROBE" not in base
    assert "AWS_SECRET_ACCESS_KEY" not in base

    env = inject_scoped_env(base, {"FACAI_USER_MODEL_KEY": "sk-granted"})
    assert env["FACAI_USER_MODEL_KEY"] == "sk-granted"
    assert "FACAI_BROKER_LEAK_PROBE" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env.get("PATH")  # 系统最小集仍在（子进程要能找到二进制）


def test_inject_scoped_env_accepts_secret_outcomes_and_skips_denied():
    granted = {"granted": True, "reason": "", "env_name": "FACAI_K", "value": "v"}
    denied = {"granted": False, "reason": "NO_SECRET_IN_SCOPE",
              "env_name": "FACAI_NO", "value": None}
    assert inject_scoped_env({}, [granted, denied]) == {"FACAI_K": "v"}


def test_extra_allowlist_passes_only_named_keys(monkeypatch):
    monkeypatch.setenv("FACAI_CHILD_ENV_ALLOWLIST", "OPENCODE_AUTH_CONTENT,GOOSE_PROVIDER")
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "explicitly-named")
    monkeypatch.setenv("GOOSE_PROVIDER", "explicit-too")
    monkeypatch.setenv("FACAI_OTHER_THING", "still-hidden")
    base = system_env_base()
    assert base["OPENCODE_AUTH_CONTENT"] == "explicitly-named"
    assert base["GOOSE_PROVIDER"] == "explicit-too"
    assert "FACAI_OTHER_THING" not in base


# --- e) opencode child env: whitelisted, user-model chain intact ---------------

def test_opencode_child_env_whitelists_host_and_injects_user_model_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_BROKER_LEAK_PROBE", "must-not-travel")
    script = ("import os, sys;"
              "print('KEY=' + str(os.environ.get('FACAI_USER_MODEL_KEY')));"
              "print('LEAK=' + str(os.environ.get('FACAI_BROKER_LEAK_PROBE')));"
              "print('HASPATH=' + str(bool(os.environ.get('PATH'))))")
    completed = OpenCodeAdapter._run([sys.executable, "-u", "-c", script], tmp_path, 60,
                                     env={"FACAI_USER_MODEL_KEY": "sk-injected"})
    assert completed.returncode == 0, completed.stderr
    assert "KEY=sk-injected" in completed.stdout      # 显式注入到达子进程
    assert "LEAK=None" in completed.stdout            # 宿主敏感键不再全量继承
    assert "HASPATH=True" in completed.stdout         # 系统最小集保持可用


def test_secret_request_feeds_opencode_child_env_end_to_end(tmp_path, monkeypatch):
    """request_capability → resolve_secret → inject_scoped_env → real _run."""
    monkeypatch.setenv("FACAI_BROKER_LEAK_PROBE", "host-secret")
    eng = _engine_with_secret_grant()
    token = encrypt(tmp_path, "sk-e2e-value")
    outcome = request_capability(eng, "opencode", "facai-native", "secret.request",
                                 resource="FACAI_USER_MODEL_KEY",
                                 scope={"root": tmp_path, "token": token})
    child_env = inject_scoped_env(system_env_base(), [outcome["secret"]])
    assert child_env["FACAI_USER_MODEL_KEY"] == "sk-e2e-value"
    assert "FACAI_BROKER_LEAK_PROBE" not in child_env

    script = ("import os, sys;"
              "print('KEY=' + str(os.environ.get('FACAI_USER_MODEL_KEY')));"
              "print('LEAK=' + str(os.environ.get('FACAI_BROKER_LEAK_PROBE')))")
    completed = OpenCodeAdapter._run([sys.executable, "-u", "-c", script], tmp_path, 60,
                                     env={k: v for k, v in child_env.items()
                                          if k == "FACAI_USER_MODEL_KEY"})
    assert completed.returncode == 0, completed.stderr
    assert "KEY=sk-e2e-value" in completed.stdout
    assert "LEAK=None" in completed.stdout
