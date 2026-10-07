"""Fake backend: a scriptable in-memory desktop for tests and offline demos.

Boundary: implements ``Backend`` over a ``FakeScene`` (goal.md §9.1 "回放录制的树与截图"). Every
capability added to ``base.py`` is added here too (AGENTS.md). Run the facade against it with
``GROK_COMPUTER_BACKEND=fake`` (optionally ``GROK_COMPUTER_FAKE_SCENARIO=<scene.json>``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from ..geometry import Display
from .base import (
    AppInfo,
    BackendError,
    BackendErrorKind,
    BackendStatus,
    Capture,
    RawElement,
    ScreenSnapshot,
    WindowInfo,
    WindowSnapshot,
)
from .fake_actions import FakeActions
from .fake_core import Snapshot
from .fake_load import load_scene, scene_from_json
from .fake_render import png, render_screen, render_window

DEFAULT_SCENE = Path(__file__).with_name("fake_scenarios") / "settings_app.json"


@dataclass
class FakeBackend(FakeActions):
    """In-memory desktop implementing the ``Backend`` protocol."""

    @classmethod
    def from_file(cls, path: Path | None = None) -> FakeBackend:
        """Load a scene file (the bundled settings-app scene by default).

        Args:
            path: Scene JSON.

        Returns:
            A backend over the loaded scene.
        """
        return cls(load_scene(path or DEFAULT_SCENE))

    @classmethod
    def from_json(cls, raw: object) -> FakeBackend:
        """Build from an already decoded scene.

        Args:
            raw: Scene JSON value.

        Returns:
            The backend.
        """
        return cls(scene_from_json(raw))

    async def start(self) -> None:
        """No connection needed."""
        self._log("start")
        self.started = True

    async def close(self) -> None:
        """Nothing to release."""
        self.started = False

    async def status(self) -> BackendStatus:
        """Always ready, all permissions granted."""
        return BackendStatus("fake", "1", sys.platform,
                             {"accessibility": True, "screen_recording": True})

    async def list_apps(self) -> list[AppInfo]:
        """Installed apps; running when they have a window."""
        self._log("list_apps")
        front = self.scene.front()
        running = {w.app for w in self.scene.windows.values()}
        return [AppInfo(a.name, a.pid if a.name in running else None, a.bundle_id,
                        a.name in running, front is not None and front.app == a.name)
                for a in self.scene.apps.values()]

    async def list_windows(self, app: str | None = None) -> list[WindowInfo]:
        """Windows, front first, optionally of one app."""
        self._log("list_windows", app=app)
        wanted = app.casefold() if app else None
        windows = [w for w in self.scene.windows.values()
                   if wanted is None or w.app.casefold() == wanted]
        return [self._info(w) for w in sorted(windows, key=lambda w: -w.z_index)]

    async def displays(self) -> list[Display]:
        """The scene's displays."""
        return list(self.scene.displays)

    async def launch_app(self, name: str) -> list[WindowInfo]:
        """Open the app's template window if it has none; bring it to the front."""
        self._log("launch_app", name=name)
        app = next((a for a in self.scene.apps.values() if a.name.casefold() == name.casefold()),
                   None)
        if app is None:
            raise BackendError(BackendErrorKind.NOT_FOUND, f"no app named {name!r}")
        windows = [w for w in self.scene.windows.values() if w.app == app.name]
        if not windows and app.template:
            windows = [self.scene.open_template(app.template, app)]
        return [self._info(w) for w in windows]

    async def focus_app(self, name: str) -> None:
        """Raise the app's front window."""
        self._log("focus_app", name=name)
        windows = [w for w in self.scene.windows.values() if w.app.casefold() == name.casefold()]
        if not windows:
            raise BackendError(BackendErrorKind.NOT_FOUND, f"{name!r} has no window")
        self.scene.raise_window(max(windows, key=lambda w: w.z_index))

    async def read_window(self, window: WindowInfo, *, screenshot: bool,
                          max_elements: int) -> WindowSnapshot:
        """Snapshot the tree (handles expire with the next snapshot) and optionally render it."""
        self._log("read_window", window=window.window_id, screenshot=screenshot)
        w = self._fake_window(window)
        self.generation += 1
        display = self._display(w.bounds)
        capture: Capture | None = None
        snap = Snapshot(self.generation)
        if screenshot:
            img = render_window(w, display.scale, self.capture_long_edge)
            capture = Capture(png(img), "image/png", img.width, img.height,
                              f"cap{self.generation}")
            snap.capture_size = (img.width, img.height)
        elif (prev := self.snapshots.get(w.window_id)) is not None:
            snap.capture_size = prev.capture_size
        self.snapshots[w.window_id] = snap
        elements: list[RawElement] = []
        index_of: dict[str, int] = {}
        for node, parent, depth in w.walk()[:max_elements]:
            index_of[node.id] = len(elements)
            elements.append(RawElement(
                index=len(elements), role=node.role, label=node.label, value=node.value,
                frame=self._frame_out(w, node.frame, snap.capture_size) if node.frame else None,
                handle=f"t{self.generation}:{node.id}", subrole=node.subrole,
                enabled=node.enabled, focused=w.focused == node.id,
                parent=index_of.get(parent) if parent else None, depth=depth,
                actions=("press",) if node.on_click else (),
                expanded=node.expanded, min_value=node.min_value, max_value=node.max_value,
                placeholder=node.placeholder))
        return WindowSnapshot(self._info(w), display, tuple(elements), self.frame_space, capture,
                              degraded=w.degraded, truncated=len(w.walk()) > max_elements)

    async def read_screen(self, *, screenshot: bool) -> ScreenSnapshot:
        """Primary display with its on-screen windows."""
        self._log("read_screen", screenshot=screenshot)
        display = self.scene.displays[0]
        windows = tuple(self._info(w) for w in sorted(self.scene.windows.values(),
                                                      key=lambda w: -w.z_index) if w.on_screen)
        capture: Capture | None = None
        if screenshot:
            img = render_screen(self.scene, display)
            capture = Capture(png(img), "image/png", img.width, img.height)
        return ScreenSnapshot(display, windows, capture)
