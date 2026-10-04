"""Per-process facade context and the preamble every tool call runs through.

Boundary: holds the long-lived collaborators (backend, policy, store, lock, state writer, trace
recorder, grounder) and maps driver failures to model-facing error codes. The preamble order is
the fail-closed order of goal.md §7.2 L2: policy loads (else ``POLICY_ERROR``), no emergency stop
(else ``USER_INTERRUPT``), this process owns the desktop (else ``BUSY``), the backend is up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..backend.base import Backend, BackendError, BackendErrorKind
from ..config import Settings
from ..errors import ErrorCode, FacadeError
from ..grounding.base import Grounder
from ..lock import DesktopLock
from ..observe.capture import EncodedImage
from ..safety.policy import PolicyHolder, SafetyRules
from ..safety.state_file import StateWriter
from ..session import ObservationStore
from ..trace.recorder import TraceRecorder

_KIND_TO_CODE: dict[BackendErrorKind, ErrorCode] = {
    BackendErrorKind.BACKGROUND_UNAVAILABLE: ErrorCode.BACKGROUND_UNAVAILABLE,
    BackendErrorKind.OCCLUDED: ErrorCode.OCCLUDED,
    BackendErrorKind.ELEVATED: ErrorCode.PERMISSION_MISSING,
    BackendErrorKind.PERMISSION: ErrorCode.PERMISSION_MISSING,
    BackendErrorKind.STALE_HANDLE: ErrorCode.STALE_OBSERVATION,
    BackendErrorKind.WINDOW_GONE: ErrorCode.STALE_OBSERVATION,
    BackendErrorKind.NOT_INTERACTABLE: ErrorCode.NOT_INTERACTABLE,
    BackendErrorKind.NOT_FOUND: ErrorCode.INVALID_ARGUMENT,
    BackendErrorKind.TIMEOUT: ErrorCode.BACKEND_ERROR,
    BackendErrorKind.UNAVAILABLE: ErrorCode.BACKEND_ERROR,
    BackendErrorKind.PROTOCOL: ErrorCode.BACKEND_ERROR,
}
_KIND_HINTS: dict[BackendErrorKind, str] = {
    BackendErrorKind.ELEVATED: (
        "The target runs elevated and refuses input from the driver. Report STATUS: blocked and "
        "ask the user to restart it without elevation."),
    BackendErrorKind.NOT_FOUND: "Check the name with apps(action=\"list\") and try again.",
    BackendErrorKind.UNAVAILABLE: (
        "Cua Driver is not reachable. Report STATUS: blocked and ask the user to run "
        "`grok-computer-mcp doctor`."),
}


def map_backend_error(err: BackendError) -> FacadeError:
    """Translate a driver failure into the model-facing error (goal.md §5.8).

    Args:
        err: The classified driver error.

    Returns:
        The facade error to report.
    """
    code = _KIND_TO_CODE[err.kind]
    message = err.message if not err.detail else f"{err.message} ({err.detail})"
    return FacadeError(code, message, hint=_KIND_HINTS.get(err.kind))


@dataclass(frozen=True)
class ToolOutput:
    """What a handler returns: structured content, compact text, at most one image."""

    structured: dict[str, object]
    text: str
    image: EncodedImage | None = None


@dataclass
class FacadeContext:
    """Long-lived state of one facade process."""

    settings: Settings
    backend: Backend
    policy: PolicyHolder
    store: ObservationStore
    lock: DesktopLock
    state: StateWriter
    recorder: TraceRecorder
    grounder: Grounder | None
    backend_started: bool = field(default=False)
    # Size of the latest capture per window id: tree-only snapshots of backends that work in
    # capture pixels are interpreted against it.
    capture_sizes: dict[int, tuple[int, int]] = field(default_factory=dict[int, tuple[int, int]])

    def check_stop(self) -> None:
        """Refuse to continue while an emergency stop flag exists (fail closed).

        The flags are the per-user one ``grok-computer-mcp stop`` sets from any shell and the
        state-directory ones (``Settings.stop_flags``); the hooks' self-check reads the same list.

        Raises:
            FacadeError: ``USER_INTERRUPT``.
        """
        if any(flag.exists() for flag in self.settings.stop_flags):
            raise FacadeError(ErrorCode.USER_INTERRUPT,
                              "The emergency stop is active (grok-computer-mcp stop).")

    async def begin(self, *, needs_desktop: bool = True) -> SafetyRules:
        """Run the fail-closed preamble.

        Args:
            needs_desktop: Take the desktop lock (everything except read-only app listings).

        Returns:
            The current safety rules.

        Raises:
            FacadeError: ``POLICY_ERROR``, ``USER_INTERRUPT``, ``BUSY`` or a mapped backend error.
        """
        rules = self.policy.rules()
        self.check_stop()
        if needs_desktop:
            self.lock.acquire()
        if not self.backend_started:
            try:
                await self.backend.start()
            except BackendError as err:
                raise map_backend_error(err) from err
            self.backend_started = True
        return rules

    async def close(self) -> None:
        """Release the desktop and the driver."""
        self.lock.release()
        if self.backend_started:
            await self.backend.close()
            self.backend_started = False
