"""Regression: registries (Agent, Model, Skill, Runtime) are isolated between tenants.
"""

import json
from pathlib import Path
from fastapi.testclient import TestClient
from facai.api import create_app

def two_user_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_USERS_JSON", '{"alice":"alice-pass","bob":"bob-pass"}')
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    api = create_app(tmp_path)
    return api, TestClient(api), TestClient(api)

def login(client, username, password):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]

def test_registries_isolation(tmp_path, monkeypatch):
    api, alice, bob = two_user_clients(tmp_path, monkeypatch)
    alice_csrf = login(alice, "alice", "alice-pass")
    bob_csrf = login(bob, "bob", "bob-pass")

    # Alice registers a private model (no api_key_env: that field is
    # operator-only since the 10-agent audit — a tenant naming one would make
    # complete() send the process env value to the model endpoint).
    alice.put("/api/v1/registries/models/alice-model", headers={"X-CSRF-Token": alice_csrf},
              json={"name": "Alice Model", "provider": "OpenAI", "endpoint": "https://api.openai.com",
                    "model": "gpt-4"}).json()
    r = alice.put("/api/v1/registries/models/env-model", headers={"X-CSRF-Token": alice_csrf},
                  json={"name": "Env Model", "provider": "OpenAI", "endpoint": "https://api.openai.com",
                        "model": "gpt-4", "api_key_env": "ALICE_KEY"})
    assert r.status_code == 403

    # Bob registers a different model with the same ID but different config
    bob.put("/api/v1/registries/models/alice-model", headers={"X-CSRF-Token": bob_csrf},
              json={"name": "Bob Version", "provider": "Anthropic", "endpoint": "https://api.anthropic.com",
                    "model": "claude-3"}).json()

    # Alice sees her version
    alice_models = alice.get("/api/v1/registries/models").json()
    assert alice_models["alice-model"]["name"] == "Alice Model"
    assert alice_models["alice-model"]["provider"] == "OpenAI"

    # Bob sees his version
    bob_models = bob.get("/api/v1/registries/models").json()
    assert bob_models["alice-model"]["name"] == "Bob Version"
    assert bob_models["alice-model"]["provider"] == "Anthropic"

    # Alice registers an approved, enabled skill (registration is an
    # immediate capability admission; enabled:true is accepted now).
    alice_skill = alice.put("/api/v1/registries/skills/alice-skill", headers={"X-CSRF-Token": alice_csrf},
                            json={"name": "alice-skill", "description": "Alice Skill", "source_url": "https://github.com/alice/skill",
                                  "license": "MIT", "review_status": "approved", "enabled": True})
    assert alice_skill.status_code == 200
    
    # Bob should NOT see Alice's skill
    bob_skills = bob.get("/api/v1/registries/skills").json()
    assert "alice-skill" not in bob_skills

    # Alice should see her skill
    alice_skills = alice.get("/api/v1/registries/skills").json()
    assert "alice-skill" in alice_skills

def test_mission_binding_honors_tenant_registries(tmp_path, monkeypatch):
    api, alice, bob = two_user_clients(tmp_path, monkeypatch)
    alice_csrf = login(alice, "alice", "alice-pass")
    bob_csrf = login(bob, "bob", "bob-pass")

    # Alice registers a model (tenants carry no api_key_env — operator-only)
    alice.put("/api/v1/registries/models/alice-only", headers={"X-CSRF-Token": alice_csrf},
              json={"name": "Alice Only", "provider": "OpenAI", "endpoint": "https://api.openai.com",
                    "model": "gpt-4"})
    
    # Alice creates a mission
    a = alice.post("/api/v1/missions", headers={"X-CSRF-Token": alice_csrf}, json={
        "title": "Alice Mission", "original_input": "...", "steps": ["Step"]}).json()
    
    # Alice can bind her model
    res = alice.put(f"/api/v1/missions/{a['id']}/binding", headers={"X-CSRF-Token": alice_csrf},
                    json={"model_id": "alice-only"})
    assert res.status_code == 200

    # Bob tries to bind Alice's model to his mission (even if he knows its ID)
    b = bob.post("/api/v1/missions", headers={"X-CSRF-Token": bob_csrf}, json={
        "title": "Bob Mission", "original_input": "...", "steps": ["Step"]}).json()
    
    res = bob.put(f"/api/v1/missions/{b['id']}/binding", headers={"X-CSRF-Token": bob_csrf},
                  json={"model_id": "alice-only"})
    assert res.status_code == 422
    assert "Unknown model" in res.json()["detail"]
