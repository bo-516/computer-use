"""Grounding dataset storage: reading and appending a dataset directory (goal.md §9.2).

Boundary: file I/O under one dataset directory; the manifest format and its validation live in
``dataset``. ``load`` validates every line and checks that each named image exists.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from dataset import DatasetError, Sample, parse_sample

MANIFEST = "manifest.jsonl"


def load(root: Path, *, include_unreviewed: bool = False) -> list[Sample]:
    """Read and validate a dataset directory.

    Args:
        root: Dataset directory.
        include_unreviewed: Also return entries nobody has reviewed yet.

    Returns:
        Samples in manifest order.

    Raises:
        DatasetError: A malformed entry, a missing image, or a duplicate image.
        OSError: The manifest cannot be read.
    """
    samples: list[Sample] = []
    seen: set[str] = set()
    lines = (root / MANIFEST).read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        where = f"{MANIFEST}:{number}"
        try:
            raw: object = json.loads(line)
        except ValueError as exc:
            raise DatasetError(f"{where}: not JSON") from exc
        if not isinstance(raw, dict):
            raise DatasetError(f"{where}: expected an object")
        sample = parse_sample(cast(dict[str, object], raw), where)
        if sample.image in seen:
            raise DatasetError(f"{where}: {sample.image} listed twice")
        if not (root / sample.image).is_file():
            raise DatasetError(f"{where}: {sample.image} not found")
        seen.add(sample.image)
        if sample.reviewed or include_unreviewed:
            samples.append(sample)
    return samples


def entry(sample: Sample) -> dict[str, object]:
    """The manifest line of a sample.

    Args:
        sample: Sample.

    Returns:
        JSON-ready object.
    """
    return {"image": sample.image, "width": sample.width, "height": sample.height,
            "app": sample.app, "source": sample.source, "reviewed": sample.reviewed,
            "targets": [{"id": t.id, "description": t.description,
                         "bbox": [round(v, 1) for v in t.bbox]} for t in sample.targets]}


def append(root: Path, samples: Sequence[Sample]) -> None:
    """Append samples to a dataset's manifest (created when missing).

    Args:
        root: Dataset directory.
        samples: Samples whose images are already in ``root``.
    """
    append_raw(root, [entry(s) for s in samples])


def append_raw(root: Path, lines: Sequence[Mapping[str, object]]) -> None:
    """Append manifest lines that may carry extra fields (``collect.py`` proposals).

    Args:
        root: Dataset directory.
        lines: Objects in the manifest format.
    """
    root.mkdir(parents=True, exist_ok=True)
    with (root / MANIFEST).open("a", encoding="utf-8") as sink:
        for line in lines:
            sink.write(json.dumps(line, ensure_ascii=False) + "\n")
