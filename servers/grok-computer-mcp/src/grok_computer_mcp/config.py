"""Facade settings, parsed from ``GROK_COMPUTER_*`` environment variables (AGENTS.md).

Boundary: ``load_settings`` is a pure function of an environment mapping, the home directory and
the working directory, so every setting is unit-testable. State and trace paths resolve through
the vendored ``hooklib.paths`` so the facade writes exactly where the hooks read (V5 fallback
included). Invalid values raise ``ConfigError``; the CLI reports it and exits before serving, so
a misconfigured facade never runs with guessed settings (fail closed).
"""

from __future__ import annotations

import os
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .envparse import ConfigError, choice, integer, optional
from .hooklib import paths as hook_paths
from .limits import (
    HOST_IMAGE_MAX_EDGE_PX,
    SCREENSHOT_LONG_EDGE_PX,
    SCREENSHOT_MIN_LONG_EDGE_PX,
    TRACE_RETENTION_DAYS,
)

BackendName = Literal["cua-driver", "fake"]
Mode = Literal["host", "sandbox"]
CuaTransportName = Literal["mcp", "cli"]
GroundingProvider = Literal["none", "uitars", "xai"]
GroundingTier = Literal["direct", "som", "strict"]
GroundingCoords = Literal["smart_resize", "relative_1000", "image"]
FrameSpaceSetting = Literal["auto", "screen_points", "window_points", "capture_pixels"]

DEFAULT_CUA_COMMAND = ("cua-driver",)
DEFAULT_XAI_URL = "https://api.x.ai/v1"
DEFAULT_XAI_MODEL = "grok-4.7"
# Retention bounds for `GROK_COMPUTER_TRACE_RETENTION_DAYS` (goal.md §7.6 default 7 days).
MAX_RETENTION_DAYS = 365

__all__ = ["ConfigError", "Settings", "load_settings", "settings_from_process"]


@dataclass(frozen=True)
class Settings:
    """Everything the facade reads from its environment."""

    backend: BackendName
    mode: Mode
    long_edge: int
    state_dir: Path
    trace_dir: Path
    lock_dir: Path
    # Emergency stop flags, canonical per-user flag first (hooklib.paths.stop_flag_paths).
    stop_flags: tuple[Path, ...]
    workspace: Path | None
    home: Path
    cua_transport: CuaTransportName
    cua_command: tuple[str, ...]
    cua_socket: str | None
    cua_frame_space: FrameSpaceSetting
    grounding_provider: GroundingProvider
    grounding_url: str | None
    grounding_model: str | None
    grounding_api_key: str | None
    grounding_tier: GroundingTier
    grounding_coords: GroundingCoords
    trace_retention_days: int
    fake_scenario: Path | None
    som_regions: bool
    session_id: str
    log_level: str


def load_settings(env: dict[str, str], home: Path, cwd: Path) -> Settings:
    """Build settings from the environment.

    Args:
        env: Process environment (a copy; never mutated).
        home: The user's home directory.
        cwd: Working directory; the project policy is read from here unless
            ``GROK_COMPUTER_WORKSPACE`` overrides it.

    Returns:
        The settings.

    Raises:
        ConfigError: A variable holds an invalid value.
    """
    backend: BackendName = choice(env, "GROK_COMPUTER_BACKEND", ("cua-driver", "fake"),
                                   "cua-driver")
    long_edge = integer(env, "GROK_COMPUTER_SCREENSHOT_LONG_EDGE", SCREENSHOT_LONG_EDGE_PX,
                     SCREENSHOT_MIN_LONG_EDGE_PX, HOST_IMAGE_MAX_EDGE_PX - 1)
    command_raw = optional(env, "GROK_COMPUTER_CUA_COMMAND")
    command = tuple(shlex.split(command_raw)) if command_raw else DEFAULT_CUA_COMMAND
    if not command:
        raise ConfigError("GROK_COMPUTER_CUA_COMMAND is empty")
    default_provider: GroundingProvider = (
        "uitars" if optional(env, "GROK_COMPUTER_GROUNDING_URL") else "none")
    provider: GroundingProvider = choice(env, "GROK_COMPUTER_GROUNDING_PROVIDER",
                                          ("none", "uitars", "xai"), default_provider)
    workspace_raw = optional(env, "GROK_COMPUTER_WORKSPACE")
    workspace = Path(workspace_raw) if workspace_raw else cwd
    scenario = optional(env, "GROK_COMPUTER_FAKE_SCENARIO")
    lock_dir = hook_paths.usable_dir(env.get("GROK_COMPUTER_LOCK_DIR"))
    return Settings(
        backend=backend,
        mode=choice(env, "GROK_COMPUTER_MODE", ("host", "sandbox"), "host"),
        long_edge=long_edge,
        state_dir=Path(hook_paths.state_dir(env, str(home))),
        trace_dir=Path(hook_paths.trace_dir(env, str(home))),
        stop_flags=tuple(Path(p) for p in hook_paths.stop_flag_paths(env, str(home))),
        # The desktop is a per-user resource shared by every plugin install, so its lock lives
        # in the fixed per-user directory rather than under GROK_PLUGIN_DATA.
        lock_dir=Path(lock_dir) if lock_dir else Path(hook_paths.fallback_root(str(home))) / "run",
        workspace=workspace if workspace.is_absolute() else None,
        home=home,
        cua_transport=choice(env, "GROK_COMPUTER_CUA_TRANSPORT", ("mcp", "cli"), "mcp"),
        cua_command=command,
        cua_socket=optional(env, "GROK_COMPUTER_CUA_SOCKET"),
        cua_frame_space=choice(
            env, "GROK_COMPUTER_CUA_FRAME_SPACE",
            ("auto", "screen_points", "window_points", "capture_pixels"), "auto"),
        grounding_provider=provider,
        grounding_url=optional(env, "GROK_COMPUTER_GROUNDING_URL"),
        grounding_model=optional(env, "GROK_COMPUTER_GROUNDING_MODEL"),
        grounding_api_key=optional(env, "GROK_COMPUTER_GROUNDING_API_KEY"),
        grounding_tier=choice(env, "GROK_COMPUTER_GROUNDING_TIER",
                               ("direct", "som", "strict"), "som"),
        grounding_coords=choice(env, "GROK_COMPUTER_GROUNDING_COORDS",
                                 ("smart_resize", "relative_1000", "image"), "smart_resize"),
        trace_retention_days=integer(env, "GROK_COMPUTER_TRACE_RETENTION_DAYS",
                                  TRACE_RETENTION_DAYS, 1, MAX_RETENTION_DAYS),
        fake_scenario=Path(scenario) if scenario else None,
        # Opt-in: text/icon regions from the driver's optional parser (its perception extension
        # may bundle AGPL components, which stay opt-in per AGENTS.md).
        som_regions=choice(env, "GROK_COMPUTER_SOM_REGIONS", ("0", "1"), "0") == "1",
        session_id=optional(env, "GROK_SESSION_ID") or uuid.uuid4().hex[:12],
        log_level=(optional(env, "GROK_COMPUTER_LOG_LEVEL") or "WARNING").upper(),
    )


def settings_from_process() -> Settings:
    """Load settings for this process.

    Returns:
        Settings built from ``os.environ``, the home directory and the working directory.

    Raises:
        ConfigError: A variable holds an invalid value.
    """
    return load_settings(dict(os.environ), Path.home(), Path.cwd())
