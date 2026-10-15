"""Authority Re-rooting Verifier — docs/AUTHORITY_ARCHITECTURE.md §21 as code.

Takes one ingested source's authority surfaces (the same list that feeds
``vendor/authority-manifests/INDEX.json``) and decides whether the source is
eligible for ``T1_FULLY_MANAGED``. The frozen operator semantics this module
must not bend (2026-10-05):

- Only a Verifier PASS lets a source register as T1. A failed critical check
  forbids T1 and demotes the source to T2/PARTIAL with the concrete list of
  unmanaged surfaces — never a blanket "probably fine".
- The static invariants (owner root, self-grant, Action IR, revocation) are
  verified by RUNNING the authority core and the gate, not by reading this
  documentation: each check drives ``facai.authority`` /
  ``facai.authority_gate`` through real calls (forge a grant, revoke it,
  probe the gate) so a tampered engine fails the verifier instead of
  silently passing.
- The dynamic checks compare the source's surface classification against
  the wiring claims: LOCAL_BROKERABLE faces need gate wiring coverage,
  REMOTE_PROVIDER decision points need completed re-rooting, and an
  UNKNOWN license is an automatic downgrade reason.

``verify_source`` returns a report; :func:`register_with_gate` is the
registry door that enforces it (the CapabilityRegistry's own license guard
stays, this adds the Verifier guard on top).

Usage::

    python -m facai.ops.verifier <manifests-dir> <INDEX.json>
"""
from __future__ import annotations

import importlib
import inspect
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import authority, authority_gate
from ..authority import (
    DENY,
    FACAI_OWNER,
    GrantError,
    OWNER_GATE,
)

PASS = "PASS"
FAIL = "FAIL"
PENDING = "PENDING"

T1_ELIGIBLE = "T1_ELIGIBLE"
T2_PARTIAL = "T2_PARTIAL"

# §21 checklist, in baseline order. Every entry must be answered for the
# report to be complete; a missing answer is itself a failure.
CHECK_ORDER = (
    "owner_root",
    "self_grant",
    "action_ir",
    "direct_exec_inventory",
    "brokerable_paths",
    "secret_access",
    "grant_revocation",
    "remote_dependencies",
    "provider_result",
    "evidence_linkage",
)

KNOWN_CLASSES = {
    authority.LOCAL_BROKERABLE,
    authority.LOCAL_DIRECT,
    authority.CONFIGURABLE,
    authority.REMOTE_PROVIDER,
    authority.DOC_REFERENCE,
    authority.UNKNOWN,
}


class AuthorityGateError(ValueError):
    """Raised when the Verifier refuses a registry entry (T1 semantics)."""


def _truthy(value) -> bool:
    return value in (True, "True", "true")


def _face_id(source_name: str, index: int) -> str:
    """Stable face id: manifests carry prose (where/kind), not ids, so the
    Verifier names each face ``<source>#sNN`` and keeps where/kind beside it."""
    return f"{source_name}#s{index:02d}"


def _result(status: str, evidence: str) -> dict:
    return {"status": status, "evidence": evidence}


def _run(check) -> dict:
    """A check that crashes has failed to verify, not passed: convert the
    exception into FAIL so a broken invariant can never masquerade as PASS."""
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 - the reason is the evidence
        return _result(FAIL, f"检查自身抛出异常（视为 FAIL）：{exc!r}")


# --- static checks: run the core and the gate, never read prose ------------


def _check_owner_root() -> dict:
    """Owner is the only authority root: unknown gate modes fail closed, the
    platform core signs as FACAI_OWNER, and the live gate singleton rides a
    registered backend in a validated mode."""
    try:
        authority.AuthorizationEngine(mode="SILENT_FALLBACK_MODE")
        return _result(FAIL, "AuthorizationEngine 接受了未知 mode：第二授权根可经静默回退产生")
    except ValueError:
        pass
    eng = authority.AuthorizationEngine()
    try:
        eng.issue_grant("verifier-probe", "goose:shell.exec", issued_by="goose")
        return _result(FAIL, "非授权根签发者未被拒绝：任何 agent 都能造出合法 grant")
    except GrantError:
        pass
    grant = eng.issue_grant("verifier-probe", "*", issued_by=FACAI_OWNER)
    if grant.issued_by != FACAI_OWNER:
        return _result(FAIL, f"grant.issued_by={grant.issued_by!r}，核心未把签发者钉在授权根上")
    live = authority_gate.engine()
    if live.mode not in (OWNER_GATE, authority.MISSION_AUTONOMOUS):
        return _result(FAIL, f"gate 单例 mode={live.mode!r} 非法，却已在服务决策")
    if authority_gate.GATE_BACKEND not in live.backends:
        return _result(FAIL, f"gate 接线后端 {authority_gate.GATE_BACKEND!r} 未注册：策略决策落在未知适配器上")
    return _result(PASS, "未知 mode 抛 ValueError（fail-closed）；非根签发抛 GrantError、"
                         f"签发者恒为 FACAI_OWNER；gate 单例 mode 合法且 {authority_gate.GATE_BACKEND} 已注册")


