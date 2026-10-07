"""Parse ``get_window_state`` elements (pure).

Boundary: no I/O. Field names follow the Cua Driver reference. Optional expanded / min / max /
placeholder are read only when present (goal.md §12 V17); a missing key stays None and the
observation line keeps today's shape. ``cua_parse`` re-exports ``elements_of`` so existing
imports keep working.
"""

from __future__ import annotations

from typing import cast

from .base import RawElement
from .cua_parse import JsonObject, items, num, rect, text

# V17: spellings a driver might use. The first key present wins; absence stays None.
_MIN_KEYS = ("min_value", "min", "minimum")
_MAX_KEYS = ("max_value", "max", "maximum")
_PLACEHOLDER_KEYS = ("placeholder", "placeholder_value")


def _optional_float(rec: JsonObject, keys: tuple[str, ...]) -> float | None:
    """The first present numeric spelling, or None when every key is absent."""
    for key in keys:
        if key in rec:
            return num(rec[key])
    return None


def _optional_str(rec: JsonObject, keys: tuple[str, ...]) -> str | None:
    """The first present string spelling, or None when every key is absent."""
    for key in keys:
        if key not in rec:
            continue
        value = rec[key]
        if isinstance(value, str):
            return value
    return None


def elements_of(structured: JsonObject) -> list[RawElement]:
    """Elements of a ``get_window_state`` answer, in document order.

    Args:
        structured: The driver's structured content.

    Returns:
        Raw elements. Expanded, min, max, and placeholder are None when the driver omits them.
    """
    out: list[RawElement] = []
    for pos, rec in enumerate(items(structured.get("elements"))):
        index = num(rec.get("element_index"))
        parent = num(rec.get("parent_index"))
        depth = num(rec.get("depth"))
        selected = rec.get("selected")
        raw_actions = rec.get("actions")
        actions: list[str] = []
        if isinstance(raw_actions, list):
            for item in cast(list[object], raw_actions):
                if isinstance(item, str):
                    actions.append(item)
        value = rec.get("value")
        expanded = rec.get("expanded") if "expanded" in rec else None
        # V17: copy only keys the driver sent. A missing key must not invent a state token.
        out.append(RawElement(
            index=int(index) if index is not None else pos, role=text(rec.get("role")),
            label=text(rec.get("label") or rec.get("title")),
            value=None if value is None else str(value), frame=rect(rec.get("frame")),
            handle=text(rec.get("element_token")) or None, subrole=text(rec.get("subrole")),
            enabled=rec.get("enabled", True) is not False, focused=rec.get("focused") is True,
            selected=selected if isinstance(selected, bool) else None,
            parent=int(parent) if parent is not None else None,
            depth=int(depth) if depth is not None else 0, actions=tuple(actions),
            expanded=expanded if isinstance(expanded, bool) else None,
            min_value=_optional_float(rec, _MIN_KEYS),
            max_value=_optional_float(rec, _MAX_KEYS),
            placeholder=_optional_str(rec, _PLACEHOLDER_KEYS)))
    return out
