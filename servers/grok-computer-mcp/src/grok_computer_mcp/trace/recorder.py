"""Local trajectory recording: one JSONL line per tool call plus key screenshots (goal.md §7.6).

Boundary: file I/O under the trace directory only; traces never leave the machine (AGENTS.md).
Typed text is recorded as length + first 2 characters (``hooklib.audit.redact``). ``purge`` backs
``grok-computer-mcp trace purge`` and the startup retention sweep (7 days by default).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
from pathlib import Path

from ..hooklib.redact import redact

TRACE_SCHEMA_VERSION = 1
FACADE_DIR_PREFIX = "facade-"
AUDIT_FILE_PREFIX = "audit-"
SECONDS_PER_DAY = 86_400


class TraceRecorder:
    """Writes ``<trace_dir>/facade-<session>/trace.jsonl`` and ``shots/<observation>.jpg``."""

    def __init__(self, trace_dir: Path, session_id: str) -> None:
        """Create the recorder (directories are created on first write).

        Args:
            trace_dir: Root trace directory (shared with the audit hook).
            session_id: Facade session id.
        """
        self.run_dir = trace_dir / f"{FACADE_DIR_PREFIX}{session_id}"
        self.session_id = session_id

    @property
    def shots_dir(self) -> Path:
        """Directory of saved screenshots."""
        return self.run_dir / "shots"

    def record(self, tool: str, arguments: dict[str, object], result: dict[str, object],
               duration_ms: float) -> None:
        """Append one call to the trace; never raises.

        Args:
            tool: Tool name.
            arguments: Validated arguments (redacted here).
            result: Structured result metadata (no screen text beyond labels already shown).
            duration_ms: Wall time of the call.
        """
        line = {"v": TRACE_SCHEMA_VERSION, "ts": time.time(), "session": self.session_id,
                "tool": tool, "input": redact(arguments), "result": result,
                "ms": round(duration_ms, 1)}
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / "trace.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            print(f"grok-computer-mcp: trace write failed: {exc}", file=sys.stderr)

    def save_screenshot(self, observation_id: str, data: bytes) -> str | None:
        """Save a screenshot as evidence the subagent can cite.

        Args:
            observation_id: Observation the image belongs to.
            data: JPEG bytes.

        Returns:
            The absolute path, or None when the write failed.
        """
        path = self.shots_dir / f"{observation_id}.jpg"
        try:
            self.shots_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            print(f"grok-computer-mcp: screenshot write failed: {exc}", file=sys.stderr)
            return None
        return str(path.resolve())


def purge(trace_dir: Path, older_than_days: float | None, now: float | None = None) -> list[Path]:
    """Delete facade trace directories and audit logs.

    Args:
        trace_dir: Root trace directory.
        older_than_days: Only entries last modified before this many days ago; None deletes all.
        now: Current time (injectable for tests).

    Returns:
        The paths removed.
    """
    if not trace_dir.is_dir():
        return []
    current = now if now is not None else time.time()
    cutoff = None if older_than_days is None else current - older_than_days * SECONDS_PER_DAY
    removed: list[Path] = []
    for entry in sorted(trace_dir.iterdir()):
        if not entry.name.startswith((FACADE_DIR_PREFIX, AUDIT_FILE_PREFIX)):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime >= cutoff:
            continue
        with contextlib.suppress(OSError):
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(entry)
    return removed
