"""PreToolUse guard: risk-grades GUI tool calls (goal.md §7.3, §7.4).

The host fails OPEN on any hook error, so this is the interactive-confirmation layer, not the
only line of defence: every R3 rule is enforced again, fail-closed, inside grok-computer-mcp.
What this layer adds is the user's confirmation prompt (``ask``) for R2/R1 and an early, clearly
worded ``deny``.

Order of checks:

1. tools outside the GUI servers -> ``defer`` (no opinion);
2. not the computer subagent -> ``deny`` (screenshots would flood the main context);
3. raw ``cua__*`` -> ``deny`` (Phase 2+: only the facade may drive Cua Driver);
4. a malformed policy file -> ``ask`` (the guard cannot tell what the user intended);
5. unreadable arguments -> ``ask``;
6. browser tools -> ``hooklib.browser``; facade tools -> ``hooklib.computer_rules``.

``GROK_COMPUTER_ASK_AS_DENY=1`` turns every ask into a logged deny for unattended runs, where a
client in always-approve mode would otherwise approve the ask.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, cast

from . import browser, computer_rules, matching
from .policy import LayerResult
from .state import as_mapping
from .verdict import ASK, DENY, Decision, ask, defer, deny

MAIN_AGENT_REASON = (
    "GUI tools may only be used by the computer subagent. Delegate with "
    "spawn_subagent(subagent_type='computer-use:computer') following the computer-use skill."
)
CUA_REASON = (
    "Raw Cua Driver tools bypass the computer facade's safety checks. Use the computer__* tools."
)
ASK_AS_DENY_PREFIX = "[ask->deny in unattended mode] "


def unwrap_input(raw: object) -> Optional[Dict[str, object]]:
    """Return the tool arguments, unwrapping a ``use_tool`` envelope if the host passes one.

    Args:
        raw: ``toolInput`` from the payload.

    Returns:
        The argument object, or None when the arguments cannot be read (not an object, or an
        unresolved ``tool_input_file``/``file`` reference): the guard then cannot tell the target.
    """
    inp = as_mapping(raw)
    if not isinstance(raw, dict):
        return None
    inner = as_mapping(inp.get("tool_input"))
    if inner and "tool_name" in inp:
        return inner
    if "tool_input_file" in inp or ("file" in inp and len(inp) == 1):
        return None
    return inp


def decide(event: Mapping[str, object], layered: LayerResult,
           state: Optional[Mapping[str, object]], approved_apps: FrozenSet[str]) -> Decision:
    """Decide one PreToolUse event.

    Args:
        event: Decoded hook payload.
        layered: Effective policy plus problems from ``policy.build_policy``.
        state: Fresh facade state (``state.pick_fresh_state``) or None.
        approved_apps: Normalized names of apps the user approved this session (R1).

    Returns:
        The decision. Malformed payload fields never raise; they become ``ask``.
    """
    tool_name = event.get("toolName")
    if not isinstance(tool_name, str) or "__" not in tool_name:
        return ask("The computer-use guard could not identify the tool being called.")
    server, _, tool = tool_name.partition("__")
    policy = layered.policy
    if server not in policy.servers:
        return defer()
    sub = event.get("subagentType")
    if policy.require_subagent and not (isinstance(sub, str) and sub in policy.subagent_types):
        return deny(MAIN_AGENT_REASON)
    if server == "cua":
        return deny(CUA_REASON)
    if layered.problems:
        listed = "; ".join(layered.problems)
        return ask(f"The computer-use policy has problems ({listed}). GUI actions need "
                   "confirmation until it is fixed.")
    inp = unwrap_input(event.get("toolInput"))
    if inp is None:
        return ask(f"Cannot read the arguments of {tool_name}. Continue?")
    if server == "browser":
        return browser.decide(tool, inp, policy)
    if tool not in computer_rules.COMPUTER_TOOLS:
        return ask(f"Unrecognized computer tool {tool_name}. Continue?")
    return computer_rules.grade(tool, inp, policy, state, approved_apps)


def finalize(decision: Decision, env: Mapping[str, str]) -> Decision:
    """Apply ``GROK_COMPUTER_ASK_AS_DENY`` (unattended runs turn every ask into a deny).

    Args:
        decision: The decision from ``decide``.
        env: Process environment.

    Returns:
        The decision to emit.
    """
    if decision.decision == ASK and env.get("GROK_COMPUTER_ASK_AS_DENY") == "1":
        return Decision(DENY, ASK_AS_DENY_PREFIX + decision.reason)
    return decision


def to_output(decision: Decision) -> Dict[str, object]:
    """Render a decision in the host's PreToolUse output format.

    Args:
        decision: Final decision.

    Returns:
        ``{"decision": ..., "reason": ...}`` (reason omitted when empty).
    """
    out: Dict[str, object] = {"decision": decision.decision}
    if decision.reason:
        out["reason"] = decision.reason
    return out


def approved_from(raw: object) -> FrozenSet[str]:
    """Read the asked-apps state written by the audit hook.

    Args:
        raw: Decoded ``asked_apps_<session>.json`` or None.

    Returns:
        Normalized names of apps already approved this session.
    """
    apps = as_mapping(raw).get("approved")
    if not isinstance(apps, list):
        return frozenset()
    names: Sequence[object] = cast(List[object], apps)
    return frozenset(matching.normalize(a) for a in names if isinstance(a, str))
