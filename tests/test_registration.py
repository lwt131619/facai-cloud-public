"""Self-service account registration and tenant isolation for registered users."""

import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.auth import hash_password, verify_password

PASSWORD = "reg-pass-123"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    return TestClient(create_app(tmp_path))


def test_password_hash_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2_sha256$200000$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password("x", "garbage")


def test_register_creates_isolated_account(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/v1/auth/register",
                           json={"username": "alice", "password": "alice-pass-1"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["registered"] is True and body["authenticated"] is True

    # The fresh session sees only its own missions and is CSRF-protected.
    headers = {"X-CSRF-Token": body["csrf_token"]}
    mission = client.post("/api/v1/missions", headers=headers,
                          json={"title": "艾丽斯任务", "original_input": "原话",
                                "steps": ["开发"]}).json()
    admin = _client(tmp_path, monkeypatch)
    admin_session = admin.post("/api/v1/auth/login",
                               json={"username": "admin", "password": PASSWORD}).json()
    assert admin.get(f"/api/v1/missions/{mission['id']}").status_code == 404
    assert [m["title"] for m in admin.get("/api/v1/missions").json()] == []
    admin.post("/api/v1/auth/logout", headers={"X-CSRF-Token": admin_session["csrf_token"]})

    # The registered user can log in again from a fresh client.
    fresh = _client(tmp_path, monkeypatch)
    login = fresh.post("/api/v1/auth/login", json={"username": "alice", "password": "alice-pass-1"})
    assert login.status_code == 200
    fresh.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    assert fresh.get(f"/api/v1/missions/{mission['id']}").json()["title"] == "艾丽斯任务"


@pytest.mark.parametrize(("payload", "status"), [
    ({"username": "ab", "password": "long-enough-pass"}, 422),
    ({"username": "bad name!", "password": "long-enough-pass"}, 422),
    ({"username": "system", "password": "long-enough-pass"}, 422),
    ({"username": "alice", "password": "short"}, 422),
])
def test_register_rejects_invalid_input_with_fixed_reasons(tmp_path, monkeypatch, payload, status):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/v1/auth/register", json=payload).status_code == status


def test_register_rejects_duplicates_and_env_shadowing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/v1/auth/register",
                       json={"username": "bob", "password": "bob-pass-123"}).status_code == 201
    assert client.post("/api/v1/auth/register",
                       json={"username": "bob", "password": "bob-pass-456"}).status_code == 409
    # The environment-configured operator account can never be shadowed
    # (rejected as a reserved name before any duplicate check matters).
    response = client.post("/api/v1/auth/register",
                           json={"username": "admin", "password": "whatever-123"})
    assert response.status_code == 422
    assert "保留" in response.json()["detail"]


def test_user_cap_counts_registered_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_MAX_USERS", "3")
    client = _client(tmp_path, monkeypatch)
    # admin (env) + 2 registered = cap reached; the third signup is rejected.
    assert client.post("/api/v1/auth/register", json={"username": "user1", "password": "pass-12345"}).status_code == 201
    assert client.post("/api/v1/auth/register", json={"username": "user2", "password": "pass-12345"}).status_code == 201
    full = client.post("/api/v1/auth/register", json={"username": "user3", "password": "pass-12345"})
    assert full.status_code == 403
    assert "名额" in full.json()["detail"]
