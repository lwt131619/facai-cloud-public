"""Registry DEFAULTS: the local GLM takes the default seat where a local GLM
endpoint exists (GLM_LOCAL_ENDPOINT), deepseek-flash stays either way, and the
seed path never runs the registration guard.

REROOT_PLAYBOOK F4: ``api.default_binding`` falls back to ``next(iter(models))``
(api.py:1631-1633), so dict order is the default seat — but a host without a
local GLM (e.g. the production VPS) must keep deepseek as default, so the seat
is gated on GLM_LOCAL_ENDPOINT. The seed path is also asserted never to run
``secretbox.validate_public_endpoint``: that guard belongs to the two
registration APIs (api.py:1092, api.py:1337), not to DEFAULTS seeding.
"""

import importlib.util

import pytest

from facai import registries
from facai.registries import DEFAULTS, Registries

GLM_LOCAL_ENDPOINT = "http://127.0.0.1:9900/v1"


def _fresh_registries(monkeypatch, env_value=None):
    """Load registries.py as a private module copy under the controlled env.

    ``DEFAULTS`` is built once at import, so the GLM_LOCAL_ENDPOINT hook can
    only be observed on a fresh load; a private copy leaves the shared module
    untouched for the other tests.
    """
    if env_value is None:
        monkeypatch.delenv("GLM_LOCAL_ENDPOINT", raising=False)
    else:
        monkeypatch.setenv("GLM_LOCAL_ENDPOINT", env_value)
    spec = importlib.util.spec_from_file_location(
        "registries_env_probe", registries.__file__)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe


def test_without_local_endpoint_deepseek_keeps_the_default_seat(monkeypatch):
    probe = _fresh_registries(monkeypatch, env_value=None)
    models = probe.DEFAULTS["models"]
    assert "glm-local" not in models              # no local GLM here — no fake seat
    assert list(models)[0] == "deepseek-flash"    # production-safe default


def test_with_local_endpoint_glm_local_leads_and_deepseek_stays(monkeypatch):
    probe = _fresh_registries(monkeypatch, GLM_LOCAL_ENDPOINT)
    models = probe.DEFAULTS["models"]
    assert list(models)[0] == "glm-local"         # front of book = default seat
    assert "deepseek-flash" in models             # demoted, never removed


def test_glm_local_entry_shape(monkeypatch):
    probe = _fresh_registries(monkeypatch, GLM_LOCAL_ENDPOINT)
    entry = probe.DEFAULTS["models"]["glm-local"]
    assert entry["name"] == "GLM Local (Ollama)"
    assert entry["provider"] == "Ollama"
    assert entry["endpoint"] == GLM_LOCAL_ENDPOINT
    assert entry["api_key_env"] == "GLM_LOCAL_KEY"
    assert entry["opencode_model"] == "facai-glm/glm-4.6"


def test_glm_local_endpoint_follows_env(monkeypatch):
    probe = _fresh_registries(monkeypatch, "http://127.0.0.1:11434/v1")
    assert probe.DEFAULTS["models"]["glm-local"]["endpoint"] == "http://127.0.0.1:11434/v1"


def test_registry_listing_preserves_order_and_deepseek(monkeypatch):
    _fresh_registries(monkeypatch, GLM_LOCAL_ENDPOINT)
    # The shared module captured DEFAULTS at import (process env has no local
    # GLM), so point it at the freshly loaded table for this test.
    probe = _fresh_registries(monkeypatch, GLM_LOCAL_ENDPOINT)
    monkeypatch.setattr(registries, "DEFAULTS", probe.DEFAULTS, raising=True)
    listed = Registries().list("models")
    keys = list(listed)
    assert keys.index("glm-local") < keys.index("deepseek-flash")
    assert listed["glm-local"]["name"] == "GLM Local (Ollama)"
    assert listed["deepseek-flash"]["api_key_env"] == "DEEPSEEK_API_KEY"


def test_seed_path_never_runs_the_endpoint_guard(monkeypatch, tmp_path):
    from facai import secretbox
    from facai.store import Store

    def boom(*args, **kwargs):
        raise AssertionError("validate_public_endpoint must not run on the seed path")

    monkeypatch.setattr(secretbox, "validate_public_endpoint", boom)
    probe = _fresh_registries(monkeypatch, GLM_LOCAL_ENDPOINT)
    monkeypatch.setattr(registries, "DEFAULTS", probe.DEFAULTS, raising=True)

    # list() seeds the DEFAULTS rows into the store — the loopback pivot
    # endpoint must survive that without ever meeting the registration guard.
    listed = Registries(Store(tmp_path)).list("models")

    assert listed["glm-local"]["endpoint"] == GLM_LOCAL_ENDPOINT


@pytest.mark.parametrize("kind", ["agents", "models", "skills", "runtimes"])
def test_registry_kinds_unchanged(kind):
    assert kind in DEFAULTS
