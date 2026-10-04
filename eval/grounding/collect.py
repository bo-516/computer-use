"""Collect benchmark screenshots through the facade itself (goal.md §9.2 data).

Boundary: drives the facade over an in-process MCP client (Cua Driver backend unless
``GROK_COMPUTER_BACKEND`` says otherwise), with its state and traces in a throwaway directory.
For each ``--app`` it calls ``observe(mode="screenshot")``, saves the canonical JPEG and proposes
targets from the accessibility tree: labelled, enabled, visible, non-secure interactive elements
of varied roles. The facade's own rules apply: deny-listed apps are refused and nothing is typed
or clicked. Entries are written ``"reviewed": false`` with three proposals as ``targets`` and the
rest under ``proposals``; a person keeps 1-3 targets, checks each box, rewrites each description
the way a user would say it, and sets ``"reviewed": true``.

    uv run python eval/grounding/collect.py --out eval/grounding/data/collected \\
        --app "System Settings" --app "Visual Studio Code"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import mcp_types as types
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataset
import manifest

from grok_computer_mcp.app import create_context
from grok_computer_mcp.config import load_settings
from grok_computer_mcp.observe.roles import INTERACTIVE_ROLES
from grok_computer_mcp.server import build_server

# Proposals kept per screenshot beyond the three initial targets (reviewers pick from these).
MAX_PROPOSALS = 12
# Boxes smaller than this (px) are too small to judge a click on and are skipped.
MIN_BOX_PX = 6


def _proposal(element: Mapping[str, object], width: int, height: int) -> dict[str, object] | None:
    """A target proposal for one observed element, or None when unsuitable."""
    role, label, box = element.get("role"), element.get("label"), element.get("bbox")
    if (role not in INTERACTIVE_ROLES or not isinstance(label, str) or not label.strip()
            or element.get("secure") is True or element.get("enabled") is False
            or element.get("visible") is False or not isinstance(box, list)):
        return None
    x, y, w, h = (float(cast(float, v)) for v in cast(list[object], box))
    if w < MIN_BOX_PX or h < MIN_BOX_PX or x < 0 or y < 0 or x + w > width or y + h > height:
        return None
    return {"description": f"the {role} '{label.strip()}'", "bbox": [x, y, w, h], "role": role}


def propose(structured: Mapping[str, object]) -> list[dict[str, object]]:
    """Ranked target proposals from an observe result: one per role first, then the rest.

    Args:
        structured: ``observe`` structured content.

    Returns:
        Proposals with ids ``t1``, ``t2`` ...
    """
    image = cast(Mapping[str, object], structured.get("image") or {})
    width, height = int(cast(int, image.get("width", 0))), int(cast(int, image.get("height", 0)))
    raw = cast(list[object], structured.get("elements") or [])
    found: list[dict[str, object]] = []
    for element in raw:
        proposal = _proposal(cast(Mapping[str, object], element), width, height) \
            if isinstance(element, dict) else None
        if proposal:
            found.append(proposal)
    roles: set[object] = set()
    firsts: list[int] = []
    for i, proposal in enumerate(found):
        if proposal["role"] not in roles:
            roles.add(proposal["role"])
            firsts.append(i)
    ordered = [found[i] for i in firsts] + [p for i, p in enumerate(found) if i not in firsts]
    limit = MAX_PROPOSALS + dataset.MAX_TARGETS
    return [{"id": f"t{i + 1}", **p} for i, p in enumerate(ordered[:limit])]


def _slug(app: str) -> str:
    """File-name stem for an app."""
    return re.sub(r"[^a-z0-9]+", "-", app.lower()).strip("-") or "app"


async def collect(out: Path, apps: Sequence[str], env: Mapping[str, str], home: Path,
                  cwd: Path) -> list[str]:
    """Observe each app once and append unreviewed entries to the dataset.

    Args:
        out: Dataset directory.
        apps: Apps to capture (frontmost window of each).
        env: Facade environment (backend selection, policy home).
        home: Home directory (user policy).
        cwd: Working directory (project policy).

    Returns:
        Lines describing what was written or refused.
    """
    log: list[str] = []
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grok-grounding-collect-") as scratch:
        facade_env = {**env, "GROK_COMPUTER_STATE_DIR": f"{scratch}/state",
                      "GROK_COMPUTER_TRACE_DIR": f"{scratch}/traces"}
        ctx = create_context(load_settings(facade_env, home, cwd))
        async with Client(build_server(ctx)) as client:
            for app in apps:
                result = await client.call_tool("observe", {"app": app, "mode": "screenshot"})
                raw: object = result.structured_content
                structured = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
                images = [b for b in result.content if isinstance(b, types.ImageContent)]
                if result.is_error or not images:
                    log.append(f"{app}: skipped ({structured.get('code', 'no image')})")
                    continue
                log.append(_save(out, app, structured, base64.b64decode(images[0].data)))
    return log


def _save(out: Path, app: str, structured: Mapping[str, object], jpeg: bytes) -> str:
    """Write one screenshot and its manifest entry; return a log line."""
    proposals = propose(structured)
    if not proposals:
        return f"{app}: no labelled interactive elements to propose"
    index = 1
    while (out / f"{_slug(app)}-{index:02d}.jpg").exists():
        index += 1
    name = f"{_slug(app)}-{index:02d}.jpg"
    (out / name).write_bytes(jpeg)
    image = cast(Mapping[str, object], structured["image"])
    targets = tuple(dataset.Target(str(p["id"]), str(p["description"]),
                                   cast(tuple[float, float, float, float],
                                        tuple(cast(list[float], p["bbox"]))))
                    for p in proposals[:dataset.MAX_TARGETS])
    sample = dataset.Sample(name, int(cast(int, image["width"])), int(cast(int, image["height"])),
                            str(structured.get("app", app)), "collect", False, targets)
    line = manifest.entry(sample)
    line["proposals"] = proposals[dataset.MAX_TARGETS:]
    manifest.append_raw(out, [line])
    return f"{app}: {name} with {len(proposals)} proposals (review before use)"


def main() -> int:
    """CLI entry point."""
    cli = argparse.ArgumentParser(description="Capture grounding screenshots via the facade")
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--app", action="append", required=True)
    args = cli.parse_args()
    for line in asyncio.run(collect(args.out, args.app, dict(os.environ), Path.home(),
                                    Path.cwd())):
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
