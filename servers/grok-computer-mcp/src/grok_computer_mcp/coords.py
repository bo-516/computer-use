"""Coordinate spaces and the transforms between them (goal.md §5.6).

Boundary: pure functions; the only module that knows about window offsets, HiDPI backing scale and
multi-monitor layout (AGENTS.md). Every coordinate the model sees or sends is in IMAGE space: the
pixels of the observation's canonical screenshot, origin at the top-left of the observed target,
even when no image was attached. Backends declare which space they act in and which space their
element frames use; handlers convert through here and nowhere else.

Spaces:

* ``IMAGE`` - canonical screenshot pixels of the observed target (window or display).
* ``DESKTOP_POINTS`` - global logical coordinates (macOS points, Windows DIPs), origin top-left of
  the primary display; window bounds are reported here.
* ``WINDOW_POINTS`` - logical coordinates relative to the target's top-left.
* ``CAPTURE_PIXELS`` - pixels of the backend's own capture of the target (Cua's window PNG); the
  backend undoes its own scaling.
* ``DESKTOP_PIXELS`` - global physical pixels: each display's backing scale applied from its
  physical origin.

The mapping goal.md §5.6.2 states, ``physical = window origin + image x (window physical size /
image size)``, is ``from_image(frame, p, Space.DESKTOP_PIXELS)``.

This module stays whole (above the 200-line split threshold, below the 400-line limit) because
AGENTS.md requires all of this mapping to live in ``coords.py``; the plain geometry values it
works on are in ``geometry.py``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

from .geometry import Display, Frame, Point, Rect, round_half_up
from .limits import HOST_IMAGE_MAX_PIXELS

# Fraction of the image a converted frame may overflow and still count as "inside" when guessing
# a backend's frame space (V14 fallback); covers borders and shadows reported outside the window.
FRAME_SPACE_TOLERANCE = 0.02


class Space(StrEnum):
    """Coordinate spaces a backend may use for actions or element frames."""

    IMAGE = "image"
    DESKTOP_POINTS = "desktop_points"
    WINDOW_POINTS = "window_points"
    CAPTURE_PIXELS = "capture_pixels"
    DESKTOP_PIXELS = "desktop_pixels"


def native_size(target: Rect, scale: float) -> tuple[int, int]:
    """Physical pixel size of a target on a display.

    Args:
        target: Target bounds in DESKTOP_POINTS.
        scale: Backing scale factor of its display.

    Returns:
        ``(width, height)`` in physical pixels, at least 1x1.
    """
    return max(1, round_half_up(target.w * scale)), max(1, round_half_up(target.h * scale))


def canonical_size(native_w: int, native_h: int, long_edge: int,
                   max_pixels: int = HOST_IMAGE_MAX_PIXELS) -> tuple[int, int]:
    """Canonical screenshot size: long edge at most ``long_edge``, never upscaled.

    Also keeps the pixel count under the host's re-encode threshold, so the host never rescales
    the image and coordinates stay exact (goal.md §5.6.1).

    Args:
        native_w: Physical width.
        native_h: Physical height.
        long_edge: Configured long edge (1280 by default).
        max_pixels: Host pixel-count threshold.

    Returns:
        ``(width, height)`` of the canonical image.
    """
    scale = min(1.0, long_edge / max(native_w, native_h))
    pixel_scale = math.sqrt(max_pixels / (native_w * native_h))
    if pixel_scale < scale:
        # Flooring guarantees floor(w*s) * floor(h*s) <= w*h*s^2 == max_pixels; rounding up
        # could overshoot the host threshold by a row.
        return (max(1, math.floor(native_w * pixel_scale)),
                max(1, math.floor(native_h * pixel_scale)))
    return max(1, round_half_up(native_w * scale)), max(1, round_half_up(native_h * scale))


def make_frame(target: Rect, display: Display, long_edge: int,
               capture_size: tuple[int, int] | None = None) -> Frame:
    """Build the frame of an observation.

    Args:
        target: Window or display bounds in DESKTOP_POINTS (positive size).
        display: Display that holds the target.
        long_edge: Canonical long edge.
        capture_size: Size of the backend's capture in pixels, when one was taken; defaults to
            the target's native size (what a backend capture of it would be).

    Returns:
        The frame.

    Raises:
        ValueError: The target has no area.
    """
    if target.w <= 0 or target.h <= 0:
        raise ValueError("target must have a positive size")
    nat_w, nat_h = native_size(target, display.scale)
    image_w, image_h = canonical_size(nat_w, nat_h, long_edge)
    cap_w, cap_h = capture_size if capture_size else (nat_w, nat_h)
    return Frame(target, display, image_w, image_h, max(1, cap_w), max(1, cap_h))


def _to_window_points(frame: Frame, p: Point, space: Space) -> Point:
    """Convert a point from ``space`` into WINDOW_POINTS."""
    if space is Space.WINDOW_POINTS:
        return p
    if space is Space.DESKTOP_POINTS:
        return Point(p.x - frame.target.x, p.y - frame.target.y)
    if space is Space.IMAGE:
        return Point(p.x * frame.target.w / frame.image_w, p.y * frame.target.h / frame.image_h)
    if space is Space.CAPTURE_PIXELS:
        return Point(p.x * frame.target.w / frame.capture_w,
                     p.y * frame.target.h / frame.capture_h)
    disp = frame.display
    desktop = Point(disp.bounds.x + (p.x - disp.origin_px.x) / disp.scale,
                    disp.bounds.y + (p.y - disp.origin_px.y) / disp.scale)
    return _to_window_points(frame, desktop, Space.DESKTOP_POINTS)


def _from_window_points(frame: Frame, p: Point, space: Space) -> Point:
    """Convert a WINDOW_POINTS point into ``space``."""
    if space is Space.WINDOW_POINTS:
        return p
    if space is Space.DESKTOP_POINTS:
        return Point(p.x + frame.target.x, p.y + frame.target.y)
    if space is Space.IMAGE:
        return Point(p.x * frame.image_w / frame.target.w, p.y * frame.image_h / frame.target.h)
    if space is Space.CAPTURE_PIXELS:
        return Point(p.x * frame.capture_w / frame.target.w,
                     p.y * frame.capture_h / frame.target.h)
    disp = frame.display
    desktop = _from_window_points(frame, p, Space.DESKTOP_POINTS)
    return Point(disp.origin_px.x + (desktop.x - disp.bounds.x) * disp.scale,
                 disp.origin_px.y + (desktop.y - disp.bounds.y) * disp.scale)


def to_image(frame: Frame, p: Point, space: Space) -> Point:
    """Convert a point from ``space`` to IMAGE space.

    Args:
        frame: Observation frame.
        p: Point in ``space``.
        space: Source space.

    Returns:
        The point in image pixels.
    """
    return _from_window_points(frame, _to_window_points(frame, p, space), Space.IMAGE)


def from_image(frame: Frame, p: Point, space: Space) -> Point:
    """Convert an IMAGE-space point to ``space`` (what a backend acts on).

    Args:
        frame: Observation frame.
        p: Point in image pixels.
        space: Target space.

    Returns:
        The point in ``space``.
    """
    return _from_window_points(frame, _to_window_points(frame, p, Space.IMAGE), space)


def rect_to_image(frame: Frame, r: Rect, space: Space) -> Rect:
    """Convert a rectangle from ``space`` to IMAGE space.

    Args:
        frame: Observation frame.
        r: Rectangle in ``space``.
        space: Source space.

    Returns:
        The rectangle in image pixels.
    """
    a = to_image(frame, Point(r.x, r.y), space)
    b = to_image(frame, Point(r.right, r.bottom), space)
    return Rect(min(a.x, b.x), min(a.y, b.y), abs(b.x - a.x), abs(b.y - a.y))


def in_image(frame: Frame, p: Point) -> bool:
    """Whether an IMAGE-space point lies on the image.

    Args:
        frame: Observation frame.
        p: Point in image pixels.

    Returns:
        True when ``0 <= x <= width`` and ``0 <= y <= height``.
    """
    return frame.image_rect.contains(p)


def display_for(target: Rect, displays: Sequence[Display]) -> Display:
    """Pick the display holding most of ``target``.

    Args:
        target: Rectangle in DESKTOP_POINTS.
        displays: Known displays (non-empty).

    Returns:
        The display with the largest overlap, or the first display when nothing overlaps.

    Raises:
        ValueError: ``displays`` is empty.
    """
    if not displays:
        raise ValueError("no displays")
    best, best_area = displays[0], 0.0
    for display in displays:
        overlap = display.bounds.intersection(target)
        if overlap is not None and overlap.area > best_area:
            best, best_area = display, overlap.area
    return best


def detect_frame_space(rects: Sequence[Rect], frame: Frame) -> Space:
    """Guess which space a backend's element frames use (V14 fallback path).

    V14: Cua Driver documents ``frame: {x, y, w, h}`` without naming its space. The facade
    defaults to detection: the space under which most frames land inside the observed image wins;
    ties prefer DESKTOP_POINTS (the accessibility APIs' convention). Set
    ``GROK_COMPUTER_CUA_FRAME_SPACE`` once V14 is confirmed.

    Args:
        rects: Element frames in the unknown space.
        frame: Observation frame.

    Returns:
        DESKTOP_POINTS, WINDOW_POINTS or CAPTURE_PIXELS.
    """
    candidates = (Space.DESKTOP_POINTS, Space.WINDOW_POINTS, Space.CAPTURE_PIXELS)
    if not rects:
        return Space.DESKTOP_POINTS
    margin_x = frame.image_w * FRAME_SPACE_TOLERANCE
    margin_y = frame.image_h * FRAME_SPACE_TOLERANCE
    bounds = Rect(-margin_x, -margin_y, frame.image_w + 2 * margin_x,
                  frame.image_h + 2 * margin_y)
    best, best_score = Space.DESKTOP_POINTS, -1
    for space in candidates:
        score = 0
        for rect in rects:
            img = rect_to_image(frame, rect, space)
            corners = (Point(img.x, img.y), Point(img.right, img.bottom))
            if all(bounds.contains(c) for c in corners):
                score += 1
        if score > best_score:
            best, best_score = space, score
    return best
