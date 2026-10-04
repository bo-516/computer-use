"""UI-TARS grounding over an OpenAI-compatible endpoint (vLLM, HF Inference Endpoint; goal.md §6.2).

Boundary: request building, output parsing and coordinate mapping are pure; ``locate`` performs the
HTTP calls. UI-TARS answers in different coordinate conventions depending on the version, which
goal.md calls out explicitly ("注意其输出坐标所在的缩放空间"):

* ``smart_resize`` (UI-TARS-1.5, Qwen2.5-VL family): absolute pixels of the image after the
  server's smart resize to multiples of 28 within a pixel budget;
* ``relative_1000`` (UI-TARS 1.0): coordinates in [0, 1000) of the image;
* ``image``: absolute pixels of the image as sent.

Confidence comes from agreement between samples (``base.cluster``).
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass

import httpx2

from ..config import GroundingCoords
from ..geometry import Point
from ..limits import GROUNDING_SAMPLES
from .base import Candidate, GroundingError, clamp, cluster
from .http import chat, image_part

# Qwen2.5-VL / UI-TARS-1.5 preprocessing (model card): patches of 28 px, pixel budget below.
SMART_RESIZE_FACTOR = 28
SMART_RESIZE_MIN_PIXELS = 100 * 28 * 28
SMART_RESIZE_MAX_PIXELS = 16384 * 28 * 28
RELATIVE_SCALE = 1000.0
SAMPLE_TEMPERATURE = 0.7
MAX_TOKENS = 64
PROMPT = ("Output only the coordinate of one point in your response. "
          "What element matches the following task: {description}")
_POINT_TAG = re.compile(r"<point>\s*(-?\d+(?:\.\d+)?)[\s,]+(-?\d+(?:\.\d+)?)\s*</point>")
_BOX4 = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
                   r"\s*(-?\d+(?:\.\d+)?)\s*\]")
_PAIR = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
_XY = re.compile(r'"?x"?\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*,\s*"?y"?\s*[:=]\s*(-?\d+(?:\.\d+)?)')


def smart_resize(height: int, width: int, factor: int = SMART_RESIZE_FACTOR,
                 min_pixels: int = SMART_RESIZE_MIN_PIXELS,
                 max_pixels: int = SMART_RESIZE_MAX_PIXELS) -> tuple[int, int]:
    """Image size the model actually sees (Qwen2.5-VL ``smart_resize``).

    Args:
        height: Sent image height.
        width: Sent image width.
        factor: Patch size.
        min_pixels: Lower pixel budget.
        max_pixels: Upper pixel budget.

    Returns:
        ``(height, width)`` after resizing.
    """
    h = max(factor, round(height / factor) * factor)
    w = max(factor, round(width / factor) * factor)
    if h * w > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        h = max(factor, math.floor(height / beta / factor) * factor)
        w = max(factor, math.floor(width / beta / factor) * factor)
    elif h * w < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h = math.ceil(height * beta / factor) * factor
        w = math.ceil(width * beta / factor) * factor
    return h, w


def parse_point(text: str) -> Point | None:
    """Extract one point from a model answer (several UI-TARS output styles).

    Args:
        text: Model output, e.g. ``click(start_box='(640,412)')`` or ``<point>640 412</point>``.

    Returns:
        The raw point (model coordinates), or None when nothing parses.
    """
    if m := _POINT_TAG.search(text):
        return Point(float(m.group(1)), float(m.group(2)))
    if m := _BOX4.search(text):
        x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
        return Point((x1 + x2) / 2, (y1 + y2) / 2)
    if m := _PAIR.search(text):
        return Point(float(m.group(1)), float(m.group(2)))
    if m := _XY.search(text):
        return Point(float(m.group(1)), float(m.group(2)))
    return None


def to_image(p: Point, width: int, height: int, coords: GroundingCoords) -> Point:
    """Map a model point into the sent image's pixels.

    Args:
        p: Raw model point.
        width: Sent image width.
        height: Sent image height.
        coords: Convention of the deployed model.

    Returns:
        The point in image pixels.
    """
    if coords == "relative_1000":
        return Point(p.x / RELATIVE_SCALE * width, p.y / RELATIVE_SCALE * height)
    if coords == "smart_resize":
        rh, rw = smart_resize(height, width)
        return Point(p.x * width / rw, p.y * height / rh)
    return p


@dataclass
class UITarsGrounder:
    """UI-TARS client."""

    base_url: str
    model: str
    api_key: str | None
    coords: GroundingCoords = "smart_resize"
    transport: httpx2.AsyncBaseTransport | None = None
    name: str = "uitars"

    def _payload(self, image_jpeg: bytes, description: str, n: int) -> dict[str, object]:
        """Chat request for ``n`` samples."""
        return {"model": self.model, "n": n, "temperature": SAMPLE_TEMPERATURE,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": [
                    image_part(image_jpeg),
                    {"type": "text", "text": PROMPT.format(description=description)}]}]}

    async def locate(self, image_jpeg: bytes, width: int, height: int,
                     description: str) -> list[Candidate]:
        """Sample the model and cluster its answers.

        Args:
            image_jpeg: Canonical screenshot.
            width: Image width.
            height: Image height.
            description: What to find.

        Returns:
            Candidates in image pixels, most agreed first.

        Raises:
            GroundingError: The endpoint failed or no answer parsed.
        """
        payload = self._payload(image_jpeg, description, GROUNDING_SAMPLES)
        texts = await chat(self.base_url, payload, self.api_key, self.transport)
        missing = GROUNDING_SAMPLES - len(texts)
        if missing > 0:  # servers that ignore `n` answer once; sample the rest separately
            extra = await asyncio.gather(*(chat(self.base_url,
                                                self._payload(image_jpeg, description, 1),
                                                self.api_key, self.transport)
                                           for _ in range(missing)))
            texts += [t for batch in extra for t in batch]
        points: list[Point] = []
        for text in texts:
            raw = parse_point(text)
            mapped = None if raw is None else clamp(to_image(raw, width, height, self.coords),
                                                    width, height)
            if mapped is not None:
                points.append(mapped)
        if not points:
            raise GroundingError("no coordinate in the model's answers")
        return cluster(points, total=max(GROUNDING_SAMPLES, len(texts)))
