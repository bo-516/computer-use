"""Typed adapter over the few Pillow calls the facade uses.

Boundary: Pillow I/O on in-memory bytes only (no files). Some Pillow stubs leave parameters
partially unknown (``Image.resize`` accepts numpy arrays), which strict type checking rejects;
calls go through ``Protocol`` views with exact signatures instead of suppressing the checker.
"""

from __future__ import annotations

import io
from typing import Protocol, cast

from PIL import Image, ImageDraw, ImageFont

Color = tuple[int, int, int]


class _ResizableImage(Protocol):
    """The part of ``Image.Image`` used for resizing, fully typed."""

    def resize(self, size: tuple[int, int], resample: int | None = None) -> Image.Image:
        """Return a resized copy."""
        ...


def open_rgb(data: bytes) -> Image.Image:
    """Decode an image and convert it to RGB.

    Args:
        data: PNG or JPEG bytes.

    Returns:
        A fully loaded RGB image.

    Raises:
        ValueError: The bytes are not a decodable image.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return img.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"cannot decode image: {exc}") from exc


def resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """High-quality resize (Lanczos); returns ``img`` itself when the size already matches.

    Args:
        img: Source image.
        size: ``(width, height)``.

    Returns:
        The resized image.
    """
    if img.size == size:
        return img
    return cast(_ResizableImage, img).resize(size, Image.Resampling.LANCZOS)


def gray_thumbnail(img: Image.Image, size: tuple[int, int]) -> bytes:
    """Grayscale thumbnail as raw bytes, one byte per pixel, row-major.

    Args:
        img: Source image.
        size: ``(width, height)`` of the thumbnail.

    Returns:
        ``width * height`` bytes.
    """
    small = cast(_ResizableImage, img.convert("L")).resize(size, Image.Resampling.BILINEAR)
    return small.tobytes()


def encode_jpeg(img: Image.Image, quality: int) -> bytes:
    """Encode as baseline JPEG.

    Args:
        img: RGB image.
        quality: JPEG quality (1-95).

    Returns:
        The JPEG bytes.
    """
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Pillow's bundled font at ``size`` px (no font files shipped with the package).

    Args:
        size: Pixel size.

    Returns:
        A scalable font when FreeType is available, else the bitmap default.
    """
    try:
        return ImageFont.load_default(size=size)
    except OSError:
        return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str,
              face: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> tuple[int, int]:
    """Width and height of rendered text.

    Args:
        draw: Drawing context.
        text: Text to measure.
        face: Font.

    Returns:
        ``(width, height)`` in pixels.
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
    return int(right - left), int(bottom - top)
