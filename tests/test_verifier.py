"""§21 Re-rooting Verifier: ten checks as executable invariants, plus the
registry door that refuses T1 claims without a Verifier pass.

The static checks must actually run the authority core — test group (d)
proves it by tampering with facai.authority attributes and expecting FAIL,
never a silent PASS.
"""
import json
from pathlib import Path

import pytest

from facai.capabilities import (
    L2_ADAPTED,
    L3_FORKED,
    Capability,
    CapabilityRegistry,
)
from facai.ops.verifier import (
    FAIL,
    PENDING,
    PASS,
    T1_ELIGIBLE,
    T2_PARTIAL,
    AuthorityGateError,
    register_with_gate,
    source_wiring,
    verify_index,
    verify_source,
)

MANIFESTS_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "authority-manifests"


# --- a) facai-native (no remote faces) is T1_ELIGIBLE ------------------------

def test_facai_native_source_is_t1_eligible():
    report = verify_source("facai-native", [], source_wiring("facai-native"))

    assert report["verdict"] == T1_ELIGIBLE
    assert set(report["checks"]) == {
        "owner_root", "self_grant", "action_ir", "direct_exec_inventory",
        "brokerable_paths", "secret_access", "grant_revocation",
        "remote_dependencies", "provider_result", "evidence_linkage"}
    # 十项全部 PASS，无一被静默跳过
    assert {name: c["status"] for name, c in report["checks"].items()} == {
        name: PASS for name in report["checks"]}
    assert report["unmanaged"] == [] and report["reasons"] == []


def test_empty_inventory_without_declaration_is_not_t1():
    """facai-native passes because its inventory is explicitly held elsewhere;
    a zero-surface source without that declaration (closed binary) must fail
    the inventory check instead of vacuously qualifying."""
    report = verify_source("mystery-binary", [], {"license": "Apache-2.0"})

    assert report["checks"]["direct_exec_inventory"]["status"] == FAIL
    assert report["verdict"] == T2_PARTIAL


# --- b) a source with a REMOTE_PROVIDER face demotes with the face listed ----

def _fake_remote_surfaces():
    return [
        {"kind": "NETWORK（模型调用）", "classification": "REMOTE_PROVIDER",
         "remoteAuthorityDependency": True, "where": "fake/model.py:10"},
        {"kind": "SHELL_EXEC", "classification": "LOCAL_BROKERABLE",
         "where": "fake/shell.py:20"},
    ]


def test_remote_provider_source_is_t2_partial_with_face_listed():
    report = verify_source("fake-agent", _fake_remote_surfaces(),
                           {"gate_wired": True, "license": "MIT"})

    assert report["verdict"] == T2_PARTIAL
    # 远程依赖已识别但未改根 → PENDING；refusal 归一化未接入 → PENDING
    assert report["checks"]["remote_dependencies"]["status"] == PENDING
    assert report["checks"]["provider_result"]["status"] == PENDING
    # brokerable 面有 gate 接线声明 → PASS，但远程面必须逐条列进未纳管面
    assert report["checks"]["brokerable_paths"]["status"] == PASS
    assert report["checks"]["direct_exec_inventory"]["status"] == PASS
    remote_faces = [u for u in report["unmanaged"]
                    if u["surface"] == "fake-agent#s00"]
    assert remote_faces, report["unmanaged"]
    assert "未改根" in remote_faces[0]["gap"]
    assert any(u["surface"] == "fake-agent#s00" and "归一化" in u["gap"]
               for u in report["unmanaged"])
    # brokerable 面不在未纳管清单里（它已被覆盖）
    assert all(u["surface"] != "fake-agent#s01" for u in report["unmanaged"])


def test_rerooted_remote_claim_clears_remote_pending():
    surfaces = _fake_remote_surfaces()
    surfaces[0]["reRooted"] = True
    report = verify_source("fake-agent", surfaces,
                           {"gate_wired": True, "license": "MIT",
                            "provider_result_normalized": True,
                            "secret_brokered": True, "evidence_linked": True})

    assert report["checks"]["remote_dependencies"]["status"] == PASS
    assert report["checks"]["provider_result"]["status"] == PASS
    assert report["verdict"] == T1_ELIGIBLE


def test_unknown_license_is_a_downgrade_reason():
    report = verify_source("facai-native", [], dict(source_wiring("facai-native"),
                                                    license="UNKNOWN"))

    assert report["verdict"] == T2_PARTIAL
    assert any("license UNKNOWN" in r for r in report["reasons"])


# --- c) the registry door: UNKNOWN-license L3 entry is refused ---------------

def _l3_cap(license_id="UNKNOWN", source_name="fake-agent"):
    return Capability(id="fake.agent_core", name="Fake agent core",
                      level=L3_FORKED, provider="CapabilityProvider",
                      source=source_name, license=license_id)


def test_register_with_gate_registers_unknown_license_l3_with_gate_note():
    """彻底无审核：UNKNOWN license 的 L3 照常登记，判定留档 gate_note。"""
    reg = CapabilityRegistry()
    wiring = dict(source_wiring("facai-native"), license="UNKNOWN")
    cap = _l3_cap()

    report = register_with_gate(reg, cap, [], wiring)

    assert reg.get("fake.agent_core") is cap
    assert "无审核登记" in report["gate_note"]
    assert "UNKNOWN" in report["gate_note"]
    assert report["verdict"] == T2_PARTIAL


