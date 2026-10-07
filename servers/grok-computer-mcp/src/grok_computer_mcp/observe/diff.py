"""Change summaries between the observation an action used and the one after it (goal.md §5.4).

Boundary: pure. Action results carry these changes instead of a screenshot; the whole list is
capped at 2 KB serialized (goal.md §5.9) and ends with a ``truncated`` marker when cut.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from ..limits import ACTION_SUMMARY_MAX_BYTES, DIFF_TEXT_MAX_CHARS, SUMMARY_MAX_CHARS
from .elements import ObservedElement
from .roles import TEXT_DATA_ROLES
from .textsel import text_body
from .tree import clip

Change = dict[str, object]
# Appeared/disappeared elements listed individually before falling back to a count.
LIST_LIMIT = 5
TRACKED_FIELDS = ("value", "label", "enabled", "focused", "selected")
# Room kept for the trailing {"kind": "truncated", "more": n} marker.
MARKER_RESERVE_BYTES = 40


@dataclass(frozen=True)
class WindowContext:
    """Window identity used for window-level changes."""

    app: str
    window_id: int | None
    title: str


def _value(el: ObservedElement, field: str) -> object:
    """Read a tracked field (secure values are never exposed)."""
    if field == "value":
        return None if el.secure else el.value
    if field == "label":
        return el.label
    if field == "enabled":
        return el.enabled
    if field == "focused":
        return el.focused
    return el.selected


def element_changes(before: Sequence[ObservedElement],
                    after: Sequence[ObservedElement]) -> list[Change]:
    """Field changes, appearances and disappearances of interactive elements.

    Args:
        before: Elements of the observation the action used.
        after: Elements observed after the action.

    Returns:
        Changes in document order of ``after``.
    """
    old = {el.ref: el for el in before if el.interactive}
    new = {el.ref: el for el in after if el.interactive}
    changes: list[Change] = []
    for ref, el in new.items():
        prev = old.get(ref)
        if prev is None:
            continue
        for field in TRACKED_FIELDS:
            a, b = _value(prev, field), _value(el, field)
            if a != b:
                changes.append({"ref": ref, "field": field, "before": a, "after": b})
    appeared = [el for ref, el in new.items() if ref not in old]
    gone = [el for ref, el in old.items() if ref not in new]
    for kind, items in (("appeared", appeared), ("disappeared", gone)):
        for el in items[:LIST_LIMIT]:
            changes.append({"ref": el.ref, "kind": kind, "role": el.role, "label": el.label})
        if len(items) > LIST_LIMIT:
            changes.append({"kind": f"{kind}_more", "count": len(items) - LIST_LIMIT})
    return changes


def _shown_text(elements: Sequence[ObservedElement]) -> list[str]:
    """Clipped static lines in document order, secrets omitted.

    Comparison is by the displayed string, not by ref. A hidden credential line is skipped
    so the secret never appears in ``text_appeared``.
    """
    shown: list[str] = []
    for el in elements:
        if el.role not in TEXT_DATA_ROLES or el.interactive or el.hidden_text:
            continue
        body = text_body(el, DIFF_TEXT_MAX_CHARS)
        if body:
            shown.append(body)
    return shown


def text_changes(before: Sequence[ObservedElement],
                 after: Sequence[ObservedElement]) -> list[Change]:
    """Static lines that appeared or disappeared, matched by text rather than ref.

    Args:
        before: Elements of the observation the action used.
        after: Elements observed after the action.

    Returns:
        Up to ``LIST_LIMIT`` ``text_appeared`` entries (document order of ``after``) then up
        to ``LIST_LIMIT`` ``text_gone`` entries (document order of ``before``). Each text is
        clipped to ``DIFF_TEXT_MAX_CHARS``.
    """
    old = _shown_text(before)
    new = _shown_text(after)
    old_set = set(old)
    new_set = set(new)
    appeared = [text for text in new if text not in old_set]
    gone = [text for text in old if text not in new_set]
    changes: list[Change] = []
    for text in appeared[:LIST_LIMIT]:
        changes.append({"kind": "text_appeared", "text": text})
    for text in gone[:LIST_LIMIT]:
        changes.append({"kind": "text_gone", "text": text})
    return changes


def dialog_changes(before: Sequence[ObservedElement],
                   after: Sequence[ObservedElement]) -> list[Change]:
    """Visible dialogs that opened or closed.

    Args:
        before: Elements of the observation the action used.
        after: Elements observed after the action.

    Returns:
        ``dialog_opened`` / ``dialog_closed`` with ``ref`` and ``label``, at most
        ``LIST_LIMIT`` of each. A dialog is not interactive, so it is not also an
        ``appeared`` element change. Its buttons are.
    """
    old = {el.ref: el for el in before if el.role == "dialog" and el.visible}
    new = [el for el in after if el.role == "dialog" and el.visible]
    new_refs = {el.ref for el in new}
    changes: list[Change] = []
    for el in new:
        if el.ref not in old:
            changes.append({"kind": "dialog_opened", "ref": el.ref, "label": el.label})
        if len([c for c in changes if c["kind"] == "dialog_opened"]) >= LIST_LIMIT:
            break
    closed = 0
    for el in before:
        if el.role == "dialog" and el.visible and el.ref not in new_refs:
            changes.append({"kind": "dialog_closed", "ref": el.ref, "label": el.label})
            closed += 1
            if closed >= LIST_LIMIT:
                break
    return changes


def window_changes(before: WindowContext, after: WindowContext) -> list[Change]:
    """Window-level changes (title, app or window switch).

    Args:
        before: Window of the observation the action used.
        after: Window observed after the action.

    Returns:
        Zero or more changes.
    """
    if (before.app, before.window_id) != (after.app, after.window_id):
        return [{"kind": "window", "before": f"{before.app} \"{before.title}\"",
                 "after": f"{after.app} \"{after.title}\""}]
    if before.title != after.title:
        return [{"kind": "window_title", "before": before.title, "after": after.title}]
    return []


def cap_changes(changes: Sequence[Change], budget_bytes: int = ACTION_SUMMARY_MAX_BYTES,
                reserve: int = 0) -> list[Change]:
    """Keep the changes that fit the budget, marking the rest as truncated.

    Args:
        changes: All changes, most important first.
        budget_bytes: Serialized budget for the list (goal.md §5.9: 2 KB).
        reserve: Bytes already used by the rest of the result.

    Returns:
        A prefix of ``changes``, plus ``{"kind": "truncated", "more": n}`` when cut.
    """
    room = budget_bytes - reserve
    kept: list[Change] = []
    used = 2  # the surrounding brackets
    for i, change in enumerate(changes):
        cost = len(json.dumps(change, ensure_ascii=False).encode()) + 1
        last = i == len(changes) - 1
        if used + cost + (0 if last else MARKER_RESERVE_BYTES) > room:
            marker: Change = {"kind": "truncated", "more": len(changes) - i}
            kept.append(marker)
            return kept
        kept.append(change)
        used += cost
    return kept


def summarize(action: str, target: str, changes: Sequence[Change]) -> str:
    """One-line action summary.

    Args:
        action: Past-tense verb ("Clicked", "Typed 5 chars into").
        target: Target description ('button "Save" (e15)').
        changes: The capped change list.

    Returns:
        At most ``SUMMARY_MAX_CHARS`` characters.
    """
    real = [c for c in changes if c.get("kind") not in ("truncated",)]
    tail = f"; {len(real)} change{'s' if len(real) != 1 else ''}" if real else "; no visible change"
    return clip(f"{action} {target}{tail}", SUMMARY_MAX_CHARS)
