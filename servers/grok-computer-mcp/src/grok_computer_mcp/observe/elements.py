"""The element record shared by tree processing, rendering, diffs and the state file.

Boundary: plain immutable data. ``bbox`` is IMAGE space (goal.md §5.6) or None when the backend
gave no frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..geometry import Rect


@dataclass(frozen=True)
class ObservedElement:
    """One element of an observation."""

    ref: str
    index: int
    handle: str | None
    role: str
    raw_role: str
    label: str
    value: str | None
    bbox: Rect | None
    interactive: bool
    visible: bool
    secure: bool
    in_form: bool
    enabled: bool
    focused: bool
    selected: bool | None
    parent_ref: str | None
    depth: int
