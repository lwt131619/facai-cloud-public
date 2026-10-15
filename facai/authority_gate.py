"""Process-level authorization gate: wires the frozen facai.authority
semantics into the execution chain.

Constraints this module must not bend:

- MISSION_AUTONOMOUS (the default) still has NO default-open: a mission may
  only execute while it carries a valid ``mission:<id>`` grant, issued once
  by the platform core (FACAI_OWNER) when the mission lands in the store.
- OWNER_GATE (``FACAI_GATE_MODE=OWNER_GATE``) accepts action-level grants
  only; a mission grant never satisfies it. The engine fails closed on an
  unknown mode value rather than silently degrading to the default.
- Capability is untouched: this gate only decides whether an action passes;
  it never removes an adapter's ability to execute.
- Grants persist through the optional store attached at startup (P0-7
  slice): issuance is written through, revocation deletes, reload skips
  expired rows. Without an attached store the frozen P0 behavior holds —
  memory only, restart = explicit refusals, never silent allowance.
"""
from __future__ import annotations

import os
import threading

from .authority import (
    FACAI_OWNER,
    GRANT_EXHAUSTED,
    GRANT_EXPIRED,
    GRANT_REQUIRED,
    MISSION_AUTONOMOUS,
    ActionIR,
    AuthorizationEngine,
    CapabilityGrant,
)

# The side effect every mission task is authorized for. It rides the T0
# facai-native execution plane, which is always registered, so an unknown
# adapter id can never turn a policy decision into a registry error.
GATE_BACKEND = "facai-native"
MISSION_RUN = "mission.run"

# Mission grants must outlive the slowest legal mission: one job may wait up
# to an hour (API timeout ceiling) for its agent, a mission runs several jobs
# with retries and backoff on top. A day covers all of that with margin while
# still expiring; operators can shorten or lengthen it via env.
DEFAULT_MISSION_GRANT_TTL_SECONDS = 86400
TTL_ENV = "FACAI_MISSION_GRANT_TTL_SECONDS"
MODE_ENV = "FACAI_GATE_MODE"

# Operator-facing copy for the MISSION_DENIED audit event: a refusal must say
# why, in the operator's language, or it invites routing around the gate.
DENY_COPY = {
    GRANT_REQUIRED: "任务缺少有效的 mission grant，已拒绝执行且未调用适配器",
    GRANT_EXPIRED: "任务的 mission grant 已过期，已拒绝执行且未调用适配器",
    GRANT_EXHAUSTED: "任务的 mission grant 次数已用尽，已拒绝执行且未调用适配器",
}

_engine = None
_engine_lock = threading.Lock()


def mission_ttl() -> int:
    """Configured mission-grant TTL; a missing or malformed value keeps the
    documented default instead of failing mission creation."""
    raw = os.getenv(TTL_ENV, "")
    try:
        parsed = int(raw.strip())
    except (AttributeError, ValueError):
        return DEFAULT_MISSION_GRANT_TTL_SECONDS
    return parsed if parsed > 0 else DEFAULT_MISSION_GRANT_TTL_SECONDS


def engine() -> AuthorizationEngine:
    """The process-level engine, created on first use from the environment."""
    global _engine
    with _engine_lock:
        if _engine is None:
            # An unknown mode raises here on purpose: an operator who asks for
            # OWNER_GATE must never get MISSION_AUTONOMOUS by silent fallback.
            _engine = AuthorizationEngine(mode=os.getenv(MODE_ENV, MISSION_AUTONOMOUS))
        return _engine


def reset_engine() -> None:
    """Drop the singleton so the next use re-reads the environment (tests)."""
    global _engine
    with _engine_lock:
        _engine = None


def issue_mission_grant(mission_id: str, actor: str = None,
                        ttl_seconds: int = None) -> CapabilityGrant:
    """Sign one ``mission:<id>`` grant in the platform core's name.

    ``actor`` scopes who may consume the grant; missions are unbound at
    creation time, so callers pass None and the grant admits every actor the
    mission is later bound to. Issuing is reserved to FACAI_OWNER by the core.
    """
    return engine().issue_grant(
        subject=actor or "*",
        action_key=f"mission:{mission_id}",
        issued_by=FACAI_OWNER,
        ttl_seconds=ttl_seconds if ttl_seconds is not None else mission_ttl(),
        note="平台核于 mission 落库时自动签发")


def authorize_for(mission_id: str, actor: str = None,
                  backend: str = GATE_BACKEND, action: str = MISSION_RUN) -> dict:
    """Decide whether this mission's next task may run. Never executes."""
    return engine().authorize(ActionIR(
        actor=actor or "opencode", backend=backend, action=action,
        resource=mission_id, mission_id=mission_id))


def attach_store(store) -> None:
    """Give the engine a persistence store and reload the grants it saved
    before the last restart. Revoked grants were deleted at revocation time,
    so they never resurrect; expired rows are skipped honestly."""
    engine().attach_persistence(store)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for grant in list(engine().grants.values()):
        if grant.expires_at is not None and grant.expires_at <= now:
            engine().grants.pop(grant.grant_id, None)


def ensure_mission_grant(mission_id: str, agent_id: str = None) -> CapabilityGrant:
    """The creation-time issuance every mission landing in the store goes
    through — the single habit that keeps MISSION_AUTONOMOUS from ever
    becoming default-open: no grant, no execution."""
    return issue_mission_grant(mission_id, actor=agent_id)


class MissionDenied(ValueError):
    """Raised when the gate refuses a mission task before the adapter runs.

    A ValueError subclass on purpose: the synchronous API path already maps
    ValueErrors to 422 and the scheduler treats them as permanent failures,
    so a refusal rides the existing failure semantics without new plumbing.
    """

    def __init__(self, reason: str, mode: str):
        self.reason = reason
        self.mode = mode
        super().__init__(f"MISSION_DENIED: {reason}")


def deny_copy(reason: str) -> str:
    """The Chinese audit line for a refusal reason; unknown reasons still get
    an explicit line instead of an empty payload."""
    return DENY_COPY.get(reason, f"任务被授权门拒绝（{reason}），已拒绝执行且未调用适配器")
