"""Observation lifecycle: ids, the records actions validate against, and per-window ref memory.

Boundary: in-memory state of one facade process (goal.md §5.3 ``session.py``). The desktop lock
that keeps a second operator out lives in ``lock.py``. An observation id stays valid for the last
``OBSERVATIONS_KEPT`` observations; an older id answers ``STALE_OBSERVATION``.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

from .backend.base import WindowInfo
from .coords import Space
from .geometry import Frame, Point
from .limits import OBSERVATIONS_KEPT
from .observe.elements import ObservedElement
from .observe.refs import RefAllocator
from .observe.som import Mark

Scope = Literal["window", "screen"]
Mode = Literal["tree", "screenshot", "som"]
# Bytes of randomness in an observation id (6 hex chars): ids only need to be unique within the
# small window of kept observations, and short ids save tokens on every action call.
OBSERVATION_ID_BYTES = 3


def _points() -> list[Point]:
    """Empty point list (typed default factory)."""
    return []


@dataclass
class Observation:
    """One observation: what was seen and how its image maps to the screen."""

    id: str
    created_at: float
    scope: Scope
    app: str
    pid: int | None
    window: WindowInfo | None
    frame: Frame
    frame_space: Space
    elements: list[ObservedElement]
    marks: dict[int, Mark]
    fingerprint: str
    image_hash: int | None = None
    screenshot_path: str | None = None
    capture_id: str | None = None
    degraded: str | None = None
    mode: Mode = "tree"
    grounded_points: list[Point] = field(default_factory=_points)

    @property
    def refs(self) -> dict[str, ObservedElement]:
        """Elements by ref (every element has one, containers included)."""
        return {el.ref: el for el in self.elements}

    @property
    def has_image(self) -> bool:
        """Whether a screenshot was taken for this observation."""
        return self.image_hash is not None

    @property
    def window_title(self) -> str:
        """Title of the observed window ("" for screen scope)."""
        return self.window.title if self.window else ""


def window_key(window: WindowInfo | None) -> str:
    """Key of a window's ref memory ("screen" for display-wide observations).

    Args:
        window: The observed window, or None for screen scope.

    Returns:
        ``"<pid>:<window_id>"`` or ``"screen"``.
    """
    return "screen" if window is None else f"{window.pid}:{window.window_id}"


class ObservationStore:
    """The last few observations of this facade process, newest last."""

    def __init__(self, keep: int = OBSERVATIONS_KEPT) -> None:
        """Create an empty store.

        Args:
            keep: How many observations stay addressable.
        """
        self._keep = keep
        self._items: OrderedDict[str, Observation] = OrderedDict()
        self._allocators: dict[str, RefAllocator] = {}

    def new_id(self) -> str:
        """A fresh observation id (``obs_`` + hex, unique among kept observations).

        Returns:
            The id.
        """
        while True:
            candidate = "obs_" + secrets.token_hex(OBSERVATION_ID_BYTES)
            if candidate not in self._items:
                return candidate

    def add(self, obs: Observation) -> None:
        """Store an observation, dropping the oldest beyond the limit.

        Args:
            obs: The observation.
        """
        self._items[obs.id] = obs
        while len(self._items) > self._keep:
            self._items.popitem(last=False)

    def get(self, obs_id: str) -> Observation | None:
        """Look up an observation.

        Args:
            obs_id: Observation id from the model.

        Returns:
            The observation, or None when unknown or expired.
        """
        return self._items.get(obs_id)

    def latest(self) -> Observation | None:
        """The newest observation, if any."""
        return next(reversed(self._items.values()), None)

    def allocator(self, window: WindowInfo | None) -> RefAllocator:
        """Ref memory for a window (created on first use).

        Args:
            window: The observed window, or None for screen scope.

        Returns:
            The allocator.
        """
        return self._allocators.setdefault(window_key(window), RefAllocator())

    def ref_in_other_observation(self, ref: str, except_id: str) -> str | None:
        """Newest other observation that knows ``ref`` (to tell STALE_REF from ELEMENT_NOT_FOUND).

        Args:
            ref: The ref the model used.
            except_id: The observation it was used with.

        Returns:
            That observation's id, or None.
        """
        for obs in reversed(self._items.values()):
            if obs.id != except_id and ref in obs.refs:
                return obs.id
        return None


def now() -> float:
    """Wall-clock time for timestamps shared with the hooks (``time.time``)."""
    return time.time()
