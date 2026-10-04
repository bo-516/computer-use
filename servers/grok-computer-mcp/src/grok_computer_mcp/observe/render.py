"""Compact observation text: one line per element, within the 16 KB tool-text budget.

Boundary: pure (goal.md §5.5). Format::

    obs_7f3a  app=MyApp  window=4521 "Settings"  image=1280x800 (attached)
    e1  button      "Back"                     [12,40,60,24]
    e7  switch      "Dark mode"         off    [40,214,220,22]
    -- 23 more elements not listed; use observe(root_ref=...) to expand --

When elements do not fit the budget, visible ones win over off-screen ones and the footer names
the containers worth expanding with ``root_ref`` (goal.md §5.5 "按可见区域优先截断").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..limits import TOOL_TEXT_MAX_BYTES, VALUE_MAX_CHARS
from .elements import ObservedElement
from .roles import TEXT_ENTRY_ROLES, TOGGLE_ROLES
from .tree import clip

ROLE_COLUMN = 12
LABEL_COLUMN = 30
# Bytes reserved for the footer lines when trimming to the budget.
FOOTER_RESERVE_BYTES = 600
# Containers named in the footer as root_ref candidates.
FOOTER_CONTAINERS = 3
# Depth indentation in root_ref expansions is capped so deep trees stay readable.
MAX_INDENT_LEVELS = 8
_TRUTHY = ("1", "true", "on", "yes", "checked", "selected")


@dataclass(frozen=True)
class Rendered:
    """Rendered text plus which elements made it in."""

    text: str
    listed: tuple[str, ...]
    omitted: int


def state_of(el: ObservedElement) -> str:
    """Short state column: on/off for toggles, the quoted value for text inputs.

    Args:
        el: The element.

    Returns:
        The state text ("" when there is nothing to show).
    """
    if el.secure:
        return "(secure, not typable)"
    if el.role in TOGGLE_ROLES:
        if el.value is not None:
            return "on" if el.value.strip().lower() in _TRUTHY else "off"
        return "on" if el.selected else "off"
    if el.value is None or not el.value.strip():
        return '""' if el.role in TEXT_ENTRY_ROLES else ""
    shown = clip(el.value, VALUE_MAX_CHARS).replace('"', "'")
    return f'"{shown}"' if el.role in TEXT_ENTRY_ROLES else shown


def element_line(el: ObservedElement, indent: int = 0, mark: int | None = None) -> str:
    """One element as a compact line.

    Args:
        el: The element.
        indent: Nesting level for subtree expansions.
        mark: Set-of-Mark number drawn on this element, if any.

    Returns:
        ``ref role "label" state [x,y,w,h] flags`` (``m<n>`` when marked).
    """
    label = '"' + el.label.replace('"', "'") + '"'
    box = "[{},{},{},{}]".format(*el.bbox.rounded()) if el.bbox else "[no box]"
    flags = [f for f, on in (("disabled", not el.enabled), ("focused", el.focused),
                             ("offscreen", not el.visible)) if on]
    parts = ["  " * min(indent, MAX_INDENT_LEVELS) + f"{el.ref:<4}", f"{el.role:<{ROLE_COLUMN}}",
             f"{label:<{LABEL_COLUMN}}"]
    state = state_of(el)
    if state:
        parts.append(f"{state:<6}")
    parts.append(box)
    parts.extend(flags)
    if mark is not None:
        parts.append(f"m{mark}")
    return " ".join(parts)


def _footer(hidden: Sequence[ObservedElement], all_elements: Sequence[ObservedElement]) -> str:
    """Footer naming how many elements were left out and where to expand."""
    if not hidden:
        return ""
    counts: dict[str, int] = {}
    hidden_refs = {el.ref for el in hidden}
    for el in all_elements:
        if el.ref in hidden_refs and el.parent_ref:
            counts[el.parent_ref] = counts.get(el.parent_ref, 0) + 1
    by_ref = {el.ref: el for el in all_elements}
    best = sorted(counts.items(), key=lambda kv: -kv[1])[:FOOTER_CONTAINERS]
    hints = ", ".join(f'{ref} {by_ref[ref].role} "{clip(by_ref[ref].label, 24)}" ({n})'
                      for ref, n in best if ref in by_ref)
    tail = f"; containers: {hints}" if hints else ""
    return (f"-- {len(hidden)} more elements not listed; use observe(root_ref=...) to expand"
            f"{tail} --")


def render(header: str, candidates: Sequence[ObservedElement],
           all_elements: Sequence[ObservedElement], *, max_elements: int,
           budget_bytes: int = TOOL_TEXT_MAX_BYTES, indent_from: int | None = None,
           marks: dict[str, int] | None = None) -> Rendered:
    """Render an observation within the element count and byte budgets.

    Args:
        header: First line (observation id, app, window, image).
        candidates: Elements eligible for listing, in document order.
        all_elements: Every element of the observation (for footer hints).
        max_elements: Line cap.
        budget_bytes: Byte cap for the whole text.
        indent_from: When expanding a subtree, the root's depth (lines are indented below it).
        marks: Set-of-Mark number per ref, appended to marked lines.

    Returns:
        The text, the refs listed, and how many candidates were left out.
    """
    mark_of = marks or {}

    def line_of(el: ObservedElement) -> str:
        indent = el.depth - indent_from if indent_from is not None else 0
        return element_line(el, indent, mark_of.get(el.ref))

    order = sorted(range(len(candidates)), key=lambda i: (not candidates[i].visible, i))
    room = budget_bytes - len(header.encode()) - FOOTER_RESERVE_BYTES
    chosen: set[int] = set()
    for i in order:
        if len(chosen) >= max_elements:
            break
        el = candidates[i]
        line = line_of(el)
        cost = len(line.encode()) + 1
        if cost > room:
            break
        room -= cost
        chosen.add(i)
    lines = [header]
    listed: list[str] = []
    for i, el in enumerate(candidates):
        if i in chosen:
            lines.append(line_of(el))
            listed.append(el.ref)
    hidden = [el for i, el in enumerate(candidates) if i not in chosen]
    footer = _footer(hidden, all_elements)
    if footer:
        lines.append(footer)
    return Rendered("\n".join(lines), tuple(listed), len(hidden))
