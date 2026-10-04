"""SessionStart self-check (goal.md §5.4 "status ... SessionStart 自检").

Boundary: pure checks over values the entry point gathers (policy layering result, directory
access, stop flag). SessionStart output is ignored by the host, so a problem is surfaced the only
visible way: one stderr line and a non-zero exit, which the host shows as a failed hook. The
check writes nothing (AGENTS.md: hooks write only the audit log and asked-apps state).
"""

from __future__ import annotations

import sys
from typing import List, Sequence, Tuple

# The hooks' language floor (AGENTS.md). Older interpreters fail before reaching this module, so
# the check guards against a future bump of the floor without a matching doc change.
MIN_PYTHON = (3, 8)


def problems(python_version: Tuple[int, int], policy_problems: Sequence[str],
             state_dir: str, state_dir_writable: bool, stop_flag: bool) -> List[str]:
    """List the problems worth telling the user about at session start.

    Args:
        python_version: ``sys.version_info[:2]``.
        policy_problems: Problems from ``policy.build_policy``.
        state_dir: The facade/hooks state directory.
        state_dir_writable: Whether it (or its nearest existing parent) is writable.
        stop_flag: Whether the emergency stop file exists.

    Returns:
        Human-readable problems, most important first; empty when all is well.
    """
    found: List[str] = []
    if python_version < MIN_PYTHON:
        have = ".".join(str(part) for part in python_version)
        need = ".".join(str(part) for part in MIN_PYTHON)
        found.append(f"python3 {have} is too old; computer-use hooks need {need}+")
    for problem in policy_problems:
        found.append(f"policy: {problem} (GUI actions are denied until fixed)")
    if not state_dir_writable:
        found.append(f"state directory {state_dir} is not writable; the guard cannot see "
                     "observations")
    if stop_flag:
        found.append("emergency stop is active (run `grok-computer-mcp resume` to clear it)")
    return found


def report(found: Sequence[str]) -> int:
    """Print the first problem to stderr.

    Args:
        found: Result of ``problems``.

    Returns:
        The exit code: 0 when there is nothing to report, 1 otherwise.
    """
    if not found:
        return 0
    extra = f" (+{len(found) - 1} more)" if len(found) > 1 else ""
    sys.stderr.write(f"computer-use: {found[0]}{extra}\n")
    return 1
