"""Backend abstraction: the only door to Cua Driver (AGENTS.md).

Boundary: the ``Backend`` protocol; its data types live in ``records.py`` and are re-exported
here. Implementations (``cua.py``, ``fake.py``) translate these calls to a concrete driver; every
capability added here is added to ``fake.py`` too. Backends do not decide anything (policy,
staleness, refs, coordinates): they report raw trees and captures in a declared coordinate space
and perform actions at handles or points the facade computed.
"""

from __future__ import annotations

from typing import Protocol

from ..coords import Space
from ..geometry import Display, Point
from .records import (
    ActionOutcome,
    AppInfo,
    BackendError,
    BackendErrorKind,
    BackendStatus,
    Button,
    Capture,
    Delivery,
    Direction,
    Effect,
    ElementTarget,
    PointTarget,
    RawElement,
    RawRegion,
    ScreenSnapshot,
    Target,
    WindowInfo,
    WindowSnapshot,
)

__all__ = [
    "ActionOutcome",
    "AppInfo",
    "Backend",
    "BackendError",
    "BackendErrorKind",
    "BackendStatus",
    "Button",
    "Capture",
    "Delivery",
    "Direction",
    "Effect",
    "ElementTarget",
    "PointTarget",
    "RawElement",
    "RawRegion",
    "ScreenSnapshot",
    "Target",
    "WindowInfo",
    "WindowSnapshot",
]


class Backend(Protocol):
    """Desktop driver used by the facade. All methods may raise ``BackendError``."""

    name: str
    action_space: Space

    async def start(self) -> None:
        """Connect to the driver (idempotent)."""
        ...

    async def close(self) -> None:
        """Release driver resources (idempotent)."""
        ...

    async def status(self) -> BackendStatus:
        """Report version, platform and permission status."""
        ...

    async def list_apps(self) -> list[AppInfo]:
        """List running and installed apps."""
        ...

    async def list_windows(self, app: str | None = None) -> list[WindowInfo]:
        """List top-level windows, optionally of one app (case-insensitive name)."""
        ...

    async def displays(self) -> list[Display]:
        """List displays with logical bounds, backing scale and physical origin."""
        ...

    async def launch_app(self, name: str) -> list[WindowInfo]:
        """Launch (or reuse) an app in the background; return its windows."""
        ...

    async def focus_app(self, name: str) -> None:
        """Bring an app to the front."""
        ...

    async def read_window(self, window: WindowInfo, *, screenshot: bool,
                          max_elements: int) -> WindowSnapshot:
        """Read the window's accessibility tree, and a capture when ``screenshot``."""
        ...

    async def read_screen(self, *, screenshot: bool) -> ScreenSnapshot:
        """Read the primary display: visible windows and an optional capture."""
        ...

    async def click(self, target: Target, *, button: Button, count: int,
                    modifiers: tuple[str, ...], delivery: Delivery) -> ActionOutcome:
        """Click an element or a point."""
        ...

    async def type_text(self, window: WindowInfo, target: ElementTarget | None, text: str,
                        *, delivery: Delivery) -> ActionOutcome:
        """Insert text into ``target`` or the window's focused element."""
        ...

    async def clear_text(self, window: WindowInfo, target: ElementTarget | None) -> ActionOutcome:
        """Empty a text field (``type_text(clear_first=True)``)."""
        ...

    async def press_keys(self, window: WindowInfo, chord: str, *,
                         delivery: Delivery) -> ActionOutcome:
        """Press one canonical chord (``cmd+s``) in the window."""
        ...

    async def scroll(self, target: Target | None, window: WindowInfo, *, direction: Direction,
                     amount: int, delivery: Delivery) -> ActionOutcome:
        """Scroll at a target, or the window when ``target`` is None."""
        ...

    async def drag(self, window: WindowInfo, start: Point, end: Point, *,
                   delivery: Delivery) -> ActionOutcome:
        """Press-drag-release between two points in ``action_space``."""
        ...

    async def cursor_position(self) -> Point | None:
        """Current pointer position in DESKTOP_POINTS, or None when unknown."""
        ...

    async def regions(self, snapshot: WindowSnapshot) -> list[RawRegion]:
        """Text/icon regions of a snapshot's capture (optional parser; [] when unavailable)."""
        ...
