"""SubagentStop gate for the computer subagent (goal.md §7.5).

Boundary: pure decision; ``hooklib.entry.verify_gate_main`` reads the payload, the session audit
log and the facade state. The host fails open, so a crash simply lets the subagent stop.

The gate blocks (once per turn, guarded by ``stopHookActive``) when:

* the final message does not follow ``STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT``;
* the report text exceeds 2 KB (screenshot paths excluded; goal.md G2);
* ``STATUS: success`` is claimed but the last GUI action came after the last observation.
"""

from __future__ import annotations

from typing import List, Mapping, Optional, Sequence

from .state import as_mapping, number
from .verdict import REPORT_MAX_BYTES, REPORT_SECTIONS, parse_report, report_text_bytes

FORMAT_REASON = (
    "End with the standard report, one section per line: " + " / ".join(REPORT_SECTIONS)
    + ". STATUS is one of success, partial, blocked, failed."
)
SIZE_REASON = (
    "Your report is longer than 2 KB. Shorten SUMMARY and EVIDENCE and reference screenshot "
    "paths instead of pasting screen content."
)
VERIFY_REASON = (
    "You reported success without observing after your last action. Call observe (or "
    "browser_snapshot), check every success criterion against it, then report again."
)


def last_gui_activity(records: Sequence[object]) -> Optional[bool]:
    """Whether the most recent GUI call in the session was an action.

    Args:
        records: Decoded audit-log lines for this session, oldest first.

    Returns:
        True if the last GUI action is newer than the last observation, False if an
        observation came after it, None when the log holds no GUI activity.
    """
    last_action = float("-inf")
    last_observe = float("-inf")
    for raw in records:
        rec = as_mapping(raw)
        ts = number(rec.get("ts"))
        if ts is None or rec.get("event") not in ("tool", "tool_failure"):
            continue
        if rec.get("observe") is True:
            last_observe = max(last_observe, ts)
        elif rec.get("action") is True and rec.get("event") == "tool":
            last_action = max(last_action, ts)
    if last_action == float("-inf") and last_observe == float("-inf"):
        return None
    return last_action > last_observe


def state_says_unverified(state: Optional[Mapping[str, object]]) -> bool:
    """Facade-side fallback: last action newer than the last explicit observation.

    Args:
        state: Latest facade state, any age.

    Returns:
        True when the state shows an action after the last observation.
    """
    if state is None:
        return False
    acted = number(state.get("last_action_at"))
    observed = number(state.get("last_observe_at"))
    return acted is not None and (observed is None or acted > observed)


def decide(event: Mapping[str, object], records: Sequence[object],
           state: Optional[Mapping[str, object]]) -> Optional[str]:
    """Decide whether to block the subagent from stopping.

    Args:
        event: Decoded SubagentStop payload.
        records: This session's audit records (may be empty).
        state: Latest facade state or None.

    Returns:
        The block reason, or None to let the subagent stop.
    """
    if event.get("stopHookActive") is True:
        return None
    message = event.get("lastAssistantMessage")
    text = message if isinstance(message, str) else ""
    status, missing = parse_report(text)
    problems: List[str] = []
    if missing:
        return FORMAT_REASON
    if report_text_bytes(text) > REPORT_MAX_BYTES:
        problems.append(SIZE_REASON)
    if status == "success":
        from_log = last_gui_activity(records)
        unverified = from_log if from_log is not None else state_says_unverified(state)
        if unverified:
            problems.append(VERIFY_REASON)
    return " ".join(problems) if problems else None
