"""Execution-side materialization of client-supplied models.

A user-owned model runs inside OpenCode through a project-level config that
references the key by environment name; the key itself rides only in the child
process environment, and the config file is excluded from git so no checkpoint
can ever capture it. Operator-owned models materialize nothing.
"""

import json

from facai.execution import user_model_runtime
from facai.secretbox import encrypt
from facai.store import Store


def _store_with_user_model(tmp_path, key="sk-their-secret"):
    store = Store(tmp_path)
    model_id = store.register_user_model(
        "client1", "我的模型", "https://example.com", "their-model",
        encrypt(tmp_path, key))
    row = store.user_model("client1", model_id)
    model = {"name": "我的模型", "endpoint": row["endpoint"], "model": row["model"],
             "provider": "facai-user", "user_model_id": model_id,
             "opencode_model": "facai-user/their-model"}
    return store, model


def _workspace(tmp_path):
    ws = tmp_path / "workspaces" / "m1"
    ws.mkdir(parents=True)
    return ws


def test_user_model_materializes_env_and_project_config(tmp_path):
    store, model = _store_with_user_model(tmp_path)
    ws = _workspace(tmp_path)
    mission = {"id": "m1", "owner_id": "client1"}

    env, config_path = user_model_runtime(store, mission, model, ws, root=tmp_path)
    assert env == {"FACAI_USER_MODEL_KEY": "sk-their-secret"}
    config = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
    provider = config["provider"]["facai-user"]
    assert provider["options"]["baseURL"] == "https://example.com"
    assert provider["options"]["apiKey"] == "{env:FACAI_USER_MODEL_KEY}"  # no inline secret
    assert "their-model" in provider["models"]
    raw = (ws / "opencode.json").read_text(encoding="utf-8")
    assert "sk-their-secret" not in raw  # the key never lands in the file
    assert "opencode.json" in (ws / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def test_operator_model_materializes_nothing(tmp_path):
    store, _model = _store_with_user_model(tmp_path)
    ws = _workspace(tmp_path)
    mission = {"id": "m1", "owner_id": "admin"}
    operator_model = {"name": "deepseek-flash", "provider": "DeepSeek",
                      "endpoint": "https://api.deepseek.com", "model": "deepseek-chat"}
    assert user_model_runtime(store, mission, operator_model, ws, root=tmp_path) == (None, None)
    assert not (ws / "opencode.json").exists()


def test_missing_credential_degrades_to_nothing(tmp_path):
    store, model = _store_with_user_model(tmp_path)
    ws = _workspace(tmp_path)
    mission = {"id": "m1", "owner_id": "someone-else"}  # not the model's owner
    assert user_model_runtime(store, mission, model, ws, root=tmp_path) == (None, None)
    assert not (ws / "opencode.json").exists()


def test_config_is_rewritten_per_run(tmp_path):
    store, model = _store_with_user_model(tmp_path)
    ws = _workspace(tmp_path)
    mission = {"id": "m1", "owner_id": "client1"}
    _, config1 = user_model_runtime(store, mission, model, ws, root=tmp_path)
    (ws / "opencode.json").write_text("stale", encoding="utf-8")
    _, config2 = user_model_runtime(store, mission, model, ws, root=tmp_path)
    assert config1 == config2
    assert json.loads((ws / "opencode.json").read_text(encoding="utf-8"))["provider"]
