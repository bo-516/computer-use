"""Screen-change detection for ``STALE_OBSERVATION`` (goal.md §5.6.4).

Boundary: pure. Two signals decide whether the screen still matches an observation:

* a tree fingerprint over the *layout* of interactive elements (ref, role, enabled, box on a 4 px
  grid). Labels and values are left out so a ticking label (a timer, a progress text) cannot
  stale every action; the action handlers compare the *target's* role, label, value and enabled
  state separately, so a switch that is already on, or a button that changed meaning in place, is
  still refused;
* a perceptual difference hash of the screenshot, for coordinate and mark actions on surfaces
  whose tree is thin.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from PIL import Image

from ..geometry import Frame
from ..limits import DHASH_SIZE, FINGERPRINT_GRID_PX
from .elements import ObservedElement
from .imaging import gray_thumbnail

FINGERPRINT_HEX_CHARS = 16


def _grid(value: float) -> int:
    """Snap a coordinate to the fingerprint grid."""
    return round(value / FINGERPRINT_GRID_PX)


def tree_fingerprint(elements: Sequence[ObservedElement], frame: Frame) -> str:
    """Fingerprint the actionable layout of an observation.

    Args:
        elements: Observation elements.
        frame: Observation frame (its image size is part of the layout).

    Returns:
        A short hex digest; equal digests mean refs and coordinates still hold.
    """
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"{frame.image_w}x{frame.image_h}".encode())
    for el in elements:
        if not el.interactive:
            continue
        box = el.bbox
        grid = (_grid(box.x), _grid(box.y), _grid(box.w), _grid(box.h)) if box else None
        digest.update(f"\n{el.ref}|{el.role}|{el.enabled}|{grid}".encode())
    return digest.hexdigest()[:FINGERPRINT_HEX_CHARS]


def dhash(image: Image.Image, size: int = DHASH_SIZE) -> int:
    """Difference hash: compare each pixel of a small grayscale thumbnail with its right neighbour.

    Args:
        image: Screenshot in any mode.
        size: Hash side; the hash has ``size * size`` bits.

    Returns:
        The hash as an integer.
    """
    pixels = gray_thumbnail(image, (size + 1, size))
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two hashes.

    Args:
        a: First hash.
        b: Second hash.

    Returns:
        The Hamming distance.
    """
    return (a ^ b).bit_count()
