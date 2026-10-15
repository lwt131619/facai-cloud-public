"""OpenCode integration boundary.

Preparation only creates the isolated workspace. Execution runs the OpenCode
CLI inside exactly one mission workspace and returns its real outcome so the
API can attribute success or failure honestly. No credential is placed on the
command line; OpenCode reads its own provider store from the environment.
"""

import json
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .broker import inject_scoped_env, system_env_base


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


_SECRET_PATTERNS = (
    re.compile(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|secret|password)[\"']?\s*:\s*[\"']?)[^\s,;}\"']+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)(\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)Bearer\s+[^\s,;\"']+"),
)


def redact_output(value):
    """Remove common credential forms before output is exposed or persisted."""
    value = _text(value)
    for pattern in _SECRET_PATTERNS:
        def replacement(match):
            if match.lastindex == 2:
                prefix = match.group(1) + match.group(2)
            elif match.lastindex == 1:
                prefix = match.group(1)
            else:
                prefix = "Bearer "
            return prefix + "[REDACTED]"
        value = pattern.sub(replacement, value)
    return value


def summarize_output(stdout):
    """Parse OpenCode's --format json stream into text, tokens and reported cost.

    Cost is summed across steps; tokens use the largest cumulative count because
    OpenCode reports a running total per session.
    """
    parts, cost, tokens, lines = [], 0.0, 0, 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        lines += 1
        part = event.get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            parts.append(part["text"])
        # Reported cost is untrusted upstream input: a bare NaN/Infinity
        # literal parses fine here but would poison the cost ledger and the
        # budget gate downstream, and bool is an int subtype that would sum
        # as 1.0. Only a finite real number may touch the running total.
        reported = part.get("cost")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool) \
                and math.isfinite(reported):
            cost += reported
        token_data = part.get("tokens")
        if isinstance(token_data, dict) and isinstance(token_data.get("total"), (int, float)):
            tokens = max(tokens, int(token_data["total"]))
    return {"text": "\n".join(parts), "cost": round(cost, 8), "tokens": tokens, "event_lines": lines}


def resolve_override_workspace(workspace_override, root):
    """Validate a caller-supplied workspace override (an isolated worktree path).

    Overrides are subject to the same rule as computed mission workspaces: the
    resolved path must live beneath ``root/workspaces`` and must not be the
    shared root itself, so a bad override can never point execution outside the
    mission tree.
    """
    try:
        resolved = Path(workspace_override).resolve()
    except (OSError, ValueError):
        raise ValueError("workspace override is invalid") from None
    base = (Path(root) / "workspaces").resolve()
    if resolved == base or base not in resolved.parents:
        raise ValueError("workspace override is outside the mission root")
    return resolved


