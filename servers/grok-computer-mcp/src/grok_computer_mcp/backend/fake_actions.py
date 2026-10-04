"""Fake backend, part 2: input actions (``FakeBackend`` combines them with the reads).

Boundary: in-memory scene mutations that mimic the driver: element handles must come from the
latest snapshot, point actions are read in the configured action space, Enter runs a focused
node's ``on_enter`` effects and save chords clear the unsaved flag.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..geometry import Point
from ..hooklib import keys
from .base import (
    ActionOutcome,
    BackendError,
    BackendErrorKind,
    Button,
    Delivery,
    Direction,
    ElementTarget,
    PointTarget,
    RawRegion,
    Target,
    WindowInfo,
    WindowSnapshot,
)
from .fake_core import FakeCore

SAVE_CHORDS = ("cmd+s", "ctrl+s")


@dataclass
class FakeActions(FakeCore):
    """Input actions over the scene."""

    async def click(self, target: Target, *, button: Button, count: int,
                    modifiers: tuple[str, ...], delivery: Delivery) -> ActionOutcome:
        """Press an element or the deepest node under a point."""
        self._log("click", target=target, button=button, count=count, modifiers=modifiers,
                  delivery=delivery)
        if isinstance(target, ElementTarget):
            w, node = self._node(target)
            return self._press(w, node)
        if target.window is None:
            raise BackendError(BackendErrorKind.PROTOCOL, "desktop clicks need a window here")
        w = self._fake_window(target.window)
        local = self._point_in(w, target.point)
        if delivery is Delivery.FOREGROUND:
            self.scene.cursor = Point(w.bounds.x + local.x, w.bounds.y + local.y)
        node = w.hit(local)
        if node is None:
            return ActionOutcome("px", "unverifiable")
        outcome = self._press(w, node)
        return ActionOutcome("px", outcome.effect, outcome.verified)

    async def type_text(self, window: WindowInfo, target: ElementTarget | None, text: str, *,
                        delivery: Delivery) -> ActionOutcome:
        """Append text to the target or the focused text node."""
        self._log("type_text", window=window.window_id, target=target, chars=len(text),
                  delivery=delivery)
        w = self._fake_window(window)
        node = self._node(target)[1] if target else (w.nodes.get(w.focused) if w.focused else None)
        if node is None or not node.accepts_text:
            raise BackendError(BackendErrorKind.NOT_INTERACTABLE, "focused element takes no text")
        w.focused = node.id
        node.value = (node.value or "") + text
        return ActionOutcome("ax", "confirmed", True)

    async def clear_text(self, window: WindowInfo, target: ElementTarget | None) -> ActionOutcome:
        """Empty the target or focused text node."""
        self._log("clear_text", window=window.window_id, target=target)
        w = self._fake_window(window)
        node = self._node(target)[1] if target else (w.nodes.get(w.focused) if w.focused else None)
        if node is None or not node.accepts_text:
            raise BackendError(BackendErrorKind.NOT_INTERACTABLE, "focused element takes no text")
        node.value = ""
        return ActionOutcome("ax", "confirmed", True)

    async def press_keys(self, window: WindowInfo, chord: str, *,
                         delivery: Delivery) -> ActionOutcome:
        """Enter submits the focused node; save chords clear the unsaved flag."""
        self._log("press_keys", window=window.window_id, chord=chord, delivery=delivery)
        w = self._fake_window(window)
        if keys.presses_enter(chord) and w.focused:
            node = w.nodes[w.focused]
            self.scene.apply(w, node, node.on_enter)
        elif chord in SAVE_CHORDS:
            w.unsaved = False
        return ActionOutcome("key_events", "unverifiable")

    async def scroll(self, target: Target | None, window: WindowInfo, *, direction: Direction,
                     amount: int, delivery: Delivery) -> ActionOutcome:
        """Record the scroll (the scene does not scroll)."""
        self._log("scroll", target=target, window=window.window_id, direction=direction,
                  amount=amount, delivery=delivery)
        return ActionOutcome("px" if isinstance(target, PointTarget) else "ax", "unverifiable")

    async def drag(self, window: WindowInfo, start: Point, end: Point, *,
                   delivery: Delivery) -> ActionOutcome:
        """Record the drag."""
        self._log("drag", window=window.window_id, start=start, end=end, delivery=delivery)
        self._point_in(self._fake_window(window), start)
        return ActionOutcome("px", "unverifiable")

    async def cursor_position(self) -> Point | None:
        """The simulated pointer."""
        return self.scene.cursor

    async def regions(self, snapshot: WindowSnapshot) -> list[RawRegion]:
        """Configured regions for the window (empty by default)."""
        return list(self.regions_by_window.get(snapshot.window.window_id, []))