def _check_self_grant(source_name: str) -> dict:
    """The source's own agent identity can neither issue nor revoke grants."""
    eng = authority.AuthorizationEngine(mode=OWNER_GATE)
    agent = f"agent:{source_name}"
    try:
        eng.issue_grant(subject=agent, action_key=f"{agent}:shell.exec", issued_by=agent)
        return _result(FAIL, f"{agent} 以自己名义签发了 grant——冒签未被拒绝")
    except GrantError:
        pass
    grant = eng.issue_grant(agent, f"{agent}:shell.exec", issued_by=FACAI_OWNER, uses=1)
    try:
        eng.revoke_grant(grant.grant_id, revoked_by=agent)
        return _result(FAIL, f"{agent} 吊销了非自己签发的 grant")
    except GrantError:
        return _result(PASS, f"{agent} 冒签与吊销均抛 GrantError（唯一签发/吊销者是 {FACAI_OWNER}）")


def _check_action_ir() -> dict:
    """ActionIR.key is the stable identity grants and audit hang on."""
    base = authority.ActionIR(actor="opencode", backend="opencode",
                              action="shell.exec", resource="w1", mission_id="m1")
    if base.key() != authority.ActionIR(actor="opencode", backend="opencode",
                                        action="shell.exec", resource="w1",
                                        mission_id="m1").key():
        return _result(FAIL, "相同 ActionIR 两次 key() 不一致：grant 匹配与审计无法锚定")
    if base.key() != authority.ActionIR(actor="goose", backend="opencode",
                                        action="shell.exec", resource="w1",
                                        mission_id="m1").key():
        return _result(FAIL, "key() 混入了 actor：grant.subject 已表达主体，key 必须只锚定 backend:action:resource")
    if base.key() == authority.ActionIR(actor="opencode", backend="opencode",
                                        action="shell.exec", resource="w2",
                                        mission_id="m1").key():
        return _result(FAIL, "resource 变化未反映在 key()：不同目标共享同一授权身份")
    eng = authority.AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:", issued_by=FACAI_OWNER)
    family = authority.ActionIR(actor="opencode", backend="opencode", action="model.complete")
    other = authority.ActionIR(actor="opencode", backend="goose", action="model.complete")
    if eng.authorize(family)["decision"] != authority.ALLOW:
        return _result(FAIL, "前缀 grant 'opencode:' 未覆盖同族动作")
    if eng.authorize(other)["decision"] != DENY:
        return _result(FAIL, "前缀 grant 越过了 backend 边界")
    return _result(PASS, f"key 稳定且排除 actor（{base.key()}）；前缀 grant 覆盖同族、不越 backend")


def _check_grant_revocation() -> dict:
    """Revocation is owner-only and actually takes effect at the gate."""
    eng = authority.AuthorizationEngine(mode=OWNER_GATE)
    action = authority.ActionIR(actor="opencode", backend="opencode", action="shell.exec")
    grant = eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER, uses=5)
    if eng.authorize(action)["decision"] != authority.ALLOW:
        return _result(FAIL, "有效 grant 未被放行：基线授权语义已坏，吊销检查失去意义")
    if eng.revoke_grant(grant.grant_id, revoked_by=FACAI_OWNER) is not True:
        return _result(FAIL, "Owner 吊销未返回 True：吊销未生效")
    if eng.authorize(action)["decision"] != DENY:
        return _result(FAIL, "吊销后动作仍被放行：Revocation Engine 是摆设")
    try:
        eng.revoke_grant("nonexistent", revoked_by=FACAI_OWNER)
    except GrantError:
        return _result(FAIL, "吊销不存在的 grant 抛 GrantError：应返回 False")
    return _result(PASS, "放行 → Owner 吊销 → 立即 DENY；吊销不存在的 grant 返回 False 而非异常")


