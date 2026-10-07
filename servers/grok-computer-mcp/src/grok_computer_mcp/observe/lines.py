"""One-line formatters for the compact observation text (goal.md §5.5).

Boundary: pure. ``render`` decides which rows fit; this module only turns one element into the
string the model reads. Container headers and static text lines are part of the tree view
(docs/26-10-07-fewer-screenshots-refactor.md FR-2, FR-3, FR-5, FR-6).
"""

from __future__ import annotations

import math

from ..limits import VALUE_MAX_CHARS
from .elements import ObservedElement
from .roles import TEXT_ENTRY_ROLES, TOGGLE_ROLES
from .tree import clip

ROLE_COLUMN = 12
LABEL_COLUMN = 30
# Depth indentation is capped so a deep tree stays readable (refactor FR-2).
MAX_INDENT_LEVELS = 8
_TRUTHY = ("1", "true", "on", "yes", "checked", "selected")


def _fmt(value: float) -> str:
    """A number without a trailing ``.0``."""
    if math.isfinite(value) and value == math.trunc(value):
        return str(int(value))
    return str(value)


def _indent(level: int) -> str:
    """Two spaces per level, capped at ``MAX_INDENT_LEVELS``."""
    return "  " * min(level, MAX_INDENT_LEVELS)


def state_of(el: ObservedElement) -> str:
    """Short state column.

    Args:
        el: The element.

    Returns:
        The state text ("" when there is nothing to show). Optional V17 fields add
        ``expanded`` / ``collapsed``, a slider's ``value (min-max)``, or ``placeholder:"…"``.
        When those fields are absent the text matches the historical shape. The slider range
        uses an en dash.
    """
    if el.secure:
        return "(secure, not typable)"
    if el.role == "disclosure" and el.expanded is not None:
        return "expanded" if el.expanded else "collapsed"
    if el.role == "slider" and el.min_value is not None and el.max_value is not None:
        span = f"({_fmt(el.min_value)}–{_fmt(el.max_value)})"
        if el.value and el.value.strip():
            return f"{el.value.strip()} {span}"
        return span
    if el.role in TOGGLE_ROLES:
        if el.value is not None:
            return "on" if el.value.strip().lower() in _TRUTHY else "off"
        return "on" if el.selected else "off"
    empty = el.value is None or not el.value.strip()
    if empty and el.placeholder and el.role in TEXT_ENTRY_ROLES:
        shown = clip(el.placeholder, VALUE_MAX_CHARS).replace('"', "'")
        return f'placeholder:"{shown}"'
    if empty:
        return '""' if el.role in TEXT_ENTRY_ROLES else ""
    shown = clip(el.value or "", VALUE_MAX_CHARS).replace('"', "'")
    return f'"{shown}"' if el.role in TEXT_ENTRY_ROLES else shown


def element_line(el: ObservedElement, indent: int = 0, mark: int | None = None) -> str:
    """One interactive element as a compact line.

    Args:
        el: The element.
        indent: Nesting level for subtree expansions and container grouping.
        mark: Set-of-Mark number drawn on this element, if any.

    Returns:
        ``ref role "label" state [x,y,w,h] flags`` (``m<n>`` when marked).
    """
    label = '"' + el.label.replace('"', "'") + '"'
    box = "[{},{},{},{}]".format(*el.bbox.rounded()) if el.bbox else "[no box]"
    flags = [f for f, on in (("disabled", not el.enabled), ("focused", el.focused),
                             ("offscreen", not el.visible)) if on]
    parts = [_indent(indent) + f"{el.ref:<4}", f"{el.role:<{ROLE_COLUMN}}",
             f"{label:<{LABEL_COLUMN}}"]
    state = state_of(el)
    if state:
        parts.append(f"{state:<6}")
    parts.append(box)
    parts.extend(flags)
    if mark is not None:
        parts.append(f"m{mark}")
    return " ".join(parts)


def container_line(el: ObservedElement, indent: int, scroll: tuple[int, int] | None) -> str:
    """A named container header.

    Args:
        el: The container.
        indent: Nesting level.
        scroll: ``(above, below)`` clipped-row counts, or None when both are zero.

    Returns:
        ``eN role "label"`` plus ``scroll ↑N ↓M`` when either count is non-zero.
    """
    label = '"' + el.label.replace('"', "'") + '"'
    line = f"{_indent(indent)}{el.ref:<4} {el.role} {label}"
    if scroll is not None and (scroll[0] or scroll[1]):
        line += f"  scroll ↑{scroll[0]} ↓{scroll[1]}"
    return line


def text_line(body: str, indent: int, *, masked: bool) -> str:
    """A static text row with no ref.

    Args:
        body: Already clipped visible text.
        indent: Nesting level.
        masked: Render ``text (hidden: credential)`` instead of the body.

    Returns:
        The line. Screen text stays quoted so it is data, not instructions.
    """
    if masked:
        return f"{_indent(indent)}text (hidden: credential)"
    shown = body.replace('"', "'")
    return f'{_indent(indent)}text "{shown}"'
