"""Process entry points for the stop-side hooks: SubagentStop verify gate and SessionStart check.

Boundary: process edge (see ``hooklib.entry``). The verify gate prints a block decision or nothing
and always exits 0 (a crash lets the subagent stop); the self-check prints one stderr line and
exits 1 when something needs the user's attention.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List

from . import io, paths, selfcheck, state, verify_gate
from .entry import load_policy, read_event, read_states, user_home

# Audit lines the verify gate reads from the end of the session log; a subagent run is capped
# at 80 turns (goal.md §4.4), so this comfortably covers one run.
VERIFY_TAIL_LINES = 400
# Upper bound on bytes read from the end of the audit log for the verify gate.
VERIFY_TAIL_BYTES = 512 * 1024


def _audit_tail(path: str) -> List[object]:
    """Decode the last lines of an audit log (oldest first); unreadable lines are skipped."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - VERIFY_TAIL_BYTES))
            lines = handle.read().decode("utf-8", "replace").splitlines()[-VERIFY_TAIL_LINES:]
    except OSError:
        return []
    out: List[object] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def verify_gate_main() -> int:
    """SubagentStop entry point. Prints a block decision or nothing.

    Returns:
        Process exit code (always 0; a crash lets the subagent stop).
    """
    try:
        env = os.environ
        home = user_home()
        event = read_event()
        records = _audit_tail(paths.audit_log_path(env, home, event.get("sessionId")))
        latest = state.latest_state(read_states(env, home))
        reason = verify_gate.decide(event, records, latest)
        if reason:
            io.emit({"decision": "block", "reason": reason})
    except Exception:  # process edge: never trap the subagent because of a gate bug
        pass
    return 0


def selfcheck_main() -> int:
    """SessionStart entry point.

    Returns:
        0 when healthy, 1 after printing the first problem to stderr.
    """
    try:
        env = os.environ
        home = user_home()
        workspace = os.environ.get("GROK_WORKSPACE_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
        layered = load_policy(home, workspace)
        state_dir = paths.state_dir(env, home)
        probe = state_dir
        while not os.path.isdir(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
        found = selfcheck.problems(
            (sys.version_info[0], sys.version_info[1]), layered.problems, state_dir,
            os.access(probe, os.W_OK),
            any(os.path.exists(flag) for flag in paths.stop_flag_paths(env, home)))
        return selfcheck.report(found)
    except Exception as exc:  # process edge: report the crash instead of failing silently
        return selfcheck.report([f"self-check crashed ({type(exc).__name__})"])
