"""Production session authentication: login, CSRF, throttling, logout, expiry."""

import base64

import pytest
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from facai import api as api_module
from facai.api import create_app
from facai.auth import SESSION_COOKIE, hash_token, is_weak_password
from facai.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    # The Basic read-only gate is opt-in; a developer shell that exports it must
    # not change what these tests exercise.
    monkeypatch.delenv("FACAI_BASIC_READONLY", raising=False)
    return TestClient(create_app(tmp_path))


def login(client, password="correct-horse"):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 200
    return response.json()


def test_session_login_csrf_and_logout(client):
    assert client.get("/api/v1/missions").status_code == 401
    session = login(client)
    assert session["authenticated"] is True
    assert session["csrf_token"]
    assert SESSION_COOKIE in client.cookies

    assert client.get("/api/v1/missions").status_code == 200
    # Unsafe request without the CSRF header is rejected.
    blocked = client.post("/api/v1/missions", json={
        "title": "A", "original_input": "A", "steps": ["Build"]})
    assert blocked.status_code == 403

    created = client.post("/api/v1/missions", headers={"X-CSRF-Token": session["csrf_token"]}, json={
        "title": "A", "original_input": "A", "steps": ["Build"]})
    assert created.status_code == 201

    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/missions").status_code == 401


def test_wrong_password_and_basic_fallback(client):
    assert client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "wrong"}).status_code == 401
    # HTTP Basic stays available for scripts and health tooling.
    encoded = base64.b64encode(b"admin:correct-horse").decode()
    headers = {"Authorization": f"Basic {encoded}"}
    assert client.get("/api/v1/missions", headers=headers).status_code == 200
    assert client.get("/", headers=headers).status_code == 200


def test_browser_navigation_redirects_to_login(client):
    page = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303
    # The original path is preserved so a deep link survives the login round-trip.
    assert page.headers["location"].startswith("/login")
    assert "next=" in page.headers["location"]
    assert client.get("/login").status_code == 200
    assert client.get("/login").text.count("登录") >= 1


SECURITY_HEADERS = (("x-content-type-options", "nosniff"),
                    ("x-frame-options", "DENY"),
                    ("referrer-policy", "same-origin"))


def test_security_headers_on_early_and_authenticated_responses(client):
    """The headers must also cover the responses no route ever produces.

    The login redirect, the anonymous 401 and the CSRF 403 are returned by the
    auth middleware, so the header middleware has to be registered after it and
    sit outside it in the stack.
    """
    anonymous_api = client.get("/api/v1/missions")
    login_redirect = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    session = login(client)
    csrf_block = client.post("/api/v1/missions", json={
        "title": "A", "original_input": "A", "steps": ["Build"]})
    page = client.get("/login")
    assert (anonymous_api.status_code, login_redirect.status_code,
            csrf_block.status_code, page.status_code) == (401, 303, 403, 200)
    assert session["csrf_token"]

    for response in (anonymous_api, login_redirect, csrf_block, page):
        for name, value in SECURITY_HEADERS:
            assert response.headers.get_list(name) == [value], (response.status_code, name)


def test_security_headers_never_override_a_handler_value(client):
    """A response that already picked a value keeps it, and is not duplicated."""
    login(client)

    @client.app.get("/api/v1/sessions/probe-headers")
    def probe_headers():
        return PlainTextResponse("ok", headers={"X-Frame-Options": "SAMEORIGIN"})

    response = client.get("/api/v1/sessions/probe-headers")
    assert response.status_code == 200
    assert response.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert response.headers.get_list("x-content-type-options") == ["nosniff"]
    assert response.headers.get_list("referrer-policy") == ["same-origin"]


def test_login_throttling(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("FACAI_LOGIN_MAX_FAILURES", "3")
    client = TestClient(create_app(tmp_path))
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json={
            "username": "admin", "password": "no"}).status_code == 401
    # Even a correct password is refused while the lockout is active.
    assert client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse"}).status_code == 429


def test_expired_session_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    app = create_app(tmp_path)
    client = TestClient(app)
    login(client)
    with app.state.store.connect() as db:
        db.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.get("/api/v1/missions").status_code == 401


def test_raw_session_token_is_never_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    app = create_app(tmp_path)
    client = TestClient(app)
    session = login(client)
    token = client.cookies.get(SESSION_COOKIE)
    with app.state.store.connect() as db:
        row = db.execute("SELECT token_hash, csrf_token FROM auth_sessions").fetchone()
    assert token != row["token_hash"]
    assert session["csrf_token"] == row["csrf_token"]
    assert hash_token(token) == row["token_hash"]
    assert "correct-horse" not in f"{row['token_hash']} {row['csrf_token']}"


