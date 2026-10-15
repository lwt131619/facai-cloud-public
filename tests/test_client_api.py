"""Client API keys: issuance, scoped calls, revocation and cross-owner refusal.

A paid client's automation authenticates with
``Authorization: Bearer facai_<id>_<secret>`` instead of the whole-account
password. The plaintext is returned exactly once, only the SHA-256 digest of
the secret half is stored, a key is confined to the client surface (missions /
jobs / artifacts / its own billing snapshot), and revoking it takes effect on
the very next request.
"""

import base64
import json
import sqlite3

from fastapi.testclient import TestClient

from facai.api import MAX_API_KEYS_PER_OWNER, create_app
from facai.auth import API_KEY_PREFIX, hash_api_key

CLIENT_PASSWORDS = {"client1": "client-pass", "client2": "client-pass"}
BASIC_ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:ck-pass").decode()}
MISSION = {"title": "API 任务", "original_input": "由 API key 创建",
           "steps": ["规划", "开发"]}


def _build(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "ck-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    app = create_app(tmp_path)
    with TestClient(app) as setup:
        for username in CLIENT_PASSWORDS:
            response = setup.post("/api/v1/auth/register",
                                  json={"username": username, "password": CLIENT_PASSWORDS[username]})
            assert response.status_code in (201, 409), response.text
    return app


def _console(app, username):
    """A TestClient holding that account's session cookie and CSRF token."""
    client = TestClient(app)
    response = client.post("/api/v1/auth/login",
                           json={"username": username, "password": CLIENT_PASSWORDS[username]})
    assert response.status_code == 200, response.text
    client.csrf = response.json()["csrf_token"]
    return client


def _mint(console):
    response = console.post("/api/v1/my/keys", headers={"X-CSRF-Token": console.csrf})
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def _secret_of(api_key):
    return api_key[len(API_KEY_PREFIX):].split("_", 1)[1]


# --- issuance, use, revocation --------------------------------------------------


def test_key_calls_missions_then_revocation_401s(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    created = _mint(console)
    api_key, key_id = created["api_key"], created["id"]

    assert api_key.startswith(API_KEY_PREFIX)
    assert created["prefix"] in api_key and api_key not in created["prefix"]

    # Read and write both work through the key, and the mission is owned by the
    # key's account -- not by the operator default.
    assert console.get("/api/v1/missions", headers=_bearer(api_key)).status_code == 200
    mission = console.post("/api/v1/missions", json=MISSION, headers=_bearer(api_key))
    assert mission.status_code == 201, mission.text
    body = mission.json()
    assert body["owner_id"] == "client1"
    listed = console.get("/api/v1/missions", headers=_bearer(api_key)).json()
    assert [m["id"] for m in listed] == [body["id"]]
    assert console.get(f"/api/v1/missions/{body['id']}", headers=_bearer(api_key)).status_code == 200
    assert console.get("/api/v1/billing/me", headers=_bearer(api_key)).status_code == 200
    assert console.get("/api/v1/artifacts", headers=_bearer(api_key)).status_code == 200

    revoked = console.delete(f"/api/v1/my/keys/{key_id}", headers={"X-CSRF-Token": console.csrf})
    assert revoked.status_code == 200 and revoked.json() == {"revoked": key_id}
    # The very next call with the same key is refused.
    assert console.get("/api/v1/missions", headers=_bearer(api_key)).status_code == 401
    assert console.post("/api/v1/missions", json=MISSION,
                        headers=_bearer(api_key)).status_code == 401
    # Revoking twice is not a second, silently different outcome.
    assert console.delete(f"/api/v1/my/keys/{key_id}",
                          headers={"X-CSRF-Token": console.csrf}).status_code == 404
    row = [k for k in console.get("/api/v1/my/keys").json() if k["id"] == key_id][0]
    assert row["revoked_at"]


def test_plaintext_is_returned_once_and_only_the_digest_is_stored(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    created = _mint(console)
    api_key, secret = created["api_key"], _secret_of(created["api_key"])

    listing = console.get("/api/v1/my/keys").json()
    assert [k["prefix"] for k in listing] == [created["prefix"]]
    # No later response carries the key material, and the stored row holds the
    # digest of the secret half rather than the secret itself.
    assert secret not in json.dumps(listing)
    assert "key_hash" not in json.dumps(listing)
    stored = sqlite3.connect(tmp_path / "facai.sqlite3").execute(
        "SELECT key_hash, prefix, revoked_at FROM api_keys WHERE id=?", (created["id"],)).fetchone()
    assert stored[0] == hash_api_key(secret)
    assert stored[1] == created["prefix"] and stored[2] is None
    # The database file itself never carries the plaintext key.
    assert api_key.encode() not in (tmp_path / "facai.sqlite3").read_bytes()

    # last_used_at is stamped by a successful call and stays pinning that moment.
    assert listing[0]["last_used_at"] is None
    assert console.get("/api/v1/missions", headers=_bearer(api_key)).status_code == 200
    used = console.get("/api/v1/my/keys").json()[0]["last_used_at"]
    assert used


def test_malformed_unknown_and_foreign_bearer_tokens_are_401(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    # No session cookie: the verdict below is about the presented token alone.
    stranger = TestClient(app)
    for token in (f"{API_KEY_PREFIX}only-two-parts",
                  f"{API_KEY_PREFIX}deadbeef_notarealsecret",
                  f"{API_KEY_PREFIX}_missingid",
                  f"{API_KEY_PREFIX}missingsecret_"):
        assert stranger.get("/api/v1/missions", headers=_bearer(token)).status_code == 401, token
    # A bearer token without the client-key marker is not a key at all: it keeps
    # falling through to the session/Basic handling and is simply unauthenticated.
    assert stranger.get("/api/v1/missions",
                        headers={"Authorization": "Bearer sk-someone-elses"}).status_code == 401


def test_scope_is_the_client_surface_and_everything_else_is_403(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    api_key = _mint(console)["api_key"]
    headers = _bearer(api_key)

    # In scope: jobs answers on its own terms (404 for an unknown id), never 403.
    assert console.get("/api/v1/jobs/nope", headers=headers).status_code == 404
    for path in ("/api/v1/registries/models", "/api/v1/ops/brief", "/api/v1/my/models",
                 "/api/v1/my/keys", "/api/v1/channels", "/api/v1/billing/recharges",
                 "/api/v1/daily-progress", "/api/v1/scheduler", "/api/v1/billing/me/extra"):
        response = console.get(path, headers=headers)
        assert response.status_code == 403, f"{path} -> {response.status_code}"
    # A key may not mint or revoke keys: management stays on a console session.
    assert console.post("/api/v1/my/keys", headers=headers).status_code == 403


def test_cross_owner_key_sees_nothing_and_cannot_revoke(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console1, console2 = _console(app, "client1"), _console(app, "client2")
    key1 = _mint(console1)
    key2 = _mint(console2)["api_key"]

    mission = console1.post("/api/v1/missions", json=MISSION, headers=_bearer(key1["api_key"]))
    assert mission.status_code == 201
    mission_id = mission.json()["id"]

    # Another owner's key lists nothing of client1's and cannot read it: the
    # owner-scoped lookup reports the same 404 as a nonexistent mission.
    assert console2.get("/api/v1/missions", headers=_bearer(key2)).json() == []
    assert console2.get(f"/api/v1/missions/{mission_id}", headers=_bearer(key2)).status_code == 404
    assert console2.get(f"/api/v1/missions/{mission_id}/events",
                        headers=_bearer(key2)).status_code == 404

    # And key management is owner-scoped too: client2 cannot revoke client1's
    # key, and client1's key keeps working afterwards.
    assert console2.delete(f"/api/v1/my/keys/{key1['id']}",
                           headers={"X-CSRF-Token": console2.csrf}).status_code == 404
    assert console2.get("/api/v1/my/keys").json()[0]["id"] != key1["id"]
    assert console1.get("/api/v1/missions", headers=_bearer(key1["api_key"])).status_code == 200


def test_key_does_not_bypass_the_budget_gate(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    api_key = _mint(console)["api_key"]
    first = console.post("/api/v1/missions", json=MISSION, headers=_bearer(api_key))
    assert first.status_code == 201
    app.state.store.add_cost(first.json()["id"], "deepseek", "deepseek-flash", 12.0)
    app.state.store.set_user_budget("client1", 10.0)

    blocked = console.post("/api/v1/missions", json=MISSION, headers=_bearer(api_key))
    assert blocked.status_code == 402
    assert console.get("/api/v1/billing/me", headers=_bearer(api_key)).json()["allowed"] is False


def test_active_keys_are_capped_per_owner(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    for _ in range(MAX_API_KEYS_PER_OWNER):
        _mint(console)
    assert console.post("/api/v1/my/keys",
                        headers={"X-CSRF-Token": console.csrf}).status_code == 409
    # A revoked key frees a slot: the cap counts live keys, not history.
    first = console.get("/api/v1/my/keys").json()[0]["id"]
    console.delete(f"/api/v1/my/keys/{first}", headers={"X-CSRF-Token": console.csrf})
    assert console.post("/api/v1/my/keys",
                        headers={"X-CSRF-Token": console.csrf}).status_code == 201


def test_operator_basic_path_is_untouched_and_listings_are_owner_scoped(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    # The pre-existing script credential (HTTP Basic) keeps working as before.
    admin = TestClient(app)
    assert admin.get("/api/v1/my/keys", headers=BASIC_ADMIN).status_code == 200
    assert admin.get("/api/v1/my/keys", headers=BASIC_ADMIN).json() == []
    key = _mint(_console(app, "client1"))["api_key"]
    # The operator's own listing never shows a tenant's key...
    assert admin.get("/api/v1/my/keys", headers=BASIC_ADMIN).json() == []
    # ...while a request carrying the tenant's key acts as that tenant, not as
    # whoever else is signed in on the connection.
    assert admin.get("/api/v1/missions", headers=_bearer(key)).status_code == 200


# --- credential rotation retires derived trust ----------------------------------


def test_password_rotation_and_reset_revoke_every_key(monkeypatch, tmp_path):
    """Client keys are derived trust in the account's password: a credential
    change -- self-service or operator-forced -- must retire all of them."""
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    key = _mint(console)["api_key"]
    assert console.get("/api/v1/missions", headers=_bearer(key)).status_code == 200

    # Self-service rotation retires the keys minted under the old credential.
    rotated = console.post("/api/v1/auth/change-password",
                           json={"current_password": CLIENT_PASSWORDS["client1"],
                                 "new_password": "Fresh-Strong-99"},
                           headers={"X-CSRF-Token": console.csrf})
    assert rotated.status_code == 200, rotated.text
    assert console.get("/api/v1/missions", headers=_bearer(key)).status_code == 401

    # An operator-forced reset does the same to keys minted since.
    admin = TestClient(app)
    assert admin.post("/api/v1/auth/login",
                      json={"username": "admin", "password": "ck-pass"}).status_code == 200
    reset = admin.post("/api/v1/auth/admin-reset-password",
                       json={"username": "client1", "new_password": "Another-Strong-77"},
                       headers={"X-CSRF-Token": admin.get("/api/v1/auth/session").json()["csrf_token"]})
    assert reset.status_code == 200, reset.text

    relogged = TestClient(app)
    assert relogged.post("/api/v1/auth/login",
                         json={"username": "client1",
                               "password": "Another-Strong-77"}).status_code == 200
    reborn = relogged.post("/api/v1/my/keys", headers={
        "X-CSRF-Token": relogged.get("/api/v1/auth/session").json()["csrf_token"]})
    assert reborn.status_code == 201, reborn.text
    assert relogged.get("/api/v1/missions",
                        headers=_bearer(reborn.json()["api_key"])).status_code == 200

    # A weak hand-out password flags the account and still kills its keys.
    second_reset = admin.post("/api/v1/auth/admin-reset-password",
                              json={"username": "client1", "new_password": "123456"},
                              headers={"X-CSRF-Token": admin.get("/api/v1/auth/session").json()["csrf_token"]})
    assert second_reset.status_code == 200
    assert second_reset.json()["must_change_password"] is True
    assert relogged.get("/api/v1/missions",
                        headers=_bearer(reborn.json()["api_key"])).status_code == 401


def test_last_used_is_stamped_only_when_scope_admits(monkeypatch, tmp_path):
    """last_used_at means "used", so a 403 out-of-scope probe leaves it alone."""
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    headers = _bearer(_mint(console)["api_key"])
    assert console.get("/api/v1/missions", headers=headers).status_code == 200
    used = console.get("/api/v1/my/keys").json()[0]["last_used_at"]
    assert used
    # Out-of-scope calls are refused without refreshing the stamp.
    assert console.get("/api/v1/channels", headers=headers).status_code == 403
    assert console.get("/api/v1/my/keys").json()[0]["last_used_at"] == used


def test_api_key_scope_ignores_trailing_slash(monkeypatch, tmp_path):
    """The trailing slash is cosmetic: it admits exactly like the bare path."""
    from facai.api import api_key_allowed
    assert api_key_allowed("/api/v1/missions/")
    assert api_key_allowed("/api/v1/missions/abc/")
    assert api_key_allowed("/api/v1/billing/me/")
    assert not api_key_allowed("/api/v1/missions-secret/")
    assert not api_key_allowed("/api/v1/my/keys/")
    assert not api_key_allowed("/")
    app = _build(monkeypatch, tmp_path)
    console = _console(app, "client1")
    headers = _bearer(_mint(console)["api_key"])
    assert console.get("/api/v1/missions/", headers=headers).status_code == 200
