"""Startup configuration must survive blank or nonsensical EnvironmentFile values.

Two field probes motivated these checks:

* ``FACAI_MAX_WORKERS=auto`` used to raise ``ValueError`` while ``facai.api`` was
  still importing, so the service never came up at all.
* A blank ``FACAI_DATA_DIR`` (the shipped template blanks settings it does not
  set) used to resolve to the process working directory, which the hardened unit
  cannot write, so SQLite failed to open on startup.

Both parser helpers are covered directly, then exercised through ``create_app``
so a regression fails the real boot path rather than a private helper.
"""

import pytest
from fastapi.testclient import TestClient

from facai.api import _env_data_dir, create_app
from facai.opencode import OpenCodeAdapter
from facai.registries import Registries
from facai.scheduler import (CONCURRENCY_LIMIT, DEFAULT_CONCURRENCY, Scheduler,
                             _worker_limit)
from facai.store import Store


def build_scheduler(tmp_path, **kwargs):
    store = Store(tmp_path)
    return Scheduler(store, OpenCodeAdapter(), Registries(store), tmp_path,
                     worker_id='startup-test', **kwargs)


# --- FACAI_DATA_DIR ---------------------------------------------------------

@pytest.mark.parametrize('value', [None, '', '   ', '\t\n'])
def test_blank_data_dir_falls_back_to_default(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv('FACAI_DATA_DIR', raising=False)
    else:
        monkeypatch.setenv('FACAI_DATA_DIR', value)
    assert _env_data_dir() == (tmp_path / 'data').resolve()


def test_data_dir_env_value_is_honored(monkeypatch, tmp_path):
    target = tmp_path / 'runtime'
    monkeypatch.setenv('FACAI_DATA_DIR', str(target))
    assert _env_data_dir() == target.resolve()


def test_data_dir_override_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv('FACAI_DATA_DIR', str(tmp_path / 'from-env'))
    explicit = tmp_path / 'explicit'
    assert _env_data_dir(explicit) == explicit.resolve()


def test_blank_override_uses_default_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert _env_data_dir('') == (tmp_path / 'data').resolve()


def test_data_dir_reads_custom_env_name(monkeypatch, tmp_path):
    monkeypatch.setenv('FACAI_ALT_DATA_DIR', str(tmp_path / 'alt'))
    assert _env_data_dir(name='FACAI_ALT_DATA_DIR') == (tmp_path / 'alt').resolve()


def test_create_app_with_blank_data_dir_keeps_db_in_default_dir(monkeypatch, tmp_path):
    """A blank value must not drop SQLite into the process working directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FACAI_DATA_DIR', '')
    app = create_app()
    assert app.state.store.root == (tmp_path / 'data').resolve()
    assert app.state.store.database == (tmp_path / 'data' / 'facai.sqlite3')
    assert (tmp_path / 'data' / 'facai.sqlite3').is_file()
    assert not (tmp_path / 'facai.sqlite3').exists()


def test_blank_data_dir_startup_serves_health_login_and_session(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FACAI_DATA_DIR', '')
    monkeypatch.delenv('FACAI_ADMIN_PASSWORD', raising=False)
    client = TestClient(create_app())

    health = client.get('/api/v1/health')
    assert health.status_code == 200 and health.json()['status'] == 'ready'

    login = client.get('/login')
    assert login.status_code == 200
    assert 'text/html' in login.headers['content-type']

    session = client.get('/api/v1/auth/session').json()
    assert session['auth_required'] is False and session['authenticated'] is True


# --- FACAI_MAX_WORKERS ------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    (None, DEFAULT_CONCURRENCY),
    ('', DEFAULT_CONCURRENCY),
    ('   ', DEFAULT_CONCURRENCY),
    ('auto', DEFAULT_CONCURRENCY),
    ('two', DEFAULT_CONCURRENCY),
    ('2.5', DEFAULT_CONCURRENCY),
    ('1', 1),
    ('2', 2),
    ('8', CONCURRENCY_LIMIT),
    ('0', 1),
    ('-5', 1),
    ('9', CONCURRENCY_LIMIT),
    ('1000', CONCURRENCY_LIMIT),
])
def test_worker_limit_parses_or_falls_back(value, expected):
    assert _worker_limit(value) == expected


@pytest.mark.parametrize('value,expected', [
    ('', DEFAULT_CONCURRENCY),
    ('auto', DEFAULT_CONCURRENCY),
    ('0', 1),
    ('12', CONCURRENCY_LIMIT),
])
def test_scheduler_tolerates_unusable_max_workers_env(monkeypatch, tmp_path, value, expected):
    monkeypatch.setenv('FACAI_MAX_WORKERS', value)
    assert build_scheduler(tmp_path).concurrency == expected


def test_explicit_concurrency_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv('FACAI_MAX_WORKERS', 'auto')
    assert build_scheduler(tmp_path, concurrency=4).concurrency == 4


def test_create_app_with_auto_max_workers_boots(monkeypatch, tmp_path):
    monkeypatch.setenv('FACAI_MAX_WORKERS', 'auto')
    monkeypatch.delenv('FACAI_ADMIN_PASSWORD', raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get('/api/v1/health').status_code == 200
    assert client.get('/api/v1/scheduler').json()['concurrency'] == DEFAULT_CONCURRENCY
