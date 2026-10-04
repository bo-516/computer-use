"""Plain geometry values shared by the facade: points, rectangles, displays, frames.

Boundary: immutable data with trivial helpers. All mapping *between* coordinate spaces (window
offset, HiDPI, multi-monitor) lives in ``coords.py`` only (AGENTS.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def round_half_up(value: float) -> int:
    """Round half-up (deterministic, unlike banker's rounding).

    Args:
        value: Number to round.

    Returns:
        The nearest integer, halves rounded up.
    """
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class Point:
    """A point in some space (floats; callers round only when a backend needs integers)."""

    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle ``(x, y, w, h)`` with non-negative size."""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        """x + w."""
        return self.x + self.w

    @property
    def bottom(self) -> float:
        """y + h."""
        return self.y + self.h

    @property
    def center(self) -> Point:
        """Center point."""
        return Point(self.x + self.w / 2, self.y + self.h / 2)

    @property
    def area(self) -> float:
        """w * h."""
        return self.w * self.h

    def contains(self, p: Point) -> bool:
        """Whether ``p`` lies inside or on the edge.

        Args:
            p: Point in the same space.

        Returns:
            True when inside.
        """
        return self.x <= p.x <= self.right and self.y <= p.y <= self.bottom

    def intersection(self, other: Rect) -> Rect | None:
        """Overlap with ``other``.

        Args:
            other: Rectangle in the same space.

        Returns:
            The overlap, or None when they do not overlap with positive area.
        """
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def rounded(self) -> tuple[int, int, int, int]:
        """Integer ``[x, y, w, h]`` for display.

        Returns:
            The rectangle rounded half-up.
        """
        return (round_half_up(self.x), round_half_up(self.y), round_half_up(self.w),
                round_half_up(self.h))


@dataclass(frozen=True)
class Display:
    """One monitor: logical bounds in DESKTOP_POINTS, backing scale, physical origin."""

    id: str
    bounds: Rect
    scale: float
    origin_px: Point


@dataclass(frozen=True)
class Frame:
    """Geometry of one observation: the target shown, its display, image and capture sizes."""

    target: Rect
    display: Display
    image_w: int
    image_h: int
    capture_w: int
    capture_h: int

    @property
    def image_rect(self) -> Rect:
        """The whole image as a rectangle in IMAGE space."""
        return Rect(0, 0, self.image_w, self.image_h)