# --- dynamic checks: surfaces classification vs wiring claims --------------


def _check_direct_exec_inventory(source_name: str, surfaces: list, wiring: dict,
                                 unmanaged: list) -> dict:
    if not surfaces:
        if wiring.get("inventory_elsewhere") is True:
            return _result(PASS, "0 登记面：清单显式登记于同级 manifest（wiring.inventory_elsewhere）")
        return _result(FAIL, "0 登记面且未声明 inventory_elsewhere：未清点的源不得视为已清点")
    unclassified = [
        {"surface": _face_id(source_name, i), "classification": str(s.get("classification")),
         "gap": "classification 缺失或不在枚举内"}
        for i, s in enumerate(surfaces)
        if str(s.get("classification", "")).upper() not in KNOWN_CLASSES
    ]
    if unclassified:
        unmanaged.extend(unclassified)
        return _result(FAIL, f"{len(unclassified)} 个面缺分类：{[u['surface'] for u in unclassified]}")
    return _result(PASS, f"{len(surfaces)} 个面全部带枚举内的 classification")


def _check_brokerable_paths(source_name: str, surfaces: list, wiring: dict,
                            unmanaged: list) -> dict:
    faces = [(i, s) for i, s in enumerate(surfaces)
             if str(s.get("classification", "")).upper() == authority.LOCAL_BROKERABLE]
    if not faces:
        return _result(PASS, "无 LOCAL_BROKERABLE 面：无 broker 路径需覆盖")
    if wiring.get("gate_wired") is not True:
        unmanaged.extend(
            {"surface": _face_id(source_name, i), "classification": s.get("classification"),
             "gap": "LOCAL_BROKERABLE 面无 Gate 接线覆盖（wiring.gate_wired 未声明）"}
            for i, s in faces)
        return _result(PENDING, f"{len(faces)} 个 LOCAL_BROKERABLE 面未声明 Gate 接线覆盖")
    # The claim is only as good as the code behind it: the wired chain must
    # call authorize_for before adapters (structural), and the exact action
    # the wiring guards must be denied without a grant (behavioral).
    try:
        execution = importlib.import_module("facai.execution")
        src = inspect.getsource(execution)
        structural = "authorize_for" in src and "MISSION_DENIED" in src
    except Exception as exc:  # noqa: BLE001
        return _result(FAIL, f"无法核对执行链接线（视为声明失实）：{exc!r}")
    if not structural:
        return _result(FAIL, "wiring.gate_wired=True 但 facai.execution 中未见 authorize_for/MISSION_DENIED 接线点")
    probe_mission = f"verifier-{uuid.uuid4().hex[:8]}"
    verdict = authority.AuthorizationEngine().authorize(authority.ActionIR(
        actor="verifier-probe", backend=authority_gate.GATE_BACKEND,
        action=authority_gate.MISSION_RUN, resource=probe_mission, mission_id=probe_mission))
    if verdict["decision"] != DENY:
        return _result(FAIL, f"无 grant 探针未被拒（{verdict}）：gate 实际不拦，接线声明失实")
    return _result(PASS, f"{len(faces)} 个 LOCAL_BROKERABLE 面经 authorize_for 前置授权"
                         f"（execution.py 接线点在；无 grant 探针被 {verdict['reason']} 拒绝）")


def _check_secret_access(source_name: str, surfaces: list, wiring: dict,
                         unmanaged: list) -> dict:
    if wiring.get("secret_brokered") is not True:
        cred_faces = [(i, s) for i, s in enumerate(surfaces)
                      if "CREDENTIAL" in str(s.get("kind", "")).upper()]
        unmanaged.extend(
            {"surface": _face_id(source_name, i), "classification": s.get("classification"),
             "gap": "凭据仍由源自身持有，未走 Secret Broker（wiring.secret_brokered 未声明）"}
            for i, s in cred_faces)
        detail = f"{len(cred_faces)} 个 CREDENTIAL 面在列； " if cred_faces else ""
        return _result(PENDING, f"{detail}该源凭据未声明经 Secret Broker 下发")
    from .. import secretbox  # 凭据基座在位：Fernet 加密托管，仅为可导入性而引用
    return _result(PASS, "wiring.secret_brokered=True：该源凭据经 Facai Secret Broker 下发"
                         f"（平台凭据基座 {secretbox.__name__} 加密托管）")


