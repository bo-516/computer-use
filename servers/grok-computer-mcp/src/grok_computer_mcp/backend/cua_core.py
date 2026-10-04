"""Cua Driver backend, part 1: session, display, apps, windows and reads.

Boundary: the only code that talks to Cua Driver (AGENTS.md), through a ``CuaTransport``. It
does not decide anything: element handles are Cua ``element_token``s from the latest
``get_window_state``, point actions are window-local screenshot pixels of the latest capture
(``Space.CAPTURE_PIXELS``; the driver reverses Retina and downscale itself), and every call
carries one session label so captures and tokens stay valid across calls (CLI transport too).
Actions live in ``cua.py`` (``CuaBackend``).
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from ..coords import Space
from ..geometry import Display, Point, Rect
from ..limits import BACKEND_CAPTURE_LONG_EDGE_PX
from ..observe.imaging import open_rgb
from .base import (
    AppInfo,
    BackendError,
    BackendErrorKind,
    BackendStatus,
    Capture,
    ScreenSnapshot,
    WindowInfo,
    WindowSnapshot,
)
from .cua_parse import (
    apps_of,
    classify,
    elements_of,
    num,
    obj,
    rect,
    text,
    windows_of,
)
from .cua_transport import CuaReply, CuaTransport

JsonObject = dict[str, object]
# Canonical key names -> Cua key vocabulary (press_key/hotkey). V16: forward delete has no
# documented name; both deletes map to Cua's "delete".
CUA_KEYS = {"enter": "return", "esc": "escape", "backspace": "delete", "delete": "delete"}
CUA_MODIFIERS = {"cmd": "cmd", "ctrl": "ctrl", "alt": "option", "shift": "shift", "fn": "fn",
                 "win": "win"}
MODIFIERS = frozenset(CUA_MODIFIERS)
DEFAULT_SCALE = 1.0


def window_target(window: WindowInfo) -> JsonObject:
    """Cua per-call target for a window."""
    return {"kind": "window", "pid": window.pid, "window_id": window.window_id}


class CuaCore:
    """Session, reads and app/window management over Cua Driver (actions are in ``cua.py``)."""

    name = "cua-driver"
    action_space = Space.CAPTURE_PIXELS

    def __init__(self, transport: CuaTransport, frame_space: Space | None,
                 session_label: str) -> None:
        """Create the backend (the transport starts on ``start``).

        Args:
            transport: MCP or CLI transport.
            frame_space: Space of element frames when configured; None means detect (V14).
            session_label: Cua session label repeated on every call.
        """
        self._transport = transport
        self._frame_space = frame_space
        self._session = session_label
        self._display = Display("primary", Rect(0, 0, 1, 1), DEFAULT_SCALE, Point(0, 0))

    async def _call(self, tool: str, args: JsonObject) -> CuaReply:
        """Call a tool with the session label; raise its classified error."""
        reply = await self._transport.call(tool, {**args, "session": self._session})
        error = classify(reply)
        if error is not None:
            raise error
        return reply

    async def start(self) -> None:
        """Start the transport and learn the main display's size and scale."""
        await self._transport.start()
        size = (await self._call("get_screen_size", {})).structured
        w, h = num(size.get("width")), num(size.get("height"))
        scale = num(size.get("scale_factor", size.get("scale"))) or DEFAULT_SCALE
        if w and h:
            self._display = Display("primary", Rect(0, 0, w, h), scale, Point(0, 0))

    async def close(self) -> None:
        """End the Cua session and stop the transport."""
        with contextlib.suppress(BackendError):
            await self._call("end_session", {})
        await self._transport.close()

    async def status(self) -> BackendStatus:
        """Permissions and health (``doctor``/``status``)."""
        perms = (await self._call("check_permissions", {})).structured
        granted = {k: v for k, v in perms.items() if isinstance(v, bool)}
        health = (await self._call("health_report", {})).structured
        version = text(health.get("version")) or text(obj(health.get("driver")).get("version"))
        return BackendStatus(self.name, version or "unknown", sys.platform, granted,
                             ready=all(granted.values()) if granted else True,
                             detail=text(health.get("summary")))

    async def list_apps(self) -> list[AppInfo]:
        """``list_apps``."""
        return apps_of((await self._call("list_apps", {})).structured)

    async def list_windows(self, app: str | None = None) -> list[WindowInfo]:
        """``list_windows``, optionally filtered by app name."""
        windows = windows_of((await self._call("list_windows", {})).structured)
        if app is None:
            return windows
        return [w for w in windows if w.app.casefold() == app.casefold()]

    async def displays(self) -> list[Display]:
        """The main display (Cua addresses the primary display; goal.md §5.6 multi-monitor
        mapping still applies to window captures, which carry their own scale)."""
        return [self._display]

    async def launch_app(self, name: str) -> list[WindowInfo]:
        """``launch_app`` (background launch; returns the app's windows)."""
        reply = await self._call("launch_app", {"name": name})
        return windows_of(reply.structured)

    async def focus_app(self, name: str) -> None:
        """``bring_to_front`` for the named app's pid."""
        app = next((a for a in await self.list_apps()
                    if a.name.casefold() == name.casefold() and a.pid), None)
        if app is None:
            raise BackendError(BackendErrorKind.NOT_FOUND, f"{name!r} is not running")
        await self._call("bring_to_front", {"pid": app.pid})

    def _capture(self, reply: CuaReply) -> Capture | None:
        """Screenshot of a ``get_window_state``/``get_desktop_state`` answer."""
        s = reply.structured
        data, mime = (reply.images[0] if reply.images else (b"", "image/png"))
        path = text(s.get("screenshot_file_path"))
        if not data and path:
            try:
                data = Path(path).read_bytes()
            except OSError:
                data = b""
        if not data:
            return None
        w, h = num(s.get("screenshot_width")), num(s.get("screenshot_height"))
        if not w or not h:
            try:
                w, h = (float(v) for v in open_rgb(data).size)
            except ValueError:
                return None
        return Capture(data, mime, int(w), int(h), text(s.get("capture_id")) or None)

    async def read_window(self, window: WindowInfo, *, screenshot: bool,
                          max_elements: int) -> WindowSnapshot:
        """``get_window_state`` (tree, plus a capture capped at 2560 px when asked)."""
        args: JsonObject = {"pid": window.pid, "window_id": window.window_id,
                            "include_screenshot": screenshot, "max_elements": max_elements}
        if screenshot:
            args["max_image_dimension"] = BACKEND_CAPTURE_LONG_EDGE_PX
        reply = await self._call("get_window_state", args)
        s = reply.structured
        bounds = rect(s.get("window_bounds")) or window.bounds
        title = text(s.get("window_title")) or window.title
        info = WindowInfo(window.window_id, window.pid, text(s.get("app_name")) or window.app,
                          title, bounds, window.on_screen, window.z_index, window.unsaved)
        scale = num(s.get("screenshot_scale")) or self._display.scale
        display = Display(self._display.id, self._display.bounds, scale, self._display.origin_px)
        degraded = text(s.get("degraded_reason")) or ("degraded" if s.get("degraded") else "")
        return WindowSnapshot(info, display, tuple(elements_of(s)), self._frame_space,
                              self._capture(reply) if screenshot else None,
                              degraded=degraded or None, truncated=s.get("truncated") is True)

    async def read_screen(self, *, screenshot: bool) -> ScreenSnapshot:
        """``list_windows`` plus ``get_desktop_state`` when a capture is wanted."""
        windows = tuple(w for w in await self.list_windows() if w.on_screen)
        capture = None
        if screenshot:
            reply = await self._call("get_desktop_state",
                                     {"max_image_dimension": BACKEND_CAPTURE_LONG_EDGE_PX})
            capture = self._capture(reply)
        return ScreenSnapshot(self._display, windows, capture)
