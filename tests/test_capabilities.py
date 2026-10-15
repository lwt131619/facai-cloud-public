"""Capability Foundry spine: registry, absorption levels, diff reports."""
import pytest

from facai.authority import (
    ALLOW,
    DENY,
    FACAI_OWNER,
    OWNER_GATE,
    GRANT_REQUIRED,
    ActionIR,
    AuthorizationEngine,
    risk_for_action,
)
from facai.capabilities import (
    GRANT_TTL_SECONDS,
    GRANT_USES,
    L0_REFERENCE,
    L2_ADAPTED,
    L3_FORKED,
    L4_FACAI_NATIVE,
    PERMISSION_FAMILIES,
    PROVIDER_CONTRACTS,
    SCHEMA_PERMISSIONS,
    Capability,
    CapabilityError,
    CapabilityRegistry,
    capability_diff,
    denied_permissions,
    grant_templates_for,
    manifest_grant_templates,
)


def cap(cap_id="planning.replan", level=L2_ADAPTED, provider="AgentProvider",
        license="MIT", **over):
    return Capability(id=cap_id, name=cap_id, level=level, provider=provider,
                      license=license, **over)


def test_capability_ids_are_namespaced():
    with pytest.raises(CapabilityError):
        CapabilityRegistry().register(cap(cap_id="replan"))
    with pytest.raises(CapabilityError):
        CapabilityRegistry().register(cap(cap_id="Planning.Replan"))


def test_unknown_level_or_provider_refused():
    with pytest.raises(CapabilityError):
        CapabilityRegistry().register(cap(level="L9_MAGIC"))
    with pytest.raises(CapabilityError):
        CapabilityRegistry().register(cap(provider="GooseMagic"))


def test_fork_and_native_register_with_unknown_license():
    """彻底无审核（2026-10-07）：L3/L4 + UNKNOWN license 照常登记，
    license 作为台账字段如实留档。"""
    reg = CapabilityRegistry()
    reg.register(cap(level=L3_FORKED, license="UNKNOWN"))
    reg.register(cap(level=L4_FACAI_NATIVE, license="UNKNOWN"))
    assert reg.get("planning.replan").license == "UNKNOWN"
    # Tracking copyleft is allowed — it must simply be on the record.
    reg.register(cap(level=L4_FACAI_NATIVE, license="AGPL-3.0"))
    assert reg.get("planning.replan").license == "AGPL-3.0"


def test_registry_roundtrip_and_levels():
    reg = CapabilityRegistry()
    reg.register(cap("planning.replan"))
    reg.register(cap("shell.exec", level=L4_FACAI_NATIVE, provider="ExecutionProvider"))
    reg.register(cap("memory.context_compaction", level=L0_REFERENCE, provider="MemoryProvider"))
    assert reg.ids() == ["memory.context_compaction", "planning.replan", "shell.exec"]
    assert [c.id for c in reg.by_level(L4_FACAI_NATIVE)] == ["shell.exec"]
    assert "AgentProvider" in PROVIDER_CONTRACTS


def test_native_coverage_is_the_strength_metric():
    reg = CapabilityRegistry()
    assert reg.native_coverage() == {"total": 0, "native": 0, "coverage": 0.0}
    for i in range(4):
        reg.register(cap(f"family.ability{i}"))
    reg.register(cap("family.native", level=L4_FACAI_NATIVE, provider="ToolProvider"))
    cov = reg.native_coverage()
    assert cov["total"] == 5 and cov["native"] == 1 and cov["coverage"] == 0.2


def test_capability_diff_shapes_a_repository_report():
    reg = CapabilityRegistry()
    reg.register(cap("shell.exec"))
    report = capability_diff([
        {"id": "planning.replan", "verdict": "new", "native_candidate": True},
        {"id": "browser.session_restore", "verdict": "new"},
        {"id": "shell.exec", "verdict": "duplicate"},
        {"id": "memory.context_compaction", "verdict": "better", "native_candidate": True},
        {"id": "vendor.remote_search", "verdict": "dependency"},
    ], reg)
    assert report["new"] == ["planning.replan", "browser.session_restore"]
    assert report["already_owned"] == ["shell.exec"]
    assert report["better_implementation_candidate"] == ["memory.context_compaction"]
    assert report["external_dependency"] == ["vendor.remote_search"]
    assert report["nativeization_candidate"] == ["planning.replan", "memory.context_compaction"]


