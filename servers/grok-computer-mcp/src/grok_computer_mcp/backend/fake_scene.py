"""Scene model for the fake backend: windows, accessibility nodes and scripted effects.

Boundary: in-memory state; the JSON loader is ``fake_load.py``, rendering ``fake_render.py`` and
the backend protocol ``fake.py``. Scenes are recorded or hand-written trees (goal.md §5.3,
"replay recorded trees and screenshots").
Node frames are WINDOW_POINTS (relative to the window's top-left); the fake converts them to the
space it is configured to report, like a real driver.

Effects are short strings attached to nodes (``on_click``, ``on_enter``):
``toggle``, ``focus``, ``focus:<id>``, ``show:<id>``, ``hide:<id>``, ``set_value:<id>:<text>``,
``copy_value:<from>:<to>``, ``set_title:<text>``, ``set_unsaved:<true|false>``, ``close_window``,
``open_window:<template>``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..geometry import Display, Point, Rect

_TRUE = ("1", "true", "on")
TEXT_ROLES = ("textfield", "axtextfield", "textarea", "axtextarea", "entry", "edit",
              "securefield", "axsecuretextfield", "searchfield", "combobox", "axcombobox")


def _nodes() -> dict[str, FakeNode]:
    """Empty node map (typed default factory)."""
    return {}


def _strs() -> list[str]:
    """Empty string list (typed default factory)."""
    return []


@dataclass
class FakeNode:
    """One accessibility node. ``frame`` is WINDOW_POINTS."""

    id: str
    role: str
    label: str = ""
    value: str | None = None
    frame: Rect | None = None
    subrole: str = ""
    enabled: bool = True
    visible: bool = True
    children: list[str] = field(default_factory=_strs)
    on_click: list[str] = field(default_factory=_strs)
    on_enter: list[str] = field(default_factory=_strs)

    @property
    def accepts_text(self) -> bool:
        """Whether typing goes into this node."""
        return self.role.lower() in TEXT_ROLES or self.subrole.lower() in TEXT_ROLES


@dataclass
class FakeWindow:
    """A top-level window with its node tree. ``bounds`` are DESKTOP_POINTS."""

    window_id: int
    app: str
    pid: int
    title: str
    bounds: Rect
    root: str
    nodes: dict[str, FakeNode] = field(default_factory=_nodes)
    focused: str | None = None
    unsaved: bool = False
    on_screen: bool = True
    z_index: int = 0

    def walk(self) -> list[tuple[FakeNode, str | None, int]]:
        """Visible nodes depth-first as ``(node, parent_id, depth)``."""
        out: list[tuple[FakeNode, str | None, int]] = []

        def visit(node_id: str, parent: str | None, depth: int) -> None:
            node = self.nodes[node_id]
            if not node.visible:
                return
            out.append((node, parent, depth))
            for child in node.children:
                visit(child, node_id, depth + 1)

        visit(self.root, None, 0)
        return out

    def hit(self, p: Point) -> FakeNode | None:
        """Deepest visible node containing a WINDOW_POINTS point."""
        best: FakeNode | None = None
        for node, _, _ in self.walk():
            if node.frame is not None and node.frame.contains(p):
                best = node
        return best


@dataclass
class FakeApp:
    """An installed app; ``template`` names the window opened on launch."""

    name: str
    pid: int
    bundle_id: str
    template: str | None = None


WindowFactory = Callable[[str, int, "FakeApp"], "FakeWindow"]


@dataclass
class FakeScene:
    """The whole fake desktop."""

    displays: list[Display]
    apps: dict[str, FakeApp]
    windows: dict[int, FakeWindow]
    # Builds a window from a named template (set by the loader; keeps this module loader-free).
    factory: WindowFactory
    cursor: Point = field(default_factory=lambda: Point(0.0, 0.0))
    next_window_id: int = 1000

    def front(self) -> FakeWindow | None:
        """The frontmost on-screen window."""
        visible = [w for w in self.windows.values() if w.on_screen]
        return max(visible, key=lambda w: w.z_index) if visible else None

    def raise_window(self, window: FakeWindow) -> None:
        """Bring a window to the front."""
        window.on_screen = True
        window.z_index = max((w.z_index for w in self.windows.values()), default=0) + 1

    def open_template(self, template: str, app: FakeApp) -> FakeWindow:
        """Open a new window from a template.

        Args:
            template: Template name.
            app: Owning app.

        Returns:
            The new, frontmost window.

        Raises:
            KeyError: Unknown template.
        """
        self.next_window_id += 1
        window = self.factory(template, self.next_window_id, app)
        self.windows[window.window_id] = window
        self.raise_window(window)
        return window

    def apply(self, window: FakeWindow, node: FakeNode, effects: list[str]) -> None:
        """Run scripted effects of ``node``.

        Args:
            window: Window the node belongs to.
            node: The node that was clicked or submitted.
            effects: Effect strings (see module docstring).
        """
        for effect in effects:
            name, _, arg = effect.partition(":")
            if name == "toggle":
                on = (node.value or "0").strip().lower() in _TRUE
                node.value = "0" if on else "1"
            elif name == "focus":
                window.focused = arg or node.id
            elif name in ("show", "hide"):
                window.nodes[arg].visible = name == "show"
            elif name == "set_value":
                target, _, text = arg.partition(":")
                window.nodes[target].value = text
            elif name == "copy_value":
                src, _, dst = arg.partition(":")
                window.nodes[dst].value = window.nodes[src].value
            elif name == "set_title":
                window.title = arg
            elif name == "set_unsaved":
                window.unsaved = arg == "true"
            elif name == "close_window":
                del self.windows[window.window_id]
            elif name == "open_window":
                self.open_template(arg, self.apps[window.app])
