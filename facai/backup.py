"""Verified SQLite backup and restore for the Facai data directory.

This backs up the mission database (missions, steps, events, sessions,
artifacts, checkpoints, costs, jobs and registries). Registry values reference
credentials by environment-variable name only, so no raw key is stored or
copied by a backup.

Workspace files and artifacts live on disk outside the database and are not
included here; copy them separately if a full workspace restore is required.
"""

import argparse
import gc
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_TABLES = {"missions", "steps", "events", "registries", "checkpoints", "costs", "jobs"}
COUNTABLE_TABLES = sorted(REQUIRED_TABLES | {"sessions", "artifacts", "auth_sessions"})


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_backup(data_dir, dest=None):
    root = Path(data_dir).resolve()
    source_file = root / "facai.sqlite3"
    if not source_file.exists():
        raise FileNotFoundError(f"No database at {source_file}")
    if dest is None:
        dest = root / "backups" / f"facai-{_timestamp()}.sqlite3"
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_file)
    target = sqlite3.connect(dest)
    try:
        with target:
            source.backup(target)  # consistent online snapshot
    finally:
        source.close()
        target.close()
    return dest


def _snapshot_database(source_path, dest_path):
    """Copy a live SQLite database consistently, including pending journal data."""
    source = sqlite3.connect(source_path, timeout=10)
    target = sqlite3.connect(dest_path, timeout=10)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def verify_backup(backup_path):
    backup_path = Path(backup_path).resolve()
    if not backup_path.exists():
        return {"ok": False, "reason": "missing", "path": str(backup_path)}
    connection = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(REQUIRED_TABLES - tables)
        missions = connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] if "missions" in tables else 0
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "reason": type(exc).__name__, "path": str(backup_path)}
    finally:
        connection.close()
    return {"ok": not missing and integrity == "ok", "integrity": integrity,
            "missing_tables": missing, "missions": missions, "path": str(backup_path)}


def _unique_path(directory, name):
    """Return a non-existing path in directory, suffixing a counter if needed."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, _, suffix = name.rpartition(".")
    for index in range(1, 1000):
        candidate = directory / (f"{stem}-{index}.{suffix}" if stem else f"{name}-{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"No free restore path for {name} in {directory}")


def _discard_staged(staged):
    """Remove a staged restore copy and its WAL sidecars."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(staged) + suffix).unlink(missing_ok=True)


def restore_backup(data_dir, backup_path):
    root = Path(data_dir).resolve()
    live = root / "facai.sqlite3"
    source = Path(backup_path).resolve()

    if source == live:
        raise ValueError(f"Refusing to restore the live database onto itself: {live}")

    report = verify_backup(source)
    if not report["ok"]:
        raise ValueError(f"Refusing to restore an unverified backup: {report}")

    root.mkdir(parents=True, exist_ok=True)

    # Stage a verified copy inside the destination directory. Keeping the staged
    # file on the same filesystem lets the final swap be an atomic os.replace.
    staged = _unique_path(root, ".facai-restore.sqlite3")
    try:
        shutil.copy2(source, staged)
        staged_report = verify_backup(staged)
        if not staged_report["ok"]:
            raise ValueError(f"Staged backup failed verification: {staged_report}")
    except BaseException:
        _discard_staged(staged)
        raise

    if live.exists():
        # Keep the previous database so a restore can itself be rolled back.
        rollback = _unique_path(root, f"facai.pre-restore-{_timestamp()}.sqlite3")
        try:
            _snapshot_database(live, rollback)
            if not verify_backup(rollback)["ok"]:
                raise ValueError("Pre-restore database snapshot failed verification")
        except Exception:
            _discard_staged(staged)
            rollback.unlink(missing_ok=True)
            raise

    # Only now that a verified copy is staged do we replace the live database.
    # os.replace is atomic; a failure leaves the previous live database usable.
    for attempt in range(2):
        try:
            os.replace(staged, live)
            # The restored file is self-contained (verified before staging).
            # Stale sidecars are now hazards: the PREVIOUS live database's WAL
            # would be replayed over the restored file by the next connection,
            # and the staged copy's own sidecars are orphans after the rename.
            for suffix in ("-wal", "-shm"):
                Path(str(live) + suffix).unlink(missing_ok=True)
                Path(str(staged) + suffix).unlink(missing_ok=True)
            return live
        except PermissionError:
            # Windows refuses to replace a file that another handle still has
            # open. Dead-but-uncollected sqlite connections can linger, so
            # collect them and retry once; if it still fails, the live database
            # is untouched and remains usable.
            if attempt == 0:
                gc.collect()
                continue
            _discard_staged(staged)
            raise
        except BaseException:
            _discard_staged(staged)
            raise
    return live


def _counts(database):
    connection = sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True)
    try:
        present = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in COUNTABLE_TABLES if table in present}
    finally:
        connection.close()


def drill_backup(data_dir, dest=None):
    """Rehearse a full restore into a throwaway directory.

    Takes a verified backup, restores it somewhere temporary and compares row
    counts against the live database. The live database is never modified and no
    credential value is read or printed: only table names and counts are reported.
    """
    live = Path(data_dir).resolve() / "facai.sqlite3"
    if not live.exists():
        raise FileNotFoundError(f"No database at {live}")
    backup = Path(dest).resolve() if dest is not None else create_backup(data_dir)
    report = verify_backup(backup)
    if not report["ok"]:
        return {"ok": False, "stage": "verify", "backup": str(backup), **report}
    live_counts = _counts(live)
    rehearsal = Path(tempfile.mkdtemp(prefix="facai-drill-"))
    try:
        restored = restore_backup(rehearsal, backup)
        restored_counts = _counts(restored)
        mismatches = {table: (live_counts.get(table, 0), restored_counts.get(table, 0))
                      for table in COUNTABLE_TABLES
                      if live_counts.get(table, 0) != restored_counts.get(table, 0)}
        return {"ok": not mismatches, "stage": "restore", "backup": str(backup),
                "restored": str(restored), "live_counts": live_counts,
                "restored_counts": restored_counts, "mismatches": mismatches}
    finally:
        shutil.rmtree(rehearsal, ignore_errors=True)


def _manifest(path):
    report = verify_backup(path)
    return json.dumps({"backup": str(path), **report}, ensure_ascii=False, indent=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Facai database backup and restore")
    parser.add_argument("command", choices=["create", "verify", "restore", "drill"])
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--dest", default=None, help="backup path for create, or source for restore/verify")
    args = parser.parse_args(argv)

    if args.command == "create":
        path = create_backup(args.data_dir, args.dest)
        print(_manifest(path))
    elif args.command == "verify":
        if not args.dest:
            parser.error("--dest is required for verify")
        print(_manifest(args.dest))
    elif args.command == "restore":
        if not args.dest:
            parser.error("--dest is required for restore")
        restored = restore_backup(args.data_dir, args.dest)
        print(f"Restored {args.dest} -> {restored}")
    else:
        print(json.dumps(drill_backup(args.data_dir, args.dest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
