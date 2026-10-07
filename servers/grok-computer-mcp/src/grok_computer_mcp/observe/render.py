"""Compact observation text: one line per element, within the 16 KB tool-text budget.

Boundary: pure (goal.md §5.5). Format::

    obs_7f3a  app=MyApp  window=4521 "Settings"  image=1280x800 (attached)  mode=tree
    e1  group "Profile"
      e7  switch      "Dark mode"         off    [40,214,220,22]
      text "Status"
    -- 23 more elements not listed; use observe(root_ref=...) to expand --

Interactive elements are chosen first (visible ones win). Named containers and static text are
added after that choice (docs/26-10-07-fewer-screenshots-refactor.md). A ``root_ref`` expansion
stays a flat indented list. ``state_of`` and ``element_line`` are re-exported for callers that
imported them from this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..geometry import Rect
from ..limits import TEXT_SECTION_MAX_LINES, TOOL_TEXT_MAX_BYTES
from .elements import ObservedElement
from .lines import container_line, element_line, state_of, text_line
from .sections import layout_rows, scroll_hints
from .textsel import select_text_lines, text_body, text_candidates
from .tree import clip

__all__ = ["Rendered", "element_line", "render", "state_of"]

# Bytes reserved for the footer lines when trimming to the budget.
FOOTER_RESERVE_BYTES = 600
# Containers named in the footer as root_ref candidates.
FOOTER_CONTAINERS = 3


@dataclass(frozen=True)
class Rendered:
    """Rendered text plus which interactive elements made it in."""

    text: str
    listed: tuple[str, ...]
    omitted: int
    text_omitted: int = 0


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


def _choose(candidates: Sequence[ObservedElement], *, header: str, max_elements: int,
            budget_bytes: int, indent_from: int | None,
            marks: dict[str, int]) -> set[int]:
    """Indices of interactive candidates that fit, visible ones first.

    Line cost uses indent 0 on the default list (grouping indent is applied later) and the
    historical depth indent on a ``root_ref`` expansion.
    """
    room = budget_bytes - len(header.encode()) - FOOTER_RESERVE_BYTES
    order = sorted(range(len(candidates)), key=lambda i: (not candidates[i].visible, i))
    chosen: set[int] = set()
    for i in order:
        if len(chosen) >= max_elements:
            break
        el = candidates[i]
        indent = 0 if indent_from is None else el.depth - indent_from
        cost = len(element_line(el, indent, marks.get(el.ref)).encode()) + 1
        if cost > room:
            break
        room -= cost
        chosen.add(i)
    return chosen


def _compose(header: str, rows: list[tuple[str, str | None]], footer: str,
             extra_footer: str, text_note: str) -> tuple[str, tuple[str, ...]]:
    """Join header, rows, and footers. A row's second item is the interactive ref, if any."""
    lines = [header]
    listed: list[str] = []
    for line, ref in rows:
        lines.append(line)
        if ref is not None:
            listed.append(ref)
    for extra in (footer, text_note, extra_footer):
        if extra:
            lines.append(extra)
    return "\n".join(lines), tuple(listed)


def render(header: str, candidates: Sequence[ObservedElement],
           all_elements: Sequence[ObservedElement], *, max_elements: int,
           budget_bytes: int = TOOL_TEXT_MAX_BYTES, indent_from: int | None = None,
           marks: dict[str, int] | None = None, text_budget_bytes: int = 0,
           image: Rect | None = None, focused_ref: str | None = None,
           extra_footer: str = "", scope_ref: str | None = None,
           grouping_elements: Sequence[ObservedElement] | None = None) -> Rendered:
    """Render an observation within the element count and byte budgets.

    Args:
        header: First line (observation id, app, window, image).
        candidates: Interactive elements eligible for listing, in document order.
        all_elements: Every element of the observation (footer hints, text, scroll).
        max_elements: Line cap for interactive elements.
        budget_bytes: Byte cap used while choosing interactive lines.
        indent_from: When expanding a subtree, the root's depth. Flat indent; no grouping
            and no text section.
        marks: Set-of-Mark number per ref, appended to marked lines.
        text_budget_bytes: Bytes left for static text after interactive lines (0 skips text).
        image: Image rectangle for scrollports that have no ``scrollarea`` ancestor. None
            skips scroll hints.
        focused_ref: Focused element; its container's text is preferred.
        extra_footer: A line placed after the other footers (the modal note).
        scope_ref: Subtree static text is drawn from (None uses ``all_elements``).
        grouping_elements: Elements whose parent links open container headers. Defaults to
            ``all_elements``. A modal listing passes the dialog subtree so outside containers
            stay closed.

    Returns:
        The text, the interactive refs listed, how many candidates were left out, and how
        many static lines did not fit. ``text_budget_bytes`` defaults to 0 so a caller that
        only lists controls keeps today's lines.
    """
    mark_of = marks or {}
    chosen = _choose(candidates, header=header, max_elements=max_elements,
                     budget_bytes=budget_bytes, indent_from=indent_from, marks=mark_of)
    hidden = [el for i, el in enumerate(candidates) if i not in chosen]
    footer = _footer(hidden, all_elements)
    if indent_from is not None:
        rows = [(element_line(el, el.depth - indent_from, mark_of.get(el.ref)), el.ref)
                for i, el in enumerate(candidates) if i in chosen]
        text, listed = _compose(header, rows, footer, extra_footer, "")
        return Rendered(text, listed, len(hidden), 0)
    texts = select_text_lines(all_elements, scope_ref=scope_ref, focused_ref=focused_ref,
                              budget_bytes=text_budget_bytes, max_lines=TEXT_SECTION_MAX_LINES)
    pool = len(text_candidates(all_elements, scope_ref=scope_ref, include_offscreen=False))
    text_omitted = max(0, pool - len(texts)) if text_budget_bytes > 0 else 0
    hints = scroll_hints(all_elements, image) if image is not None else {}
    items = [candidates[i] for i in sorted(chosen)] + list(texts)
    planned = layout_rows(items, grouping_elements or all_elements, hints)
    rows: list[tuple[str, str | None]] = []
    for row in planned:
        if row.kind == "container":
            rows.append((container_line(row.element, row.indent, row.scroll), None))
        elif row.kind == "text":
            body = text_body(row.element)
            rows.append((text_line(body, row.indent, masked=row.element.hidden_text), None))
        else:
            rows.append((element_line(row.element, row.indent, mark_of.get(row.element.ref)),
                         row.element.ref))
    note = f"-- {text_omitted} text lines not listed --" if text_omitted else ""
    text, listed = _compose(header, rows, footer, extra_footer, note)
    return Rendered(text, listed, len(hidden), text_omitted)
