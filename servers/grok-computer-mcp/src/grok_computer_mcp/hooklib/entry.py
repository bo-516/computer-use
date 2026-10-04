"""Process entry points for the hooks: read stdin and files, call the pure deciders, write output.

The guard and audit entry points are here; the verify gate and self-check are in ``entry_stop``.

Boundary: this is the process edge, the one place ``except Exception`` is allowed, and each such
handler maps to a defined outcome (AGENTS.md): guard -> ``ask``; audit -> exit 0; verify gate ->
let the subagent stop; self-check -> report the crash and exit 1. The host fails open on hook
errors anyway, but an explicit outcome keeps the behaviour predictable and logged.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Mapping, Optional

from . import audit, guard, io, paths, policy, state
from .verdict import ALLOW, DEFER, ask


def user_home() -> str:
    """The user's home directory (``HOME`` wins so tests can isolate it)."""
    return os.environ.get("HOME") or os.path.expanduser("~")


def _workspace(event: Mapping[str, object]) -> Optional[str]:
    """Workspace root from the payload, else from the runner-injected environment."""
    root = event.get("workspaceRoot")
    if isinstance(root, str) and root:
        return root
    return os.environ.get("GROK_WORKSPACE_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")


def load_policy(home: str, workspace: Optional[str]) -> policy.LayerResult:
    """Read the user and project policy files and layer them over the defaults.

    Args:
        home: The user's home directory.
        workspace: Session workspace root, if known.

    Returns:
        The layering result; unreadable or invalid files are reported as problems.
    """
    user_path = paths.user_policy_path(home)
    project_path = paths.project_policy_path(workspace)
    layers: List[object] = []
    problems: List[str] = []
    for path in (user_path, project_path):
        if path is None:
            layers.append(None)
            continue
        status, value = io.read_json_file(path)
        if status == io.STATUS_ERROR:
            problems.append(str(value))
        layers.append(value if status == io.STATUS_OK else None)
    result = policy.build_policy(layers[0], layers[1], user_path, project_path or "")
    return policy.LayerResult(result.policy, tuple(problems) + result.problems, result.warnings)


def read_states(env: Mapping[str, str], home: str) -> List[object]:
    """Decode every candidate ``last_observation.json`` (missing or broken files become None)."""
    out: List[object] = []
    for directory in paths.state_dir_candidates(env, home):
        status, value = io.read_json_file(os.path.join(directory, paths.STATE_FILE))
        out.append(value if status == io.STATUS_OK else None)
    return out


def read_event() -> Dict[str, object]:
    """Read and decode the hook payload; raises ValueError/TypeError when it is not an object."""
    raw = io.read_stdin_json(sys.stdin)
    payload = state.as_mapping(raw)
    if not isinstance(raw, dict):
        raise TypeError("hook payload is not a JSON object")
    return payload


def guard_main() -> int:
    """PreToolUse entry point. Always prints a decision and exits 0.

    Returns:
        Process exit code (0).
    """
    env = os.environ
    home = user_home()
    try:
        event = read_event()
    except (ValueError, TypeError, OSError):
        io.emit(guard.to_output(guard.finalize(
            ask("The computer-use guard could not parse the hook payload."), env)))
        return 0
    try:
        layered = load_policy(home, _workspace(event))
        fresh = state.pick_fresh_state(read_states(env, home), time.time(),
                                       layered.policy.state_max_age_s)
        _, approved_raw = io.read_json_file(
            paths.asked_apps_path(env, home, event.get("sessionId")))
        decision = guard.decide(event, layered, fresh, guard.approved_from(approved_raw))
    except Exception as exc:  # process edge: the guard must still answer (ask, never allow)
        decision = ask(f"The computer-use guard failed ({type(exc).__name__}). Continue?")
    final = guard.finalize(decision, env)
    if final.decision not in (ALLOW, DEFER):
        io.append_jsonl(paths.audit_log_path(env, home, event.get("sessionId")), {
            "v": audit.AUDIT_SCHEMA_VERSION, "ts": time.time(), "event": "guard",
            "session": state.text(event.get("sessionId")),
            "subagent": state.text(event.get("subagentType")),
            "tool": state.text(event.get("toolName")), "decision": final.decision,
            "original": decision.decision, "reason": final.reason,
        })
    io.emit(guard.to_output(final))
    return 0


def audit_main() -> int:
    """PostToolUse/PostToolUseFailure entry point. Never blocks.

    Returns:
        Process exit code (always 0).
    """
    try:
        env = os.environ
        home = user_home()
        event = read_event()
        inp = guard.unwrap_input(event.get("toolInput")) or {}
        now = time.time()
        session = event.get("sessionId")
        record = audit.build_record(event, now, inp)
        io.append_jsonl(paths.audit_log_path(env, home, session), record)
        if record["event"] == "tool":
            fresh = state.pick_fresh_state(read_states(env, home), now,
                                           policy.DEFAULT_POLICY.state_max_age_s)
            app = audit.approved_app(event, inp, fresh)
            if app:
                asked = paths.asked_apps_path(env, home, session)
                _, existing = io.read_json_file(asked)
                io.write_json_atomic(asked, audit.merge_approved(existing, app))
    except Exception:  # process edge: auditing must never affect the tool call
        pass
    return 0
