"""Grounding benchmark dataset: canonical screenshots with labelled targets (goal.md §9.2).

Boundary: pure; the manifest format and its validation (``manifest`` reads and writes dataset
directories). A dataset is a directory holding ``manifest.jsonl`` and the images it names, one
entry per line::

    {"image": "settings-01.jpg", "width": 1280, "height": 800, "app": "System Settings",
     "source": "collect", "reviewed": true,
     "targets": [{"id": "t1", "description": "the Wi-Fi switch", "bbox": [x, y, w, h]}]}

Images are canonical observations (long edge at most 1280 px, goal.md §5.4), so a model sees what
``locate`` would send it. ``bbox`` uses the facade's ``[x, y, w, h]`` image-pixel convention.
``source`` is ``collect`` (``collect.py``), ``synthetic`` (``synth.py``) or ``public:<name>`` for
a subset of a public GUI grounding set. Entries proposed by ``collect.py`` stay
``"reviewed": false`` until a person has checked the boxes and written the descriptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from grok_computer_mcp.limits import SCREENSHOT_LONG_EDGE_PX

# goal.md §9.2: each screenshot labels 1-3 targets.
MIN_TARGETS = 1
MAX_TARGETS = 3
# A description is one short noun phrase, like the ones the subagent passes to ``locate``.
MAX_DESCRIPTION_CHARS = 200


class DatasetError(ValueError):
    """A manifest entry is malformed or names a missing image."""


@dataclass(frozen=True)
class Target:
    """One element to find, with its ground-truth box in image pixels."""

    id: str
    description: str
    bbox: tuple[float, float, float, float]

    def contains(self, x: float, y: float) -> bool:
        """Whether an image point lies inside the box (edges included).

        Args:
            x: Image x.
            y: Image y.

        Returns:
            True for a hit.
        """
        bx, by, bw, bh = self.bbox
        return bx <= x <= bx + bw and by <= y <= by + bh


@dataclass(frozen=True)
class Sample:
    """One screenshot and its targets."""

    image: str
    width: int
    height: int
    app: str
    source: str
    reviewed: bool
    targets: tuple[Target, ...]


def _number(value: object, where: str) -> float:
    """A JSON number (booleans rejected)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DatasetError(f"{where}: expected a number")
    return float(value)


def _text(value: object, where: str) -> str:
    """A non-empty JSON string."""
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{where}: expected a non-empty string")
    return value


def parse_target(raw: object, width: int, height: int, where: str) -> Target:
    """Validate one target.

    Args:
        raw: Decoded target object.
        width: Image width.
        height: Image height.
        where: Location for error messages.

    Returns:
        The target.

    Raises:
        DatasetError: Missing fields, an empty or long description, or a box that is empty or
            leaves the image.
    """
    if not isinstance(raw, dict):
        raise DatasetError(f"{where}: target must be an object")
    item = cast(dict[str, object], raw)
    description = _text(item.get("description"), f"{where}.description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise DatasetError(f"{where}: description longer than {MAX_DESCRIPTION_CHARS} chars")
    box = item.get("bbox")
    if not isinstance(box, list) or len(cast(list[object], box)) != 4:
        raise DatasetError(f"{where}.bbox: expected [x, y, w, h]")
    x, y, w, h = (_number(v, f"{where}.bbox") for v in cast(list[object], box))
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        raise DatasetError(f"{where}.bbox: empty or outside the {width}x{height} image")
    return Target(_text(item.get("id"), f"{where}.id"), description, (x, y, w, h))


def parse_sample(raw: Mapping[str, object], where: str) -> Sample:
    """Validate one manifest entry (the image file itself is checked by ``manifest.load``).

    Args:
        raw: Decoded manifest line.
        where: Location for error messages.

    Returns:
        The sample.

    Raises:
        DatasetError: The entry breaks the format above.
    """
    image = _text(raw.get("image"), f"{where}.image")
    if PurePosixPath(image).is_absolute() or ".." in PurePosixPath(image).parts:
        raise DatasetError(f"{where}.image: must be a path inside the dataset directory")
    width, height = raw.get("width"), raw.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise DatasetError(f"{where}: width and height must be positive integers")
    if max(width, height) > SCREENSHOT_LONG_EDGE_PX:
        raise DatasetError(f"{where}: not a canonical image (long edge > "
                           f"{SCREENSHOT_LONG_EDGE_PX} px)")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list):
        raise DatasetError(f"{where}.targets: expected a list")
    targets = tuple(parse_target(t, width, height, f"{where}.targets[{i}]")
                    for i, t in enumerate(cast(list[object], targets_raw)))
    if not MIN_TARGETS <= len(targets) <= MAX_TARGETS:
        raise DatasetError(f"{where}: {MIN_TARGETS}-{MAX_TARGETS} targets per image")
    if len({t.id for t in targets}) != len(targets):
        raise DatasetError(f"{where}: duplicate target ids")
    return Sample(image, width, height, str(raw.get("app", "")),
                  str(raw.get("source", "collect")), raw.get("reviewed") is True, targets)
