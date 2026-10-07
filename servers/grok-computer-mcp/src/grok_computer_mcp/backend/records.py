"""Data types shared by every backend: windows, elements, captures, targets, outcomes, errors.

Boundary: plain immutable values (no I/O). ``base.py`` defines the ``Backend`` protocol over them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from ..coords import Space
from ..geometry import Display, Point, Rect

Button = Literal["left", "right", "middle"]
Effect = Literal["confirmed", "unverifiable", "suspected_noop", "unknown"]
Direction = Literal["up", "down", "left", "right"]


class Delivery(StrEnum):
    """How input reaches the target (Cua's delivery ladder)."""

    BACKGROUND = "background"
    FOREGROUND = "foreground"


class BackendErrorKind(StrEnum):
    """Driver failures the facade maps to model-facing error codes (goal.md §5.8)."""

    BACKGROUND_UNAVAILABLE = "background_unavailable"
    OCCLUDED = "occluded"
    ELEVATED = "elevated"
    PERMISSION = "permission"
    STALE_HANDLE = "stale_handle"
    WINDOW_GONE = "window_gone"
    NOT_INTERACTABLE = "not_interactable"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    PROTOCOL = "protocol"


class BackendError(Exception):
    """A driver call failed in a way the facade can classify."""

    def __init__(self, kind: BackendErrorKind, message: str, *, detail: str = "") -> None:
        """Create the error.

        Args:
            kind: Classified failure.
            message: Human-readable message (may be shown to the model).
            detail: Extra context for logs (missing permission names, raw driver code).
        """
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class AppInfo:
    """A running or installed app."""

    name: str
    pid: int | None
    bundle_id: str | None = None
    running: bool = True
    active: bool = False


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window. ``bounds`` are DESKTOP_POINTS."""

    window_id: int
    pid: int | None
    app: str
    title: str
    bounds: Rect
    on_screen: bool = True
    z_index: int | None = None
    unsaved: bool = False


@dataclass(frozen=True)
class RawElement:
    """One accessibility node as the backend reports it (frame in the snapshot's frame space)."""

    index: int
    role: str
    label: str
    value: str | None = None
    frame: Rect | None = None
    handle: str | None = None
    subrole: str = ""
    enabled: bool = True
    focused: bool = False
    selected: bool | None = None
    parent: int | None = None
    depth: int = 0
    actions: tuple[str, ...] = ()
    # V17: optional. Missing keys stay None; the observation line then matches today's shape.
    expanded: bool | None = None
    min_value: float | None = None
    max_value: float | None = None
    placeholder: str | None = None


@dataclass(frozen=True)
class Capture:
    """An image the backend captured. ``width``/``height`` are CAPTURE_PIXELS."""

    data: bytes
    mime: str
    width: int
    height: int
    capture_id: str | None = None


@dataclass(frozen=True)
class WindowSnapshot:
    """Tree and optional capture of one window, read together."""

    window: WindowInfo
    display: Display
    elements: tuple[RawElement, ...]
    frame_space: Space | None
    capture: Capture | None = None
    degraded: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class RawRegion:
    """A text or icon region from the driver's optional visual parser (CAPTURE_PIXELS)."""

    frame: Rect
    kind: str
    text: str = ""


@dataclass(frozen=True)
class ScreenSnapshot:
    """The whole display: visible windows and an optional capture."""

    display: Display
    windows: tuple[WindowInfo, ...]
    capture: Capture | None = None


@dataclass(frozen=True)
class ElementTarget:
    """Act on an element of the latest snapshot of ``window``."""

    window: WindowInfo
    index: int
    handle: str | None
    fallback_point: Point | None = None


@dataclass(frozen=True)
class PointTarget:
    """Act at ``point`` (in the backend's ``action_space``) inside ``window`` or the desktop."""

    window: WindowInfo | None
    point: Point
    capture_id: str | None = None


Target = ElementTarget | PointTarget


@dataclass(frozen=True)
class ActionOutcome:
    """What the driver reports about an action it performed."""

    path: str
    effect: Effect = "unknown"
    verified: bool | None = None
    escalation: str | None = None


@dataclass(frozen=True)
class BackendStatus:
    """Management status (``grok-computer-mcp status`` / ``doctor``, never shown to the model)."""

    name: str
    version: str
    platform: str
    permissions: dict[str, bool] = field(default_factory=dict[str, bool])
    ready: bool = True
    detail: str = ""
