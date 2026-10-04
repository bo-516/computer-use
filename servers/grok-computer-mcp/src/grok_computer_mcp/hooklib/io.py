"""Small, failure-tolerant file and stdio helpers for the hooks.

Boundary: the only module in ``hooklib`` that touches files or stdio. Every function either
returns a status or swallows ``OSError``/``ValueError`` and reports failure, because the hooks
must always reach a defined outcome (guard: a decision; audit and verify gate: exit 0). Writes are
limited to the audit log and the asked-apps state (AGENTS.md).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import IO, Dict, Tuple

# Largest policy or state file we read. The state contract is <= 64 KB (AGENTS.md); the margin
# tolerates an older writer without letting a corrupt file stall the 5 s hook budget.
MAX_JSON_FILE_BYTES = 256 * 1024
# Largest hook payload we parse. The host clips tool inputs and messages well below this.
MAX_STDIN_BYTES = 4 * 1024 * 1024

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"


def read_json_file(path: str) -> Tuple[str, object]:
    """Read and decode a JSON file.

    Args:
        path: File to read.

    Returns:
        ``("ok", value)``, ``("missing", None)`` or ``("error", message)``.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_JSON_FILE_BYTES + 1)
    except FileNotFoundError:
        return STATUS_MISSING, None
    except OSError as exc:
        return STATUS_ERROR, f"cannot read {path}: {exc.strerror or exc}"
    if len(data) > MAX_JSON_FILE_BYTES:
        return STATUS_ERROR, f"{path} is larger than {MAX_JSON_FILE_BYTES} bytes"
    try:
        return STATUS_OK, json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return STATUS_ERROR, f"{path} is not valid JSON: {exc}"


def read_stdin_json(stream: IO[str]) -> object:
    """Decode the hook payload from ``stream``.

    Args:
        stream: Usually ``sys.stdin``.

    Returns:
        The decoded JSON value.

    Raises:
        ValueError: The payload is empty, too large or not JSON.
    """
    text = stream.read(MAX_STDIN_BYTES + 1)
    if len(text) > MAX_STDIN_BYTES:
        raise ValueError("hook payload too large")
    if not text.strip():
        raise ValueError("empty hook payload")
    return json.loads(text)


def emit(payload: Dict[str, object]) -> None:
    """Write one JSON object to stdout (the hook protocol channel).

    Args:
        payload: The decision or gate output.
    """
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def append_jsonl(path: str, record: Dict[str, object]) -> bool:
    """Append one JSON line, creating parent directories.

    Args:
        path: Log file.
        record: Record to write.

    Returns:
        True on success, False on any filesystem error (never raises).
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except (OSError, ValueError, TypeError):
        return False


def write_json_atomic(path: str, value: object) -> bool:
    """Write JSON through a temp file and rename, so readers never see a partial file.

    Args:
        path: Destination file.
        value: JSON-serializable value.

    Returns:
        True on success, False on any filesystem or serialization error (never raises).
    """
    # A pid-suffixed sibling keeps the rename on one filesystem without importing tempfile
    # (and its shutil/random imports) into the hot path.
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return False
