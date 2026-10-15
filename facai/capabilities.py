"""FACAI Capability Foundry — data spine (operator doctrine, 2026-10-05).

The platform absorbs *capabilities*, not repositories: even if every vendored
repo disappeared tomorrow, Facai's core abilities must remain. This module is
the registry that makes that claim checkable.

- Every absorbed ability gets a namespaced capability id
  (``planning.replan``, ``memory.context_compaction``, ``shell.exec`` ...).
- Every entry records its absorption level L0..L4 — from "read the design"
  to "Facai owns an independent implementation" — plus its source and
  license, so "make it our product" never collides with "cannot redistribute".
- :func:`capability_diff` turns a fresh repository scan into the standard
  REPOSITORY CAPABILITY REPORT (new / already owned / better candidate /
  duplicate / external dependency / nativeization candidate).

This is deliberately storage-free policy, like facai/authority.py: the
Foundry's scanners (Repo Scanner, Capability Miner, License/SBOM analyzer)
land as separate ops modules and feed this registry.

Dify plugin-manifest absorption (2026-10-06): an ingested source's capability
manifest carries an explicit ``permissions`` section per capability entry —
the six brokerable action families, each ``allowed: true/false`` plus a scope
description. Deny-by-default: only an explicit declaration opens a family.
At registration time :func:`grant_templates_for` /
:func:`manifest_grant_templates` map those declarations into *restricted*
grant templates shaped after facai/authority.py's ``CapabilityGrant``
(subject + ``backend:action[:resource]`` action key, bounded ttl/uses,
risk label) — the Owner still does the actual issuing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from . import authority

# Absorption levels (§三 of the doctrine): the registry's strength metric is
# Native Capability Coverage — how much sits at L4 — not repository count.
L0_REFERENCE = "L0_REFERENCE"          # design read, nothing shipped
L1_WRAPPED = "L1_WRAPPED"              # third-party called in place
L2_ADAPTED = "L2_ADAPTED"              # behind a Facai adapter
L3_FORKED = "L3_FORKED"                # maintained, license-tracked fork
L4_FACAI_NATIVE = "L4_FACAI_NATIVE"    # Facai's own independent implementation

# Provider contracts (§二): everything surfaces through one of these, so a
# native module and an external adapter are interchangeable above the line.
PROVIDER_CONTRACTS = (
    "CapabilityProvider",
    "AgentProvider",
    "ModelProvider",
    "ToolProvider",
    "MemoryProvider",
    "ExecutionProvider",
)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Licenses that let Facai vendor and redistribute an implementation.
_REDISTRIBUTABLE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD",
    "Python-2.0", "Zlib", "Unlicense", "CC0-1.0", "CC-BY-4.0", "MPL-2.0",
}

# Forks/native implementations of copyleft code are tracked, not refused —
# but they must say so on the record.
_COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0"}


class CapabilityError(ValueError):
    """Raised when a registry entry would break the absorption invariants."""


@dataclass(frozen=True)
class Capability:
    """One absorbable ability, tracked from discovery to native ownership."""

    id: str                    # namespaced: <family>.<ability>
    name: str
    level: str                 # L0..L4 constant
    provider: str              # one of PROVIDER_CONTRACTS
    source: str = ""           # repo/agent it was seen in ("facai-native" when ours)
    license: str = "UNKNOWN"   # SPDX where known; recorded as data, never a gate
    native_candidate: bool = False
    note: str = ""


def _validate(cap: Capability) -> None:
    if not _ID_RE.match(cap.id):
        raise CapabilityError(f"capability id must be <family>.<ability> lowercase: {cap.id!r}")
    if cap.level not in (L0_REFERENCE, L1_WRAPPED, L2_ADAPTED, L3_FORKED, L4_FACAI_NATIVE):
        raise CapabilityError(f"unknown absorption level: {cap.level}")
    if cap.provider not in PROVIDER_CONTRACTS:
        raise CapabilityError(f"capability must surface via a Facai provider contract: {cap.provider}")
    # 彻底无审核（2026-10-07 操作员指令）：L3/L4 + UNKNOWN license 不再拒绝
    # 登记——license 只是台账字段，能力吸收永不被审核卡住。


class CapabilityRegistry:
    """The FACAI CAPABILITY REGISTRY: one entry per absorbed ability."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, cap: Capability) -> Capability:
        _validate(cap)
        self._caps[cap.id] = cap
        return cap

    def get(self, cap_id: str) -> Optional[Capability]:
        return self._caps.get(cap_id)

    def ids(self) -> list[str]:
        return sorted(self._caps)

    def by_level(self, level: str) -> list[Capability]:
        return [c for c in self._caps.values() if c.level == level]

    def native_coverage(self) -> dict:
        """The real strength metric: how much of the graph Facai owns."""
        total = len(self._caps)
        native = len(self.by_level(L4_FACAI_NATIVE))
        return {"total": total, "native": native,
                "coverage": round(native / total, 4) if total else 0.0}


