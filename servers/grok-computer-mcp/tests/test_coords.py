"""Coordinate mapping, parametrized over DPI scale, window offset, multi-monitor layout and aspect
ratio (AGENTS.md "Tests")."""

from __future__ import annotations

import itertools

import pytest

from grok_computer_mcp.coords import (
    Space,
    canonical_size,
    detect_frame_space,
    display_for,
    from_image,
    in_image,
    make_frame,
    native_size,
    rect_to_image,
    to_image,
)
from grok_computer_mcp.geometry import Display, Point, Rect
from grok_computer_mcp.limits import (
    HOST_IMAGE_MAX_EDGE_PX,
    HOST_IMAGE_MAX_PIXELS,
    SCREENSHOT_LONG_EDGE_PX,
)

SCALES = (1.0, 1.25, 1.5, 2.0)
OFFSETS = (Point(0, 0), Point(100, 50), Point(37.5, 812.25))
# (width, height) in points: 16:9, 4:3, 21:9, portrait, tiny, very wide.
SIZES = ((1440, 810), (800, 600), (2560, 1080), (600, 1100), (320, 200), (3000, 400))

# Two monitors: a 1.0x display left of a 2.0x primary display, physical origins side by side.
LEFT = Display("left", Rect(-1920, 0, 1920, 1080), 1.0, Point(-1920, 0))
PRIMARY = Display("primary", Rect(0, 0, 1512, 982), 2.0, Point(0, 0))
TOP = Display("top", Rect(0, -1080, 1920, 1080), 1.5, Point(0, -1620))


def close(a: Point, b: Point, tol: float = 1e-6) -> bool:
    """Whether two points are equal within ``tol``."""
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol


CASES = list(itertools.product(SCALES, OFFSETS, SIZES))


@pytest.mark.parametrize("scale,offset,size", CASES)
def test_canonical_image_respects_host_thresholds(
    scale: float, offset: Point, size: tuple[int, int]
) -> None:
    display = Display("d", Rect(-5000, -5000, 20000, 20000), scale, Point(0, 0))
    frame = make_frame(Rect(offset.x, offset.y, *size), display, SCREENSHOT_LONG_EDGE_PX)
    native_w, native_h = native_size(frame.target, scale)
    assert max(frame.image_w, frame.image_h) <= SCREENSHOT_LONG_EDGE_PX
    assert max(frame.image_w, frame.image_h) < HOST_IMAGE_MAX_EDGE_PX
    assert frame.image_w * frame.image_h <= HOST_IMAGE_MAX_PIXELS
    assert frame.image_w <= native_w and frame.image_h <= native_h  # never upscaled
    # Each axis is rounded independently: the short side is within one pixel of the exact
    # proportional size (the transforms scale each axis separately, so this costs no accuracy).
    if native_w >= native_h:
        assert abs(frame.image_h - native_h * frame.image_w / native_w) <= 1
    else:
        assert abs(frame.image_w - native_w * frame.image_h / native_h) <= 1


@pytest.mark.parametrize("scale,offset,size", CASES)
@pytest.mark.parametrize(
    "space", [Space.DESKTOP_POINTS, Space.WINDOW_POINTS, Space.CAPTURE_PIXELS, Space.DESKTOP_PIXELS]
)
def test_round_trip_through_every_space(
    scale: float, offset: Point, size: tuple[int, int], space: Space
) -> None:
    display = Display("d", Rect(-5000, -5000, 20000, 20000), scale, Point(-10000, -10000))
    frame = make_frame(Rect(offset.x, offset.y, *size), display, SCREENSHOT_LONG_EDGE_PX)
    for p in (Point(0, 0), Point(frame.image_w, frame.image_h), Point(17.25, 403.5)):
        assert close(to_image(frame, from_image(frame, p, space), space), p)


@pytest.mark.parametrize("scale,offset,size", CASES)
def test_image_corners_and_center_map_to_the_window(
    scale: float, offset: Point, size: tuple[int, int]
) -> None:
    display = Display("d", Rect(-5000, -5000, 20000, 20000), scale, Point(0, 0))
    target = Rect(offset.x, offset.y, *size)
    frame = make_frame(target, display, SCREENSHOT_LONG_EDGE_PX)
    assert close(from_image(frame, Point(0, 0), Space.DESKTOP_POINTS), Point(target.x, target.y))
    corner = Point(frame.image_w, frame.image_h)
    assert close(
        from_image(frame, corner, Space.DESKTOP_POINTS), Point(target.right, target.bottom)
    )
    center = Point(frame.image_w / 2, frame.image_h / 2)
    assert close(from_image(frame, center, Space.WINDOW_POINTS), Point(size[0] / 2, size[1] / 2))