def test_auth_disabled_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/missions").status_code == 200
    assert client.get("/api/v1/auth/session").json() == {
        "auth_required": False, "authenticated": True, "username": "admin",
        "is_admin": True}
    assert client.get("/api/v1/health").json()["auth_required"] is False


def test_secure_cookie_flag_respects_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("FACAI_COOKIE_SECURE", "1")
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct-horse"})
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_multi_user_mission_and_cost_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USERS_JSON", '{"alice":"alice-pass","bob":"bob-pass"}')
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    app = create_app(tmp_path)
    alice = TestClient(app)
    bob = TestClient(app)

    alice_session = alice.post("/api/v1/auth/login", json={
        "username": "alice", "password": "alice-pass"}).json()
    bob_session = bob.post("/api/v1/auth/login", json={
        "username": "bob", "password": "bob-pass"}).json()
    assert alice_session["username"] == "alice"
    assert bob_session["username"] == "bob"

    alice_mission = alice.post("/api/v1/missions", headers={
        "X-CSRF-Token": alice_session["csrf_token"]}, json={
            "title": "Alice", "original_input": "private", "steps": ["Build"]}).json()
    bob_mission = bob.post("/api/v1/missions", headers={
        "X-CSRF-Token": bob_session["csrf_token"]}, json={
            "title": "Bob", "original_input": "private", "steps": ["Build"]}).json()

    assert [m["title"] for m in alice.get("/api/v1/missions").json()] == ["Alice"]
    assert [m["title"] for m in bob.get("/api/v1/missions").json()] == ["Bob"]
    assert alice.get(f"/api/v1/missions/{bob_mission['id']}").status_code == 404
    assert bob.get(f"/api/v1/missions/{alice_mission['id']}").status_code == 404

    store = app.state.store
    store.add_cost(alice_mission["id"], "mock", "model", 2.5, tokens=10)
    alice_message = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id="alice-1", text="alice secret",
        owner_id="alice")
    bob_message = store.record_channel_message(
        "telegram", "INBOUND", "RECEIVED", external_id="bob-1", text="bob secret",
        owner_id="bob")
    assert alice.get(f"/api/v1/missions/{alice_mission['id']}").json()["cost_total"] == 2.5
    assert bob.get(f"/api/v1/missions/{alice_mission['id']}").status_code == 404
    assert [m["text"] for m in alice.get("/api/v1/inbox").json()] == ["alice secret"]
    assert [m["text"] for m in bob.get("/api/v1/inbox").json()] == ["bob secret"]
    assert alice.get(f"/api/v1/channels/telegram/messages/{bob_message}/events").status_code == 404


def test_multi_user_job_id_guessing_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USERS_JSON", '{"alice":"alice-pass","bob":"bob-pass"}')
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    app = create_app(tmp_path)
    alice, bob = TestClient(app), TestClient(app)
    alice_session = alice.post("/api/v1/auth/login", json={
        "username": "alice", "password": "alice-pass"}).json()
    bob_session = bob.post("/api/v1/auth/login", json={
        "username": "bob", "password": "bob-pass"}).json()

    mission = alice.post("/api/v1/missions", headers={
        "X-CSRF-Token": alice_session["csrf_token"]}, json={
            "title": "Alice", "original_input": "private", "steps": ["Build"]}).json()
    alice.put(f"/api/v1/missions/{mission['id']}/binding", headers={
        "X-CSRF-Token": alice_session["csrf_token"]},
        json={"agent_id": "opencode", "model_id": "deepseek-flash", "runtime_id": "local"})
    job = alice.post(f"/api/v1/missions/{mission['id']}/jobs", headers={
        "X-CSRF-Token": alice_session["csrf_token"]},
        json={"prompt": "alice private prompt", "step_id": mission["steps"][0]["id"]}).json()
    assert job["status"] == "QUEUED"

    # A globally unique job id must not let another tenant read its payload.
    assert bob.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    # Nor mutate it: pause/resume/cancel/retry all resolve through the mission owner.
    for action in ("pause", "resume", "cancel", "retry"):
        assert bob.post(f"/api/v1/jobs/{job['id']}/{action}", headers={
            "X-CSRF-Token": bob_session["csrf_token"]}).status_code == 404

    assert bob.get(f"/api/v1/missions/{mission['id']}/jobs").status_code == 404
    owner_view = alice.get(f"/api/v1/jobs/{job['id']}").json()
    assert owner_view["prompt"] == "alice private prompt"
    assert owner_view["status"] == "QUEUED"


