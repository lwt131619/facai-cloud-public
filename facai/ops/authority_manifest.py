"""Consolidate per-source AuthorityManifest drafts into one machine index.

The seven-route authority-surface retrieval left one draft manifest per
ingested source under ``vendor/authority-manifests/`` (target, origin,
managementClass, summary, surfaces[], reRooting[]). This module folds them
into the operator-ratified schema so downstream tooling (Verifier, Router,
Ingestion automation) never re-parses prose:

    {
      "generated_at": ...,
      "sources": {
        "goose": {
          "authority_surfaces": 31,
          "remote_decision_points": 12,
          "management_class": "T2",
          "local":     {"brokered": 5, "direct": 0},
          "remote":    {"provider_controlled": 12},
          "reroot":    {"configurable": 9, "adapter_required": 5, "unresolved": 12},
          "verification": {"status": "PARTIAL", "checks": {...}}
        }, ...
    }

``verification.status`` stays PARTIAL until docs/AUTHORITY_ARCHITECTURE.md
§21's Re-rooting Verifier passes a source; the per-check flags make the gap
explicit instead of implying readiness. Usage::

    python -m facai.ops.authority_manifest <manifests-dir> <out.json>
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_CLASS_KEYS = {
    "LOCAL_BROKERABLE": "adapter_required",
    "LOCAL_DIRECT": None,          # inventoried, no re-rooting action needed
    "CONFIGURABLE": "configurable",
    "REMOTE_PROVIDER": "unresolved",
    # 文档/示例链接引用（ingest 初扫）：已清点，但不是裁决点——同样无需改根动作。
    "DOC_REFERENCE": None,
    "UNKNOWN": "unresolved",
}


def _trust_class(management_class: str) -> str:
    match = re.match(r"(T\d)", management_class or "")
    return match.group(1) if match else "T3"


def _verifier_checks(surfaces: list[dict]) -> dict:
    """§21 checklist as of today: what the code base already guarantees.

    owner_root/self_grant/action_ir/grant_revocation are enforced by
    facai/authority.py with tests; the rest become True only when the
    corresponding wiring lands (Gate wiring, Capability Broker, Secret
    Broker, result normalization, evidence linkage).
    """
    classified = bool(surfaces)
    return {
        "owner_root": True,
        "self_grant": "IMPOSSIBLE",
        "action_ir": True,
        "direct_exec_inventory": "COMPLETE" if classified else "MISSING",
        "brokerable_paths": "PENDING_WIRING",
        "secret_access": "PENDING_BROKER",
        "grant_revocation": True,
        "remote_dependencies": "CLASSIFIED" if classified else "MISSING",
        "provider_result": "PENDING_NORMALIZATION",
        "evidence_linkage": "PENDING",
    }


def consolidate(manifests_dir: Path) -> dict:
    sources: dict[str, dict] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        surfaces = data.get("surfaces") or []
        reroot = {"configurable": 0, "adapter_required": 0, "unresolved": 0}
        local = {"brokered": 0, "direct": 0}
        remote = 0
        for surface in surfaces:
            key = _CLASS_KEYS.get(str(surface.get("classification")).upper(), "unresolved")
            if str(surface.get("classification")).upper() == "LOCAL_BROKERABLE":
                local["brokered"] += 1
            elif str(surface.get("classification")).upper() == "LOCAL_DIRECT":
                local["direct"] += 1
            elif str(surface.get("classification")).upper() == "REMOTE_PROVIDER":
                remote += 1
            if key:
                reroot[key] += 1
        name = path.stem
        sources[name] = {
            "management_class": _trust_class(str(data.get("managementClass", ""))),
            "authority_surfaces": len(surfaces),
            "remote_decision_points": sum(
                1 for s in surfaces if s.get("remoteAuthorityDependency") in (True, "True", "true")),
            "local": local,
            "remote": {"provider_controlled": remote},
            "reroot": reroot,
            "verification": {"status": "PARTIAL", "checks": _verifier_checks(surfaces)},
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema": "facai.authority-manifest/1",
        "sources": sources,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m facai.ops.authority_manifest <manifests-dir> <out.json>")
        return 2
    manifests_dir, out = Path(argv[0]), Path(argv[1])
    index = consolidate(manifests_dir)
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(s["authority_surfaces"] for s in index["sources"].values())
    remote = sum(s["remote_decision_points"] for s in index["sources"].values())
    print(f"sources: {len(index['sources'])}  surfaces: {total}  remote_decision_points: {remote}")
    print(f"index: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
