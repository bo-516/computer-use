"""Stable refs: ``e7`` keeps naming the same element across observations of a window.

Boundary: pure apart from the ``RefAllocator`` memory the caller owns (one per window, kept in the
session). Identity is the element's role, label and ancestry plus its occurrence index; an element
whose label changed in place (same role, parent and box) keeps its ref; anything else gets the
next free number. Stable refs are what make action diffs such as "e7 value 0 -> 1" meaningful
(goal.md §5.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..backend.base import RawElement
from ..geometry import Rect
from .elements import ObservedElement

# Ancestors folded into an element's identity; deeper context rarely disambiguates more.
IDENTITY_DEPTH = 3
# Two boxes this close (image px) on every edge are "the same place" for in-place matching.
SAME_PLACE_PX = 4.0
# Identity keys remembered per window; bounds memory on long sessions.
MAX_REMEMBERED_IDS = 5000


def _ids() -> dict[tuple[str, ...], str]:
    """Empty identity map (typed default factory)."""
    return {}


def _elements() -> list[ObservedElement]:
    """Empty element list (typed default factory)."""
    return []


@dataclass
class RefAllocator:
    """Per-window ref memory: identity key -> ref, plus the last observation's elements."""

    counter: int = 0
    by_identity: dict[tuple[str, ...], str] = field(default_factory=_ids)
    previous: list[ObservedElement] = field(default_factory=_elements)

    def next_ref(self) -> str:
        """Allocate a fresh ref.

        Returns:
            ``e<n>`` with a number never used before in this window.
        """
        self.counter += 1
        return f"e{self.counter}"


def _identity(raw: Sequence[RawElement], pos: int, roles: Sequence[str],
              by_index: dict[int, int]) -> tuple[str, ...]:
    """Role/label chain of an element and up to ``IDENTITY_DEPTH`` ancestors."""
    parts: list[str] = []
    current: int | None = pos
    for _ in range(IDENTITY_DEPTH + 1):
        if current is None:
            break
        el = raw[current]
        parts.append(f"{roles[current]}:{' '.join(el.label.split())}")
        current = by_index.get(el.parent) if el.parent is not None else None
    return tuple(parts)


def _same_place(a: Rect | None, b: Rect | None) -> bool:
    """Whether two boxes are within ``SAME_PLACE_PX`` on every edge."""
    if a is None or b is None:
        return False
    return (abs(a.x - b.x) <= SAME_PLACE_PX and abs(a.y - b.y) <= SAME_PLACE_PX
            and abs(a.w - b.w) <= SAME_PLACE_PX and abs(a.h - b.h) <= SAME_PLACE_PX)


def assign_refs(raw: Sequence[RawElement], roles: Sequence[str], boxes: Sequence[Rect | None],
                refs: RefAllocator) -> list[str]:
    """Assign a ref to every element of a snapshot.

    Args:
        raw: Snapshot elements in document order.
        roles: Normalized role of each element.
        boxes: IMAGE-space box of each element.
        refs: The window's ref memory; updated in place (except ``previous``, which the caller
            sets once the elements are built).

    Returns:
        One ref per element, in order.
    """
    by_index = {el.index: pos for pos, el in enumerate(raw)}
    seen: dict[tuple[str, ...], int] = {}
    keys: list[tuple[str, ...]] = []
    assigned: list[str | None] = []
    used: set[str] = set()
    for pos in range(len(raw)):
        base = _identity(raw, pos, roles, by_index)
        occurrence = seen.get(base, 0)
        seen[base] = occurrence + 1
        key = (*base, str(occurrence))
        keys.append(key)
        ref = refs.by_identity.get(key)
        if ref is not None and ref not in used:
            assigned.append(ref)
            used.add(ref)
        else:
            assigned.append(None)
    unmatched = [p for p in refs.previous if p.ref not in used]
    for pos, ref in enumerate(assigned):
        if ref is not None:
            continue
        parent = raw[pos].parent
        parent_pos = by_index.get(parent) if parent is not None else None
        parent_ref = assigned[parent_pos] if parent_pos is not None else None
        match = next((p for p in unmatched if p.role == roles[pos] and p.parent_ref == parent_ref
                      and _same_place(p.bbox, boxes[pos])), None)
        if match is not None:
            unmatched.remove(match)
            assigned[pos] = match.ref
        else:
            assigned[pos] = refs.next_ref()
    if len(refs.by_identity) > MAX_REMEMBERED_IDS:
        refs.by_identity.clear()
    final = [ref or refs.next_ref() for ref in assigned]
    for key, ref in zip(keys, final, strict=True):
        refs.by_identity[key] = ref
    return final
