"""Set-of-Mark: numbered boxes on the screenshot so the model picks a number, not a coordinate.

Boundary: pure image drawing (goal.md §6.1). Candidates come from accessibility boxes first and,
when the optional region parser is enabled, from text/icon regions. Colors are a fixed palette,
label size scales with the image, and at most 80 marks are drawn per page; mark numbers are global
to the observation so page 2 continues at 81.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..geometry import Point, Rect
from ..limits import MARKS_PER_PAGE, SOM_FONT_DIVISOR, SOM_MIN_FONT_PX
from .elements import ObservedElement
from .imaging import Color, font, text_size

# High-contrast palette that stays readable under white label text.
PALETTE: tuple[Color, ...] = (
    (230, 25, 75), (0, 130, 200), (60, 160, 60), (245, 130, 48), (145, 30, 180),
    (200, 40, 200), (0, 128, 128), (170, 110, 40), (128, 0, 0), (0, 0, 128),
)
LABEL_TEXT: Color = (255, 255, 255)
BOX_WIDTH_PX = 2
LABEL_PAD_PX = 2
CANDIDATE_RADIUS_PX = 10


@dataclass(frozen=True)
class Mark:
    """A numbered candidate in IMAGE space; ``ref`` is set for accessibility-backed marks."""

    number: int
    bbox: Rect
    ref: str | None
    role: str
    label: str


@dataclass(frozen=True)
class Region:
    """A non-accessibility candidate (text or icon) in IMAGE space."""

    bbox: Rect
    kind: str
    text: str


def marks_for(elements: Sequence[ObservedElement], regions: Sequence[Region],
              page: int) -> tuple[list[Mark], int]:
    """Number the candidates and return one page of marks.

    Args:
        elements: Observation elements; visible interactive ones with boxes become marks.
        regions: Extra text/icon regions (may be empty).
        page: 1-based page.

    Returns:
        ``(marks_on_page, page_count)``.
    """
    numbered: list[Mark] = []
    for el in elements:
        if el.interactive and el.visible and el.bbox is not None:
            numbered.append(Mark(len(numbered) + 1, el.bbox, el.ref, el.role, el.label))
    covered = [m.bbox for m in numbered]
    for region in regions:
        if not any(_mostly_inside(region.bbox, box) for box in covered):
            numbered.append(Mark(len(numbered) + 1, region.bbox, None, region.kind, region.text))
    pages = max(1, -(-len(numbered) // MARKS_PER_PAGE))
    start = (min(max(page, 1), pages) - 1) * MARKS_PER_PAGE
    return numbered[start:start + MARKS_PER_PAGE], pages


def _mostly_inside(inner: Rect, outer: Rect) -> bool:
    """Whether at least half of ``inner`` lies inside ``outer``."""
    overlap = inner.intersection(outer)
    return overlap is not None and inner.area > 0 and overlap.area / inner.area >= 0.5


def draw_marks(img: Image.Image, marks: Sequence[Mark]) -> Image.Image:
    """Draw numbered boxes.

    Args:
        img: Canonical screenshot (not modified).
        marks: Marks to draw.

    Returns:
        A copy with the marks drawn.
    """
    out = img.copy()
    draw = ImageDraw.Draw(out)
    face = font(max(SOM_MIN_FONT_PX, max(out.size) // SOM_FONT_DIVISOR))
    for mark in marks:
        color = PALETTE[mark.number % len(PALETTE)]
        box = mark.bbox
        draw.rectangle((box.x, box.y, box.right, box.bottom), outline=color, width=BOX_WIDTH_PX)
        text = str(mark.number)
        tw, th = text_size(draw, text, face)
        lw, lh = tw + 2 * LABEL_PAD_PX, th + 2 * LABEL_PAD_PX
        lx = min(max(box.x, 0), out.width - lw)
        ly = box.y - lh if box.y - lh >= 0 else box.y
        draw.rectangle((lx, ly, lx + lw, ly + lh), fill=color)
        draw.text((lx + LABEL_PAD_PX, ly + LABEL_PAD_PX - 1), text, fill=LABEL_TEXT, font=face)
    return out


def draw_candidates(img: Image.Image, points: Sequence[Point]) -> Image.Image:
    """Draw numbered rings at grounding candidates (low-confidence ``locate``, goal.md §6.2).

    Args:
        img: Canonical screenshot (not modified).
        points: Candidate points in IMAGE space, best first.

    Returns:
        A copy with candidates 1..n drawn.
    """
    out = img.copy()
    draw = ImageDraw.Draw(out)
    face = font(max(SOM_MIN_FONT_PX, max(out.size) // SOM_FONT_DIVISOR))
    r = CANDIDATE_RADIUS_PX
    for n, p in enumerate(points, start=1):
        color = PALETTE[n % len(PALETTE)]
        draw.ellipse((p.x - r, p.y - r, p.x + r, p.y + r), outline=color, width=BOX_WIDTH_PX + 1)
        draw.line((p.x - r, p.y, p.x + r, p.y), fill=color, width=1)
        draw.line((p.x, p.y - r, p.x, p.y + r), fill=color, width=1)
        draw.text((p.x + r + 2, p.y - r), str(n), fill=color, font=face)
    return out
