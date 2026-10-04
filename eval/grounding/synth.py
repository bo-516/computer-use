"""Synthetic benchmark screenshots: drawn settings windows with known boxes (goal.md §9.2).

Boundary: Pillow drawing and file output under the target directory. Synthetic images exercise
the pipeline (dataset format, clients, scoring) and give a quick, cheap sanity check of a model.
They do not replace the 100 collected screenshots §9.2 asks for: real apps have denser layouts,
icons and themes. Output is deterministic for a given seed.

    uv run python eval/grounding/synth.py --out eval/grounding/data/synthetic --count 30
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataset
import manifest

from grok_computer_mcp.limits import JPEG_QUALITY
from grok_computer_mcp.observe.imaging import encode_jpeg, font, text_size

# A canonical 16:10 observation: long edge = the facade's 1280 px (goal.md §5.4).
WIDTH, HEIGHT = 1280, 800
FONT_PX = 15
TITLE_BAR_PX = 32
ROW_PX = 34
Color = tuple[int, int, int]
THEMES: dict[str, dict[str, Color]] = {
    "light": {"desk": (92, 120, 150), "win": (246, 246, 246), "bar": (225, 225, 225),
              "side": (234, 234, 238), "text": (30, 30, 30), "box": (255, 255, 255),
              "line": (170, 170, 170), "accent": (10, 110, 230)},
    "dark": {"desk": (40, 44, 52), "win": (36, 36, 38), "bar": (52, 52, 56),
             "side": (44, 44, 48), "text": (225, 225, 225), "box": (60, 60, 64),
             "line": (95, 95, 100), "accent": (64, 150, 255)},
}
SECTIONS = ("General", "Appearance", "Network", "Privacy", "Notifications", "Accounts", "Storage",
            "Updates", "Keyboard", "Display")
TOGGLES = ("Launch at login", "Show in menu bar", "Automatic updates", "Send crash reports",
           "Dark mode", "Play sounds", "Use large text", "Sync over cellular")
FIELDS = ("Display name", "Email", "Server address", "Port", "Proxy", "Username")
BUTTONS = ("Save", "Cancel", "Apply", "Reset", "Export", "Import", "Sign out", "Check now")


@dataclass(frozen=True)
class Drawn:
    """A drawn element that can become a target."""

    kind: str
    description: str
    bbox: tuple[float, float, float, float]


def _draw_window(draw: ImageDraw.ImageDraw, rng: random.Random, theme: dict[str, Color],
                 frame: tuple[int, int, int, int]) -> list[Drawn]:
    """Draw one settings window; return its targetable elements."""
    face = font(FONT_PX)
    x0, y0, w, h = frame
    draw.rectangle((x0, y0, x0 + w, y0 + h), fill=theme["win"], outline=theme["line"])
    draw.rectangle((x0, y0, x0 + w, y0 + TITLE_BAR_PX), fill=theme["bar"])
    out = [Drawn("close", "the close button in the window's title bar",
                 (x0 + 8, y0 + 8, 16, 16))]
    draw.ellipse((x0 + 8, y0 + 8, x0 + 24, y0 + 24), fill=(236, 95, 90))
    draw.text((x0 + w // 2 - 60, y0 + 8), "Settings", fill=theme["text"], font=face)
    side_w = 200
    draw.rectangle((x0, y0 + TITLE_BAR_PX, x0 + side_w, y0 + h), fill=theme["side"])
    sections = rng.sample(SECTIONS, rng.randint(6, 8))
    for i, name in enumerate(sections):
        top = y0 + TITLE_BAR_PX + 12 + i * ROW_PX
        if i == 0:
            draw.rectangle((x0 + 6, top, x0 + side_w - 6, top + ROW_PX - 4), fill=theme["accent"])
        draw.text((x0 + 18, top + 8), name, fill=theme["text"], font=face)
        out.append(Drawn("section", f"the '{name}' item in the sidebar",
                         (x0 + 6, top, side_w - 12, ROW_PX - 4)))
    left, top = x0 + side_w + 32, y0 + TITLE_BAR_PX + 24
    draw.text((left, top), sections[0], fill=theme["text"], font=font(FONT_PX + 7))
    top += 48
    for label in rng.sample(TOGGLES, rng.randint(2, 4)):
        draw.rectangle((left, top, left + 18, top + 18), fill=theme["box"], outline=theme["line"])
        if rng.random() < 0.5:
            draw.line((left + 4, top + 9, left + 8, top + 14, left + 15, top + 4),
                      fill=theme["accent"], width=2)
        draw.text((left + 28, top + 1), label, fill=theme["text"], font=face)
        out.append(Drawn("toggle", f"the checkbox labelled '{label}'", (left, top, 18, 18)))
        top += ROW_PX
    top += 12
    for label in rng.sample(FIELDS, rng.randint(1, 3)):
        draw.text((left, top + 6), label, fill=theme["text"], font=face)
        box = (left + 160, top, min(320, x0 + w - left - 200), 28)
        draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), fill=theme["box"],
                       outline=theme["line"])
        out.append(Drawn("field", f"the text field for '{label}'", box))
        top += ROW_PX + 8
    right = x0 + w - 24
    for label in rng.sample(BUTTONS, rng.randint(2, 3)):
        tw, th = text_size(draw, label, face)
        bw, bh = tw + 32, th + 16
        bx, by = right - bw, y0 + h - bh - 20
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=6, fill=theme["box"],
                               outline=theme["line"])
        draw.text((bx + 16, by + 6), label, fill=theme["text"], font=face)
        out.append(Drawn("button", f"the '{label}' button", (bx, by, bw, bh)))
        right = bx - 12
    return out


def make(index: int, rng: random.Random) -> tuple[bytes, dataset.Sample]:
    """Draw one screenshot and pick 1-3 targets of different kinds.

    Args:
        index: Sequence number (used in the file name).
        rng: Random source.

    Returns:
        JPEG bytes and the sample.
    """
    theme = THEMES[rng.choice(sorted(THEMES))]
    img = Image.new("RGB", (WIDTH, HEIGHT), theme["desk"])
    draw = ImageDraw.Draw(img)
    x0, y0 = rng.randint(0, 180), rng.randint(0, 90)
    frame = (x0, y0, rng.randint(860, WIDTH - x0 - 1), rng.randint(560, HEIGHT - y0 - 1))
    drawn = _draw_window(draw, rng, theme, frame)
    kinds = rng.sample(sorted({d.kind for d in drawn}), rng.randint(dataset.MIN_TARGETS,
                                                                    dataset.MAX_TARGETS))
    targets = tuple(dataset.Target(f"t{i + 1}", pick.description, pick.bbox)
                    for i, pick in enumerate(rng.choice([d for d in drawn if d.kind == k])
                                             for k in kinds))
    name = f"synth-{index:04d}.jpg"
    return encode_jpeg(img, JPEG_QUALITY), dataset.Sample(name, WIDTH, HEIGHT, "Settings",
                                                          "synthetic", True, targets)


def generate(out: Path, count: int, seed: int) -> list[dataset.Sample]:
    """Write ``count`` screenshots and a fresh manifest to ``out``.

    Args:
        out: Dataset directory (its manifest is replaced).
        count: Number of screenshots.
        seed: Random seed.

    Returns:
        The samples written.
    """
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / manifest.MANIFEST).unlink(missing_ok=True)
    samples: list[dataset.Sample] = []
    for index in range(1, count + 1):
        jpeg, sample = make(index, rng)
        (out / sample.image).write_bytes(jpeg)
        samples.append(sample)
    manifest.append(out, samples)
    return samples


def main() -> int:
    """CLI entry point."""
    cli = argparse.ArgumentParser(description="Generate synthetic grounding screenshots")
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--count", type=int, default=30)
    cli.add_argument("--seed", type=int, default=7)
    args = cli.parse_args()
    samples = generate(args.out, args.count, args.seed)
    print(f"wrote {len(samples)} screenshots to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
