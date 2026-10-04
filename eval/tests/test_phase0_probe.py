"""Phase 0 probe plugin: the stdlib probe server speaks MCP and the payload hook redacts typed
text and fails open, on the test interpreter and on Python 3.8."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import mcp_types as types
import pytest
import run_probes
from mcp import Client, StdioServerParameters

from grok_computer_mcp.observe.imaging import open_rgb

PLUGIN = Path(__file__).resolve().parents[1] / "phase0" / "probe-plugin"
PYTHON = os.environ.get("GROK_COMPUTER_TEST_PYTHON") or sys.executable


@pytest.fixture()
def plugin(tmp_path: Path) -> Path:
    """A copy of the probe plugin with generated assets."""
    target = tmp_path / "plugin"
    shutil.copytree(PLUGIN, target)
    run_probes.make_assets(target / "assets", run_probes.random.Random(1))
    return target


def _log(root: Path, name: str) -> list[dict[str, object]]:
    """Records of a probe log."""
    path = root / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.anyio
async def test_probe_server_speaks_mcp(plugin: Path, tmp_path: Path):
    logs = tmp_path / "logs"
    env = {"PATH": os.environ.get("PATH", ""), "GROK_COMPUTER_PROBE_LOG": str(logs),
           "GROK_COMPUTER_PROBE_ID": "unit", "GROK_COMPUTER_PROBE_DATA": "${GROK_PLUGIN_DATA}"}
    params = StdioServerParameters(command=PYTHON, args=[str(plugin / "bin" / "probe_mcp.py"),
                                                         "probe"], env=env)
    codes = json.loads((plugin / "assets" / "codes.json").read_text())
    async with Client(params, mode="legacy") as client:
        names = [t.name for t in (await client.list_tools()).tools]
        assert names == ["show_code", "structured_code", "env", "echo"]
        shown = await client.call_tool("show_code", {"size": "canonical"})
        image = [b for b in shown.content if isinstance(b, types.ImageContent)]
        assert len(image) == 1 and image[0].mime_type == "image/jpeg"
        assert open_rgb(base64.b64decode(image[0].data)).size == (1280, 800)
        assert codes["canonical"]["code"] not in str(shown.content)  # only in the pixels
        structured = await client.call_tool("structured_code", {})
        assert structured.structured_content == {"code": codes["structured"]["code"]}
        assert codes["structured"]["code"] not in str(structured.content)
        env_result = await client.call_tool("env", {})
        assert "${GROK_PLUGIN_DATA}" in str(env_result.content)
        echoed = await client.call_tool("echo", {"text": "ping"})
        assert isinstance(echoed.content[0], types.TextContent)
        assert echoed.content[0].text == "ping"
    events = [r["event"] for r in _log(logs, "probe-mcp.jsonl")]
    assert events[0] == "start" and {"show_code", "env", "echo"} <= set(events)


def _hook(plugin: Path, stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the payload hook."""
    return subprocess.run([PYTHON, str(plugin / "bin" / "dump_payload.py"), "pre-all"],
                          input=stdin, capture_output=True, text=True, env=env, check=False)


def test_payload_hook_redacts_typed_text_and_fails_open(plugin: Path, tmp_path: Path):
    env = {"PATH": os.environ.get("PATH", ""), "GROK_COMPUTER_PROBE_LOG": str(tmp_path / "l"),
           "GROK_COMPUTER_PROBE_ID": "delegate"}
    payload = {"toolName": "probe__echo", "subagentType": "computer-use-probe:probe",
               "toolInput": {"text": "secret words", "nested": [{"value": "hunter2"}]}}
    done = _hook(plugin, json.dumps(payload), env)
    assert (done.returncode, done.stdout) == (0, "")
    record = _log(tmp_path / "l", "hooks.jsonl")[0]
    tool_input = cast(dict[str, dict[str, object]], record["payload"])["toolInput"]
    assert tool_input["text"] == {"len": 12, "head": "se"}
    assert tool_input["nested"] == [{"value": {"len": 7, "head": "hu"}}]
    assert record["label"] == "pre-all"
    assert cast(dict[str, object], record["env"])["GROK_COMPUTER_PROBE_ID"] == "delegate"
    assert _hook(plugin, "not json", env).returncode == 0
    assert _log(tmp_path / "l", "hooks.jsonl")[1]["payload"] == {"unparsed_len": 8}
    (tmp_path / "file").write_text("x")
    blocked = {**env, "GROK_COMPUTER_PROBE_LOG": str(tmp_path / "file" / "sub")}
    assert _hook(plugin, "{}", blocked).returncode == 0  # unwritable log: still exits 0


def test_probe_scripts_run_on_python38(plugin: Path, tmp_path: Path):
    py38 = shutil.which("python3.8")
    if py38 is None:
        pytest.skip("python3.8 is not on PATH")
    for script in ("probe_mcp.py", "dump_payload.py"):
        subprocess.run([py38, "-m", "py_compile", str(plugin / "bin" / script)], check=True)
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    done = subprocess.run([py38, str(plugin / "bin" / "probe_mcp.py")], input=request, text=True,
                          capture_output=True, check=True,
                          env={"GROK_COMPUTER_PROBE_LOG": str(tmp_path)})
    assert len(json.loads(done.stdout)["result"]["tools"]) == 4
