"""Draw fake windows so screenshot, Set-of-Mark and staleness paths run without a desktop.

Boundary: pure rendering of a ``FakeWindow``/``FakeScene`` into PNG bytes at a given backing
scale. The look is schematic (boxes, labels, values); what matters for tests is that pixels move
when the scene changes and that geometry matches the node frames exactly.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from ..geometry import Display, Rect
from ..observe.imaging import Color, font, resize
from .fake_scene import FakeScene, FakeWindow

BACKGROUND: Color = (246, 246, 246)
DESKTOP: Color = (40, 60, 90)
BORDER: Color = (90, 90, 90)
TEXT: Color = (20, 20, 20)
BUTTON: Color = (205, 225, 250)
FIELD: Color = (255, 255, 255)
ON: Color = (40, 140, 70)
DISABLED: Color = (200, 200, 200)
BASE_FONT_PT = 11
_ON_VALUES = ("1", "true", "on")


def _scaled(rect: Rect, scale: float, dx: float = 0.0, dy: float = 0.0) -> tuple[float, float,
                                                                                  float, float]:
    """A WINDOW_POINTS rect as a pixel box, offset by ``(dx, dy)`` pixels."""
    return (dx + rect.x * scale, dy + rect.y * scale, dx + rect.right * scale,
            dy + rect.bottom * scale)


def draw_window(img: Image.Image, window: FakeWindow, scale: float, dx: float = 0.0,
                dy: float = 0.0) -> None:
    """Draw a window's visible nodes onto ``img``.

    Args:
        img: Canvas.
        window: Window to draw.
        scale: Backing scale (pixels per point).
        dx: Horizontal pixel offset of the window on the canvas.
        dy: Vertical pixel offset of the window on the canvas.
    """
    draw = ImageDraw.Draw(img)
    face = font(max(8, round(BASE_FONT_PT * scale)))
    draw.rectangle(_scaled(Rect(0, 0, window.bounds.w, window.bounds.h), scale, dx, dy),
                   fill=BACKGROUND, outline=BORDER)
    for node, _, _ in window.walk():
        if node.frame is None or node.id == window.root:
            continue
        box = _scaled(node.frame, scale, dx, dy)
        role = node.role.lower()
        fill = DISABLED if not node.enabled else (
            FIELD if node.accepts_text else BUTTON if "button" in role else None)
        draw.rectangle(box, fill=fill, outline=BORDER)
        text = node.label
        if node.accepts_text and node.value:
            secure = "secure" in role or "secure" in node.subrole.lower()
            text = "•" * len(node.value) if secure else node.value
        if "check" in role or "switch" in node.subrole.lower():
            on = (node.value or "").strip().lower() in _ON_VALUES
            mark = (box[0] + 2, box[1] + 2, box[0] + 2 + (box[3] - box[1]) - 4, box[3] - 2)
            draw.rectangle(mark, fill=ON if on else FIELD, outline=BORDER)
        draw.text((box[0] + 4 * scale + (box[3] - box[1] if "check" in role else 0), box[1] + 2),
                  text, fill=TEXT, font=face)


def png(img: Image.Image) -> bytes:
    """Encode as PNG (the format real drivers return)."""
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def render_window(window: FakeWindow, scale: float, cap_long_edge: int | None) -> Image.Image:
    """Render one window at its native pixel size, optionally capped like a driver would.

    Args:
        window: Window to render.
        scale: Backing scale of its display.
        cap_long_edge: Downscale so the long edge is at most this (None: native).

    Returns:
        The rendered image.
    """
    size = (max(1, round(window.bounds.w * scale)), max(1, round(window.bounds.h * scale)))
    img = Image.new("RGB", size, BACKGROUND)
    draw_window(img, window, scale)
    if cap_long_edge and max(size) > cap_long_edge:
        ratio = cap_long_edge / max(size)
        img = resize(img, (max(1, round(size[0] * ratio)), max(1, round(size[1] * ratio))))
    return img


def render_screen(scene: FakeScene, display: Display) -> Image.Image:
    """Render every on-screen window of a display in z order.

    Args:
        scene: The scene.
        display: Display to render (native pixels).

    Returns:
        The rendered desktop.
    """
    scale = display.scale
    size = (round(display.bounds.w * scale), round(display.bounds.h * scale))
    img = Image.new("RGB", size, DESKTOP)
    for window in sorted(scene.windows.values(), key=lambda w: w.z_index):
        if window.on_screen and display.bounds.intersection(window.bounds) is not None:
            draw_window(img, window, scale, (window.bounds.x - display.bounds.x) * scale,
                        (window.bounds.y - display.bounds.y) * scale)
    return img