def test_entries_without_id_are_skipped():
    assert capability_diff([{"verdict": "new"}, {}], CapabilityRegistry())["new"] == []


# --- Dify 插件 manifest 对标吸收：permissions 节 → 受限 grant 模板 -------------
# （TASKQUEUE 对标吸收项：收录 CLI 产出带权限声明的 capability manifest，
# 注册时映射为受限 grant 模板——deny-by-default，显式声明才放开。）


def test_permission_vocabulary_is_locked_to_broker_capability_actions():
    """词表与 facai/broker.CAPABILITY_ACTIONS 逐字对齐，漂移即测试失败。"""
    from facai.broker import CAPABILITY_ACTIONS

    assert PERMISSION_FAMILIES == CAPABILITY_ACTIONS


def test_denied_permissions_is_the_deny_by_default_baseline():
    perms = denied_permissions()
    assert [p["action"] for p in perms] == list(PERMISSION_FAMILIES)
    assert all(p["allowed"] is False for p in perms)
    assert all(p["resources"] == [] and p["evidence"] == "" for p in perms)


def _manifest_entry(cap_id="shell.exec", license_id="MIT",
                    allowed=(), resources=None) -> dict:
    """一条 manifest 能力条目：六族全枚举，仅 allowed 里的族放开。"""
    perms = denied_permissions("未声明")
    for p in perms:
        if p["action"] in allowed:
            p["allowed"] = True
            p["scope"] = f"{p['action']} 调用面（测试 scope）"
            p["resources"] = list(resources or [])
            p["evidence"] = "app/runner.py:5"
    return {"id": cap_id, "license": license_id, "permissions": perms}


def test_grant_templates_deny_by_default_produces_no_template():
    out = grant_templates_for(_manifest_entry(allowed=()), subject="absorbed:mini")

    assert out["grants"] == []
    assert out["denied"] == list(PERMISSION_FAMILIES)
    assert out["rejected"] == []


def test_grant_templates_follow_capability_grant_semantics():
    out = grant_templates_for(
        _manifest_entry(allowed=("shell.exec",)), subject="absorbed:mini")

    assert len(out["grants"]) == 1
    t = out["grants"][0]
    assert isinstance(t.subject, str) and t.subject == "absorbed:mini"
    assert t.action_key == "facai-native:shell.exec"   # ActionIR.key 形状 backend:action
    assert t.ttl_seconds == GRANT_TTL_SECONDS and t.uses == GRANT_USES
    assert t.risk == risk_for_action("shell.exec")     # 照 authority 风险分级表


def test_declared_resource_scope_pins_exact_action_key():
    entry = _manifest_entry(cap_id="model.invoke",
                            allowed=("network.*", "provider.invoke"),
                            resources=["api.miniprovider.example"])

    out = grant_templates_for(entry, subject="absorbed:mini")

    keys = sorted(t.action_key for t in out["grants"])
    assert keys == ["facai-native:network.*:api.miniprovider.example",
                    "facai-native:provider.invoke:api.miniprovider.example"]
    net = next(t for t in out["grants"] if t.action == "network.*")
    assert net.resource == "api.miniprovider.example"
    assert net.risk == risk_for_action("network.*")


def test_out_of_vocabulary_action_can_never_mint_a_template():
    entry = _manifest_entry(allowed=("shell.exec",))
    entry["permissions"].append(
        {"action": "arbitrary.root", "allowed": True, "scope": "越权声明",
         "resources": [], "evidence": "evil.py:1"})

    out = grant_templates_for(entry, subject="absorbed:mini")

    assert out["rejected"] == ["arbitrary.root"]
    assert all(t.action in PERMISSION_FAMILIES for t in out["grants"])