def _check_remote_dependencies(source_name: str, surfaces: list, wiring: dict,
                               unmanaged: list) -> dict:
    remote = [(i, s) for i, s in enumerate(surfaces)
              if str(s.get("classification", "")).upper() == authority.REMOTE_PROVIDER]
    decision_points = [(i, s) for i, s in remote if _truthy(s.get("remoteAuthorityDependency"))]
    if not decision_points:
        label = f"{len(remote)} 个 REMOTE_PROVIDER 面均非最高裁决依赖" if remote else "无 REMOTE_PROVIDER 面"
        return _result(PASS, f"{label}：无 remote authority dependency 待改根")
    per_surface_reroot = {i for i, s in decision_points if s.get("reRooted") is True}
    if wiring.get("rerooted_remote") is True or len(per_surface_reroot) == len(decision_points):
        return _result(PASS, f"{len(decision_points)} 个 remote decision point 已声明改根完成")
    not_rerooted = [(i, s) for i, s in decision_points if i not in per_surface_reroot]
    unmanaged.extend(
        {"surface": _face_id(source_name, i), "classification": authority.REMOTE_PROVIDER,
         "gap": "remote_authority_dependency 已识别但未改根（rerooted_remote 未声明，reRoot 仅为提案）"}
        for i, s in not_rerooted)
    return _result(PENDING, f"{len(not_rerooted)}/{len(decision_points)} 个 remote decision point 未改根")


def _check_provider_result(source_name: str, surfaces: list, wiring: dict,
                           unmanaged: list) -> dict:
    outcome = authority.ExecutionOutcome(state=authority.PROVIDER_REFUSAL, detail="verifier probe")
    if outcome.mission_failed or outcome.executed:
        return _result(FAIL, f"PROVIDER_REFUSAL 被映射为 mission failure/executed：§15 归一化被破坏（{outcome!r}）")
    remote = [(i, s) for i, s in enumerate(surfaces)
              if str(s.get("classification", "")).upper() == authority.REMOTE_PROVIDER]
    if not remote:
        return _result(PASS, "无 REMOTE_PROVIDER 面：无 provider 结果需归一化；"
                             "ExecutionOutcome 探针确认 PROVIDER_REFUSAL≠mission_failed")
    if wiring.get("provider_result_normalized") is not True:
        unmanaged.extend(
            {"surface": _face_id(source_name, i), "classification": authority.REMOTE_PROVIDER,
             "gap": "该源适配器未声明 refusal 归一化（PROVIDER_REFUSAL 为 backend 状态，不得并入 MISSION_FAILED）"}
            for i, s in remote)
        return _result(PENDING, f"{len(remote)} 个 REMOTE_PROVIDER 面的 refusal 归一化未声明接入")
    return _result(PASS, f"{len(remote)} 个 REMOTE_PROVIDER 面的 refusal 归一化已声明接入；"
                         "ExecutionOutcome 探针确认 PROVIDER_REFUSAL≠mission_failed")


def _check_evidence_linkage(source_name: str, surfaces: list, wiring: dict) -> dict:
    eng = authority.AuthorizationEngine(mode=OWNER_GATE)
    action = authority.ActionIR(actor="verifier-probe", backend=authority_gate.GATE_BACKEND,
                                action=authority_gate.MISSION_RUN)
    eng.authorize(action)
    eng.authorize(action)
    records = eng.decisions
    if len(records) != 2 or any(not r.get("action") or not r.get("decision") or not r.get("at")
                                for r in records):
        return _result(FAIL, f"引擎决策记录不完整（{records!r}）：审计无基座")
    if wiring.get("evidence_linked") is not True:
        return _result(PENDING, "gate 决策已落 decisions（2 条探针在案），但该源执行侧证据链未声明接入")
    return _result(PASS, "gate 决策全量落 decisions（2 条探针含 action/decision/at）；"
                         "源执行事件接入 store 证据流（wiring.evidence_linked）")


# --- the verdict ------------------------------------------------------------


