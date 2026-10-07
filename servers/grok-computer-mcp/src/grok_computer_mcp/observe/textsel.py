"""Choose and mask the static text lines of an observation (refactor FR-3, FR-12).

Boundary: pure. Selection does not render and does not touch a backend. Credential masking uses
the same word list as ``SafetyRules.credential_word``; ordinary prose that merely mentions a
word (``Token count: 3``) stays visible because it is neither a credential label nor a long token.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..hooklib.matching import normalize
from ..limits import CREDENTIAL_TOKEN_MIN_CHARS, TEXT_LINE_MAX_CHARS
from ..safety.policy import SafetyRules
from .elements import ObservedElement
from .lines import MAX_INDENT_LEVELS, text_line
from .roles import CONTAINER_ROLES, TEXT_DATA_ROLES
from .tree import clip, subtree

_LONG_RUN = re.compile(rf"\S{{{CREDENTIAL_TOKEN_MIN_CHARS},}}")
_EDGE = re.compile(r"^[\s:.\-'\"()]+|[\s:.\-'\"()]+$")


def text_body(el: ObservedElement, limit: int = TEXT_LINE_MAX_CHARS) -> str:
    """Visible string of a static element: its value when set, otherwise its label.

    Args:
        el: The element.
        limit: Maximum characters after cleaning.

    Returns:
        The clipped string ("" when both are empty).
    """
    raw = el.value if el.value and el.value.strip() else el.label
    return clip(raw, limit)


def _pure_credential(text: str, rules: SafetyRules) -> bool:
    """Whether ``text`` is only a credential phrase, not a sentence that mentions one."""
    hit = rules.credential_word(text)
    if not hit:
        return False
    rest = normalize(text).replace(hit, "", 1)
    return _EDGE.sub("", rest) == ""


def _long_token(text: str) -> bool:
    """Whether ``text`` contains a run of at least ``CREDENTIAL_TOKEN_MIN_CHARS`` non-spaces."""
    return _LONG_RUN.search(text) is not None


def _entire_token(text: str) -> bool:
    """Whether ``text`` itself is one long non-space run."""
    return _LONG_RUN.fullmatch(text.strip()) is not None


def text_is_hidden(el: ObservedElement, prev: ObservedElement | None,
                   parent: ObservedElement | None, rules: SafetyRules) -> bool:
    """Whether a static line must be rendered as ``text (hidden: credential)``.

    Args:
        el: The text element.
        prev: The previous element in document order, if any.
        parent: The parent element, if it is in the same tree.
        rules: Safety rules (credential words).

    Returns:
        True for a credential label, a long token on such a label, or a long token that
        follows one. ``Token count: 3`` is False: it mentions ``token`` but is prose.
    """
    body = text_body(el)
    if _pure_credential(el.label, rules) or _pure_credential(body, rules):
        return True
    if rules.credential_word(el.label) is not None and (_long_token(body) or _long_token(el.label)):
        return True
    follows = False
    if prev is not None and rules.credential_word(prev.label) is not None:
        follows = True
    if parent is not None and rules.credential_word(parent.label) is not None:
        follows = True
    return follows and _entire_token(body)


def mark_credential_text(elements: Sequence[ObservedElement],
                         rules: SafetyRules) -> list[ObservedElement]:
    """Copy ``elements`` with ``hidden_text`` set on secret static lines.

    Args:
        elements: One observation, document order.
        rules: Safety rules.

    Returns:
        A new list. Non-text elements are unchanged. Not written to the state file.
    """
    by_ref = {el.ref: el for el in elements}
    out: list[ObservedElement] = []
    prev: ObservedElement | None = None
    for el in elements:
        hidden = False
        if el.role in TEXT_DATA_ROLES:
            parent = by_ref.get(el.parent_ref) if el.parent_ref else None
            hidden = text_is_hidden(el, prev, parent, rules)
        out.append(replace(el, hidden_text=True) if hidden else el)
        prev = el
    return out


def _duplicate(el: ObservedElement, body: str, prev_body: str | None,
               elements: Sequence[ObservedElement], by_ref: dict[str, ObservedElement]) -> bool:
    """Skip a line that repeats its parent, a sibling control, or the previous line."""
    if prev_body is not None and body == prev_body:
        return True
    parent = by_ref.get(el.parent_ref) if el.parent_ref else None
    if parent is not None and body == parent.label:
        return True
    for other in elements:
        same_parent = other.parent_ref == el.parent_ref and other.ref != el.ref
        if same_parent and other.interactive and body == other.label:
            return True
    return False


def text_candidates(elements: Sequence[ObservedElement], *, scope_ref: str | None,
                    include_offscreen: bool) -> list[ObservedElement]:
    """Static lines eligible to show, in document order, before the byte budget.

    Args:
        elements: The observation (or the scoped subset).
        scope_ref: Keep only this subtree (None keeps ``elements``).
        include_offscreen: Keep off-screen lines. Default listings pass False; a ``root_ref``
            expansion is what shows them.

    Returns:
        Deduped text, heading, and progress elements with a non-empty body.
    """
    scoped = list(subtree(elements, scope_ref)) if scope_ref else list(elements)
    allowed = {el.ref for el in scoped}
    by_ref = {el.ref: el for el in elements}
    out: list[ObservedElement] = []
    prev_body: str | None = None
    for el in elements:
        if el.ref not in allowed or el.role not in TEXT_DATA_ROLES:
            continue
        if not include_offscreen and not el.visible:
            continue
        body = text_body(el)
        if not body or _duplicate(el, body, prev_body, elements, by_ref):
            continue
        out.append(el)
        prev_body = body
    return out


def _rank(el: ObservedElement, modal_refs: set[str],
          focus_refs: set[str]) -> tuple[int, int, int, int]:
    """Modal, then heading, then the focused container, then document order."""
    return (0 if el.ref in modal_refs else 1, 0 if el.role == "heading" else 1,
            0 if el.ref in focus_refs else 1, el.index)


def select_text_lines(elements: Sequence[ObservedElement], *, scope_ref: str | None,
                      focused_ref: str | None, budget_bytes: int, max_lines: int,
                      include_offscreen: bool = False) -> list[ObservedElement]:
    """Pick static lines that fit the text budget, highest priority first.

    Args:
        elements: Observation elements.
        scope_ref: Subtree to draw from (None uses all of ``elements``).
        focused_ref: Focused element, so its container wins over other text.
        budget_bytes: Byte cap for the text section (0 selects nothing).
        max_lines: Line cap.
        include_offscreen: Keep off-screen lines.

    Returns:
        The chosen elements in document order. Line cost assumes the deepest indent so the
        rendered section stays inside ``budget_bytes``.
    """
    if budget_bytes <= 0 or max_lines <= 0:
        return []
    pool = text_candidates(elements, scope_ref=scope_ref, include_offscreen=include_offscreen)
    modal_refs: set[str] = set()
    dialogs = [el for el in elements if el.role == "dialog" and el.visible]
    if dialogs:
        modal_refs = {el.ref for el in subtree(elements, dialogs[-1].ref)}
    focus_refs: set[str] = set()
    if focused_ref:
        by_ref = {el.ref: el for el in elements}
        cursor = by_ref.get(focused_ref)
        container: ObservedElement | None = None
        while cursor is not None and cursor.parent_ref:
            cursor = by_ref.get(cursor.parent_ref)
            if cursor is not None and cursor.role in CONTAINER_ROLES and cursor.label.strip():
                container = cursor
                break
        if container is not None:
            focus_refs = {el.ref for el in subtree(elements, container.ref)}
    chosen: list[ObservedElement] = []
    used = 0
    for el in sorted(pool, key=lambda item: _rank(item, modal_refs, focus_refs)):
        if len(chosen) >= max_lines:
            break
        line = text_line(text_body(el), MAX_INDENT_LEVELS, masked=el.hidden_text)
        cost = len(line.encode()) + 1
        if used + cost > budget_bytes:
            continue
        chosen.append(el)
        used += cost
    chosen.sort(key=lambda item: item.index)
    return chosen