class OpenCodeAdapter:
    def __init__(self, binary=None, runner=None):
        self.binary = binary
        self.runner = runner or self._run
        # OpenCode keeps one shared state store under HOME, so two concurrent
        # `opencode run` processes can kill each other with SQLite
        # "database is locked" (observed live on parallel mission branches,
        # 2026-10-03). Execution is therefore serialized by default; parallel
        # branches keep their isolated worktrees, they just take turns on the
        # CLI. Set FACAI_OPENCODE_SERIAL=0 on a host that gives each run its
        # own state directory.
        self._serial = os.getenv("FACAI_OPENCODE_SERIAL", "1") != "0"
        self._exec_lock = threading.Lock()

    def availability(self):
        binary = self.binary or shutil.which("opencode")
        return {"installed": binary is not None, "binary": binary}

    @staticmethod
    def _run(command, cwd, timeout, on_output=None, env=None):
        # Whitelist base + explicit per-run injection (facai.broker): the child
        # no longer inherits the whole host environment. Credentials travel
        # only as explicitly granted keys (e.g. FACAI_USER_MODEL_KEY from the
        # user-model runtime); everything else this process carries — tokens,
        # provider keys, operator settings — stays here. A deployment that
        # needs named pass-through keys (e.g. OPENCODE_AUTH_CONTENT) lists
        # them in FACAI_CHILD_ENV_ALLOWLIST; nothing else crosses over.
        # Reader threads drain both pipes concurrently and publish complete
        # lines as they arrive; this works on both Windows and POSIX (unlike
        # selectors over subprocess pipes on Windows).
        child_env = inject_scoped_env(system_env_base(), env or {})
        process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1, env=child_env)
        output = {"stdout": [], "stderr": []}

        def drain(stream_name, pipe):
            try:
                for line in iter(pipe.readline, ""):
                    output[stream_name].append(line)
                    if on_output:
                        on_output(stream_name, redact_output(line.rstrip("\r\n")))
            finally:
                pipe.close()

        readers = [threading.Thread(target=drain, args=(name, pipe), daemon=True)
                   for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            raise subprocess.TimeoutExpired(command, timeout,
                                            output="".join(output["stdout"]),
                                            stderr="".join(output["stderr"])) from exc
        for reader in readers:
            reader.join()
        return subprocess.CompletedProcess(command, process.returncode,
                                           "".join(output["stdout"]), "".join(output["stderr"]))

    def workspace(self, mission, root):
        workspace = (Path(root) / "workspaces" / mission).resolve()
        base = (Path(root) / "workspaces").resolve()
        if workspace.parent != base:
            raise ValueError("Invalid mission workspace")
        return workspace

    def prepare(self, mission, root):
        workspace = self.workspace(mission, root)
        workspace.mkdir(parents=True, exist_ok=True)
        return {"mission_id": mission, "workspace": str(workspace), **self.availability()}

    def command(self, prompt, workspace, model_ref=None):
        availability = self.availability()
        binary = self.binary or availability["binary"]
        if binary is None:
            raise ValueError("OpenCode binary is not installed")
        command = [binary, "run", "--format", "json"]
        if model_ref:
            command += ["--model", model_ref]
        command += ["--dir", str(workspace)]
        command.append(prompt)
        return command

    def execute(self, mission, root, prompt, model_ref=None, timeout=300, on_output=None,
                workspace_override=None, env=None):
        workspace = self.workspace(mission, root)
        if workspace_override is not None:
            # An isolated worktree path for parallel same-mission branches; it
            # passes the same validation as a computed mission workspace.
            workspace = resolve_override_workspace(workspace_override, root)
        workspace.mkdir(parents=True, exist_ok=True)
        command = self.command(prompt, workspace, model_ref)
        streamed_lines = 0

        def emit(stream, line):
            nonlocal streamed_lines
            streamed_lines += 1
            if on_output:
                on_output(stream, redact_output(line))

        try:
            def _invoke(env=None):
                if self.runner == self._run:
                    return self.runner(command, workspace, timeout, on_output=emit, env=env)
                # Preserve the historical injected-runner API used by callers
                # and tests. New runners may opt into the output callback.
                import inspect
                if "on_output" in inspect.signature(self.runner).parameters:
                    if "env" in inspect.signature(self.runner).parameters:
                        return self.runner(command, workspace, timeout, on_output=emit, env=env)
                    return self.runner(command, workspace, timeout, on_output=emit)
                return self.runner(command, workspace, timeout)

            if self._serial:
                with self._exec_lock:
                    completed = _invoke(env=env)
            else:
                completed = _invoke(env=env)
        except subprocess.TimeoutExpired as exc:
            return {"status": "OPENCODE_TIMEOUT", "exit_code": None, "workspace": str(workspace),
                    "stdout": redact_output(exc.stdout), "stderr": redact_output(exc.stderr),
                    "streamed": streamed_lines > 0}
        exit_code = getattr(completed, "returncode", None)
        return {
            "status": "OPENCODE_COMPLETED" if exit_code == 0 else "OPENCODE_FAILED",
            "exit_code": exit_code,
            "workspace": str(workspace),
            "stdout": redact_output(getattr(completed, "stdout", "")),
            "stderr": redact_output(getattr(completed, "stderr", "")),
            "streamed": streamed_lines > 0,
        }