def capability_diff(discovered: list[dict], registry: CapabilityRegistry) -> dict:
    """REPOSITORY CAPABILITY REPORT for one scanned source.

    Each discovered entry: {id, name?, verdict} where verdict is one of
    "new" | "better" | "duplicate" | "dependency". Anything already in the
    registry is reported under already_owned regardless of verdict.
    """
    report: dict[str, list[str]] = {
        "new": [], "already_owned": [], "better_implementation_candidate": [],
        "duplicate_wrapper": [], "external_dependency": [], "nativeization_candidate": [],
    }
    for entry in discovered:
        cap_id = str(entry.get("id", ""))
        if not cap_id:
            continue
        if registry.get(cap_id) is not None:
            report["already_owned"].append(cap_id)
        verdict = str(entry.get("verdict", "new"))
        if verdict == "new":
            report["new"].append(cap_id)
        elif verdict == "better":
            report["better_implementation_candidate"].append(cap_id)
        elif verdict == "dependency":
            report["external_dependency"].append(cap_id)
        else:
            report["duplicate_wrapper"].append(cap_id)
        if entry.get("native_candidate"):
            report["nativeization_candidate"].append(cap_id)
    return report


# --- Dify plugin-manifest absorption: explicit permission declarations -------
# The permission vocabulary is pinned verbatim to facai/broker.CAPABILITY_ACTIONS
# (the frozen brokerable action families; tests/test_capabilities.py keeps a
# lockstep assertion so the two cannot drift). A manifest's permissions section
# always enumerates all six: whatever is not explicitly allowed stays False —
# not declaring a family is never read as allowing it.

PERMISSION_FAMILIES = (
    "shell.exec", "filesystem.write", "network.*",
    "docker.run", "secret.request", "provider.invoke",
)

SCHEMA_PERMISSIONS = "facai.permissions/1"

# Bounded defaults for restricted grant templates (docs/AUTHORITY_ARCHITECTURE.md
# §10 example order of magnitude). A template is only a request shape; the real
# grant still goes through AuthorizationEngine.issue_grant, whose FACAI_OWNER
# root is the only issuer.
GRANT_TTL_SECONDS = 3600
GRANT_USES = 10


def denied_permissions(scope: str = "未声明：deny-by-default") -> list[dict]:
    """The all-denied baseline of a permissions section: full enumeration, none open.

    One entry per family: {action, allowed, scope, resources, evidence}.
    Producers (facai/ops/ingest.py) copy-and-fill the families a source
    explicitly declares; everything else keeps ``allowed: False``.
    """
    return [{"action": fam, "allowed": False, "scope": scope,
             "resources": [], "evidence": ""}
            for fam in PERMISSION_FAMILIES]


