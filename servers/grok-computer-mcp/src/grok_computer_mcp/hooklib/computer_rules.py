"""Risk grading for calls to the ``computer`` facade (goal.md §7.3).

Boundary: pure. ``hooklib.guard.decide`` calls ``grade`` only after it has established that the
caller is the computer subagent and the policy loaded cleanly. Every R3 rule here is enforced
again, fail-closed, by grok-computer-mcp; this layer adds the user's confirmation prompts.

Grading order: R0 read-only -> allow; R3 -> deny; unknown target -> allow only for allow-listed
apps, else ask; R2 -> ask; R1 first GUI action in an app this session -> ask; otherwise allow.
"""

from __future__ import annotations

from typing import FrozenSet, List, Mapping, Optional, Tuple

from . import keys, matching
from .defaults import Policy
from .state import TEXT_ENTRY_ROLES, any_secure, as_mapping, window_context
from .targets import TargetInfo, resolve_target
from .verdict import Decision, allow, ask, deny

COMPUTER_TOOLS = (
    "observe", "click", "type_text", "press_keys", "scroll", "drag", "apps", "wait_for", "locate",
)
READ_ONLY_TOOLS = ("observe", "wait_for", "locate", "status")
READ_ONLY_APP_ACTIONS = ("list", "windows")
# Labels of close buttons; clicking one on a window with unsaved work is R2.
CLOSE_LABELS = ("close", "close window", "close tab", "关闭", "关闭窗口")


def _describe(target: TargetInfo, app: str) -> str:
    """Human-readable target description for prompts."""
    where = f" in {app}" if app else ""
    return f"{target.role or 'element'} \"{target.label or '(unlabeled)'}\"{where}"


def explicit_app(tool: str, inp: Mapping[str, object]) -> str:
    """App named directly in the arguments (``observe(app=..)``, ``apps(name=..)``).

    Args:
        tool: Facade tool name.
        inp: Call arguments.

    Returns:
        The app name, or "".
    """
    for key in ("app", "name") if tool == "apps" else ("app",):
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def targets_of(tool: str, inp: Mapping[str, object],
               state: Optional[Mapping[str, object]]) -> Tuple[List[Optional[TargetInfo]], bool]:
    """Resolve every element a call touches.

    Args:
        tool: Facade tool name.
        inp: Call arguments.
        state: Fresh state or None.

    Returns:
        ``(targets, element_based)``. ``element_based`` is False for calls without an element
        target (``apps`` launch/focus, ``scroll`` with no target); ``targets`` holds None for every
        target that could not be resolved.
    """
    if tool == "drag":
        obs = inp.get("observation_id")
        obs_id = obs if isinstance(obs, str) else ""
        ends = [as_mapping(inp.get("from")), as_mapping(inp.get("to"))]
        return [resolve_target(tool, end, state, obs_id) if end else None for end in ends], True
    if tool == "apps":
        return [], False
    if tool == "scroll" and not any(k in inp for k in ("ref", "mark", "point")):
        return [], False
    return [resolve_target(tool, inp, state)], True


def grade(tool: str, inp: Mapping[str, object], policy: Policy,
          state: Optional[Mapping[str, object]], approved: FrozenSet[str]) -> Decision:
    """Grade one facade call.

    Args:
        tool: Facade tool name (one of ``COMPUTER_TOOLS``).
        inp: Call arguments.
        policy: Effective policy.
        state: Fresh facade state or None.
        approved: Normalized apps the user already approved this session.

    Returns:
        The decision.
    """
    named = explicit_app(tool, inp)
    state_app, title, unsaved = window_context(state)
    if tool in READ_ONLY_TOOLS or (tool == "apps" and inp.get("action") in READ_ONLY_APP_ACTIONS):
        pattern = matching.denied_app_pattern(named, "", policy.deny_apps) if named else None
        return deny(f"App '{named}' is on the deny list ({pattern}).") if pattern else allow()

    targets, element_based = targets_of(tool, inp, state)
    known = [t for t in targets if t is not None]
    app = next((t.app for t in known if t.app), "") or named or state_app
    same_window = bool(app) and matching.normalize(app) == matching.normalize(state_app)
    for name in sorted({app, named} - {""}):
        pattern = matching.denied_app_pattern(name, title if same_window else "", policy.deny_apps)
        if pattern:
            return deny(f"Target app '{name}' is on the deny list ({pattern}).")
    r3 = r3_input(tool, inp, policy, known, state)
    if r3 is not None:
        return r3
    if tool == "scroll" and not element_based:
        return allow()
    if element_based and (not targets or any(t is None for t in targets)):
        if app and matching.allowed_app(app, policy.allow_apps):
            return allow()
        where = f" in '{app}'" if app else ""
        return ask(f"Cannot verify the target of computer__{tool}{where}. Continue?")
    r2 = r2_checks(tool, inp, policy, known, app=app, unsaved=unsaved)
    if r2 is not None:
        return r2
    if app and not matching.allowed_app(app, policy.allow_apps) \
            and matching.normalize(app) not in approved:
        return ask(f"First GUI action in '{app}' this session. Allow?")
    return allow()


