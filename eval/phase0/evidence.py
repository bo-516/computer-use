"""Phase 0 evidence: probe logs, run results and the images found in trace exports.

Boundary: reads one run directory written by ``run_probes.py``; no network, no writes. Payload
field names are read in grok's camelCase with snake_case fallbacks, since confirming the names is
itself part of what the probes verify.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from grok_computer_mcp.observe.imaging import open_rgb

Json = dict[str, object]
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
# Base64 runs shorter than this cannot hold a screenshot; skipping them keeps scans fast.
MIN_BASE64_CHARS = 512
BASE64_RUN = re.compile(rb"[A-Za-z0-9+/]{%d,}={0,2}" % MIN_BASE64_CHARS)
# Trace members larger than this are not session logs; skip rather than load them whole.
MAX_MEMBER_BYTES = 64 * 1024 * 1024


@dataclass
class Evidence:
    """Everything one probe run directory holds."""

    codes: dict[str, Json]
    hooks: list[Json]
    server: list[Json]
    results: dict[str, Json]
    trace_images: dict[str, list[tuple[int, int]]]
    inspect: Json


def jsonl(path: Path) -> list[Json]:
    """Objects of a JSONL file ([] when missing; bad lines skipped)."""
    if not path.is_file():
        return []
    out: list[Json] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item: object = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(cast(Json, item))
    return out


def json_file(path: Path) -> Json:
    """A JSON object file ({} when missing or not an object)."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast(Json, data) if isinstance(data, dict) else {}


def image_sizes(blob: bytes) -> list[tuple[int, int]]:
    """Sizes of the images embedded in ``blob`` as base64 or stored raw."""
    found: list[tuple[int, int]] = []
    candidates = [blob] if blob.startswith(IMAGE_MAGIC) else []
    for match in BASE64_RUN.finditer(blob.replace(b"\\/", b"/")):
        try:
            candidates.append(base64.b64decode(match.group(0), validate=True))
        except (binascii.Error, ValueError):
            continue
    for data in candidates:
        if data.startswith(IMAGE_MAGIC):
            try:
                found.append(open_rgb(data).size)
            except ValueError:
                continue
    return found


def trace_images(path: Path) -> list[tuple[int, int]]:
    """Image sizes recorded anywhere in a ``grok trace --local`` export."""
    sizes: list[tuple[int, int]] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    sizes += image_sizes(handle.read())
    except (OSError, tarfile.TarError):
        return sizes
    return sizes


def load(out: Path) -> Evidence:
    """Read a probe run directory.

    Args:
        out: ``--out`` of ``run_probes.py``.

    Returns:
        The evidence (missing pieces are empty).
    """
    results = {str(r.get("run")): r for r in (json_file(p) for p in
                                              sorted((out / "results").glob("*.json")))}
    traces = {p.name.removesuffix(".tar.gz"): trace_images(p)
              for p in sorted((out / "traces").glob("*.tar.gz"))}
    codes = {k: cast(Json, v) for k, v in json_file(out / "codes.json").items()
             if isinstance(v, dict)}
    return Evidence(codes, jsonl(out / "logs" / "hooks.jsonl"),
                    jsonl(out / "logs" / "probe-mcp.jsonl"), results, traces,
                    json_file(out / "inspect.json"))


def field(payload: object, *names: str) -> object:
    """The first present field of a payload among ``names`` (None when absent)."""
    if not isinstance(payload, dict):
        return None
    data = cast(Json, payload)
    return next((data[n] for n in names if n in data), None)


def run_of(record: Json) -> str:
    """The probe run a hook or server record belongs to."""
    run = field(record.get("env"), "GROK_COMPUTER_PROBE_ID")
    return str(run or record.get("probe") or "")


def output(ev: Evidence, run_id: str) -> str:
    """Raw stdout of a run ("" when it did not run)."""
    return str(ev.results.get(run_id, {}).get("stdout", ""))
