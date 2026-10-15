"""FACAI Owner authority core (P0-1..P0-6 of the frozen baseline).

Implements the smallest enforceable slice of docs/AUTHORITY_ARCHITECTURE.md:

- ``FACAI_OWNER`` is the single authority root. Nothing here can create a
  second one: every grant is stamped with its issuer and ``issue_grant``
  refuses any issuer that is not the root.
- ``CapabilityGrant`` is the only portable permission object. Agents may
  REQUEST or CONSUME grants; issuing is reserved to the Owner (or code
  explicitly running as the Owner, i.e. the platform core itself).
- ``ActionIR`` is the uniform description of a side effect an adapter is
  about to perform (run a CLI, call a model, touch the chain).
- ``BackendRegistry`` records every executable backend with its trust class
  (T0 native .. T3 external) and its authority-surface classification, so
  the gate can tell a local brokerable action from a remote-provider one.
- ``AuthorizationEngine`` is the Owner gate. It is additive by design: it
  never removes an adapter's capability, it only decides whether a given
  action may pass, and ``MISSION_AUTONOMOUS`` mode (the default) keeps
  today's behaviour — capability present, action flows. ``OWNER_GATE``
  mode is the switch the operator flips when a mission must present
  grants before anything executes.
- ``PROVIDER_REFUSAL`` is a backend execution state, not a mission failure:
  "this backend did not execute" must never be read as "the Owner's grant
  was revoked".

No I/O and no storage: this module is pure policy so tests can exercise it
without a store; persistence lands with the console wiring (P0-7+).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

FACAI_OWNER = "FACAI_OWNER"

# Trust classes (§7 of the baseline): T0 ships with the platform, T3 is a
# closed binary the platform can only adapter-wrap, never re-root.
TRUST_NATIVE = "T0_FACAI_NATIVE"
TRUST_FULLY_MANAGED = "T1_FULLY_MANAGED"
TRUST_PARTIALLY_MANAGED = "T2_PARTIALLY_MANAGED"
TRUST_EXTERNAL = "T3_EXTERNAL"

# Authority-surface classifications (§5): what kind of decision point a
# surface is. REMOTE_PROVIDER surfaces are exactly the ones re-rooting
# targets; LOCAL_BROKERABLE ones are where a broker/gate can intercept.
# DOC_REFERENCE (ingest 实测反馈 INGEST_FIRST_RUN §7-2)：文档/示例里的链接
# 引用——已清点但不是裁决点，永不进 REMOTE_PROVIDER 误报池、不需 re-root。
LOCAL_BROKERABLE = "LOCAL_BROKERABLE"
LOCAL_DIRECT = "LOCAL_DIRECT"
CONFIGURABLE = "CONFIGURABLE"
REMOTE_PROVIDER = "REMOTE_PROVIDER"
DOC_REFERENCE = "DOC_REFERENCE"
UNKNOWN = "UNKNOWN"

# Gate modes. MISSION_AUTONOMOUS keeps current capability semantics.
OWNER_GATE = "OWNER_GATE"
MISSION_AUTONOMOUS = "MISSION_AUTONOMOUS"

ALLOW = "ALLOW"
DENY = "DENY"

# Deny reasons (§11/§12 of the baseline, operator-ratified 2026-10-05):
# a gate that cannot explain WHY it refused is a gate people route around.
GRANT_REQUIRED = "GRANT_REQUIRED"
GRANT_EXPIRED = "GRANT_EXPIRED"
GRANT_EXHAUSTED = "GRANT_EXHAUSTED"

# A backend-side refusal (quota, auth, upstream 4xx/5xx). Distinct from any
# local decision so the console can show "backend did not run" without
# implying the Owner withdrew permission.
PROVIDER_REFUSAL = "PROVIDER_REFUSAL"

# --- risk annotation (pure labeling layer) -----------------------------------
# A coarse per-action risk label for audit/console display. It is NEVER read
# by ``authorize``: grants, gate modes and deny reasons are untouched, so an
# annotation bug cannot change an ALLOW into a DENY or back.
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"

# Default mapping per action family. shell/docker run arbitrary code (high);
# filesystem writes, network access and secret requests touch state or
# credentials without executing code (medium); provider/mission invocations
# are brokered platform work (low). "network.*" is a family wildcard in
# facai/broker.CAPABILITY_ACTIONS, so every "network.<verb>" inherits medium.
RISK_BY_ACTION = {
    "shell.exec": RISK_HIGH,
    "docker.run": RISK_HIGH,
    "filesystem.write": RISK_MEDIUM,
    "network.*": RISK_MEDIUM,
    "secret.request": RISK_MEDIUM,
    "provider.invoke": RISK_LOW,
    "mission.run": RISK_LOW,
}


def risk_for_action(action: str) -> str:
    """The default risk label for one action verb.

    Unmapped actions default to ``low`` — the same default a grant carries —
    so a new family never silently inherits a scarier label than the table
    maps; the operator extends the table explicitly instead.
    """
    if action in RISK_BY_ACTION:
        return RISK_BY_ACTION[action]
    if action.startswith("network."):
        return RISK_MEDIUM
    return RISK_LOW


def _now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ActionIR:
    """One side effect, described well enough to authorize or refuse it."""

    actor: str            # requesting agent/backend id, e.g. "opencode"
    backend: str          # executing backend id from the registry
    action: str           # verb, e.g. "shell.exec", "model.complete", "chain.credit"
    resource: str = ""    # optional target, e.g. workspace path or model id
    mission_id: str = ""
    risk_level: Optional[str] = None   # explicit label wins; else risk_for_action

    def __post_init__(self):
        # Pure annotation, resolved once at construction: an explicitly passed
        # risk_level stands, everything else is labeled from the frozen action
        # table. The gate never reads this field.
        if not self.risk_level:
            object.__setattr__(self, "risk_level", risk_for_action(self.action))

    def key(self) -> str:
        """Stable identity for grant matching and audit lines.

        The actor is deliberately excluded — the grant's ``subject`` already
        scopes who may consume it, so a key names only backend:action[:resource].
        """
        return f"{self.backend}:{self.action}" + (f":{self.resource}" if self.resource else "")


@dataclass(frozen=True)
class Backend:
    """An executable backend recorded in the registry."""

    id: str
    name: str
    trust_class: str                      # T0..T3 constant
    management: str                       # "facai_native" | "adapter" | "external"
    classification: str = UNKNOWN         # dominant surface classification
    remote_authority_dependency: bool = False


def seed_backends() -> dict:
    """The inventory from docs/AUTHORITY_SURFACE_INVENTORY.md, in code.

    zcode stays T3: closed binary, adapter-wrapped only. facai-native is the
    platform's own execution plane (T0). Everything else is fully managed
    source we vendored.
    """
    rows = (
        Backend("facai-native", "FACAI 自有执行编排", TRUST_NATIVE, "facai_native",
                LOCAL_BROKERABLE, False),
        Backend("opencode", "OpenCode agent CLI", TRUST_FULLY_MANAGED, "adapter",
                LOCAL_BROKERABLE, False),
        Backend("goose", "Goose agent CLI", TRUST_FULLY_MANAGED, "adapter",
                LOCAL_BROKERABLE, False),
        Backend("hermes", "Hermes Agent", TRUST_FULLY_MANAGED, "adapter",
                LOCAL_BROKERABLE, False),
        Backend("autoclaw", "AutoClaw 桥接实例", TRUST_PARTIALLY_MANAGED, "adapter",
                LOCAL_BROKERABLE, False),
        Backend("zcode", "ZCode (闭源宿主)", TRUST_EXTERNAL, "external",
                UNKNOWN, True),
    )
    return {b.id: b for b in rows}


@dataclass(frozen=True)
class CapabilityGrant:
    """A permission object. Only FACAI_OWNER may ever be the issuer."""

    grant_id: str
    subject: str          # who may consume it (agent/backend/mission id)
    action_key: str       # ActionIR.key() the grant covers, or a prefix ending with ':'
    issued_by: str
    issued_at: datetime
    expires_at: Optional[datetime]
    uses_left: Optional[int]      # None = unlimited until expiry
    note: str = ""
    risk: str = RISK_LOW  # display label inherited from the ActionIR at issuance

    def covers(self, action: ActionIR) -> bool:
        if self.action_key.endswith(":"):
            if not action.key().startswith(self.action_key):
                return False
        elif self.action_key not in (action.key(), "*"):
            return False
        if self.expires_at is not None and _now() >= self.expires_at:
            return False
        return True


class GrantError(ValueError):
    """Raised when a grant would violate the authority invariants."""


class AuthorizationEngine:
    """The Owner gate. Additive: decides, never strips capability."""

    def __init__(self, mode: str = MISSION_AUTONOMOUS, backends: Optional[dict] = None):
        if mode not in (OWNER_GATE, MISSION_AUTONOMOUS):
            raise ValueError(f"unknown gate mode: {mode}")
        self.mode = mode
        self.backends = dict(backends) if backends is not None else seed_backends()
        self.grants: dict[str, CapabilityGrant] = {}
        self.decisions: list[dict] = []
        self._use_count: dict[str, int] = {}
        self._persist = None

    # --- registry -----------------------------------------------------------
    def register_backend(self, backend: Backend) -> None:
        self.backends[backend.id] = backend

    def backend(self, backend_id: str) -> Optional[Backend]:
        return self.backends.get(backend_id)

    # --- grants -------------------------------------------------------------
    def issue_grant(self, subject: str, action_key: str, issued_by: str,
                    ttl_seconds: Optional[int] = None, uses: Optional[int] = None,
                    note: str = "", action: Optional[ActionIR] = None) -> CapabilityGrant:
        if issued_by != FACAI_OWNER:
            # The invariant the whole baseline hangs on: agents can request or
            # consume, but a grant that did not come from the root is void.
            raise GrantError("only FACAI_OWNER may issue capability grants")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise GrantError("grant ttl must be positive")
        if uses is not None and uses <= 0:
            raise GrantError("grant uses must be positive")
        expires = None if ttl_seconds is None else _now() + timedelta(seconds=ttl_seconds)
        # ``risk`` is display-only: when the issuance is tied to a concrete
        # ActionIR the grant inherits its label, otherwise the low default.
        # It never enters covers()/authorize(), so behavior is untouched.
        risk = action.risk_level if action is not None else RISK_LOW
        grant = CapabilityGrant(
            grant_id=hashlib.sha256(secrets.token_bytes(16)).hexdigest()[:16],
            subject=subject, action_key=action_key, issued_by=issued_by,
            issued_at=_now(), expires_at=expires, uses_left=uses, note=note,
            risk=risk)
        self.grants[grant.grant_id] = grant
        self._persist_grant(grant)
        return grant

    def revoke_grant(self, grant_id: str, revoked_by: str) -> bool:
        if revoked_by != FACAI_OWNER:
            raise GrantError("only FACAI_OWNER may revoke capability grants")
        removed = self.grants.pop(grant_id, None) is not None
        if removed:
            self._persist_revoke(grant_id)
        return removed

    # --- persistence (P0-7 slice): optional store-backed survival -----------
    # Without a store the engine behaves exactly as the frozen P0 core did
    # (memory-only, restart = explicit refusals). With one attached, every
    # issuance is written through and a fresh process can reload the
    # non-revoked, unexpired grants — restarts stop manufacturing
    # MISSION_DENIED for work that was legitimately authorized.

    def attach_persistence(self, store) -> None:
        self._persist = store
        for row in self._persist.load_grants():
            issued = (datetime.fromisoformat(row["issued_at"])
                      if row.get("issued_at") else _now())
            expires = (datetime.fromisoformat(row["expires_at"])
                       if row.get("expires_at") else None)
            self.grants[row["grant_id"]] = CapabilityGrant(
                grant_id=row["grant_id"], subject=row["subject"],
                action_key=row["action_key"], issued_by=FACAI_OWNER,
                issued_at=issued, expires_at=expires,
                uses_left=None, note="从持久层恢复")

    def _persist_grant(self, grant: CapabilityGrant) -> None:
        if getattr(self, "_persist", None) is not None:
            self._persist.save_grant(
                grant.grant_id, grant.subject, grant.action_key,
                grant.issued_at.isoformat() if grant.issued_at else None,
                grant.expires_at.isoformat() if grant.expires_at else None)

    def _persist_revoke(self, grant_id: str) -> None:
        if getattr(self, "_persist", None) is not None:
            self._persist.delete_grant(grant_id)

    def _subject_matches(self, grant: CapabilityGrant, action: ActionIR) -> bool:
        """Subject + key shape only — ignores expiry and use counts."""
        if grant.subject not in (action.actor, "*"):
            return False
        if action.mission_id and grant.action_key == f"mission:{action.mission_id}":
            return True
        if grant.action_key == "*":
            return True
        if grant.action_key.endswith(":"):
            return action.key().startswith(grant.action_key)
        return grant.action_key == action.key()

    def _matching_grant(self, action: ActionIR, allow_mission_grants: bool) -> Optional[CapabilityGrant]:
        for grant in self.grants.values():
            if grant.uses_left is not None and \
                    self._use_count.get(grant.grant_id, 0) >= grant.uses_left:
                continue
            if grant.expires_at is not None and _now() >= grant.expires_at:
                continue
            # OWNER_GATE accepts only action-level grants: "allow for this
            # mission" is precisely what the other mode exists to express.
            if not allow_mission_grants and action.mission_id and \
                    grant.action_key == f"mission:{action.mission_id}" and \
                    not grant.action_key.endswith(":") and grant.action_key != "*":
                continue
            if self._subject_matches(grant, action):
                return grant
        return None

    def _stale_grant_reason(self, action: ActionIR, allow_mission_grants: bool) -> Optional[str]:
        """Distinguish expired from exhausted when a grant exists but is dead."""
        for grant in self.grants.values():
            if not self._subject_matches(grant, action):
                continue
            is_mission_only = action.mission_id and \
                grant.action_key == f"mission:{action.mission_id}"
            if is_mission_only and not allow_mission_grants:
                continue
            if grant.expires_at is not None and _now() >= grant.expires_at:
                return GRANT_EXPIRED
            used = self._use_count.get(grant.grant_id, 0)
            if grant.uses_left is not None and used >= grant.uses_left:
                return GRANT_EXHAUSTED
        return None

    # --- the gate -----------------------------------------------------------
    def authorize(self, action: ActionIR) -> dict:
        """Decide one action. Returns {decision, reason, grant_id, backend}.

        Operator-ratified semantics (2026-10-05) — there is NO default-open:

        - MISSION_AUTONOMOUS: the mission must carry a valid mission grant
          (``mission:<id>``, issued once when the Owner's core creates the
          mission) or an action grant; anything else is GRANT_REQUIRED.
        - OWNER_GATE: a valid action-level grant is required; a mission
          grant alone never satisfies this mode.
        - Invalid / expired / exhausted grants are refused with the specific
          reason — they never silently fall through to an execution path.

        Unknown backends are refused in both modes: registering a backend is
        the local equivalent of trusting it.
        """
        backend = self.backends.get(action.backend)
        grant = None
        mission_grants_ok = self.mode == MISSION_AUTONOMOUS
        if backend is None:
            decision, reason = DENY, f"backend '{action.backend}' is not registered"
        else:
            grant = self._matching_grant(action, mission_grants_ok)
            if grant is None:
                stale = self._stale_grant_reason(action, mission_grants_ok)
                decision = DENY
                reason = stale or GRANT_REQUIRED
                grant = None
            else:
                decision, reason = ALLOW, ""
                if grant.uses_left is not None:
                    self._use_count[grant.grant_id] = self._use_count.get(grant.grant_id, 0) + 1
        record = {"action": action.key(), "decision": decision, "reason": reason,
                  "grant_id": grant.grant_id if grant else None,
                  "backend": action.backend, "mode": self.mode,
                  "risk_level": action.risk_level,
                  "at": _now().isoformat()}
        self.decisions.append(record)
        return record


@dataclass
class ExecutionOutcome:
    """Adapter result wrapper: a backend state, never a mission verdict."""

    state: str                    # "EXECUTED" | "PROVIDER_REFUSAL" | "TIMEOUT" | ...
    detail: str = ""
    cost_usd: float = 0.0
    events: list = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return self.state == "EXECUTED"

    @property
    def mission_failed(self) -> bool:
        # PROVIDER_REFUSAL is explicitly NOT a mission failure (§15): the
        # mission may retry on another backend the Owner has registered.
        return self.state in {"ADAPTER_ERROR"}
