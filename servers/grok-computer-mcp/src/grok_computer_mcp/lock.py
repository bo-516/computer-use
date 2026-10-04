"""One operator per desktop: a cross-process lock held while a facade is driving the GUI.

Boundary: file I/O on one lock file (goal.md §4.4, AGENTS.md "a second caller gets BUSY"). The
screen, focus and clipboard are shared by every grok session, so the lock lives in a fixed
per-user directory, not under a plugin's data dir. It is taken lazily on the first GUI call and
released after ``LOCK_IDLE_RELEASE_S`` without calls, so an idle grok session does not block
another one forever. The OS releases the lock if the process dies.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from .errors import ErrorCode, FacadeError
from .hooklib.state import as_mapping
from .limits import LOCK_IDLE_RELEASE_S

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: IO[str]) -> bool:
        """Non-blocking exclusive lock of byte 0 (Windows)."""
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(handle: IO[str]) -> None:
        """Release the byte-0 lock (Windows)."""
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(handle: IO[str]) -> bool:
        """Non-blocking exclusive advisory lock (POSIX)."""
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(handle: IO[str]) -> None:
        """Release the advisory lock (POSIX)."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DesktopLock:
    """Lazily acquired, idle-released exclusive lock on one desktop."""

    def __init__(self, path: Path, idle_release_s: float = LOCK_IDLE_RELEASE_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """Create the lock object (nothing is locked yet).

        Args:
            path: Lock file, e.g. ``~/.grok-computer/run/desktop-host.lock``.
            idle_release_s: Release after this long without ``acquire``.
            clock: Monotonic clock (injectable for tests).
        """
        self.path = path
        self.idle_release_s = idle_release_s
        self._clock = clock
        self._handle: IO[str] | None = None
        self._last_used = 0.0

    @property
    def held(self) -> bool:
        """Whether this process holds the lock."""
        return self._handle is not None

    def _holder(self) -> str:
        """Describe the current holder from the lock file, best effort."""
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return "another process"
        info = as_mapping(raw)
        if not info:
            return "another process"
        pid = info.get("pid", "?")
        since = info.get("since")
        when = time.strftime("%H:%M:%S", time.localtime(since)) if isinstance(since, float) else "?"
        return f"grok-computer-mcp pid {pid} (since {when})"

    def acquire(self) -> None:
        """Take the lock (or refresh it when already held).

        Raises:
            FacadeError: ``BUSY`` when another process holds the desktop.
        """
        self._last_used = self._clock()
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        if not _try_lock(handle):
            handle.close()
            raise FacadeError(ErrorCode.BUSY, f"The desktop is in use by {self._holder()}.")
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "since": time.time()}))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Release the lock if held (idempotent)."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        with contextlib.suppress(OSError):
            handle.seek(0)
            handle.truncate()
            _unlock(handle)
        handle.close()

    def release_if_idle(self) -> bool:
        """Release when unused for ``idle_release_s``.

        Returns:
            True when the lock was released by this call.
        """
        if self._handle is not None and self._clock() - self._last_used >= self.idle_release_s:
            self.release()
            return True
        return False
