"""Capability Broker (batch [3]) + Secret Broker execution (batch [4]).

The broker is a porter, never a judge. Every capability decision belongs to
the frozen :mod:`facai.authority` engine:

    request → AuthorizationEngine.authorize(ActionIR) → ALLOW → scoped capability
                                              → DENY  → CapabilityDenied(reason)

Nothing here issues grants, re-interprets deny reasons, keeps its own actor
allowlist or its own decision log — a second judgment layer would be a second
authority root, which the baseline forbids. The engine's semantics are
untouched: mission grants satisfy MISSION_AUTONOMOUS, action grants satisfy
OWNER_GATE, and use-limited grants are consumed by ``authorize()`` itself, so
one-shot enforcement stays exactly where it lives today. This module is a
callable library for the [8] Adapter SDK batch; the execution chain is not
switched over here.

The Secret Broker (§22 of docs/AUTHORITY_ARCHITECTURE.md) is likewise not a
second engine: it is the concrete executor of the ``secret.request`` action
family. The engine decides; the broker then decrypts the already-encrypted
token through :mod:`facai.secretbox` and returns the plaintext for THIS
execution only — in memory, never on disk, never in the decision log, never
in an event payload. A denied or failed resolution returns a deny decision
without the value (the exact shape ``facai/execution.py:68`` already uses for
user-model keys, now broker-shaped).

Child-process env hygiene (audit CREDENTIAL item at ``facai/opencode.py:121``):
:func:`inject_scoped_env` builds the child env from an explicit whitelist of
system keys plus granted keys only — ``dict(os.environ)`` full inheritance is
retired. Operators name any extra pass-through keys explicitly via
``FACAI_CHILD_ENV_ALLOWLIST``; nothing else ever crosses the boundary.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .authority import ALLOW, ActionIR
from .secretbox import SecretBoxError, decrypt

# The frozen capability action families (operator-ratified 2026-10-05). A
# request outside this set is refused before the engine is even consulted:
# the engine judges actions, and an unlisted action has no grant vocabulary
# to be judged against — letting it through would invent a permission.
CAPABILITY_ACTIONS = ("shell.exec", "filesystem.write", "network.*",
                      "docker.run", "secret.request", "provider.invoke")

# The one action family the broker executes in-process (see resolve_secret).
SECRET_REQUEST = "secret.request"

# Deployment-named pass-through keys for child processes. This keeps the
# whitelist discipline (named keys only, never full inheritance) while letting
# a self-hosted deployment carry the adapter credentials it actually relies on
# (e.g. OPENCODE_AUTH_CONTENT) without widening the code-side base set.
CHILD_ENV_ALLOWLIST_ENV = "FACAI_CHILD_ENV_ALLOWLIST"

# The minimal system set a child CLI (node-based OpenCode/goose, python, git)
# needs to start and reach its endpoints: process/binary resolution, OS
# plumbing and TLS trust roots. These are identities and plumbing, not
# credentials — everything secret-shaped stays out unless explicitly granted.
SYSTEM_ENV_KEYS = (
    # Windows process/OS plumbing
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "ALLUSERSPROFILE",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "TEMP", "TMP",
    "NUMBER_OF_PROCESSORS", "OS", "PROCESSOR_ARCHITECTURE",
    "COMPUTERNAME", "USERNAME",
    # POSIX process/OS plumbing
    "HOME", "USER", "SHELL", "TERM", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    # TLS trust roots (certificates, not credentials)
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "CURL_CA_BUNDLE",
)


class CapabilityDenied(ValueError):
    """The engine refused the action; the broker dispatched nothing.

    A ValueError subclass on purpose, mirroring ``MissionDenied``: refusal
    rides the existing failure semantics without new plumbing.
    """

    def __init__(self, reason: str, verdict: dict = None):
        self.reason = reason
        self.verdict = dict(verdict or {})
        super().__init__(f"CAPABILITY_DENIED: {reason}")


def _now():
    return datetime.now(timezone.utc)


def _grant_ttl(engine, grant_id):
    """Remaining seconds of the grant behind an ALLOW verdict.

    The capability lives exactly as long as the grant that backs it — the
    broker never mints a lifetime of its own. ``None`` mirrors the grant:
    unlimited until revoked.
    """
    grant = engine.grants.get(grant_id)
    if grant is None or grant.expires_at is None:
        return None
    remaining = (grant.expires_at - _now()).total_seconds()
    return max(0, int(remaining))


def _capability(action, backend, resource, verdict, subject):
    """The scoped handle: names what was authorized and by which grant.

    The credential of record is the engine's grant; the capability merely
    references it, so revocation or exhaustion in the engine takes effect
    without any broker-side state to clean up.
    """
    return {"action": action, "backend": backend, "resource": resource,
            "grant_id": verdict.get("grant_id"), "subject": subject}


def request_capability(engine, grant_subject, backend, action, resource="",
                       mission_id="", scope=None) -> dict:
    """Turn one request into one scoped capability — after the engine allows it.

    The single judgment is ``engine.authorize`` on the request's ActionIR;
    this function never decides anything itself. Returns
    ``{"capability", "scope", "ttl"}``; a ``CapabilityDenied`` (carrying the
    engine's reason) means nothing was dispatched.

    For ``secret.request`` the ALLOW verdict is executed immediately by
    :func:`resolve_secret`: the scope must carry the transport-only keys
    ``token`` (the secretbox-encrypted value) and ``root`` (the data dir for
    the master key). The outcome rides the returned dict as ``secret``; the
    transport keys are stripped from the echoed scope so the encrypted token
    does not proliferate through caller state.
    """
    if action not in CAPABILITY_ACTIONS:
        raise CapabilityDenied("UNKNOWN_ACTION", {
            "action": action, "actor": grant_subject, "backend": backend,
            "resource": resource, "mission_id": mission_id,
            "reason": f"action '{action}' is not a brokerable capability family"})
    verdict = engine.authorize(ActionIR(actor=grant_subject, backend=backend,
                                        action=action, resource=resource,
                                        mission_id=mission_id))
    if verdict["decision"] != ALLOW:
        raise CapabilityDenied(verdict["reason"], verdict)
    scope = dict(scope or {})
    ttl = _grant_ttl(engine, verdict["grant_id"])
    if action == SECRET_REQUEST:
        root = scope.pop("root", None)
        outcome = resolve_secret(root, resource, scope)
        scope.pop("token", None)
        if not outcome.get("granted"):
            # 拒绝时返回 deny 而非值：决策返回，值恒为 None。
            return {"capability": None, "scope": scope, "ttl": ttl, "denied": True,
                    "reason": outcome.get("reason", ""), "secret": outcome}
        return {"capability": _capability(action, backend, resource, verdict, grant_subject),
                "scope": scope, "ttl": ttl, "denied": False, "reason": "",
                "secret": outcome}
    return {"capability": _capability(action, backend, resource, verdict, grant_subject),
            "scope": scope, "ttl": ttl}


def resolve_secret(root, env_name, scope) -> dict:
    """The secret.request executor: decrypt for THIS execution, in memory only.

    Returns a decision dict — ``{"granted": True, "env_name", "value"}`` on
    success, ``{"granted": False, "reason", "value": None}`` on refusal — so a
    failed resolution can never be mistaken for an empty credential. No
    authorization happens here: the judgment already happened in
    ``request_capability`` (a second check would be a second engine, and would
    double-consume use-limited grants). The plaintext exists only inside this
    return value; callers pass it straight into :func:`inject_scoped_env`.
    """
    scope = dict(scope or {})
    token = scope.get("token") or ""
    if not token:
        return {"granted": False, "reason": "NO_SECRET_IN_SCOPE",
                "env_name": env_name, "value": None}
    try:
        value = decrypt(root, token)
    except SecretBoxError as exc:
        return {"granted": False, "reason": "DECRYPT_FAILED",
                "detail": str(exc), "env_name": env_name, "value": None}
    return {"granted": True, "reason": "", "env_name": env_name, "value": value}


def system_env_base(environ=None) -> dict:
    """The whitelist base: documented system keys only, harvested from the env.

    Keys absent from the process contribute nothing; keys beyond
    ``SYSTEM_ENV_KEYS`` and the operator-named allowlist contribute nothing —
    that is the whole point.
    """
    environ = os.environ if environ is None else environ
    keys = list(SYSTEM_ENV_KEYS) + list(extra_allowed_keys(environ))
    base = {}
    for key in keys:
        value = environ.get(key)
        if value:
            base[key] = value
    return base


def extra_allowed_keys(environ=None) -> tuple:
    """Operator-named additional pass-through keys (comma-separated env)."""
    environ = os.environ if environ is None else environ
    raw = (environ.get(CHILD_ENV_ALLOWLIST_ENV) or "").strip()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _grant_entries(grants):
    """Normalize granted secrets into (env_name, value) pairs.

    Accepts either an already-authorized mapping (``{env_name: value}``) or an
    iterable of :func:`resolve_secret` outcomes — of which only ``granted``
    ones become keys. A denied secret contributes nothing, by construction.
    """
    if not grants:
        return []
    if hasattr(grants, "items"):
        return [(str(name), value) for name, value in grants.items() if name and value]
    entries = []
    for outcome in grants:
        if isinstance(outcome, dict) and outcome.get("granted") \
                and outcome.get("env_name") and outcome.get("value"):
            entries.append((str(outcome["env_name"]), outcome["value"]))
    return entries


def inject_scoped_env(base_env=None, grants=None) -> dict:
    """Child-process env = whitelist base + explicitly granted keys.

    ``base_env`` is the already-whitelisted base (:func:`system_env_base` by
    default); ``grants`` are the authorized secret.request results (a mapping
    or resolve_secret outcomes). This is the merge point the user-model
    runtime paradigm (``facai/execution.py:68``) uses — full
    ``dict(os.environ)`` inheritance never happens here.
    """
    base = system_env_base() if base_env is None else dict(base_env)
    for name, value in _grant_entries(grants):
        base[name] = value
    return base