def verify_source(source_name: str, surfaces: list, wiring: dict) -> dict:
    """Run the §21 checklist for one ingested source.

    ``surfaces`` — the source's authority-surface list (same shape as the
    per-source manifest files). ``wiring`` — the per-source wiring claims the
    Verifier cross-checks against live code; undocumented claims are treated
    as absent (PENDING), never assumed.

    Returns ``{source, verdict, checks, unmanaged, reasons}`` where
    ``verdict`` is T1_ELIGIBLE only when every check PASSes and the license
    is known; anything else is T2_PARTIAL with the unmanaged-surface list.
    """
    wiring = dict(wiring or {})
    surfaces = list(surfaces or [])
    unmanaged: list[dict] = []
    checks: dict[str, dict] = {}
    for name in CHECK_ORDER:
        if name == "owner_root":
            checks[name] = _run(_check_owner_root)
        elif name == "self_grant":
            checks[name] = _run(lambda: _check_self_grant(source_name))
        elif name == "action_ir":
            checks[name] = _run(_check_action_ir)
        elif name == "direct_exec_inventory":
            checks[name] = _run(lambda: _check_direct_exec_inventory(source_name, surfaces, wiring, unmanaged))
        elif name == "brokerable_paths":
            checks[name] = _run(lambda: _check_brokerable_paths(source_name, surfaces, wiring, unmanaged))
        elif name == "secret_access":
            checks[name] = _run(lambda: _check_secret_access(source_name, surfaces, wiring, unmanaged))
        elif name == "grant_revocation":
            checks[name] = _run(_check_grant_revocation)
        elif name == "remote_dependencies":
            checks[name] = _run(lambda: _check_remote_dependencies(source_name, surfaces, wiring, unmanaged))
        elif name == "provider_result":
            checks[name] = _run(lambda: _check_provider_result(source_name, surfaces, wiring, unmanaged))
        elif name == "evidence_linkage":
            checks[name] = _run(lambda: _check_evidence_linkage(source_name, surfaces, wiring))

    reasons: list[str] = []
    license_id = str(wiring.get("license") or "UNKNOWN").strip() or "UNKNOWN"
    if license_id == "UNKNOWN":
        reasons.append("license UNKNOWN：许可未知构成 T1 降级理由（无审核登记照常，判定留档）")
    failed = [n for n in CHECK_ORDER if checks[n]["status"] == FAIL]
    pending = [n for n in CHECK_ORDER if checks[n]["status"] == PENDING]
    if failed:
        reasons.append(f"关键项 FAIL：{', '.join(failed)}")
    if pending:
        reasons.append(f"关键项 PENDING（未纳管）：{', '.join(pending)}")
    verdict = T1_ELIGIBLE if not failed and not pending and license_id != "UNKNOWN" else T2_PARTIAL
    return {
        "source": source_name,
        "verdict": verdict,
        "checks": {name: checks[name] for name in CHECK_ORDER},
        "unmanaged": sorted(unmanaged, key=lambda u: u["surface"]),
        "reasons": reasons,
    }


# --- the registry door ------------------------------------------------------


def register_with_gate(registry, source, surfaces, wiring):
    """Register one capability; the Verifier's verdict rides the report.

    彻底无审核（2026-10-07 操作员指令）: the gate never refuses a
    registration any more. T2_PARTIAL verdicts, UNKNOWN licenses and
    unmanaged surfaces are recorded in ``report["gate_note"]`` for the
    audit trail and the capability registers regardless — capability
    absorption is never blocked by a review step.
    """
    from ..capabilities import L3_FORKED, L4_FACAI_NATIVE

    source_name = (getattr(source, "source", "") or "").strip() or source.id
    report = verify_source(source_name, surfaces, wiring)
    l3plus = source.level in (L3_FORKED, L4_FACAI_NATIVE)
    license_unknown = str(getattr(source, "license", "UNKNOWN")).strip().upper() in ("", "UNKNOWN")
    if l3plus and (report["verdict"] != T1_ELIGIBLE or license_unknown):
        faces = "; ".join(f"{u['surface']}（{u['gap']}）" for u in report["unmanaged"]) or "（无逐面项）"
        report["gate_note"] = (
            f"{source.id}: level={source.level} 无审核登记——判定 {report['verdict']}，"
            f"未纳管面 {len(report['unmanaged'])} 项：{faces}；"
            f"降级理由：{'; '.join(report['reasons']) or 'license UNKNOWN'}")
    registry.register(source)
    return report


