"""Load fake-backend scenes from JSON (recorded or hand-written trees, goal.md §9.1).

Boundary: parsing only. Windows are ``{"title", "bounds": [x, y, w, h], "root": {node},
"focused"?, "unsaved"?}`` with nested ``children``; apps list their open ``windows`` and an optional
launch ``template``; displays give ``bounds``, ``scale`` and ``origin_px``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

from ..geometry import Display, Point, Rect
from ..hooklib.state import as_mapping
from .fake_scene import FakeApp, FakeNode, FakeScene, FakeWindow


def _num(value: object) -> float | None:
    """A JSON number as float (booleans excluded)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _rect(value: object) -> Rect | None:
    """Parse ``[x, y, w, h]``."""
    if not isinstance(value, list):
        return None
    nums = [_num(v) for v in cast(list[object], value)]
    if len(nums) != 4 or any(n is None for n in nums):
        return None
    x, y, w, h = (n or 0.0 for n in nums)
    return Rect(x, y, w, h)


def _str_list(value: object) -> list[str]:
    """A JSON list of strings (other items dropped)."""
    if not isinstance(value, list):
        return []
    return [v for v in cast(list[object], value) if isinstance(v, str)]


def _flatten(raw: object, nodes: dict[str, FakeNode], counter: list[int]) -> str:
    """Parse a nested node object into ``nodes``; return its id."""
    item = as_mapping(raw)
    counter[0] += 1
    node_id = str(item.get("id") or f"n{counter[0]}")
    children = [_flatten(child, nodes, counter)
                for child in cast(list[object], item.get("children") or [])]
    value = item.get("value")
    expanded = item.get("expanded")
    placeholder = item.get("placeholder", item.get("placeholder_value"))
    nodes[node_id] = FakeNode(
        id=node_id, role=str(item.get("role", "group")), label=str(item.get("label", "")),
        value=None if value is None else str(value), frame=_rect(item.get("frame")),
        subrole=str(item.get("subrole", "")), enabled=item.get("enabled", True) is not False,
        visible=item.get("visible", True) is not False, children=children,
        on_click=_str_list(item.get("on_click")), on_enter=_str_list(item.get("on_enter")),
        # V17: only a present key is stored. Missing keys stay None.
        expanded=expanded if isinstance(expanded, bool) else None,
        min_value=_num(item.get("min_value", item.get("min", item.get("minimum")))),
        max_value=_num(item.get("max_value", item.get("max", item.get("maximum")))),
        placeholder=placeholder if isinstance(placeholder, str) else None)
    return node_id


def window_from_json(raw: object, window_id: int, app: str, pid: int) -> FakeWindow:
    """Build a window from its JSON description.

    Args:
        raw: ``{"title", "bounds": [x,y,w,h], "root": {node}, "focused"?, "unsaved"?}``.
        window_id: Id to assign.
        app: Owning app name.
        pid: Owning process id.

    Returns:
        The window.

    Raises:
        ValueError: The description has no bounds.
    """
    item = as_mapping(raw)
    bounds = _rect(item.get("bounds"))
    if bounds is None:
        raise ValueError(f"window {window_id} of {app} has no bounds")
    nodes: dict[str, FakeNode] = {}
    root = _flatten(item.get("root") or {"role": "window"}, nodes, [0])
    focused = item.get("focused")
    return FakeWindow(window_id, app, pid, str(item.get("title", app)), bounds, root, nodes,
                      focused=focused if isinstance(focused, str) else None,
                      unsaved=item.get("unsaved") is True,
                      on_screen=item.get("on_screen", True) is not False)


def load_scene(path: Path) -> FakeScene:
    """Load a scene from JSON.

    Args:
        path: Scene file (see ``fake_scenarios/settings_app.json``).

    Returns:
        The scene.

    Raises:
        ValueError: The file is not a valid scene.
        OSError: The file cannot be read.
    """
    return scene_from_json(json.loads(path.read_text(encoding="utf-8")))


def scene_from_json(raw: object) -> FakeScene:
    """Build a scene from decoded JSON.

    Args:
        raw: ``{"displays": [...], "apps": [...], "templates": {...}}``.

    Returns:
        The scene; each app's ``windows`` open in order, the last one frontmost.

    Raises:
        ValueError: Missing displays or malformed windows.
    """
    data = as_mapping(raw)
    displays: list[Display] = []
    for i, item in enumerate(cast(list[object], data.get("displays") or [])):
        d = as_mapping(item)
        bounds = _rect(d.get("bounds"))
        origin = _rect([*cast(list[object], d.get("origin_px") or [0, 0]), 0, 0])
        if bounds is None or origin is None:
            raise ValueError(f"display {i} needs bounds and origin_px")
        displays.append(Display(str(d.get("id", f"d{i}")), bounds, _num(d.get("scale")) or 1.0,
                                Point(origin.x, origin.y)))
    if not displays:
        raise ValueError("a scene needs at least one display")
    templates = dict(as_mapping(data.get("templates")))

    def factory(name: str, window_id: int, app: FakeApp) -> FakeWindow:
        return window_from_json(copy.deepcopy(templates[name]), window_id, app.name, app.pid)

    scene = FakeScene(displays, {}, {}, factory)
    for item in cast(list[object], data.get("apps") or []):
        a = as_mapping(item)
        template = a.get("template")
        app = FakeApp(str(a["name"]), int(_num(a.get("pid")) or 0), str(a.get("bundle_id", "")),
                      template if isinstance(template, str) else None)
        scene.apps[app.name] = app
        for win in cast(list[object], a.get("windows") or []):
            scene.next_window_id += 1
            window = window_from_json(win, scene.next_window_id, app.name, app.pid)
            scene.windows[window.window_id] = window
            scene.raise_window(window)
    return scene
