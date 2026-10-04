"""Guard rules for the ``browser`` server (Playwright MCP, pinned in ``.mcp.json``).

Boundary: pure. Playwright runs with ``--isolated`` (no user cookies or saved logins), so by
default page actions are allowed and only risky element descriptions ask (goal.md §7.4). On top
of that, three Playwright capabilities would widen what the subagent can reach and are gated:

* ``filename`` arguments write files relative to the workspace root; the subagent must not get
  file writes (AGENTS.md: never give it edit tools), so any ``filename`` is denied;
* ``browser_run_code_unsafe`` runs arbitrary Playwright code: denied; ``browser_evaluate`` runs
  page JavaScript: ask;
* ``browser_file_upload`` / ``browser_drop`` with ``paths`` read local files into the page: ask.

Credential fields are R3 here too: typing into an element described as a password/OTP/card field
is denied, matching the facade's secure-field rule.
"""

from __future__ import annotations

from typing import List, Mapping, Optional, Pattern, Tuple, cast

from . import matching
from .defaults import Policy
from .verdict import Decision, allow, ask, deny

# Tools that only read the page. Anything not listed here or handled below asks.
READ_ONLY_TOOLS = (
    "browser_snapshot", "browser_take_screenshot", "browser_console_messages",
    "browser_network_requests", "browser_network_request", "browser_find", "browser_wait_for",
    "browser_get_config", "browser_generate_locator", "browser_highlight",
    "browser_hide_highlight", "browser_annotate",
)
READ_ONLY_PREFIXES = ("browser_verify_",)
# Page-level actions with no element target; safe in an isolated profile. (Accepting a dialog
# is handled before this list is consulted: it may confirm an action, so it asks.)
UNTARGETED_TOOLS = (
    "browser_navigate_back", "browser_press_key", "browser_resize", "browser_tabs",
    "browser_close", "browser_emulate_media", "browser_resume", "browser_handle_dialog",
)
# Tools whose `element`/`startElement`/`endElement` describe what they act on.
ELEMENT_TOOLS = {
    "browser_click": ("element",),
    "browser_hover": ("element",),
    "browser_select_option": ("element",),
    "browser_type": ("element",),
    "browser_drop": ("element",),
    "browser_drag": ("startElement", "endElement"),
}
DENIED_TOOLS = ("browser_run_code_unsafe",)
# URL schemes that are fine in an isolated browser; file: and javascript: read local data or run
# code in the page and are denied, anything else asks.
SAFE_SCHEMES = ("http", "https", "about")
DENIED_SCHEMES = ("file", "javascript")
# How much of a script or path list to show in a confirmation prompt.
PROMPT_EXCERPT_CHARS = 160


def _text(value: object) -> str:
    """Return ``value`` if it is a string, else ""."""
    return value if isinstance(value, str) else ""


def _excerpt(text: str) -> str:
    """Shorten text for a confirmation prompt."""
    text = " ".join(text.split())
    if len(text) <= PROMPT_EXCERPT_CHARS:
        return text
    return text[: PROMPT_EXCERPT_CHARS - 1] + "…"


def _url_scheme(url: str) -> str:
    """Return the lower-cased URL scheme, or "" when there is none."""
    head, sep, _ = url.strip().partition(":")
    return head.casefold() if sep and head.isalpha() else ""


def _form_fields(value: object) -> List[Mapping[str, object]]:
    """Return the ``fields`` list of ``browser_fill_form`` as mappings."""
    if not isinstance(value, list):
        return []
    out: List[Mapping[str, object]] = []
    for item in cast(List[object], value):
        if isinstance(item, dict):
            out.append({str(k): v for k, v in cast(Mapping[object, object], item).items()})
    return out


def _describe(tool: str, inp: Mapping[str, object]) -> Tuple[List[str], bool]:
    """Collect the element descriptions a call carries.

    Args:
        tool: Playwright tool name.
        inp: Tool arguments.

    Returns:
        ``(descriptions, missing)`` where ``missing`` is True when an element-targeting tool
        omitted a description, so the guard cannot tell what it acts on.
    """
    if tool == "browser_fill_form":
        fields = _form_fields(inp.get("fields"))
        names = [_text(f.get("name")) for f in fields]
        return names, not fields or not all(names)
    keys = ELEMENT_TOOLS.get(tool, ())
    descs = [_text(inp.get(k)) for k in keys]
    return [d for d in descs if d], any(not d for d in descs)


def decide(tool: str, inp: Mapping[str, object], policy: Policy) -> Decision:
    """Classify one Playwright MCP call.

    Args:
        tool: Tool name without the ``browser__`` prefix.
        inp: Tool arguments.
        policy: Effective policy.

    Returns:
        The guard decision for the call.
    """
    if _text(inp.get("filename")):
        return deny(f"Browser file writes are disabled for the computer subagent: call {tool} "
                    "without 'filename'.")
    if tool in DENIED_TOOLS:
        return deny(f"{tool} runs arbitrary code and is disabled for the computer subagent.")
    if tool in READ_ONLY_TOOLS or tool.startswith(READ_ONLY_PREFIXES):
        return allow()
    if tool == "browser_navigate":
        return _navigate(_text(inp.get("url")))
    if tool == "browser_evaluate":
        return ask(f"Run JavaScript in the page: {_excerpt(_text(inp.get('function')))}")
    paths = inp.get("paths")
    if tool in ("browser_file_upload", "browser_drop") and isinstance(paths, list) and paths:
        shown = ", ".join(_text(p) for p in cast(List[object], paths))
        return ask(f"Send local files to the web page: {_excerpt(shown)}")
    if tool == "browser_handle_dialog" and inp.get("accept") is True:
        return ask("Accept the page's dialog (it may confirm an action).")
    if tool in UNTARGETED_TOOLS:
        if policy.allow_isolated_browser:
            return allow()
        return ask(f"Browser action {tool}. Continue?")
    if tool not in ELEMENT_TOOLS and tool != "browser_fill_form":
        return ask(f"Unrecognized browser tool {tool}. Continue?")
    return _element_action(tool, inp, policy)


def _navigate(url: str) -> Decision:
    """Decide ``browser_navigate`` by URL scheme."""
    scheme = _url_scheme(url)
    if scheme in DENIED_SCHEMES:
        return deny(f"Navigating to {scheme}: URLs is disabled for the computer subagent.")
    if scheme not in SAFE_SCHEMES:
        return ask(f"Open {_excerpt(url or '<empty url>')} in the browser?")
    return allow()


def _element_action(tool: str, inp: Mapping[str, object], policy: Policy) -> Decision:
    """Decide an element-targeting browser call (click, type, fill_form ...)."""
    descriptions, missing = _describe(tool, inp)
    credential: Optional[Pattern[str]] = matching.compile_words(policy.credential_words)
    if tool in ("browser_type", "browser_fill_form"):
        for desc in descriptions:
            word = matching.find_word(desc, credential)
            if word:
                return deny(f"Refusing to type into a credential field (\"{_excerpt(desc)}\" "
                            f"matches '{word}'). Ask the user to enter it.")
    if missing:
        return ask(f"Cannot tell which element {tool} acts on (no element description). Continue?")
    risky = matching.compile_words(policy.risky_words)
    for desc in descriptions:
        word = matching.find_word(desc, risky)
        if word:
            return ask(f"About to {tool} on \"{_excerpt(desc)}\" in the browser ('{word}').")
    if policy.allow_isolated_browser:
        return allow()
    shown = _excerpt(", ".join(descriptions))
    return ask(f"Browser action {tool} on \"{shown}\". Continue?")