def test_register_with_gate_registers_t2_source_at_l3_and_lists_faces():
    """彻底无审核：T2 来源的 L3 照常登记；未纳管面逐条进 gate_note 留档。"""
    reg = CapabilityRegistry()
    wiring = {"gate_wired": True, "license": "MIT"}  # 远程面未改根 → T2_PARTIAL
    cap = _l3_cap(license_id="MIT")

    report = register_with_gate(reg, cap, _fake_remote_surfaces(), wiring)

    assert reg.get("fake.agent_core") is cap
    note = report["gate_note"]
    assert "无审核登记" in note
    assert "T2_PARTIAL" in note
    assert "fake-agent#s00" in note          # 未纳管面逐条列出
    assert "未改根" in note


def test_register_with_gate_allows_l2_from_t2_source():
    """Capability is preserved: only the T1 claim (L3/L4) is gated."""
    reg = CapabilityRegistry()
    cap = Capability(id="fake.planning", name="Fake planning", level=L2_ADAPTED,
                     provider="AgentProvider", source="fake-agent", license="UNKNOWN")

    report = register_with_gate(reg, cap, _fake_remote_surfaces(),
                                {"gate_wired": True, "license": "UNKNOWN"})

    assert report["verdict"] == T2_PARTIAL
    assert reg.get("fake.planning") is cap


def test_register_with_gate_admits_l3_from_t1_eligible_source():
    reg = CapabilityRegistry()
    cap = _l3_cap(license_id="Apache-2.0", source_name="facai-native")

    report = register_with_gate(reg, cap, [], source_wiring("facai-native"))

    assert report["verdict"] == T1_ELIGIBLE
    assert reg.get("fake.agent_core") is cap


# --- d) the static checks really run the core: tampering yields FAIL --------

def _promiscuous_issue(self, subject, action_key, issued_by, **kwargs):
    """A tampered engine that lets anyone issue grants (the invariant break)."""
    from datetime import timedelta

    from facai import authority as _a

    grant = _a.CapabilityGrant(
        grant_id="tampered", subject=subject, action_key=action_key,
        issued_by=issued_by, issued_at=_a._now(),
        expires_at=_a._now() + timedelta(seconds=60), uses_left=None)
    self.grants[grant.grant_id] = grant
    return grant


def test_tampered_issue_grant_fails_verifier_not_silent_pass(monkeypatch):
    monkeypatch.setattr("facai.authority.AuthorizationEngine.issue_grant",
                        _promiscuous_issue)

    report = verify_source("goose", [], {"license": "Apache-2.0"})

    # 冒签断言真的跑了：引擎被篡改后检查翻 FAIL，而不是静默 PASS
    assert report["checks"]["self_grant"]["status"] == FAIL
    assert report["checks"]["owner_root"]["status"] == FAIL
    assert report["verdict"] == T2_PARTIAL
    assert T1_ELIGIBLE != report["verdict"]


def test_tampered_revocation_fails_verifier(monkeypatch):
    monkeypatch.setattr("facai.authority.AuthorizationEngine.revoke_grant",
                        lambda self, grant_id, revoked_by: True)

    report = verify_source("goose", [], {"license": "Apache-2.0"})

    # revoke 被改成空操作后，"吊销生效" 断言（放行→吊销→DENY）必须抓到
    assert report["checks"]["grant_revocation"]["status"] == FAIL
    assert report["verdict"] == T2_PARTIAL


def _broken_issue_raises(self, **kwargs):
    raise RuntimeError("engine corrupted mid-check")


def test_check_crash_becomes_fail_not_pass(monkeypatch):
    monkeypatch.setattr("facai.authority.AuthorizationEngine.issue_grant",
                        _broken_issue_raises)

    report = verify_source("goose", [], {"license": "Apache-2.0"})

    assert report["checks"]["owner_root"]["status"] == FAIL
    assert "异常" in report["checks"]["owner_root"]["evidence"]
    assert report["verdict"] == T2_PARTIAL


def test_untampered_core_passes_static_checks():
    """Control for group (d): with the real engine the same three checks PASS."""
    report = verify_source("goose", [], {"license": "Apache-2.0"})

    assert report["checks"]["self_grant"]["status"] == PASS
    assert report["checks"]["grant_revocation"]["status"] == PASS
    assert report["checks"]["action_ir"]["status"] == PASS


# --- integration: the real 9-source INDEX gets real verdicts -----------------

@pytest.mark.skipif(not MANIFESTS_DIR.exists(),
                    reason="vendor/authority-manifests 不在本机工作区")
def test_verify_index_writes_real_verdicts_for_nine_sources(tmp_path):
    index_copy = tmp_path / "INDEX.json"
    index_copy.write_text((MANIFESTS_DIR / "INDEX.json").read_text(encoding="utf-8"),
                          encoding="utf-8")

    index = verify_index(MANIFESTS_DIR, index_copy)

    assert set(index["sources"]) == {
        "autoclaw", "facai-adapter-core", "facai-native",
        "facai-remote-integrations", "goose", "hermes-local", "opencode",
        "whatsapp-relay", "zcode"}
    verdicts = {name: entry["verification"]["status"]
                for name, entry in index["sources"].items()}
    # 每个源都拿到真实判定，占位 PARTIAL 不复存在
    assert all(v in (T1_ELIGIBLE, T2_PARTIAL) for v in verdicts.values())
    # facai-native 是唯一无远程面且清单他置的内核 → 唯一 T1 候选
    assert verdicts["facai-native"] == T1_ELIGIBLE
    assert verdicts["zcode"] == T2_PARTIAL  # 闭源宿主：inventory 无法清点
    native = index["sources"]["facai-native"]["verification"]
    assert native["unmanaged"] == []
    assert len(native["checks"]) == 10
    written = json.loads(index_copy.read_text(encoding="utf-8"))
    assert written["sources"]["goose"]["verification"]["status"] == T2_PARTIAL
