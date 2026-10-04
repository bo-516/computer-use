"""The facade as a process: stdio MCP (legacy handshake) and the CLI subcommands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from facade_helpers import decode
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio


def env_for(tmp_path: Path) -> dict[str, str]:
    """Environment of an isolated facade process on the fake backend."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        **os.environ,
        "HOME": str(home),
        "GROK_COMPUTER_BACKEND": "fake",
        "GROK_PLUGIN_DATA": str(tmp_path / "data"),
        "GROK_COMPUTER_LOCK_DIR": str(tmp_path / "locks"),
    }


def cli(
    tmp_path: Path, *args: str, stdin: str = "", extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m grok_computer_mcp <args>``."""
    return subprocess.run(
        [sys.executable, "-m", "grok_computer_mcp", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**env_for(tmp_path), **(extra or {})},
        timeout=60,
        check=False,
    )


@pytest.mark.slow
async def test_stdio_server_with_legacy_handshake(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "grok_computer_mcp"], env=env_for(tmp_path)
    )
    async with Client(params, mode="legacy") as client:
        names = [t.name for t in (await client.list_tools()).tools]
        assert names[0] == "observe" and len(names) == 9
        result = decode(await client.call_tool("observe", {"app": "MyApp", "mode": "screenshot"}))
        assert not result.is_error, result.text
        assert len(result.images) == 1


def test_version_and_status(tmp_path: Path) -> None:
    assert cli(tmp_path, "version").stdout.strip() == "0.2.0"
    status = json.loads(cli(tmp_path, "status").stdout)
    assert status["backend"] == "fake" and status["emergency_stop"] is False


def test_invalid_configuration_exits_2(tmp_path: Path) -> None:
    result = cli(tmp_path, "status", extra={"GROK_COMPUTER_SCREENSHOT_LONG_EDGE": "4000"})
    assert result.returncode == 2
    assert "GROK_COMPUTER_SCREENSHOT_LONG_EDGE" in result.stderr


def test_stop_and_resume(tmp_path: Path) -> None:
    cli(tmp_path, "stop")
    assert json.loads(cli(tmp_path, "status").stdout)["emergency_stop"] is True
    cli(tmp_path, "resume")
    assert json.loads(cli(tmp_path, "status").stdout)["emergency_stop"] is False


def test_stop_from_a_plain_shell_reaches_the_hosted_facade(tmp_path: Path) -> None:
    # The user's shell has no GROK_PLUGIN_DATA; the facade grok starts does.
    shell = {"GROK_PLUGIN_DATA": "", "GROK_COMPUTER_STATE_DIR": ""}
    assert cli(tmp_path, "stop", extra=shell).returncode == 0
    assert (tmp_path / "home" / ".grok-computer" / "STOP").exists()
    assert json.loads(cli(tmp_path, "status").stdout)["emergency_stop"] is True
    cli(tmp_path, "resume")
    assert json.loads(cli(tmp_path, "status", extra=shell).stdout)["emergency_stop"] is False


def test_trace_purge(tmp_path: Path) -> None:
    traces = tmp_path / "data" / "traces"
    (traces / "facade-old").mkdir(parents=True)
    (traces / "audit-s.jsonl").write_text("{}\n")
    (traces / "unrelated.txt").write_text("keep")
    assert cli(tmp_path, "trace", "purge", "--all").returncode == 0
    assert sorted(p.name for p in traces.iterdir()) == ["unrelated.txt"]


def test_hook_subcommands_share_the_plugin_logic(tmp_path: Path) -> None:
    payload = json.dumps({"toolName": "computer__observe", "toolInput": {}})
    denied = cli(tmp_path, "guard", stdin=payload)
    assert json.loads(denied.stdout)["decision"] == "deny"
    allowed = cli(
        tmp_path,
        "guard",
        stdin=json.dumps(
            {"toolName": "computer__observe", "toolInput": {}, "subagentType": "computer"}
        ),
    )
    assert json.loads(allowed.stdout) == {"decision": "allow"}
    gate = cli(tmp_path, "verify-gate", stdin=json.dumps({"lastAssistantMessage": "hi"}))
    assert json.loads(gate.stdout)["decision"] == "block"
    assert cli(tmp_path, "audit", stdin="garbage").returncode == 0
    assert cli(tmp_path, "selfcheck").returncode == 0


@pytest.mark.slow
def test_doctor_on_the_fake_backend(tmp_path: Path) -> None:
    result = cli(tmp_path, "doctor", "--json")
    checks = {c["name"]: c for c in json.loads(result.stdout)}
    assert checks["screenshot"]["ok"] is True
    assert checks["permission accessibility"]["ok"] is True
