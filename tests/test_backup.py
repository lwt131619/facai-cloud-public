"""Backup, verify and restore must round-trip real data and stay secret-free."""

import sqlite3
from pathlib import Path

import pytest

import facai.backup as backup_module
from facai.backup import create_backup, drill_backup, restore_backup, verify_backup
from facai.store import Store


def seed(tmp_path):
    store = Store(tmp_path)
    mission = store.create('关键任务', '原始要求', ['开发', '测试'])
    store.bind(mission['id'], 'opencode', 'deepseek-flash')
    store.registry_register('models', 'persisted', {'provider': 'local', 'api_key_env': 'LOCAL_KEY'})
    store.append(mission['id'], 'NOTE', {'text': 'hello'})
    return store, mission['id']


def test_backup_verify_restore_round_trip(tmp_path):
    store, mission_id = seed(tmp_path)
    backup = create_backup(tmp_path)

    report = verify_backup(backup)
    assert report['ok'] is True and report['integrity'] == 'ok'
    assert report['missions'] == 1

    # Mutate the live database, then restore the snapshot.
    store.create('后续任务', 'x', ['开发'])
    store.registry_register('models', 'later', {'provider': 'local', 'api_key_env': 'K'})
    assert len(store.list()) == 2

    restore_backup(tmp_path, backup)
    reopened = Store(tmp_path)
    titles = [m['title'] for m in reopened.list()]
    assert titles == ['关键任务']
    assert reopened.get(mission_id)['model_id'] == 'deepseek-flash'
    assert any(event['type'] == 'NOTE' for event in reopened.events(mission_id))
    assert 'later' not in reopened.registry_list('models')
    assert reopened.registry_list('models')['persisted']['api_key_env'] == 'LOCAL_KEY'

    # A previous database is preserved so the restore itself can be rolled back.
    assert list(tmp_path.glob('facai.pre-restore-*.sqlite3'))


def test_restore_refuses_unverified_file(tmp_path):
    seed(tmp_path)
    bogus = tmp_path / 'bogus.sqlite3'
    bogus.write_text('not a database', encoding='utf-8')
    with pytest.raises(ValueError):
        restore_backup(tmp_path, bogus)


def test_restore_rejects_source_resolving_to_live_database(tmp_path):
    seed(tmp_path)
    live = tmp_path / 'facai.sqlite3'
    assert live.exists()
    with pytest.raises(ValueError):
        restore_backup(tmp_path, live)
    # The rejected restore must not have disturbed the live database.
    assert [m['title'] for m in Store(tmp_path).list()] == ['关键任务']
    assert not list(tmp_path.glob('.facai-restore*'))


def test_restore_stage_failure_leaves_live_usable(tmp_path, monkeypatch):
    store, _ = seed(tmp_path)
    backup = create_backup(tmp_path)
    store.create('后续任务', 'x', ['开发'])
    assert len(store.list()) == 2

    real_copy = backup_module.shutil.copy2

    def failing_copy(src, dst, *args, **kwargs):
        if Path(dst).name.startswith('.facai-restore'):
            raise OSError('staging failed')
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup_module.shutil, 'copy2', failing_copy)
    with pytest.raises(OSError):
        restore_backup(tmp_path, backup)

    # Live data is still the post-backup version and no staging files remain.
    assert len(Store(tmp_path).list()) == 2
    assert not list(tmp_path.glob('.facai-restore*'))
    assert not list(tmp_path.glob('facai.pre-restore-*.sqlite3'))


def test_restore_success_replaces_live_and_keeps_rollback(tmp_path):
    store, mission_id = seed(tmp_path)
    backup = create_backup(tmp_path)
    store.create('快照之后', 'x', ['开发'])
    assert len(store.list()) == 2

    restore_backup(tmp_path, backup)

    reopened = Store(tmp_path)
    assert [m['title'] for m in reopened.list()] == ['关键任务']
    assert reopened.get(mission_id)['model_id'] == 'deepseek-flash'
    assert not list(tmp_path.glob('.facai-restore*'))
    rollback_files = list(tmp_path.glob('facai.pre-restore-*.sqlite3'))
    assert len(rollback_files) == 1
    assert verify_backup(rollback_files[0])['ok'] is True


def test_restore_replace_failure_keeps_live_database_usable(tmp_path, monkeypatch):
    store, _ = seed(tmp_path)
    backup = create_backup(tmp_path)
    store.create('后续任务', 'x', ['开发'])

    def fail_replace(source, destination):
        raise PermissionError('live database is busy')

    monkeypatch.setattr(backup_module.os, 'replace', fail_replace)
    with pytest.raises(PermissionError, match='busy'):
        restore_backup(tmp_path, backup)

    assert len(Store(tmp_path).list()) == 2
    assert not list(tmp_path.glob('.facai-restore*'))
    rollback_files = list(tmp_path.glob('facai.pre-restore-*.sqlite3'))
    assert len(rollback_files) == 1 and verify_backup(rollback_files[0])['ok'] is True


def test_restore_drill_rehearses_without_touching_live_data(tmp_path):
    store, mission_id = seed(tmp_path)
    report = drill_backup(tmp_path)
    assert report['ok'] is True
    assert report['live_counts']['missions'] == 1
    assert report['restored_counts']['missions'] == 1
    assert report['mismatches'] == {}
    # The live database is untouched and the rehearsal directory is cleaned up.
    reopened = Store(tmp_path)
    assert [m['title'] for m in reopened.list()] == ['关键任务']
    assert not list(tmp_path.glob('facai.pre-restore-*.sqlite3'))


def test_restore_drill_reports_count_drift(tmp_path):
    store, _ = seed(tmp_path)
    backup = create_backup(tmp_path)
    # Add data after the snapshot; the drill restores the snapshot into a fresh
    # directory, so counts must match the backup, not the mutated live database.
    store.create('快照之后', 'x', ['开发'])
    report = drill_backup(tmp_path, dest=backup)
    assert report['ok'] is False
    assert report['restored_counts']['missions'] == 1


def test_backup_contains_no_raw_api_keys(tmp_path):
    store = Store(tmp_path)
    store.registry_register('models', 'persisted', {
        'provider': 'local', 'endpoint': 'http://127.0.0.1:11434/v1',
        'model': 'example', 'api_key_env': 'LOCAL_KEY'})
    backup = create_backup(tmp_path)
    with sqlite3.connect(backup) as db:
        values = ' '.join(str(row[0]) for row in db.execute("SELECT value FROM registries"))
    assert 'api_key_env' in values and 'sk-' not in values
