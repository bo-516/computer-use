"""Isolated grok environments, the headless invocation contract (goal.md §9.5) and the runner
CLI's selection and dry run."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path

import grok_runner
import pytest
import run_grok

FAKE_GROK = """#!/bin/sh
printf '%s\\0' "$@" > "$GROK_FAKE_DIR/argv"
printf '%s\\n' "HOME=$HOME" "ASK_AS_DENY=$GROK_COMPUTER_ASK_AS_DENY" \\
  "TRACE=$GROK_COMPUTER_TRACE_DIR" "STATE=$GROK_COMPUTER_STATE_DIR" > "$GROK_FAKE_DIR/env"
echo "$GROK_FAKE_OUTPUT"
exit "${GROK_FAKE_EXIT:-0}"
"""


def _mcp(home: Path) -> dict[str, dict[str, dict[str, object]]]:
    """The installed plugin's .mcp.json."""
    path = home / ".grok" / "plugins" / "computer-use" / ".mcp.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_runs_the_local_facade_with_harness_paths(tmp_path: Path):
    home, traces, state = tmp_path / "home", tmp_path / "traces", tmp_path / "state"
    grok_runner.install_plugin(home, traces, state, local_facade=True)
    grok_runner.install_plugin(home, traces, state, local_facade=True)  # reinstall replaces
    servers = _mcp(home)["mcpServers"]
    computer = servers["computer"]
    assert computer["args"] == ["--from", str(grok_runner.FACADE), "grok-computer-mcp"]
    env = computer["env"]
    assert isinstance(env, dict)
    assert env["GROK_COMPUTER_TRACE_DIR"] == str(traces)
    assert env["GROK_COMPUTER_STATE_DIR"] == str(state)
    assert env["GROK_COMPUTER_BACKEND"] == "cua-driver"
    assert "--isolated" in str(servers["browser"]["args"])
    assert set(servers) == {"computer", "browser"}
    config = (home / ".grok" / "config.toml").read_text(encoding="utf-8")
    assert 'enabled = ["computer-use"]' in config
    assert (home / ".grok" / "plugins" / "computer-use" / "agents" / "computer.md").is_file()


def test_install_can_keep_the_pinned_published_facade(tmp_path: Path):
    grok_runner.install_plugin(tmp_path, tmp_path / "t", tmp_path / "s", local_facade=False)
    assert _mcp(tmp_path)["mcpServers"]["computer"]["args"] == ["grok-computer-mcp@0.2.0"]


def test_workspace_policy_allow_lists_the_variant_apps(tmp_path: Path):
    grok_runner.prepare_workspace(tmp_path, ["Mousepad", "Chromium"])
    policy = json.loads((tmp_path / ".grok" / "computer-policy.json").read_text())
    assert policy == {"allow_apps": ["Mousepad", "Chromium"]}


@pytest.fixture()
def fake_grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``grok`` on PATH that records its argv and environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "grok"
    script.write_text(FAKE_GROK, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GROK_FAKE_DIR", str(tmp_path))
    monkeypatch.setenv("GROK_FAKE_OUTPUT", json.dumps(
        {"num_turns": 7, "usage": {"total_tokens": 4321}, "total_cost_usd": 0.12}))
    return tmp_path


def _run(root: Path) -> grok_runner.GrokResult:
    """Invoke the runner with fixed arguments."""
    return grok_runner.run_grok("Turn Dark mode on.", workspace=root / "ws", grok_home=root / "h",
                                trace_dir=root / "t", state_dir=root / "s", model="grok-4.7",
                                max_turns=12, timeout_s=30)


def test_headless_run_is_unattended_and_denies_the_raw_driver(fake_grok: Path):
    result = _run(fake_grok)
    assert result.exit_code == 0
    argv = (fake_grok / "argv").read_text().split("\0")[:-1]
    assert argv[0] == "-p" and argv[1].startswith("Turn Dark mode on.")
    assert "computer-use:computer" in argv[1]
    flags = (["--output-format", "json"], ["--always-approve"], ["--max-turns", "12"],
             ["--deny", "MCPTool(cua__*)"], ["-m", "grok-4.7"], ["--cwd", str(fake_grok / "ws")])
    for flag in flags:
        assert any(argv[i:i + len(flag)] == flag for i in range(len(argv))), flag
    env = dict(line.split("=", 1) for line in (fake_grok / "env").read_text().splitlines())
    assert env["ASK_AS_DENY"] == "1"  # every guard ask becomes a logged deny (§9.5)
    assert env["HOME"] == str(fake_grok / "h")
    assert (env["TRACE"], env["STATE"]) == (str(fake_grok / "t"), str(fake_grok / "s"))
    assert grok_runner.usage_of(result.output) == (4321, 7, 0.12)


def test_failures_and_unparseable_output(fake_grok: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_FAKE_OUTPUT", "not json")
    monkeypatch.setenv("GROK_FAKE_EXIT", "3")
    result = _run(fake_grok)
    assert (result.exit_code, result.output) == (3, {})
    monkeypatch.setenv("PATH", str(fake_grok / "empty"))
    assert _run(fake_grok).exit_code == 127


def test_usage_ignores_malformed_fields():
    assert grok_runner.usage_of({}) == (None, None, None)
    assert grok_runner.usage_of({"usage": "x", "num_turns": "3", "total_cost_usd": "1"}) == (
        None, None, None)
    assert grok_runner.usage_of({"total_cost_usd": 2}) == (None, None, 2.0)


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    """Runner CLI arguments."""
    values: dict[str, object] = {
        "variants": None, "category": None, "subset": None, "out": tmp_path / "out",
        "model": None, "port": 0, "timeout": 5.0, "max_turns": 2, "baseline": None,
        "pypi_facade": False, "dry_run": True}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_selection_by_ids_category_and_subset(tmp_path: Path):
    assert [v.id for v in run_grok.select(_args(tmp_path, variants="rt-07,native-01"))] == [
        "native-01", "rt-07"]
    assert len(run_grok.select(_args(tmp_path, category="dev_loop"))) == 10
    assert {v.id for v in run_grok.select(_args(tmp_path, subset="smoke"))} == set(
        run_grok.SUBSETS["smoke"])
    assert len(run_grok.select(_args(tmp_path))) == 60


def test_host_runs_allow_the_browser_that_shows_fixture_windows():
    assert run_grok.host_apps(["Mousepad", "bench-ui"]) == ["Mousepad", "Chromium",
                                                            "Google Chrome"]


def test_dry_run_sets_up_and_summarizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROK_COMPUTER_EVAL_BROWSER", "true")  # no real browser window
    args = _args(tmp_path, variants="webview-01")
    assert asyncio.run(run_grok.main_async(args)) == 0
    out: Path = args.out
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    assert [(r["variant"], r["error"]) for r in rows] == [("webview-01", "dry run")]
    summary = json.loads((out / "summary.json").read_text())
    assert summary["tasks"] == 1 and summary["errors"] == ["webview-01"]
    policy = json.loads((out / "webview-01" / "workspace" / ".grok" / "computer-policy.json")
                        .read_text())
    assert policy["allow_apps"] == ["Chromium", "Google Chrome"]
    assert (out / "webview-01" / "grok-home" / ".grok" / "plugins" / "computer-use").is_dir()
    assert asyncio.run(run_grok.main_async(_args(tmp_path, variants="nope"))) == 2
