"""The guard's decision value and the subagent report contract.

Boundary: pure data, shared by ``guard``, ``browser`` and ``verify_gate``. The report format
``STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT`` is also written into ``agents/computer.md`` and
``skills/computer-use/SKILL.md``; ``tests/test_contracts.py`` keeps the three in step (AGENTS.md).
"""

from __future__ import annotations

import re
from typing import List, NamedTuple, Optional, Tuple

ALLOW = "allow"
DENY = "deny"
ASK = "ask"
DEFER = "defer"

REPORT_SECTIONS = ("STATUS", "SUMMARY", "EVIDENCE", "BLOCKERS", "NEXT")
REPORT_STATUSES = ("success", "partial", "blocked", "failed")
# The parent agent receives at most 2 KB of report text plus screenshot paths (goal.md G2, §5.9).
REPORT_MAX_BYTES = 2048
_STATUS_LINE = re.compile(r"^[ \t]*STATUS:[ \t]*(success|partial|blocked|failed)\b", re.I | re.M)
# Absolute file paths (POSIX or Windows) are excluded from the size budget.
_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:\\|/)[^\s`'\"<>|]+")


class Decision(NamedTuple):
    """A PreToolUse outcome. ``reason`` is shown to the user (ask) or the model (deny)."""

    decision: str
    reason: str = ""


def allow() -> Decision:
    """Return an allow decision (``allow`` only means "not blocked", never auto-approve)."""
    return Decision(ALLOW)


def defer() -> Decision:
    """Return a defer decision: no opinion, the host's normal permission flow applies."""
    return Decision(DEFER)


def deny(reason: str) -> Decision:
    """Return a deny decision with the reason the model will read."""
    return Decision(DENY, reason)


def ask(reason: str) -> Decision:
    """Return an ask decision with the reason shown in the host's confirmation prompt."""
    return Decision(ASK, reason)


def parse_report(message: str) -> Tuple[Optional[str], List[str]]:
    """Check a subagent's final message against the report contract.

    Args:
        message: ``lastAssistantMessage`` from the SubagentStop payload.

    Returns:
        ``(status, missing_sections)``: the lower-cased STATUS value (None if absent or invalid)
        and the section labels that do not start a line.
    """
    match = _STATUS_LINE.search(message)
    status = match.group(1).lower() if match else None
    missing = [
        section for section in REPORT_SECTIONS[1:]
        if not re.search(rf"^[ \t]*{section}:", message, re.M)
    ]
    if status is None:
        missing.insert(0, "STATUS")
    return status, missing


def report_text_bytes(message: str) -> int:
    """Size of the report in UTF-8 bytes, not counting absolute file paths.

    Args:
        message: The report text.

    Returns:
        Byte count used for the 2 KB budget.
    """
    return len(_PATH_TOKEN.sub("", message).encode("utf-8"))
