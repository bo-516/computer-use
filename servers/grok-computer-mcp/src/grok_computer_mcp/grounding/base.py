"""Grounding interface: natural-language target -> image coordinates (goal.md §6.2).

Boundary: the protocol plus pure helpers. Implementations call an external model over HTTP
(``uitars.py``, ``xai.py``); the facade always hands them the canonical, policy-masked JPEG of the
current observation and gets back candidates in that image's pixel space.

Models rarely report a usable confidence, so implementations sample several answers and
``cluster`` turns agreement into confidence: the share of samples that land together.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..geometry import Point, Rect
from ..limits import GROUNDING_CLUSTER_RADIUS_PX, GROUNDING_MAX_CANDIDATES


class GroundingError(Exception):
    """The grounding endpoint failed or answered something unparseable."""


@dataclass(frozen=True)
class Candidate:
    """A located target in IMAGE space."""

    point: Point
    confidence: float
    bbox: Rect | None = None


class Grounder(Protocol):
    """A grounding model client."""

    name: str

    async def locate(self, image_jpeg: bytes, width: int, height: int,
                     description: str) -> list[Candidate]:
        """Find ``description`` in the image.

        Args:
            image_jpeg: Canonical screenshot (JPEG).
            width: Image width in pixels.
            height: Image height in pixels.
            description: What to find.

        Returns:
            Candidates, best first, with points inside the image.

        Raises:
            GroundingError: The endpoint failed or the answer could not be parsed.
        """
        ...


def cluster(points: Sequence[Point], radius: float = GROUNDING_CLUSTER_RADIUS_PX,
            limit: int = GROUNDING_MAX_CANDIDATES, total: int | None = None) -> list[Candidate]:
    """Group sampled points; confidence is the share of samples in a group.

    Args:
        points: One point per usable sample (already in image space).
        radius: Samples closer than this to a group's centre join it.
        limit: Maximum candidates returned.
        total: Samples requested (unusable answers count against confidence); defaults to
            ``len(points)``.

    Returns:
        Group centres, most supported first.
    """
    groups: list[list[Point]] = []
    for p in points:
        for group in groups:
            cx = sum(q.x for q in group) / len(group)
            cy = sum(q.y for q in group) / len(group)
            if (p.x - cx) ** 2 + (p.y - cy) ** 2 <= radius ** 2:
                group.append(p)
                break
        else:
            groups.append([p])
    total = max(1, total if total is not None else len(points))
    ranked = sorted(groups, key=len, reverse=True)[:limit]
    return [Candidate(Point(sum(q.x for q in g) / len(g), sum(q.y for q in g) / len(g)),
                      len(g) / total) for g in ranked]


def clamp(p: Point, width: int, height: int) -> Point | None:
    """Keep points on the image; drop those clearly outside it.

    Args:
        p: Candidate point.
        width: Image width.
        height: Image height.

    Returns:
        The point clamped to the image, or None when it lies more than a pixel outside.
    """
    if p.x < -1 or p.y < -1 or p.x > width + 1 or p.y > height + 1:
        return None
    return Point(min(max(p.x, 0.0), float(width)), min(max(p.y, 0.0), float(height)))