def r3_input(tool: str, inp: Mapping[str, object], policy: Policy, known: List[TargetInfo],
             state: Optional[Mapping[str, object]]) -> Optional[Decision]:
    """Secure fields, credential labels and dangerous keys (R3).

    Args:
        tool: Facade tool name.
        inp: Call arguments.
        policy: Effective policy.
        known: Targets that could be resolved.
        state: Fresh state or None.

    Returns:
        A deny (or an ask for keys that cannot be parsed), or None when no R3 rule applies.
    """
    if tool == "type_text":
        credential = matching.compile_words(policy.credential_words)
        for target in known:
            if target.secure:
                return deny("Refusing to type into a secure (password) field. Ask the user to "
                            "enter it.")
            word = matching.find_word(target.label, credential)
            if word and target.role in TEXT_ENTRY_ROLES:
                return deny(f"Refusing to type into a credential field (\"{target.label}\" "
                            f"matches '{word}').")
        if not known and not isinstance(inp.get("ref"), str) and any_secure(state):
            return deny("Focus is unknown and this window has a secure field; pass the ref of the "
                        "field to type into.")
    if tool == "press_keys":
        chords = keys.chords_of(inp.get("keys"))
        if chords is None or len(chords) > keys.MAX_CHORDS_PER_CALL:
            return ask("Cannot parse the keys of computer__press_keys. Continue?")
        for chord in chords:
            if not keys.canonical_chords(chord):
                return ask(f"Cannot parse key combination '{chord}'. Continue?")
            entry = keys.dangerous_chord(chord, policy.dangerous_keys)
            if entry:
                return deny(f"Key combination '{chord}' is not allowed ({entry}).")
    return None


def r2_checks(tool: str, inp: Mapping[str, object], policy: Policy, known: List[TargetInfo],
              *, app: str, unsaved: bool) -> Optional[Decision]:
    """Risky labels, Enter in a form, closing a window with unsaved work (R2).

    Args:
        tool: Facade tool name.
        inp: Call arguments.
        policy: Effective policy.
        known: Resolved targets.
        app: Target app ("" when unknown).
        unsaved: Whether the target window reports unsaved changes.

    Returns:
        An ask, or None when nothing risky was found.
    """
    risky = matching.compile_words(policy.risky_words)
    for target in known:
        word = matching.find_word(target.label, risky)
        if word:
            return ask(f"About to {tool} on {_describe(target, app)} ('{word}').")
    if tool == "press_keys":
        chords = keys.chords_of(inp.get("keys")) or []
        focused = known[0] if known else None
        if focused and focused.in_form and any(keys.presses_enter(c) for c in chords):
            return ask(f"Pressing Enter may submit the form ({_describe(focused, app)}).")
        if unsaved and any(keys.closes_window(c) for c in chords):
            return ask(f"Closing a window with unsaved changes in {app or 'the app'}.")
    if tool == "click" and unsaved:
        for target in known:
            if matching.normalize(target.label) in CLOSE_LABELS:
                return ask(f"Closing a window with unsaved changes in {app or 'the app'}.")
    return None
