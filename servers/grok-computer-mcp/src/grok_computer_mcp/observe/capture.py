"""Canonical screenshots: resize to the observation frame, mask, encode (goal.md §5.6.1, §7.6).

Boundary: pure image transforms over in-memory bytes. The canonical size comes from the frame
(``coords.make_frame``), never from the image, so a tree-only observation and the next screenshot
share one coordinate space. Dimensions are never changed to meet the byte target; only JPEG
quality steps down, because dimensions *are* the coordinate system.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..backend.base import Capture
from ..geometry import Frame, Rect
from ..limits import (
    HOST_IMAGE_MAX_BYTES,
    HOST_IMAGE_MAX_EDGE_PX,
    HOST_IMAGE_MAX_PIXELS,
    JPEG_FALLBACK_QUALITIES,
    JPEG_QUALITY,
    TARGET_IMAGE_BYTES,
)
from .imaging import encode_jpeg, font, open_rgb, resize, text_size

MASK_FILL = (32, 32, 32)
MASK_TEXT = (220, 220, 220)
MASK_LABEL = "hidden by policy"
MASK_FONT_PX = 14


@dataclass(frozen=True)
class EncodedImage:
    """A screenshot ready for MCP image content."""

    data: bytes
    width: int
    height: int
    quality: int
    mime: str = "image/jpeg"


def canonical(capture: Capture, frame: Frame) -> Image.Image:
    """Decode a backend capture and resize it to the frame's image size.

    Args:
        capture: Backend capture (any size).
        frame: Observation frame.

    Returns:
        RGB image of exactly ``frame.image_w x frame.image_h``.

    Raises:
        ValueError: The capture cannot be decoded.
    """
    return resize(open_rgb(capture.data), (frame.image_w, frame.image_h))


def mask(img: Image.Image, rects: Sequence[Rect]) -> Image.Image:
    """Paint over regions that must not reach the model (deny-listed windows, goal.md §7.6).

    Args:
        img: Canonical image.
        rects: IMAGE-space regions to hide.

    Returns:
        A masked copy (the input is untouched); ``img`` itself when there is nothing to hide.
    """
    if not rects:
        return img
    out = img.copy()
    draw = ImageDraw.Draw(out)
    face = font(MASK_FONT_PX)
    for rect in rects:
        clipped = Rect(0, 0, out.width, out.height).intersection(rect)
        if clipped is None:
            continue
        draw.rectangle((clipped.x, clipped.y, clipped.right, clipped.bottom), fill=MASK_FILL)
        tw, th = text_size(draw, MASK_LABEL, face)
        if tw < clipped.w and th < clipped.h:
            draw.text((clipped.x + (clipped.w - tw) / 2, clipped.y + (clipped.h - th) / 2),
                      MASK_LABEL, fill=MASK_TEXT, font=face)
    return out


def encode(img: Image.Image) -> EncodedImage:
    """Encode as JPEG within the size budget without changing dimensions.

    Args:
        img: Canonical RGB image.

    Returns:
        The first quality in ``(80, 70, ...)`` that fits 400 KB, else the smallest encoding.

    Raises:
        ValueError: The image breaks a host threshold (dimensions or the 1.5 MB limit) and would
            be rescaled by the host, which would silently break coordinates.
    """
    if max(img.size) >= HOST_IMAGE_MAX_EDGE_PX or img.width * img.height > HOST_IMAGE_MAX_PIXELS:
        raise ValueError(f"image {img.width}x{img.height} exceeds host thresholds")
    best: EncodedImage | None = None
    for quality in (JPEG_QUALITY, *JPEG_FALLBACK_QUALITIES):
        data = encode_jpeg(img, quality)
        best = EncodedImage(data, img.width, img.height, quality)
        if len(data) <= TARGET_IMAGE_BYTES:
            return best
    assert best is not None
    if len(best.data) > HOST_IMAGE_MAX_BYTES:
        raise ValueError(f"screenshot is {len(best.data)} bytes even at quality {best.quality}")
    return best