@dataclass(frozen=True)
class GrantTemplate:
    """A restricted grant template, field-for-field with ``CapabilityGrant``.

    ``action_key`` keeps the ActionIR.key shape ``backend:action[:resource]``:
    a declared resource scope pins the template to an exact resource match, it
    is never widened into a prefix wildcard. ttl/uses are bounded, ``risk``
    rides the authority.RISK_BY_ACTION table (display label only, never read
    by authorize — same semantics as the grants it templates).
    """

    subject: str
    action_key: str
    action: str
    resource: str
    ttl_seconds: int
    uses: int
    risk: str
    note: str


def _license_trace(license_id: str) -> str:
    """Copyleft / unknown licenses ride along on every template note."""
    if license_id == "UNKNOWN" or license_id in _COPYLEFT:
        return f"（license {license_id} 留痕）"
    return ""


def grant_templates_for(entry: dict, subject: str, backend: str = "facai-native",
                        ttl_seconds: int = GRANT_TTL_SECONDS,
                        uses: int = GRANT_USES) -> dict:
    """Map one manifest capability entry's permissions section to grant templates.

    deny-by-default: only ``allowed is True`` entries produce a template — one
    per declared resource (exact ``backend:action:resource`` key) or one
    family-level ``backend:action`` template when no resource is declared.
    Families with allowed=False land in ``denied`` with no template. An action
    outside the six-family vocabulary can never mint a template (recorded in
    ``rejected`` — no invented permission). A copyleft/UNKNOWN license is
    traced in every note (existing license-tracking semantics, kept).

    Returns ``{grants: [GrantTemplate...], denied: [action...], rejected: [action...]}``.
    """
    grants: list[GrantTemplate] = []
    denied: list[str] = []
    rejected: list[str] = []
    license_id = str(entry.get("license") or "").strip() or "UNKNOWN"
    trace = _license_trace(license_id)
    cap_id = str(entry.get("id", "?"))
    for perm in entry.get("permissions") or []:
        action = str(perm.get("action", ""))
        if action not in PERMISSION_FAMILIES:
            rejected.append(action)
            continue
        if perm.get("allowed") is not True:
            denied.append(action)
            continue
        resources = [str(r) for r in (perm.get("resources") or []) if str(r)]
        for resource in resources or [""]:
            key = f"{backend}:{action}" + (f":{resource}" if resource else "")
            note = f"由 capability manifest 条目 {cap_id} 的 permissions 声明映射{trace}"
            grants.append(GrantTemplate(
                subject=subject, action_key=key, action=action, resource=resource,
                ttl_seconds=ttl_seconds, uses=uses,
                risk=authority.risk_for_action(action), note=note))
    return {"grants": grants, "denied": denied, "rejected": rejected}


def manifest_grant_templates(manifest: dict, subject: str,
                             backend: str = "facai-native",
                             ttl_seconds: int = GRANT_TTL_SECONDS,
                             uses: int = GRANT_USES) -> dict:
    """Aggregate :func:`grant_templates_for` over a whole capability manifest.

    Entries without their own ``license`` fall back to the manifest's top-level
    one, so the copyleft/UNKNOWN trace never silently disappears. This is the
    registration-side door: the ingest CLI never registers, so whatever
    registers later maps its permission declarations through here first.
    """
    fallback = str(manifest.get("license") or "").strip() or "UNKNOWN"
    grants: list[GrantTemplate] = []
    denied: list[str] = []
    rejected: list[str] = []
    for raw in manifest.get("capabilities") or []:
        entry = dict(raw)
        if not str(entry.get("license") or "").strip():
            entry["license"] = fallback
        out = grant_templates_for(entry, subject=subject, backend=backend,
                                  ttl_seconds=ttl_seconds, uses=uses)
        grants.extend(out["grants"])
        denied.extend(out["denied"])
        rejected.extend(out["rejected"])
    return {"grants": grants, "denied": sorted(set(denied)),
            "rejected": sorted(set(rejected)), "license": fallback}
