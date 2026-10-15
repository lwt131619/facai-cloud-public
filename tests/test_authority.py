"""P0 authority-core invariants (docs/AUTHORITY_ARCHITECTURE.md §26-27)."""
from datetime import timedelta

import pytest

from facai.authority import (
    DENY,
    FACAI_OWNER,
    GrantError,
    OWNER_GATE,
    ActionIR,
    AuthorizationEngine,
    ExecutionOutcome,
    PROVIDER_REFUSAL,
    TRUST_EXTERNAL,
    TRUST_NATIVE,
    seed_backends,
)


def action(actor="opencode", backend="opencode", verb="shell.exec", resource="", mission_id=""):
    return ActionIR(actor=actor, backend=backend, action=verb, resource=resource, mission_id=mission_id)


def test_only_the_owner_may_issue_grants():
    eng = AuthorizationEngine()
    with pytest.raises(GrantError):
        eng.issue_grant("opencode", "opencode:shell.exec:", issued_by="goose")
    grant = eng.issue_grant("opencode", "*", issued_by=FACAI_OWNER)
    assert grant.issued_by == FACAI_OWNER


def test_owner_gate_requires_a_live_grant():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    denied = eng.authorize(action())
    assert denied["decision"] == DENY
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER, ttl_seconds=60)
    assert eng.authorize(action())["decision"] == "ALLOW"


def test_mission_autonomous_requires_a_mission_grant():
    """No default-open: even autonomous missions present their mission grant."""
    eng = AuthorizationEngine()  # default MISSION_AUTONOMOUS
    denied = eng.authorize(action(mission_id="m-1"))
    assert denied["decision"] == DENY and denied["reason"] == "GRANT_REQUIRED"
    eng.issue_grant("opencode", "mission:m-1", issued_by=FACAI_OWNER, ttl_seconds=600)
    allowed = eng.authorize(action(mission_id="m-1"))
    assert allowed["decision"] == "ALLOW"
    assert eng.decisions[-1]["mode"] == "MISSION_AUTONOMOUS"


def test_action_grant_also_satisfies_mission_autonomous():
    eng = AuthorizationEngine()
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER)
    assert eng.authorize(action())["decision"] == "ALLOW"


def test_mission_grant_never_satisfies_owner_gate():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "mission:m-1", issued_by=FACAI_OWNER)
    denied = eng.authorize(action(mission_id="m-1"))
    assert denied["decision"] == DENY and denied["reason"] == "GRANT_REQUIRED"


def test_expired_grant_reports_expired_reason():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER, ttl_seconds=1)
    g = next(iter(eng.grants.values()))
    object.__setattr__(g, "expires_at", g.issued_at - timedelta(seconds=1))
    result = eng.authorize(action())
    assert result["decision"] == DENY and result["reason"] == "GRANT_EXPIRED"


def test_exhausted_grant_reports_exhausted_reason():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER, uses=1)
    assert eng.authorize(action())["decision"] == "ALLOW"
    again = eng.authorize(action())
    assert again["decision"] == DENY and again["reason"] == "GRANT_EXHAUSTED"


def test_expired_grant_denies_in_gate_mode():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER,
                    ttl_seconds=-1 if False else 1)
    # Force expiry without sleeping: rewind issued_at directly.
    g = next(iter(eng.grants.values()))
    object.__setattr__(g, "expires_at", g.issued_at - timedelta(seconds=1))
    assert eng.authorize(action())["decision"] == DENY


def test_use_limited_grant_exhausts_then_denies():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:shell.exec", issued_by=FACAI_OWNER, uses=2)
    assert eng.authorize(action())["decision"] == "ALLOW"
    assert eng.authorize(action())["decision"] == "ALLOW"
    assert eng.authorize(action())["decision"] == DENY


def test_subject_scoping_other_agent_cannot_consume():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("goose", "opencode:shell.exec", issued_by=FACAI_OWNER)
    assert eng.authorize(action(actor="opencode"))["decision"] == DENY


def test_unknown_backend_is_refused_even_autonomous():
    eng = AuthorizationEngine()
    a = action(actor="mystery", backend="mystery")
    assert eng.authorize(a)["decision"] == DENY
    assert "not registered" in eng.authorize(a)["reason"]


def test_registry_seeds_match_inventory_and_trust_classes():
    eng = AuthorizationEngine()
    assert eng.backend("facai-native").trust_class == TRUST_NATIVE
    assert eng.backend("zcode").trust_class == TRUST_EXTERNAL
    assert eng.backend("zcode").remote_authority_dependency is True
    assert eng.backend("opencode").management == "adapter"
    assert set(eng.backends) == {"facai-native", "opencode", "goose", "hermes",
                                 "autoclaw", "zcode"}


def test_revoke_is_owner_only_and_takes_effect():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    g = eng.issue_grant("opencode", "*", issued_by=FACAI_OWNER)
    with pytest.raises(GrantError):
        eng.revoke_grant(g.grant_id, revoked_by="opencode")
    assert eng.revoke_grant(g.grant_id, revoked_by=FACAI_OWNER) is True
    assert eng.authorize(action())["decision"] == DENY


def test_provider_refusal_is_not_a_mission_failure():
    outcome = ExecutionOutcome(state=PROVIDER_REFUSAL, detail="429 quota")
    assert outcome.executed is False
    assert outcome.mission_failed is False


def test_prefix_grants_cover_action_families():
    eng = AuthorizationEngine(mode=OWNER_GATE)
    eng.issue_grant("opencode", "opencode:", issued_by=FACAI_OWNER)
    assert eng.authorize(action(verb="shell.exec"))["decision"] == "ALLOW"
    assert eng.authorize(action(verb="model.complete"))["decision"] == "ALLOW"
    assert eng.authorize(action(actor="goose"))["decision"] == DENY