def test_copyleft_and_unknown_license_rides_the_note_record():
    mit = grant_templates_for(_manifest_entry(license_id="MIT",
                                              allowed=("shell.exec",)),
                              subject="s")
    unknown = grant_templates_for(_manifest_entry(license_id="UNKNOWN",
                                                  allowed=("shell.exec",)),
                                  subject="s")
    agpl = grant_templates_for(_manifest_entry(license_id="AGPL-3.0",
                                               allowed=("shell.exec",)),
                               subject="s")

    assert "license" not in mit["grants"][0].note
    assert "license UNKNOWN 留痕" in unknown["grants"][0].note
    assert "license AGPL-3.0 留痕" in agpl["grants"][0].note


def test_templates_issue_into_authority_engine_and_stay_scoped():
    """模板灌进 AuthorizationEngine 后：圈内 ALLOW、圈外/未声明族 DENY。"""
    entry = _manifest_entry(cap_id="model.invoke",
                            allowed=("network.*",),   # provider.invoke 未声明
                            resources=["api.miniprovider.example"])
    out = grant_templates_for(entry, subject="absorbed:mini", backend="opencode")

    eng = AuthorizationEngine(mode=OWNER_GATE)
    issued = []
    for t in out["grants"]:
        probe = ActionIR(actor=t.subject, backend="opencode", action=t.action,
                         resource=t.resource)
        grant = eng.issue_grant(subject=t.subject, action_key=t.action_key,
                                issued_by=FACAI_OWNER, ttl_seconds=t.ttl_seconds,
                                uses=t.uses, note=t.note, action=probe)
        assert grant.risk == t.risk          # risk 标签随 ActionIR 继承
        issued.append(grant)

    inside = eng.authorize(ActionIR(actor="absorbed:mini", backend="opencode",
                                    action="network.*",
                                    resource="api.miniprovider.example"))
    assert inside["decision"] == ALLOW
    outside = eng.authorize(ActionIR(actor="absorbed:mini", backend="opencode",
                                     action="network.*", resource="other.example"))
    assert outside["decision"] == DENY                # 资源圈外不借道
    undeclared = eng.authorize(ActionIR(actor="absorbed:mini", backend="opencode",
                                        action="provider.invoke"))
    assert undeclared["decision"] == DENY
    assert undeclared["reason"] == GRANT_REQUIRED     # 未声明族无模板 → 无 grant


def test_manifest_grant_templates_aggregates_and_falls_back_to_manifest_license():
    manifest = {
        "schema": SCHEMA_PERMISSIONS,
        "license": "UNKNOWN",
        "capabilities": [
            _manifest_entry(cap_id="a", license_id="", allowed=("shell.exec",)),
            _manifest_entry(cap_id="b", allowed=("docker.run",)),
        ],
    }

    out = manifest_grant_templates(manifest, subject="absorbed:mini")

    assert out["license"] == "UNKNOWN"
    assert {t.action_key for t in out["grants"]} == {
        "facai-native:shell.exec", "facai-native:docker.run"}
    # 无自带 license 的条目回退到 manifest 顶层 license 并留痕；MIT 条目不留痕
    by_key = {t.action_key: t for t in out["grants"]}
    assert "license UNKNOWN 留痕" in by_key["facai-native:shell.exec"].note
    assert "license" not in by_key["facai-native:docker.run"].note
    # 聚合 denied 是逐条目拒绝的并集：条目 a 拒 docker.run…、条目 b 拒 shell.exec… → 六族全在案
    assert out["denied"] == sorted(PERMISSION_FAMILIES)
    assert out["rejected"] == []


def test_registration_door_maps_manifest_permissions_for_registered_caps():
    """注册路径：登记 L0 条目后，其 manifest permissions 映射为受限 grant 模板。"""
    reg = CapabilityRegistry()
    entry = _manifest_entry(cap_id="net.x", license_id="MIT",
                            allowed=("network.*",), resources=["api.x.example"])
    reg.register(Capability(id="net.x", name="absorbed net", level=L0_REFERENCE,
                            provider="ToolProvider", license="MIT"))

    assert reg.get("net.x") is not None
    out = grant_templates_for(entry, subject="absorbed:src")
    assert [t.action_key for t in out["grants"]] == [
        "facai-native:network.*:api.x.example"]
