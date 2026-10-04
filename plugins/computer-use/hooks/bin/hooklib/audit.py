"""PostToolUse / PostToolUseFailure audit records (goal.md §7.5, §7.6).

Boundary: pure record building; ``hooklib.entry.audit_main`` does the file I/O and always exits 0
(the audit never blocks). Privacy rules (AGENTS.md):

* typed text is recorded as length + first 2 characters only (``hooklib.redact``);
* tool results are never copied verbatim, only structured metadata (ok, code, observation id).

The audit log doubles as the source for two other hooks: the verify gate reads which GUI calls
ran after the last observation, and the R1 bookkeeping records which apps the user approved.
"""

from __future__ import annotations

import json
from typing import Dict, List, Mapping, Optional, cast

from .browser import READ_ONLY_PREFIXES as BROWSER_READ_ONLY_PREFIXES
from .browser import READ_ONLY_TOOLS as BROWSER_READ_ONLY_TOOLS
from .computer_rules import READ_ONLY_APP_ACTIONS
from .computer_rules import READ_ONLY_TOOLS as COMPUTER_READ_ONLY_TOOLS
from .redact import redact
from .state import as_mapping, text

AUDIT_SCHEMA_VERSION = 1
# Keys whose string values are user-typed or page-bound text.
# Fields copied from a facade result's structuredContent into the audit record.
RESULT_FIELDS = ("ok", "code", "observation_id", "app", "delivery", "retryable", "mode")
# Observation tools whose call counts as "looked at the screen" for the verify gate.
OBSERVE_TOOLS = ("computer__observe", "computer__wait_for", "browser__browser_snapshot",
                 "browser__browser_take_screenshot")

def structured_result(result: object) -> Dict[str, object]:
    """Find the facade's structuredContent inside a hook ``toolResult``.

    Args:
        result: ``toolResult`` (an object, or the model-facing text for large results).

    Returns:
        The structured content, or ``{}`` when it cannot be found.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return {}
    item = as_mapping(result)
    for key in ("structuredContent", "structured_content"):
        nested = as_mapping(item.get(key))
        if nested:
            return nested
    return item if "ok" in item else {}


def result_meta(result: object) -> Dict[str, object]:
    """Summarize a tool result without copying screen content.

    Args:
        result: ``toolResult`` from the payload.

    Returns:
        Selected structured fields, or the result size when nothing structured is present.
    """
    structured = structured_result(result)
    if structured:
        meta = {k: structured[k] for k in RESULT_FIELDS if k in structured}
        changes = structured.get("changes")
        if isinstance(changes, list):
            meta["changes"] = len(cast(List[object], changes))
        return meta
    size = len(result) if isinstance(result, str) else len(json.dumps(result, default=str))
    return {"size": size}


def is_action(tool_name: str, inp: Mapping[str, object]) -> bool:
    """Whether a GUI call changes the screen (anything but an observation or a read).

    Args:
        tool_name: Qualified ``server__tool`` name.
        inp: Call arguments.

    Returns:
        True for actions, False for read-only calls.
    """
    server, _, tool = tool_name.partition("__")
    if server == "computer":
        if tool == "apps":
            return inp.get("action") not in READ_ONLY_APP_ACTIONS
        return tool not in COMPUTER_READ_ONLY_TOOLS
    if server == "browser":
        return not (tool in BROWSER_READ_ONLY_TOOLS or tool.startswith(BROWSER_READ_ONLY_PREFIXES))
    return False


def build_record(event: Mapping[str, object], now: float,
                 inp: Mapping[str, object]) -> Dict[str, object]:
    """Build one audit line for a finished GUI tool call.

    Args:
        event: Decoded PostToolUse/PostToolUseFailure payload.
        now: Current time.
        inp: Unwrapped call arguments.

    Returns:
        The JSON-serializable record.
    """
    tool = text(event.get("toolName"))
    names = (text(event.get("hookEventName")), text(event.get("hook_event_name")))
    failed = any("failure" in n.casefold() for n in names) or (
        "error" in event and "toolResult" not in event)
    record: Dict[str, object] = {
        "v": AUDIT_SCHEMA_VERSION,
        "ts": now,
        "event": "tool_failure" if failed else "tool",
        "session": text(event.get("sessionId")),
        "subagent": text(event.get("subagentType")),
        "tool": tool,
        "input": redact(dict(inp)),
        "action": is_action(tool, inp),
        "observe": tool in OBSERVE_TOOLS,
    }
    if failed:
        record["error_len"] = len(text(event.get("error")))
    else:
        record["result"] = result_meta(event.get("toolResult"))
    return record


def approved_app(event: Mapping[str, object], inp: Mapping[str, object],
                 state: Optional[Mapping[str, object]]) -> str:
    """App to mark as approved (R1) after a facade action actually ran.

    Args:
        event: Decoded PostToolUse payload (successful runs only).
        inp: Unwrapped call arguments.
        state: Fresh facade state or None.

    Returns:
        The app name, or "" when the call was not a facade action or the app is unknown.
    """
    tool = text(event.get("toolName"))
    if not tool.startswith("computer__") or not is_action(tool, inp):
        return ""
    app = text(structured_result(event.get("toolResult")).get("app"))
    if not app and tool == "computer__apps":
        app = text(inp.get("name"))
    if not app and state is not None:
        app = text(state.get("app"))
    return app.strip()


def merge_approved(raw: object, app: str) -> Dict[str, object]:
    """Add ``app`` to the asked-apps state.

    Args:
        raw: Decoded existing ``asked_apps_<session>.json`` or None.
        app: App the user approved.

    Returns:
        The new state object ``{"approved": [...]}``.
    """
    current = as_mapping(raw).get("approved")
    names: List[str] = []
    if isinstance(current, list):
        names = [a for a in cast(List[object], current) if isinstance(a, str)]
    if app.casefold() not in (n.casefold() for n in names):
        names.append(app)
    return {"approved": names}