def test_multi_user_configuration_is_capped_at_five(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_USERS_JSON", '{"1":"p","2":"p","3":"p","4":"p","5":"p","6":"p"}')
    with pytest.raises(RuntimeError, match="at most five users"):
        create_app(tmp_path)


def _csrf(client):
    return client.get("/api/v1/auth/session").json()["csrf_token"]


def test_register_change_password_and_weak_rejection(client):
    reg = client.post("/api/v1/auth/register", json={"username": "buyer9", "password": "Strong-Pass-123"})
    assert reg.status_code == 201, reg.text
    csrf = _csrf(client)
    hdr = {"X-CSRF-Token": csrf}

    bad = client.post("/api/v1/auth/change-password",
                      json={"current_password": "nope", "new_password": "Another-Strong-99"}, headers=hdr)
    assert bad.status_code == 401

    weak = client.post("/api/v1/auth/change-password",
                       json={"current_password": "Strong-Pass-123", "new_password": "12345678"}, headers=hdr)
    assert weak.status_code == 422

    ok = client.post("/api/v1/auth/change-password",
                     json={"current_password": "Strong-Pass-123", "new_password": "New-Strong-456"}, headers=hdr)
    assert ok.status_code == 200
    client.post("/api/v1/auth/logout")
    relog = client.post("/api/v1/auth/login", json={"username": "buyer9", "password": "New-Strong-456"})
    assert relog.status_code == 200
    assert relog.json().get("must_change_password") is False


def test_admin_reset_weak_password_forces_change(client):
    client.post("/api/v1/auth/register", json={"username": "buyer10", "password": "Strong-Pass-123"})
    login(client)  # log back in as admin
    csrf = _csrf(client)
    r = client.post("/api/v1/auth/admin-reset-password",
                    json={"username": "buyer10", "new_password": "123456"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True

    client.post("/api/v1/auth/logout")
    relog = client.post("/api/v1/auth/login", json={"username": "buyer10", "password": "123456"})
    assert relog.status_code == 200
    assert relog.json()["must_change_password"] is True


def test_change_password_rejects_operator_account(client):
    login(client)
    csrf = _csrf(client)
    r = client.post("/api/v1/auth/change-password",
                    json={"current_password": "correct-horse", "new_password": "Whatever-Strong-1"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_is_weak_password_covers_repeats_runs_and_common_credentials():
    """Every shape an automated cracker tries first is refused."""
    for weak in ("aaaaaaaa", "11111111", "aaaabbbb", "abcabcabc",
                 "123456789", "qwertyui", "asdfghjk", "abcdefgh",
                 "qwerty", "password123", "admin123", "letmein1",
                 "Password1!", "trustno1!", "12345678"):
        assert is_weak_password(weak) is True, weak
    for strong in ("pass-12345", "Strong-Pass-123", "correct-horse",
                   "Tr0ub4dor&3", "alice-pass-1"):
        assert is_weak_password(strong) is False, strong


def test_register_rejects_newly_covered_weak_passwords(client):
    """The wordlist and run rules are wired into the registration endpoint."""
    for password in ("qwertyui", "aaaaaaaa", "password123", "admin123"):
        rejected = client.post("/api/v1/auth/register",
                               json={"username": "buyer11", "password": password})
        assert rejected.status_code == 422, (password, rejected.text)


def test_must_change_password_blocks_writes_until_rotated(client):
    """A handed-out weak password grants reads and rotation only, not writes."""
    client.post("/api/v1/auth/register",
                json={"username": "buyer12", "password": "Strong-Pass-123"})
    login(client)  # back as the operator to force the reset
    reset = client.post("/api/v1/auth/admin-reset-password",
                        json={"username": "buyer12", "new_password": "qwertyui"},
                        headers={"X-CSRF-Token": _csrf(client)})
    assert reset.status_code == 200 and reset.json()["must_change_password"] is True

    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={
        "username": "buyer12", "password": "qwertyui"}).status_code == 200
    session = client.get("/api/v1/auth/session").json()
    assert session["must_change_password"] is True

    # Reads still work so the console can render the rotation prompt...
    assert client.get("/api/v1/missions").status_code == 200
    # ...but every other write waits for the rotation.
    blocked = client.post("/api/v1/missions", headers={"X-CSRF-Token": session["csrf_token"]},
                          json={"title": "A", "original_input": "A", "steps": ["Build"]})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "请先修改密码"
    assert client.post("/api/v1/billing/recharge", headers={"X-CSRF-Token": session["csrf_token"]},
                       json={"amount_usd": 100}).status_code == 403

    rotated = client.post("/api/v1/auth/change-password", headers={"X-CSRF-Token": _csrf(client)},
                          json={"current_password": "qwertyui",
                                "new_password": "Fresh-Strong-77"})
    assert rotated.status_code == 200

    # Rotation retires every session, so the new credential must log in again.
    assert client.post("/api/v1/auth/login", json={
        "username": "buyer12", "password": "Fresh-Strong-77"}).status_code == 200
    session = client.get("/api/v1/auth/session").json()
    assert session["must_change_password"] is False
    created = client.post("/api/v1/missions", headers={"X-CSRF-Token": session["csrf_token"]},
                          json={"title": "A", "original_input": "A", "steps": ["Build"]})
    assert created.status_code == 201


def test_basic_readonly_gate_refuses_writes(client, monkeypatch):
    """FACAI_BASIC_READONLY=1 makes Basic a read-only credential.

    A browser replays cached Basic credentials on a cross-site form post, so an
    operator can take that class of write off the table entirely.
    """
    monkeypatch.setenv("FACAI_BASIC_READONLY", "1")
    encoded = base64.b64encode(b"admin:correct-horse").decode()
    headers = {"Authorization": f"Basic {encoded}"}
    assert client.get("/api/v1/missions", headers=headers).status_code == 200
    assert client.get("/api/v1/health").status_code == 200

    blocked = client.post("/api/v1/missions", headers=headers, json={
        "title": "A", "original_input": "A", "steps": ["Build"]})
    assert blocked.status_code == 403
    assert "Basic" in blocked.json()["detail"]

    # A session with its CSRF token is the writer the gate points at.
    session = login(client)
    created = client.post("/api/v1/missions", headers={"X-CSRF-Token": session["csrf_token"]},
                          json={"title": "A", "original_input": "A", "steps": ["Build"]})
    assert created.status_code == 201


def test_basic_write_is_allowed_with_a_warning_while_the_gate_is_off(client, monkeypatch, caplog):
    """The default keeps script-style Basic writes working, but says so once."""
    monkeypatch.delenv("FACAI_BASIC_READONLY", raising=False)
    # The warning fires once per account per process; reset it so the assertion
    # does not depend on which tests ran before this one.
    monkeypatch.setattr(api_module, "_basic_write_warned", set())
    caplog.set_level("WARNING")

    encoded = base64.b64encode(b"admin:correct-horse").decode()
    created = client.post("/api/v1/missions", json={
        "title": "A", "original_input": "A", "steps": ["Build"]},
        headers={"Authorization": f"Basic {encoded}"})
    assert created.status_code == 201
    assert any("FACAI_BASIC_READONLY" in record.message for record in caplog.records)


def _register_and_flag(client, username):
    """Register an account, force a weak operator reset, log back in as it."""
    client.post("/api/v1/auth/register", json={"username": username, "password": "Strong-Pass-123"})
    client.post("/api/v1/auth/logout")
    login(client)  # back as the operator to force the reset
    reset = client.post("/api/v1/auth/admin-reset-password",
                        json={"username": username, "new_password": "qwertyui"},
                        headers={"X-CSRF-Token": _csrf(client)})
    assert reset.status_code == 200 and reset.json()["must_change_password"] is True
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={
        "username": username, "password": "qwertyui"}).status_code == 200


def test_must_change_gate_covers_basic_writes(client):
    """The rotation gate is judged on the owner, not on the credential path.

    A reset account used to write freely over HTTP Basic -- including minting
    new, persistent keys -- while only the session path was gated. The calls
    here carry no session cookie, so the Basic branch of the middleware is
    what has to refuse them.
    """
    _register_and_flag(client, "buyer13")

    encoded = base64.b64encode(b"buyer13:qwertyui").decode()
    headers = {"Authorization": f"Basic {encoded}"}
    scripts = TestClient(client.app)
    # Reads stay available so a script client can still list what it owns...
    assert scripts.get("/api/v1/missions", headers=headers).status_code == 200
    # ...but every write waits for the rotation, key minting included: a reset
    # must not be outrun by a freshly minted persistent credential.
    blocked = scripts.post("/api/v1/missions", headers=headers, json={
        "title": "A", "original_input": "A", "steps": ["Build"]})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "请先修改密码"
    assert scripts.post("/api/v1/my/keys", headers=headers).status_code == 403

    # Rotation is the one allowed write over the flagged credential, and it
    # clears the gate for the new one.
    rotated = scripts.post("/api/v1/auth/change-password", headers=headers,
                           json={"current_password": "qwertyui",
                                 "new_password": "Fresh-Strong-88"})
    assert rotated.status_code == 200
    unblocked = base64.b64encode(b"buyer13:Fresh-Strong-88").decode()
    created = scripts.post("/api/v1/missions", headers={"Authorization": f"Basic {unblocked}"},
                           json={"title": "A", "original_input": "A", "steps": ["Build"]})
    assert created.status_code == 201


def test_must_change_gate_covers_bearer_keys(client):
    """A client API key cannot route around its account's rotation gate.

    The flag here is raised without touching the password or the key, so the
    403 below is provably the gate and not a reset's key revocation.
    """
    client.post("/api/v1/auth/register", json={"username": "buyer14", "password": "Strong-Pass-123"})
    minted = client.post("/api/v1/my/keys", headers={"X-CSRF-Token": _csrf(client)})
    assert minted.status_code == 201, minted.text
    key = minted.json()["api_key"]
    headers = {"Authorization": f"Bearer {key}"}
    mission = {"title": "A", "original_input": "A", "steps": ["Build"]}
    assert client.post("/api/v1/missions", headers=headers, json=mission).status_code == 201

    # Raise the flag only, keeping the credential and the key exactly as they
    # were minted.
    users = client.app.state.store.platform_users()
    client.app.state.store.set_platform_user_password("buyer14", users["buyer14"],
                                                      must_change=True)

    assert client.get("/api/v1/missions", headers=headers).status_code == 200
    blocked = client.post("/api/v1/missions", headers=headers, json=mission)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "请先修改密码"


def test_foreign_authorization_headers_do_not_lock_the_egress_ip(tmp_path, monkeypatch):
    """Only a rejected Basic attempt counts; foreign schemes stay a bare 401.

    An office NAT shares one egress address, so noise from someone else's
    client (a stray ``Bearer sk-...``) must not lock the whole address out.
    """
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("FACAI_LOGIN_MAX_FAILURES", "2")
    app = create_app(tmp_path)
    client = TestClient(app)

    foreign = ("Bearer sk-someone-elses", "Basic !!!not-base64", "Bearer whatever")
    for _ in range(3):
        for header in foreign:
            assert client.get("/api/v1/missions",
                              headers={"Authorization": header}).status_code == 401
    # None of the foreign noise counted: the real credential still logs in.
    assert client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse"}).status_code == 200

    # Well-formed Basic credentials that fail verification DO count. The
    # cookie-free client keeps the successful login's session out of the way.
    scripts = TestClient(app)
    encoded = base64.b64encode(b"admin:wrong").decode()
    for _ in range(2):
        assert scripts.get("/api/v1/missions",
                           headers={"Authorization": f"Basic {encoded}"}).status_code == 401
    assert scripts.get("/api/v1/missions",
                       headers={"Authorization": f"Basic {encoded}"}).status_code == 429
    assert client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "correct-horse"}).status_code == 429


def test_session_reports_server_decided_is_admin(client):
    """Admin-ness is a server verdict on the resolved username, not a guess."""
    assert client.get("/api/v1/auth/session").json()["is_admin"] is False

    login(client)
    assert client.get("/api/v1/auth/session").json()["is_admin"] is True

    # A self-registered tenant sits on the same account list but is not the
    # operator, whatever its username looks like.
    client.post("/api/v1/auth/register", json={"username": "admin-fan", "password": "Strong-Pass-123"})
    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True and session["is_admin"] is False


def test_is_admin_follows_the_configured_operator_name(tmp_path, monkeypatch):
    """The verdict keys on FACAI_ADMIN_USER, not the literal name "admin"."""
    monkeypatch.setenv("FACAI_ADMIN_USER", "boss")
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/v1/auth/login",
                           json={"username": "boss", "password": "correct-horse"})
    assert response.status_code == 200
    session = client.get("/api/v1/auth/session").json()
    assert session["username"] == "boss" and session["is_admin"] is True
