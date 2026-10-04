"""Build the facade from settings: backend, grounder, policy, lock, state writer, recorder.

Boundary: object construction only (no I/O until a tool runs, except the startup trace sweep).
Sandbox mode (goal.md §7.7) is the same facade pointed at a driver inside a VM or container:
``GROK_COMPUTER_CUA_SOCKET`` names the daemon endpoint, or ``GROK_COMPUTER_CUA_COMMAND`` wraps the
driver command (``ssh my-vm cua-driver``); the desktop lock is keyed by the target, so a sandbox
and the host desktop can be driven by different sessions at once.
"""

from __future__ import annotations

import hashlib

from .backend.base import Backend
from .backend.cua import CuaBackend
from .backend.cua_cli import CliTransport
from .backend.cua_transport import CuaTransport, McpTransport
from .backend.fake import FakeBackend
from .config import Settings
from .coords import Space
from .grounding.base import Grounder
from .grounding.uitars import UITarsGrounder
from .grounding.xai import XaiGrounder
from .handlers.context import FacadeContext
from .lock import DesktopLock
from .safety.policy import PolicyHolder
from .safety.state_file import StateWriter
from .session import ObservationStore
from .trace.recorder import TraceRecorder, purge

FRAME_SPACES = {"screen_points": Space.DESKTOP_POINTS, "window_points": Space.WINDOW_POINTS,
                "capture_pixels": Space.CAPTURE_PIXELS}
DEFAULT_UITARS_MODEL = "ui-tars-1.5-7b"
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_DEFAULT_MODEL = "grok-4.7"
TARGET_KEY_CHARS = 12


def create_backend(settings: Settings) -> Backend:
    """The configured desktop backend.

    Args:
        settings: Facade settings.

    Returns:
        A fake backend (tests, demos) or the Cua Driver backend.
    """
    if settings.backend == "fake":
        return FakeBackend.from_file(settings.fake_scenario)
    transport: CuaTransport
    if settings.cua_transport == "cli":
        transport = CliTransport(settings.cua_command, settings.cua_socket)
    else:
        transport = McpTransport(settings.cua_command, settings.cua_socket)
    return CuaBackend(transport, FRAME_SPACES.get(settings.cua_frame_space),
                      f"grok-computer-{settings.session_id}")


def create_grounder(settings: Settings) -> Grounder | None:
    """The configured grounding model client (goal.md §6.3 tiers decide how it is used).

    Args:
        settings: Facade settings.

    Returns:
        A UI-TARS or xAI client, or None when grounding is not configured.
    """
    if settings.grounding_provider == "uitars" and settings.grounding_url:
        return UITarsGrounder(settings.grounding_url,
                              settings.grounding_model or DEFAULT_UITARS_MODEL,
                              settings.grounding_api_key, settings.grounding_coords)
    if settings.grounding_provider == "xai":
        return XaiGrounder(settings.grounding_url or XAI_BASE_URL,
                           settings.grounding_model or XAI_DEFAULT_MODEL,
                           settings.grounding_api_key)
    return None


def lock_name(settings: Settings) -> str:
    """Lock file name for the desktop this facade drives.

    Args:
        settings: Facade settings.

    Returns:
        ``desktop-host.lock`` on the host, ``desktop-<hash>.lock`` per sandbox target.
    """
    if settings.mode == "host" and not settings.cua_socket:
        return "desktop-host.lock"
    target = f"{settings.cua_socket}|{' '.join(settings.cua_command)}"
    digest = hashlib.sha256(target.encode()).hexdigest()[:TARGET_KEY_CHARS]
    return f"desktop-{digest}.lock"


def create_context(settings: Settings, backend: Backend | None = None,
                   grounder: Grounder | None = None) -> FacadeContext:
    """Assemble the facade context and sweep expired traces.

    Args:
        settings: Facade settings.
        backend: Backend override (tests).
        grounder: Grounder override (tests).

    Returns:
        The context.
    """
    purge(settings.trace_dir, settings.trace_retention_days)
    return FacadeContext(
        settings=settings, backend=backend or create_backend(settings),
        policy=PolicyHolder(settings.home, settings.workspace), store=ObservationStore(),
        lock=DesktopLock(settings.lock_dir / lock_name(settings)),
        state=StateWriter(settings.state_dir),
        recorder=TraceRecorder(settings.trace_dir, settings.session_id),
        grounder=grounder if grounder is not None else create_grounder(settings))
