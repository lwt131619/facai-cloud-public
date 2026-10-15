"""Session-based admin authentication for a public-facing deployment.

Passwords and session secrets are read from the process environment only; no
secret is ever written to the database, a cookie payload is never stored raw,
and no credential is logged. Sessions are opaque random tokens whose SHA-256
hash is persisted, are bound to a per-session CSRF token, and expire after a
configurable TTL. Login failures are throttled per client address.

HTTP Basic remains accepted for scripts and health checks, but browsers use the
login form, an HttpOnly SameSite=Strict cookie and the CSRF header.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import json
from datetime import datetime, timezone
from uuid import uuid4

SESSION_COOKIE = "facai_session"
CSRF_HEADER = "x-csrf-token"
USERNAME = "admin"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Client API keys are presented as ``Authorization: Bearer facai_<id>_<secret>``.
# The marker keeps a key distinguishable from an unrelated bearer token, which
# must keep falling through to the session/Basic handling.
API_KEY_PREFIX = "facai_"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")
PBKDF2_ITERATIONS = 200_000
RESERVED_USERNAMES = frozenset({"admin", "root", "facai", "operator", "system"})

PASSWORD_MIN_LENGTH = 8
# A "weak" password is one a casual or automated guesser cracks instantly:
# trivially short, one repeated character or a repeated short block, or a
# well-known credential -- all tested against the password's guessable core.
_WEAK_PASSWORD_RES = (
    re.compile(r"^(.)\1+$"),                # 11111111, aaaaaaaa
    re.compile(r"^(.{1,4})\1+$"),           # abababab, abcabcabc
)
# A guessable core padded with filler ("Password123!", "trustno1!") is still
# the same guess, so every password is also tested in progressively de-fanged
# forms: without trailing punctuation, without the trailing digit run, and
# without either.
def _password_cores(password):
    """Yield the raw, trimmed and de-suffixed lowercased forms."""
    lowered = password.lower()
    trimmed = re.sub(r"[^a-z0-9]+$", "", lowered)
    return (lowered, trimmed, re.sub(r"[^a-z]+$", "", lowered),
            re.sub(r"\d+$", "", trimmed))


# A password that is nothing but a contiguous slice of one of these runs is a
# keyboard walk, not a secret: "qwertyui", "123456789", "abcdefgh".
_PASSWORD_RUNS = (
    "0123456789", "9876543210",
    "abcdefghijklmnopqrstuvwxyz", "zyxwvutsrqponmlkjihgfedcba",
    "qwertyuiop", "poiuytrewq", "asdfghjkl", "lkjhgfdsa", "zxcvbnm", "mnbvcxz",
)
# Credentials an automated guesser tries before anything else, matched against
# the password lowercased with its trailing digits and punctuation dropped:
# "Password123!" is rejected, "pass-12345" is not.
_COMMON_PASSWORDS = frozenset({
    "password", "passw0rd", "passwd", "123456", "1234567", "12345678",
    "123456789", "1234567890", "qwerty", "qwertyuiop", "asdfghjkl", "zxcvbnm",
    "admin", "administrator", "root", "letmein", "welcome", "monkey", "dragon",
    "iloveyou", "abc123", "master", "sunshine", "princess", "football",
    "trustno1", "changeme", "guest", "secret", "facai", "123123",
})


def gates_off() -> bool:
    """FACAI_GATES_OFF=1：运营方一键解除本机条框（2026-10-12 运营决定）。

    覆盖：登录失败锁定（429 节流）、弱密码校验、保留用户名限制、
    mission 创建滑窗限速（api.py 侧同读本开关）。
    不覆盖：402 活跃任务容量上限、API key 作用域、会话/CSRF 体系——
    那些是计费与账号隔离机制，不是条框。
    """
    return os.getenv("FACAI_GATES_OFF", "").strip().lower() in ("1", "true", "yes", "on")


def is_weak_password(password):
    """True when a password is too guessable to accept for a paid account."""
    if gates_off():
        # 条框解除：只要求"非空字符串"，其余全放行。
        return not (isinstance(password, str) and password)
    if not isinstance(password, str):
        return True
    if len(password) < PASSWORD_MIN_LENGTH:
        return True
    for candidate in _password_cores(password):
        if not candidate:
            continue
        if any(pattern.match(candidate) for pattern in _WEAK_PASSWORD_RES):
            return True
        if candidate in _COMMON_PASSWORDS:
            return True
        if len(candidate) >= 3 and any(candidate in run for run in _PASSWORD_RUNS):
            return True
        # "aaaabbbb" survives the repeat/run rules but carries no more entropy.
        if len(candidate) >= 4 and len(set(candidate)) <= 2:
            return True
    return False


def hash_password(password):
    """Salted PBKDF2-SHA256; only the hash never the password is persisted."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("ascii"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, hexdigest = (stored or "").split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                        salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(candidate.hex(), hexdigest)
    except (ValueError, TypeError):
        return False


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_api_key(secret):
    """SHA-256 digest of a key's secret half; all the store ever receives."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _default_username():
    return os.getenv("FACAI_ADMIN_USER", USERNAME)


class AuthManager:
    def __init__(self, store):
        self.store = store
        # Fail during startup for malformed or over-limit tenant configuration.
        self.users

    @property
    def enabled(self):
        return bool(self.users)

    @property
    def users(self):
        """Environment credentials plus self-registered accounts, capped at five."""
        configured = self._env_users()
        registered = self.store.platform_users()
        merged = {**registered, **configured}
        limit = self.max_users
        if len(merged) > limit:
            # Environment entries always win; drop newest registered accounts beyond
            # the cap instead of locking every operator out of the instance.
            for name in list(registered):
                if len(merged) <= limit:
                    break
                merged.pop(name, None)
        return merged

    def _env_users(self):
        raw = os.getenv("FACAI_USERS_JSON", "").strip()
        if raw:
            try:
                configured = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("FACAI_USERS_JSON must be a JSON object of usernames to passwords") from exc
            if not isinstance(configured, dict) or not configured:
                raise RuntimeError("FACAI_USERS_JSON must be a non-empty JSON object")
            if len(configured) > 5:
                raise RuntimeError("FACAI supports at most five users")
            if any(not isinstance(name, str) or not name.strip() or
                   not isinstance(password, str) or not password
                   for name, password in configured.items()):
                raise RuntimeError("FACAI_USERS_JSON entries require non-empty username and password strings")
            return configured
        password = os.getenv("FACAI_ADMIN_PASSWORD") or ""
        return {self.username: password} if password else {}

    @property
    def username(self):
        return _default_username()

    @property
    def max_users(self):
        try:
            return max(1, int(os.getenv("FACAI_MAX_USERS", "5")))
        except ValueError:
            return 5

    def register_user(self, username, password):
        """Create a self-service account, or return a fixed rejection reason.

        Registration is capped (default five accounts total), environment
        credentials can never be shadowed, and every account gets the same
        per-owner mission isolation as a configured user.
        """
        username = (username or "").strip()
        if not _USERNAME_RE.match(username):
            return None, "invalid_username"
        if not gates_off() and username.lower() in RESERVED_USERNAMES:
            return None, "reserved_username"
        if not isinstance(password, str) or is_weak_password(password):
            return None, "weak_password"
        merged = self.users
        if username in merged:
            return None, "username_taken"
        if len(merged) >= self.max_users:
            return None, "user_limit_reached"
        try:
            self.store.create_platform_user(username, hash_password(password))
        except ValueError:
            return None, "username_taken"
        return username, None

    def change_password(self, username, current, new):
        """Rotate a self-registered account's password after verifying the old one.

        Environment-backed accounts (the operator) are deliberately excluded:
        their credential is the env var, not the database, and changing it here
        would silently diverge from ``FACAI_ADMIN_PASSWORD``. The caller must
        own the account; nothing here can touch a different user's record.
        """
        username = (username or "").strip()
        if not username or username not in self.store.platform_users():
            return None, "not_a_platform_user"
        if not self.verify_credentials(username, current):
            return None, "wrong_current_password"
        if not isinstance(new, str) or is_weak_password(new):
            return None, "weak_password"
        self.store.set_platform_user_password(username, hash_password(new), must_change=False)
        # A rotated credential must retire sessions that predate it, and the
        # client API keys minted under it: keys are derived trust in the
        # account's password, not an independent grant.
        self.store.delete_user_auth_sessions(username)
        self.store.revoke_all_api_keys(username)
        return username, None

    def admin_reset_password(self, username, new):
        """Operator-forced password reset for a registered account.

        A weak target password is accepted but flags the account to rotate on
        next login -- the admin can hand out a temporary password without
        leaving the account permanently on a guessable credential.
        """
        username = (username or "").strip()
        if not username or username not in self.store.platform_users():
            return None, "not_a_platform_user"
        if not isinstance(new, str) or not new:
            return None, "weak_password"
        self.store.set_platform_user_password(username, hash_password(new),
                                              must_change=is_weak_password(new))
        self.store.delete_user_auth_sessions(username)
        # The old credential is compromised or handed out of band, so keys
        # minted under it die with it; the must-change gate then keeps the
        # account from minting replacements until it rotates.
        self.store.revoke_all_api_keys(username)
        return username, None

    @property
    def ttl_seconds(self):
        try:
            return max(60, int(os.getenv("FACAI_SESSION_TTL", "28800")))
        except ValueError:
            return 28800

    @property
    def max_failures(self):
        try:
            return max(1, int(os.getenv("FACAI_LOGIN_MAX_FAILURES", "5")))
        except ValueError:
            return 5

    @property
    def failure_window(self):
        try:
            return max(30, int(os.getenv("FACAI_LOGIN_WINDOW", "300")))
        except ValueError:
            return 300

    def verify_credentials(self, username, password):
        users = self.users
        stored = users.get(username or "", "")
        valid_user = any(hmac.compare_digest(username or "", configured) for configured in users)
        if stored.startswith("pbkdf2_sha256$"):
            valid_password = verify_password(password or "", stored)
        else:
            valid_password = hmac.compare_digest(password or "", stored)
        return bool(stored) and valid_user and valid_password

    def is_locked(self, client_ip):
        if gates_off():
            return False
        return self.store.login_failures(client_ip) >= self.max_failures

    def login(self, username, password, client_ip):
        """Return (session, None) on success, (None, reason) otherwise."""
        if self.is_locked(client_ip):
            return None, "throttled"
        if not self.verify_credentials(username, password):
            self.store.record_login_failure(client_ip, self.failure_window)
            return None, "invalid"
        self.store.clear_login_failures(client_ip)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires_at = self.store.create_auth_session(hash_token(token), csrf, username, self.ttl_seconds)
        return {"token": token, "csrf_token": csrf, "username": username,
                "expires_at": expires_at,
                "must_change_password": self.store.platform_user_must_change(username)}, None

    def session_from_token(self, token):
        if not token:
            return None
        session = self.store.get_auth_session(hash_token(token))
        if session is None:
            return None
        if session["expires_at"] < datetime.now(timezone.utc).isoformat():
            self.store.delete_auth_session(session["token_hash"])
            return None
        self.store.touch_auth_session(session["token_hash"])
        return session

    def session_from_request(self, request):
        return self.session_from_token(request.cookies.get(SESSION_COOKIE))

    def check_basic(self, request):
        return self.basic_username(request) is not None

    @staticmethod
    def _basic_pair(request):
        """(username, password) from a well-formed Basic header, else None.

        A header in another scheme, undecodable base64 or a payload without
        the ``user:password`` separator names no account at all.
        """
        header = request.headers.get("authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            username, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None
        if scheme.lower() != "basic":
            return None
        return username, password

    def basic_username(self, request):
        pair = self._basic_pair(request)
        if pair is None:
            return None
        username, supplied = pair
        return username if self.verify_credentials(username, supplied) else None

    def basic_username_claimed(self, request):
        """The username a Basic header claims, before any verification.

        The per-IP throttle counts a rejected Basic attempt because the header
        names an account; a malformed or foreign Authorization header (a
        Bearer token of some other scheme) names nobody and must never be able
        to lock a shared egress address out of the instance.
        """
        pair = self._basic_pair(request)
        return pair[0] if pair else None

    # --- client API keys ---------------------------------------------------------

    @staticmethod
    def _split_api_key(token):
        """Split ``facai_<id>_<secret>`` into (id, secret), or None.

        ``maxsplit=2`` keeps the secret intact even when it contains its own
        underscores; a token missing the marker or one of the two halves is not
        a key and is reported as unparseable rather than looked up.
        """
        if not token or not token.startswith(API_KEY_PREFIX):
            return None
        parts = token.split("_", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None
        return parts[1], parts[2]

    def issue_api_key(self, owner):
        """Mint one client API key; the plaintext is returned exactly once.

        Only the digest of the secret half is persisted and only the prefix is
        listed again, so the platform itself cannot replay a client's key.
        """
        key_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        prefix = f"{API_KEY_PREFIX}{key_id}_{secret[:4]}"
        row = self.store.create_api_key(key_id, owner, hash_api_key(secret), prefix)
        return {"id": key_id, "prefix": prefix, "created_at": row["created_at"],
                "api_key": f"{API_KEY_PREFIX}{key_id}_{secret}"}

    def verify_api_key(self, token):
        """Resolve a presented key to ``(owner_id, key_id)``, or ``(None, None)``.

        The id half is public (it labels the key in the console), so only the
        secret is hashed and compared in constant time. A malformed key, an
        unknown id, a revoked key and a key whose account no longer exists are
        all reported the same way. ``last_used_at`` is stamped by the caller
        once the key's scope has admitted the call: verification alone is not
        a use of the key.
        """
        parts = self._split_api_key(token)
        if parts is None:
            return None, None
        key_id, secret = parts
        row = self.store.get_api_key(key_id)
        if row is None or row["revoked_at"]:
            return None, None
        if not hmac.compare_digest(row["key_hash"], hash_api_key(secret)):
            return None, None
        if row["owner_id"] not in self.users:
            return None, None
        return row["owner_id"], key_id

    @staticmethod
    def valid_csrf(session, supplied):
        return bool(supplied) and hmac.compare_digest(session["csrf_token"], supplied)

    def logout(self, request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            self.store.delete_auth_session(hash_token(token))

    def prune(self):
        return self.store.prune_auth_sessions()

    @staticmethod
    def cookie_secure(request):
        setting = (os.getenv("FACAI_COOKIE_SECURE", "auto") or "auto").lower()
        if setting in {"1", "true", "yes", "on"}:
            return True
        if setting in {"0", "false", "no", "off"}:
            return False
        if not os.getenv("FACAI_TRUST_PROXY"):
            return False
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        return forwarded == "https" or request.url.scheme == "https"
