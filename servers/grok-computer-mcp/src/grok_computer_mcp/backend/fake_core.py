"""State, test controls and helpers of the fake backend (the protocol methods are in ``fake.py``).

Boundary: in-memory only. Mirrors the Cua Driver behaviours the facade depends on: element handles
expire with the next snapshot, point actions are read in the configured action space (capture
pixels need a prior screenshot, like ``screenshot_context_missing``), and failures can be queued.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..coords import Space
from ..geometry import Display, Point, Rect
from .base import (
    ActionOutcome,
    BackendError,
    BackendErrorKind,
    Delivery,
    ElementTarget,
    RawRegion,
    WindowInfo,
)
from .fake_scene import FakeNode, FakeScene, FakeWindow


def _calls() -> list[tuple[str, dict[str, object]]]:
    """Empty call log (typed default factory)."""
    return []


@dataclass
class _Failure:
    """A queued failure for one method (optionally only for one delivery mode)."""

    kind: BackendErrorKind
    delivery: Delivery | None


@dataclass
class Snapshot:
    """Bookkeeping for the latest snapshot of a window."""

    generation: int
    capture_size: tuple[int, int] | None = None


@dataclass
class FakeCore:
    """Scene, configuration, queued failures and snapshot bookkeeping."""

    scene: FakeScene
    action_space: Space = Space.CAPTURE_PIXELS
    frame_space: Space = Space.DESKTOP_POINTS
    capture_long_edge: int | None = None
    name: str = "fake"
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=_calls)
    started: bool = False
    regions_by_window: dict[int, list[RawRegion]] = field(
        default_factory=dict[int, list[RawRegion]])
    failures: dict[str, list[_Failure]] = field(default_factory=dict[str, list[_Failure]])
    snapshots: dict[int, Snapshot] = field(default_factory=dict[int, Snapshot])
    generation: int = 0
    pending_pointer: Point | None = None

    # --- test controls -------------------------------------------------------------------
    def fail_next(self, method: str, kind: BackendErrorKind, *, times: int = 1,
                  delivery: Delivery | None = None) -> None:
        """Make the next ``times`` calls of ``method`` fail with ``kind``.

        Args:
            method: Backend method name (``click``, ``read_window`` ...).
            kind: Failure to raise.
            times: How many calls fail.
            delivery: Only fail calls with this delivery mode.
        """
        self.failures.setdefault(method, []).extend(_Failure(kind, delivery) for _ in range(times))

    def move_pointer(self, p: Point) -> None:
        """Simulate the user moving the physical mouse (DESKTOP_POINTS)."""
        self.scene.cursor = p

    def move_pointer_during_next_foreground_action(self, p: Point) -> None:
        """Simulate the user grabbing the mouse while the next foreground action runs."""
        self.pending_pointer = p

    def window(self, title: str) -> FakeWindow:
        """Find a window by title (test helper)."""
        return next(w for w in self.scene.windows.values() if w.title == title)

    # --- helpers ----------------------------------------------------------------------------
    def _log(self, method: str, **args: object) -> None:
        """Record a call and raise a queued failure if one matches."""
        self.calls.append((method, args))
        delivery = args.get("delivery")
        if self.pending_pointer is not None and delivery is Delivery.FOREGROUND:
            self.scene.cursor, self.pending_pointer = self.pending_pointer, None
        queue = self.failures.get(method) or []
        for failure in queue:
            if failure.delivery is None or failure.delivery == delivery:
                queue.remove(failure)
                raise BackendError(failure.kind, f"injected {failure.kind.value} on {method}")

    def _display(self, rect: Rect) -> Display:
        """Display holding most of ``rect``."""
        best = self.scene.displays[0]
        best_area = 0.0
        for display in self.scene.displays:
            overlap = display.bounds.intersection(rect)
            if overlap is not None and overlap.area > best_area:
                best, best_area = display, overlap.area
        return best

    def _fake_window(self, window: WindowInfo) -> FakeWindow:
        """The scene window behind a ``WindowInfo``."""
        found = self.scene.windows.get(window.window_id)
        if found is None:
            raise BackendError(BackendErrorKind.WINDOW_GONE, f"window {window.window_id} is gone")
        return found

    @staticmethod
    def _info(w: FakeWindow) -> WindowInfo:
        """``WindowInfo`` of a scene window."""
        return WindowInfo(w.window_id, w.pid, w.app, w.title, w.bounds, w.on_screen, w.z_index,
                          w.unsaved)

    def _frame_out(self, w: FakeWindow, rect: Rect, capture: tuple[int, int] | None) -> Rect:
        """Convert a WINDOW_POINTS rect into the configured frame space."""
        if self.frame_space is Space.DESKTOP_POINTS:
            return Rect(rect.x + w.bounds.x, rect.y + w.bounds.y, rect.w, rect.h)
        if self.frame_space is Space.CAPTURE_PIXELS and capture is not None:
            sx, sy = capture[0] / w.bounds.w, capture[1] / w.bounds.h
            return Rect(rect.x * sx, rect.y * sy, rect.w * sx, rect.h * sy)
        return rect

    def _point_in(self, w: FakeWindow, p: Point) -> Point:
        """Convert an action-space point into WINDOW_POINTS."""
        if self.action_space is Space.DESKTOP_POINTS:
            return Point(p.x - w.bounds.x, p.y - w.bounds.y)
        if self.action_space is Space.WINDOW_POINTS:
            return p
        snap = self.snapshots.get(w.window_id)
        if snap is None or snap.capture_size is None:
            raise BackendError(BackendErrorKind.PROTOCOL, "screenshot_context_missing: read the "
                               "window with a screenshot first")
        cap_w, cap_h = snap.capture_size
        return Point(p.x * w.bounds.w / cap_w, p.y * w.bounds.h / cap_h)

    def _node(self, target: ElementTarget) -> tuple[FakeWindow, FakeNode]:
        """Resolve an element handle, refusing handles from replaced snapshots."""
        w = self._fake_window(target.window)
        gen, _, node_id = (target.handle or "").partition(":")
        snap = self.snapshots.get(w.window_id)
        if snap is None or gen != f"t{snap.generation}" or node_id not in w.nodes:
            raise BackendError(BackendErrorKind.STALE_HANDLE, "stale_element_token")
        return w, w.nodes[node_id]

    def _press(self, w: FakeWindow, node: FakeNode) -> ActionOutcome:
        """Click a node: refuse disabled nodes, run its effects, focus text inputs."""
        if not node.enabled:
            raise BackendError(BackendErrorKind.NOT_INTERACTABLE, f"{node.label!r} is disabled")
        if node.accepts_text:
            w.focused = node.id
        self.scene.apply(w, node, node.on_click)
        return ActionOutcome("ax", "confirmed", True)
