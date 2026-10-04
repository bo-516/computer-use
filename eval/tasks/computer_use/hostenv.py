"""Harness host services for the task checks, configured through environment variables.

Boundary: file and subprocess I/O on the harness host (where grok, the hooks and the browser server
run). ``eval/harness/run_grok.py`` sets the variables before invoking Cua Bench:

* ``GROK_COMPUTER_EVAL_STATE`` - JSON file shared with the fixture web server (page state);
* ``GROK_COMPUTER_EVAL_AUDIT_DIR`` - directory of the hooks' ``audit-*.jsonl`` files.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# Host commands (dev-loop builds and checks) get this long before they count as failed.
HOST_COMMAND_TIMEOUT_S = 300

Json = dict[str, object]


@dataclass(frozen=True)
class EnvHost:
    """Host services backed by a state file and an audit directory."""

    state_file: Path
    audit_dir: Path

    @classmethod
    def from_env(cls) -> EnvHost:
        """Build from ``GROK_COMPUTER_EVAL_*`` variables (temp defaults for local runs)."""
        root = Path(os.environ.get("TMPDIR", "/tmp")) / "grok-computer-eval"
        return cls(Path(os.environ.get("GROK_COMPUTER_EVAL_STATE", root / "state.json")),
                   Path(os.environ.get("GROK_COMPUTER_EVAL_AUDIT_DIR", root / "audit")))

    def run(self, cmd: str) -> int:
        """Run a host command through the shell; return its exit code."""
        try:
            return subprocess.run(cmd, shell=True, check=False,
                                  timeout=HOST_COMMAND_TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            return 124

    def write(self, path: str, text: str) -> None:
        """Write a host file, creating parent directories."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def state(self) -> Json:
        """Current fixture state ({} when none)."""
        try:
            data: object = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast(Json, data) if isinstance(data, dict) else {}

    def post(self, key: str, payload: object) -> None:
        """Set one fixture state key (``None`` removes it)."""
        data = self.state()
        if payload is None:
            data.pop(key, None)
        else:
            data[key] = payload
        self.write(str(self.state_file), json.dumps(data))

    def audit_records(self) -> list[Json]:
        """All audit-log records written by the hooks during the run."""
        out: list[Json] = []
        for path in sorted(self.audit_dir.glob("audit-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item: object = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    out.append(cast(Json, item))
        return out