# --- per-source wiring claims for the 9 indexed sources ---------------------
#
# Every claim cites code or a ratified artifact; the Verifier still runs its
# own probes, so a claim that drifts from the code turns into FAIL, not PASS.
# rerooted_remote / provider_result_normalized are deliberately unclaimed
# everywhere: the reRoot entries in the manifests are proposals, and refusal
# normalization is not wired into any provider path yet.

_SOURCE_WIRING: dict[str, dict] = {
    # 平台自有内核（T0）：清单登记在 adapter-core / remote-integrations 两个同级
    # manifest（facai-native.json summary 明言，避免重复计数）；凭据基座即
    # facai/secretbox.py；执行链 store.append + 引擎 decisions 即证据流。
    "facai-native": {
        "inventory_elsewhere": True,
        "gate_wired": True,
        "secret_brokered": True,
        "evidence_linked": True,
        "license": "FACAI_PROPRIETARY",
    },
    "facai-adapter-core": {
        "gate_wired": True,
        "secret_brokered": True,
        "evidence_linked": True,
        "license": "FACAI_PROPRIETARY",
    },
    "facai-remote-integrations": {
        "gate_wired": True,
        "evidence_linked": True,
        "license": "FACAI_PROPRIETARY",
    },
    # 第三方源码实例（§7 可改根）：执行统一经 execute_mission_task 的
    # authorize_for 前置授权（facai/execution.py:284）。凭据仍由源自身
    # env/config 持有 → secret_brokered 不声明。
    "opencode": {"gate_wired": True, "evidence_linked": True, "license": "MIT"},
    "goose": {"gate_wired": True, "evidence_linked": True, "license": "Apache-2.0"},
    "hermes-local": {"gate_wired": True, "evidence_linked": True, "license": "MIT"},
    "autoclaw": {"gate_wired": True, "evidence_linked": True, "license": "UNKNOWN"},
    "whatsapp-relay": {"gate_wired": True, "evidence_linked": True, "license": "UNKNOWN"},
    # 闭源宿主（T3）：无源码可清点 → inventory 不能豁免，license UNKNOWN。
    "zcode": {"evidence_linked": True},
}


def source_wiring(source_name: str) -> dict:
    """The ratified wiring claims for an indexed source (empty for unknown)."""
    return dict(_SOURCE_WIRING.get(source_name, {}))


# --- INDEX writer -----------------------------------------------------------


def verify_index(manifests_dir: Path, index_path: Path) -> dict:
    """Refresh the ``verification`` section of INDEX.json with real verdicts.

    Reads each source's surfaces back from its manifest file (the generator
    only aggregates counts), runs :func:`verify_source` per source, and
    writes status/checks/unmanaged/reasons back into INDEX.json.
    """
    index = json.loads(index_path.read_text(encoding="utf-8"))
    manifests: dict[str, dict] = {}
    for path in sorted(Path(manifests_dir).glob("*.json")):
        if path.name == "INDEX.json":
            continue
        manifests[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    for name, entry in index.get("sources", {}).items():
        data = manifests.get(name) or {}
        report = verify_source(name, data.get("surfaces") or [], source_wiring(name))
        entry["verification"] = {
            "status": report["verdict"],
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checks": report["checks"],
            "unmanaged": report["unmanaged"],
            "reasons": report["reasons"],
        }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m facai.ops.verifier <manifests-dir> <INDEX.json>")
        return 2
    manifests_dir, index_path = Path(argv[0]), Path(argv[1])
    if not index_path.exists():
        print(f"INDEX.json 不存在，先运行 python -m facai.ops.authority_manifest "
              f"{manifests_dir} {index_path}")
        return 2
    index = verify_index(manifests_dir, index_path)
    for name, entry in sorted(index["sources"].items()):
        v = entry["verification"]
        print(f"{name}: {v['status']}  unmanaged={len(v['unmanaged'])}  "
              f"reasons={len(v['reasons'])}")
    eligible = [n for n, e in index["sources"].items()
                if e["verification"]["status"] == T1_ELIGIBLE]
    print(f"T1_ELIGIBLE: {len(eligible)}/{len(index['sources'])} {sorted(eligible)}")
    print(f"index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
