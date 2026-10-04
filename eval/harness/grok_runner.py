"""Prepare an isolated grok environment for one variant and run grok headless (goal.md §9.5).

Boundary: file and subprocess I/O on the harness host. Every variant gets its own workspace,
trace directory and state directory. The plugin is installed into an isolated grok home
(``<home>/.grok/plugins`` is auto-trusted), its ``.mcp.json`` is rewritten to run the facade from
the local source and to write traces where the harness reads them, and the workspace gets a
project policy that allow-lists the variant's apps. Unattended runs set
``GROK_COMPUTER_ASK_AS_DENY=1`` so every guard ``ask`` becomes a logged deny (§9.5).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "computer-use"
FACADE = REPO / "servers" / "grok-computer-mcp"
PROMPT_SUFFIX = (
    "\n\nUse the computer-use skill: delegate all GUI work to the computer-use:computer "
    "subagent, one task at a time, and wait for its report. Do not ask me questions; if the "
    "subagent reports STATUS: blocked, stop and report why.")
# Static deny rules for unattended runs (goal.md B.2): the raw driver is never callable.
DENY_RULES = ("MCPTool(cua__*)",)


@dataclass(frozen=True)
class GrokResult:
    """What one grok invocation produced."""

    exit_code: int
    duration_s: float
    output: dict[str, object]
    stderr_tail: str


def install_plugin(grok_home: Path, trace_dir: Path, state_dir: Path, local_facade: bool) -> None:
    """Install the plugin into an isolated grok home and enable it.

    Args:
        grok_home: HOME for the grok process (``<home>/.grok`` is created).
        trace_dir: Where hooks and the facade write audit logs and traces.
        state_dir: Facade <-> hooks state directory.
        local_facade: Run the facade from ``servers/grok-computer-mcp`` instead of PyPI.
    """
    target = grok_home / ".grok" / "plugins" / "computer-use"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PLUGIN, target)
    mcp_path = target / ".mcp.json"
    mcp: dict[str, object] = json.loads(mcp_path.read_text(encoding="utf-8"))
    servers = cast(dict[str, dict[str, object]], mcp["mcpServers"])
    computer = servers["computer"]
    if local_facade:
        computer["args"] = ["--from", str(FACADE), "grok-computer-mcp"]
    env = cast(dict[str, str], computer.setdefault("env", {}))
    env.update(GROK_COMPUTER_TRACE_DIR=str(trace_dir), GROK_COMPUTER_STATE_DIR=str(state_dir))
    mcp_path.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    config = grok_home / ".grok" / "config.toml"
    config.write_text('[plugins]\nenabled = ["computer-use"]\n', encoding="utf-8")


def prepare_workspace(workspace: Path, allow_apps: list[str]) -> None:
    """Create the workspace with a project policy allow-listing the variant's apps.

    Args:
        workspace: Directory grok runs in (``--cwd``).
        allow_apps: Apps the variant operates (R1 asks would otherwise deny them).
    """
    policy = workspace / ".grok" / "computer-policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps({"allow_apps": allow_apps}), encoding="utf-8")


def run_grok(prompt: str, *, workspace: Path, grok_home: Path, trace_dir: Path, state_dir: Path,
             model: str | None, max_turns: int, timeout_s: float) -> GrokResult:
    """Run ``grok -p`` once.

    Args:
        prompt: Task description (the suffix with delegation rules is appended).
        workspace: ``--cwd``.
        grok_home: HOME for grok (isolated config and plugins; auth via environment).
        trace_dir: Trace directory (hooks read it from the environment).
        state_dir: State directory.
        model: Model id, or None for grok's default.
        max_turns: Main-agent turn cap.
        timeout_s: Wall-clock cap.

    Returns:
        Exit code, duration, the parsed JSON result (``{}`` when unparseable) and stderr tail.
    """
    argv = ["grok", "-p", prompt + PROMPT_SUFFIX, "--output-format", "json", "--cwd",
            str(workspace), "--always-approve", "--max-turns", str(max_turns)]
    for rule in DENY_RULES:
        argv += ["--deny", rule]
    if model:
        argv += ["-m", model]
    env = {**os.environ, "HOME": str(grok_home), "GROK_COMPUTER_ASK_AS_DENY": "1",
           "GROK_COMPUTER_TRACE_DIR": str(trace_dir), "GROK_COMPUTER_STATE_DIR": str(state_dir)}
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env,
                              timeout=timeout_s, check=False)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = 124, "", f"timeout after {timeout_s}s: {exc}"
    except FileNotFoundError:
        code, out, err = 127, "", "grok is not installed or not on PATH"
    try:
        parsed: object = json.loads(out) if out.strip() else {}
    except ValueError:
        parsed = {}
    output = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
    return GrokResult(code, time.monotonic() - start, output, err[-2000:])


def usage_of(output: dict[str, object]) -> tuple[int | None, int | None, float | None]:
    """``(total_tokens, num_turns, total_cost_usd)`` from grok's JSON result."""
    usage = output.get("usage")
    total = cast(dict[str, object], usage).get("total_tokens") if isinstance(usage, dict) else None
    turns = output.get("num_turns")
    cost = output.get("total_cost_usd")
    return (total if isinstance(total, int) else None, turns if isinstance(turns, int) else None,
            float(cost) if isinstance(cost, int | float) else None)
