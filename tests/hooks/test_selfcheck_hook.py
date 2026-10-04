"""SessionStart self-check: silent when healthy, one stderr line and exit 1 on problems."""

from __future__ import annotations

from hookenv import HookEnv

SESSION_START = {
    "hookEventName": "session_start",
    "hook_event_name": "SessionStart",
    "sessionId": "s1",
    "source": "startup",
}


def test_healthy_setup_is_silent(hooks: HookEnv) -> None:
    result = hooks.run("selfcheck.py", SESSION_START)
    assert result.code == 0
    assert result.stdout == "" and result.stderr == ""


def test_broken_user_policy_is_reported(hooks: HookEnv) -> None:
    hooks.write_user_policy("{broken")
    result = hooks.run("selfcheck.py", SESSION_START)
    assert result.code == 1
    assert result.stderr.startswith("computer-use: policy:")
    assert result.stdout == ""


def test_emergency_stop_is_reported(hooks: HookEnv) -> None:
    hooks.state_dir.mkdir(parents=True)
    (hooks.state_dir / "STOP").write_text("", encoding="utf-8")
    result = hooks.run("selfcheck.py", SESSION_START)
    assert result.code == 1
    assert "emergency stop" in result.stderr


def test_per_user_emergency_stop_is_reported(hooks: HookEnv) -> None:
    # `grok-computer-mcp stop` from a plain shell writes the per-user flag, not the state dir.
    flag = hooks.home / ".grok-computer" / "STOP"
    flag.parent.mkdir(parents=True)
    flag.write_text("", encoding="utf-8")
    result = hooks.run("selfcheck.py", SESSION_START)
    assert result.code == 1
    assert "emergency stop" in result.stderr


def test_selfcheck_writes_nothing(hooks: HookEnv) -> None:
    hooks.run("selfcheck.py", SESSION_START)
    assert not hooks.state_dir.exists()
    assert not (hooks.data / "traces").exists()