@pytest.mark.parametrize("scale,offset,size", CASES)
def test_physical_mapping_matches_goal_formula(
    scale: float, offset: Point, size: tuple[int, int]
) -> None:
    """goal.md §5.6.2: physical = window origin + image x (window physical size / image size)."""
    display = Display("d", Rect(0, 0, 10000, 10000), scale, Point(0, 0))
    target = Rect(offset.x, offset.y, *size)
    frame = make_frame(target, display, SCREENSHOT_LONG_EDGE_PX)
    p = Point(123.0, 45.5)
    physical = from_image(frame, p, Space.DESKTOP_PIXELS)
    origin_px = Point(target.x * scale, target.y * scale)
    expected = Point(
        origin_px.x + p.x * (target.w * scale / frame.image_w),
        origin_px.y + p.y * (target.h * scale / frame.image_h),
    )
    assert close(physical, expected, tol=1e-6)


@pytest.mark.parametrize(
    "display,target",
    [
        (LEFT, Rect(-1600, 200, 1200, 700)),  # window on the 1.0x display left of the primary
        (PRIMARY, Rect(100, 100, 1200, 700)),  # window on the 2.0x primary
        (TOP, Rect(300, -900, 1000, 600)),  # window on a 1.5x display above the primary
    ],
)
def test_multi_monitor_physical_origin(display: Display, target: Rect) -> None:
    assert display_for(target, (LEFT, PRIMARY, TOP)) is display
    frame = make_frame(target, display, SCREENSHOT_LONG_EDGE_PX)
    top_left = from_image(frame, Point(0, 0), Space.DESKTOP_PIXELS)
    expected = Point(
        display.origin_px.x + (target.x - display.bounds.x) * display.scale,
        display.origin_px.y + (target.y - display.bounds.y) * display.scale,
    )
    assert close(top_left, expected)
    assert frame.image_w == min(SCREENSHOT_LONG_EDGE_PX, round(target.w * display.scale)) or (
        frame.image_h == SCREENSHOT_LONG_EDGE_PX
    )


def test_window_spanning_displays_uses_the_larger_overlap() -> None:
    spanning = Rect(-300, 100, 1000, 600)  # 300 pt on LEFT, 700 pt on PRIMARY
    assert display_for(spanning, (LEFT, PRIMARY)) is PRIMARY
    assert display_for(Rect(9000, 9000, 10, 10), (LEFT, PRIMARY)) is LEFT


def test_capture_pixels_follow_the_backend_capture_size() -> None:
    """A backend capped at 1600 px still maps exactly: its PNG is a third space."""
    frame = make_frame(Rect(0, 0, 1440, 900), PRIMARY, 1280, capture_size=(1600, 1000))
    assert (frame.image_w, frame.image_h) == (1280, 800)
    assert close(from_image(frame, Point(640, 400), Space.CAPTURE_PIXELS), Point(800, 500))


def test_canonical_size_never_upscales_and_caps_pixel_count() -> None:
    assert canonical_size(800, 600, 1280) == (800, 600)
    assert canonical_size(2880, 1800, 1280) == (1280, 800)
    w, h = canonical_size(1999, 1999, 1999)
    assert w * h <= HOST_IMAGE_MAX_PIXELS and w == h


def test_rect_conversion_and_bounds_check() -> None:
    frame = make_frame(Rect(100, 50, 1440, 900), PRIMARY, 1280)
    rect = rect_to_image(frame, Rect(100, 50, 144, 90), Space.DESKTOP_POINTS)
    assert rect.rounded() == (0, 0, 128, 80)
    assert in_image(frame, Point(1280, 800)) and not in_image(frame, Point(1281, 10))


def test_make_frame_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="positive size"):
        make_frame(Rect(0, 0, 0, 10), PRIMARY, 1280)


@pytest.mark.parametrize("space", [Space.DESKTOP_POINTS, Space.WINDOW_POINTS, Space.CAPTURE_PIXELS])
def test_frame_space_detection_recovers_the_space(space: Space) -> None:
    """V14 fallback: frames generated in a known space are detected as that space."""
    frame = make_frame(Rect(400, 300, 1200, 800), PRIMARY, 1280, capture_size=(2400, 1600))
    image_rects = [Rect(10, 10, 100, 30), Rect(600, 300, 200, 40), Rect(1100, 700, 150, 60)]
    rects: list[Rect] = []
    for r in image_rects:
        a = from_image(frame, Point(r.x, r.y), space)
        b = from_image(frame, Point(r.right, r.bottom), space)
        rects.append(Rect(a.x, a.y, b.x - a.x, b.y - a.y))
    assert detect_frame_space(rects, frame) is space


def test_frame_space_detection_defaults_to_desktop_points() -> None:
    frame = make_frame(Rect(0, 0, 800, 600), PRIMARY, 1280)
    assert detect_frame_space([], frame) is Space.DESKTOP_POINTS
