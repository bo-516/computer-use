"""Helpers for the plugin hook tests (imported by the test modules and ``conftest.py``).

Hooks are tested the way the host runs them (AGENTS.md): a JSON payload piped to the script on
stdin, asserting the stdout decision and exit code 0. Every test gets an isolated HOME,
GROK_PLUGIN_DATA and workspace. The interpreter defaults to the one running pytest; the suite is
3.8-compatible, so ``uv run --python 3.8 --no-project --with pytest==8.3.5 pytest tests/hooks``
exercises the hooks on the oldest supported Python.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, cast

REPO = Path(__file__).resolve().parents[2]
HOOKS_BIN = REPO / "plugins" / "computer-use" / "hooks" / "bin"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PYTHON = os.environ.get("GROK_COMPUTER_TEST_PYTHON") or sys.executable
SUBAGENT = "computer-use:computer"
# Generous ceiling for one hook subprocess in tests; the real budget is checked separately.
HOOK_TIMEOUT_S = 20

if str(HOOKS_BIN) not in sys.path:
    sys.path.insert(0, str(HOOKS_BIN))


class HookResult:
    """Outcome of one hook run."""

    def __init__(self, code: int, stdout: str, stderr: str) -> None:
        """Store the raw process outcome."""
        self.code = code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def output(self) -> Optional[Dict[str, object]]:
        """The decoded stdout JSON object, or None when stdout is empty."""
        if not self.stdout.strip():
            return None
        value = json.loads(self.stdout)
        assert isinstance(value, dict), self.stdout
        return cast(Dict[str, object], value)

    @property
    def decision(self) -> str:
        """The ``decision`` field of the output ("" when absent)."""
        out = self.output or {}
        return str(out.get("decision", ""))

    @property
    def reason(self) -> str:
        """The ``reason`` field of the output ("" when absent)."""
        out = self.output or {}
        return str(out.get("reason", ""))


class HookEnv:
    """An isolated home, plugin data dir and workspace for running hooks."""

    def __init__(self, root: Path) -> None:
        """Create the directory layout under ``root``."""
        self.home = root / "home"
        self.data = root / "plugin-data"
        self.workspace = root / "workspace"
        for path in (self.home, self.data, self.workspace):
            path.mkdir(parents=True)
        self.env: Dict[str, str] = {
            "HOME": str(self.home),
            "GROK_PLUGIN_DATA": str(self.data),
            "GROK_PLUGIN_ROOT": str(HOOKS_BIN.parent.parent),
            "PATH": os.environ.get("PATH", ""),
        }

    @property
    def state_dir(self) -> Path:
        """Primary state directory (``$GROK_PLUGIN_DATA/state``)."""
        return self.data / "state"

    @property
    def fallback_state_dir(self) -> Path:
        """Fallback state directory (``~/.grok-computer/state``)."""
        return self.home / ".grok-computer" / "state"

    def write_state(
        self, state: Dict[str, object], age: float = 0.0, directory: Optional[Path] = None
    ) -> None:
        """Write ``last_observation.json`` stamped ``age`` seconds ago."""
        target = directory or self.state_dir
        target.mkdir(parents=True, exist_ok=True)
        data = dict(state)
        data["updated_at"] = time.time() - age
        (target / "last_observation.json").write_text(json.dumps(data), encoding="utf-8")

    def write_user_policy(self, policy: object) -> None:
        """Write ``~/.grok-computer/policy.json`` (raw string or JSON value)."""
        path = self.home / ".grok-computer" / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")

    def write_project_policy(self, policy: object) -> None:
        """Write ``<workspace>/.grok/computer-policy.json``."""
        path = self.workspace / ".grok" / "computer-policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")

    def run(
        self, script: str, payload: object, extra_env: Optional[Dict[str, str]] = None
    ) -> HookResult:
        """Pipe ``payload`` (a JSON value or a raw string) into ``hooks/bin/<script>``."""
        env = dict(self.env)
        env.update(extra_env or {})
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(
            [PYTHON, str(HOOKS_BIN / script)],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=HOOK_TIMEOUT_S,
            check=False,
        )
        return HookResult(proc.returncode, proc.stdout, proc.stderr)

    def audit_records(self, session: str = "s1") -> List[Dict[str, object]]:
        """Decode the session audit log written by the hooks."""
        path = self.data / "traces" / (f"audit-{session}.jsonl")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def approved_apps(self, session: str = "s1") -> List[str]:
        """Apps recorded as approved in the asked-apps state."""
        path = self.state_dir / (f"asked_apps_{session}.json")
        if not path.exists():
            return []
        return list(json.loads(path.read_text(encoding="utf-8"))["approved"])


def event(
    tool: str,
    tool_input: object = None,
    subagent: Optional[str] = SUBAGENT,
    session: str = "s1",
    workspace: Optional[Path] = None,
    **extra: object,
) -> Dict[str, object]:
    """Build a PreToolUse-style payload."""
    payload: Dict[str, object] = {
        "hookEventName": "pre_tool_use",
        "hook_event_name": "PreToolUse",
        "sessionId": session,
        "toolName": tool,
        "toolInput": {} if tool_input is None else tool_input,
        "permissionMode": "default",
    }
    if subagent is not None:
        payload["subagentType"] = subagent
    if workspace is not None:
        payload["workspaceRoot"] = str(workspace)
        payload["cwd"] = str(workspace)
    payload.update(extra)
    return payload


def load_fixture(name: str) -> Dict[str, object]:
    """Load a JSON fixture from ``tests/hooks/fixtures``."""
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(Dict[str, object], value)


def allow_myapp(hooks: HookEnv) -> None:
    """Allow-list MyApp in the project policy (the app under development)."""
    hooks.write_project_policy({"allow_apps": ["MyApp"]})


def run_guard(
    hooks: HookEnv,
    tool: str,
    tool_input: object = None,
    extra_env: Optional[Dict[str, str]] = None,
    subagent: Optional[str] = SUBAGENT,
    session: str = "s1",
) -> HookResult:
    """Run the guard for one call with the workspace set."""
    payload = event(tool, tool_input, subagent=subagent, session=session, workspace=hooks.workspace)
    result = hooks.run("guard.py", payload, extra_env)
    assert result.code == 0, result.stderr
    return result


def decision_of(result: HookResult) -> Tuple[str, str]:
    """``(decision, reason)`` of a guard run."""
    return result.decision, result.reason
