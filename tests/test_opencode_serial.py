"""OpenCode execution serialization and per-run credential env passthrough.

OpenCode's shared state store makes concurrent `opencode run` processes kill
each other with SQLite "database is locked" (observed live on parallel mission
branches, 2026-10-03). Execution is serialized by default; the injected-runner
API and per-run env additions must both survive that change.
"""

import threading
import time

from facai.opencode import OpenCodeAdapter


class _ConcurrencyProbe:
    def __init__(self, hold=0.05):
        self.hold = hold
        self.current = 0
        self.max_seen = 0
        self.envs = []
        self._lock = threading.Lock()

    def __call__(self, command, cwd, timeout, on_output=None, env=None):
        with self._lock:
            self.current += 1
            self.max_seen = max(self.max_seen, self.current)
            self.envs.append(dict(env or {}))
        try:
            time.sleep(self.hold)
            class _R:  # minimal CompletedProcess stand-in
                returncode = 0
                stdout = ""
                stderr = ""
            return _R
        finally:
            with self._lock:
                self.current -= 1


def test_serialized_by_default_so_parallel_branches_cannot_lock():
    probe = _ConcurrencyProbe()
    adapter = OpenCodeAdapter(binary="opencode", runner=probe)
    assert adapter._serial is True
    threads = [threading.Thread(target=adapter.execute, kwargs={
        "mission": f"m{i}", "root": "/tmp", "prompt": "p", "env": {"USER_KEY": f"k{i}"}})
        for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert probe.max_seen == 1  # never two opencode processes at once
    # Lock acquisition order across threads is not deterministic; the payload is.
    assert sorted(e.get("USER_KEY") for e in probe.envs) == sorted(f"k{i}" for i in range(4))


def test_serial_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FACAI_OPENCODE_SERIAL", "0")
    probe = _ConcurrencyProbe(hold=0.15)
    adapter = OpenCodeAdapter(binary="opencode", runner=probe)
    assert adapter._serial is False
    threads = [threading.Thread(target=adapter.execute, kwargs={
        "mission": f"m{i}", "root": "/tmp", "prompt": "p"}) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert probe.max_seen >= 2  # genuinely parallel when the host isolates state


def test_env_reaches_the_legacy_runner_signature():
    seen = {}

    def legacy_runner(command, cwd, timeout):  # no env/on_output kwargs at all
        seen["command"] = command
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R

    adapter = OpenCodeAdapter(binary="opencode", runner=legacy_runner)
    result = adapter.execute("m1", "/tmp", "p", env={"USER_KEY": "k"})
    assert result["status"] == "OPENCODE_COMPLETED"
    assert seen["command"][0] == "opencode"
