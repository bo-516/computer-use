"""Verify gate (SubagentStop): report contract and observe-before-success."""

from __future__ import annotations

import json
import time
from typing import Dict, List

from hookenv import HookEnv

GOOD_REPORT = (
    "STATUS: success\n"
    "SUMMARY: Toggled Dark mode and the window switched to the dark theme.\n"
    "EVIDENCE: Dark mode -> on; /tmp/traces/obs_7f3b.jpg\n"
    "BLOCKERS: none\n"
    "NEXT: nothing\n"
)


def stop(message: str, active: bool = False, session: str = "s1") -> Dict[str, object]:
    """A SubagentStop payload."""
    return {
        "hookEventName": "subagent_stop",
        "hook_event_name": "SubagentStop",
        "sessionId": session,
        "subagentType": "computer-use:computer",
        "phase": "gate",
        "stopHookActive": active,
        "lastAssistantMessage": message,
    }


def write_audit(hooks: HookEnv, records: List[Dict[str, object]], session: str = "s1") -> None:
    """Write audit lines for the session."""
    path = hooks.data / "traces" / f"audit-{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def gui(ts: float, tool: str, action: bool, observe: bool) -> Dict[str, object]:
    """One audit record for a GUI call."""
    return {"v": 1, "ts": ts, "event": "tool", "tool": tool, "action": action, "observe": observe}


def test_stop_hook_active_lets_it_stop(hooks: HookEnv) -> None:
    result = hooks.run("verify_gate.py", stop("no report at all", active=True))
    assert result.code == 0 and result.stdout == ""


def test_missing_sections_block(hooks: HookEnv) -> None:
    result = hooks.run("verify_gate.py", stop("STATUS: success\nSUMMARY: did it"))
    assert result.decision == "block"
    assert "STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT" in result.reason


def test_invalid_status_blocks(hooks: HookEnv) -> None:
    result = hooks.run("verify_gate.py", stop(GOOD_REPORT.replace("success", "done")))
    assert result.decision == "block"


def test_success_after_observation_passes(hooks: HookEnv) -> None:
    now = time.time()
    write_audit(
        hooks,
        [
            gui(now - 5, "computer__click", True, False),
            gui(now - 2, "computer__observe", False, True),
        ],
    )
    result = hooks.run("verify_gate.py", stop(GOOD_REPORT))
    assert result.code == 0 and result.stdout == ""


def test_success_without_observing_after_last_action_blocks(hooks: HookEnv) -> None:
    now = time.time()
    write_audit(
        hooks,
        [
            gui(now - 5, "computer__observe", False, True),
            gui(now - 2, "browser__browser_click", True, False),
        ],
    )
    result = hooks.run("verify_gate.py", stop(GOOD_REPORT))
    assert result.decision == "block"
    assert "observ" in result.reason


def test_failed_actions_do_not_count(hooks: HookEnv) -> None:
    now = time.time()
    failed = gui(now - 1, "computer__click", True, False)
    failed["event"] = "tool_failure"
    write_audit(hooks, [gui(now - 5, "computer__observe", False, True), failed])
    assert hooks.run("verify_gate.py", stop(GOOD_REPORT)).stdout == ""


def test_state_file_is_the_fallback_without_audit_log(
    hooks: HookEnv, settings_state: Dict[str, object]
) -> None:
    state = dict(settings_state)
    state["last_action_at"] = time.time()
    state["last_observe_at"] = time.time() - 10
    hooks.write_state(state, age=600)
    result = hooks.run("verify_gate.py", stop(GOOD_REPORT))
    assert result.decision == "block"


def test_partial_status_is_not_checked_for_verification(hooks: HookEnv) -> None:
    now = time.time()
    write_audit(hooks, [gui(now - 1, "computer__click", True, False)])
    report = GOOD_REPORT.replace("STATUS: success", "STATUS: partial")
    assert hooks.run("verify_gate.py", stop(report)).stdout == ""


def test_oversized_report_blocks_but_paths_do_not_count(hooks: HookEnv) -> None:
    long_paths = " ".join(f"/tmp/traces/shot_{i:03d}.jpg" for i in range(200))
    with_paths = GOOD_REPORT.replace("/tmp/traces/obs_7f3b.jpg", long_paths)
    assert hooks.run("verify_gate.py", stop(with_paths)).stdout == ""
    bloated = GOOD_REPORT.replace("Toggled Dark mode", "Toggled Dark mode " + "x" * 2100)
    result = hooks.run("verify_gate.py", stop(bloated))
    assert result.decision == "block"
    assert "2 KB" in result.reason


def test_malformed_payload_lets_it_stop(hooks: HookEnv) -> None:
    for payload in ("nope", "", "[]"):
        result = hooks.run("verify_gate.py", payload)
        assert result.code == 0 and result.stdout == ""
