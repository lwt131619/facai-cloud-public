"""Goose (Block's open-source agent CLI) execution boundary.

Mirrors the OpenCode adapter contract exactly: prepare an isolated mission
workspace, run the CLI inside exactly that workspace, capture raw output and
attribute the real outcome. Goose results reuse the OpenCode status vocabulary
(``OPENCODE_COMPLETED`` / ``OPENCODE_FAILED`` / ``OPENCODE_TIMEOUT``) so the
whole pipeline — events, checkpoints, cost parsing, step transitions — works
without a second code path.

No credential is placed on the command line; goose reads its own provider
store from the environment.
"""

import shutil
import subprocess
from pathlib import Path

from .opencode import redact_output, summarize_output


class GooseAdapter:
    def __init__(self, binary=None, runner=None):
        self.binary = binary
        self.runner = runner

    def availability(self):
        binary = self.binary or shutil.which("goose")
        return {"installed": binary is not None, "binary": binary}

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
            raise ValueError("Goose binary is not installed")
        command = [binary, "run", "--dir", str(workspace)]
        if model_ref:
            command += ["--model", model_ref]
        command += ["--text", prompt]
        return command

    def execute(self, mission, root, prompt, model_ref=None, timeout=300, on_output=None,
                workspace_override=None):
        from .opencode import OpenCodeAdapter, resolve_override_workspace

        workspace = self.workspace(mission, root)
        if workspace_override is not None:
            # An isolated worktree path for parallel same-mission branches; it
            # passes the same validation as a computed mission workspace.
            workspace = resolve_override_workspace(workspace_override, root)
        workspace.mkdir(parents=True, exist_ok=True)
        command = self.command(prompt, workspace, model_ref)
        runner = self.runner or OpenCodeAdapter._run
        streamed_lines = 0

        def emit(stream, line):
            nonlocal streamed_lines
            streamed_lines += 1
            if on_output:
                on_output(stream, redact_output(line))

        try:
            completed = runner(command, workspace, timeout, on_output=emit)
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


__all__ = ["GooseAdapter", "summarize_output"]
