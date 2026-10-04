"""A stand-in for ``cua-driver mcp``: a tiny MCP server answering in Cua Driver's documented shapes.

Run as ``python fake_cua_driver.py mcp``. One window ("Demo" / "Counter") with an Increment button
whose clicks increment a Count label. Element tokens expire with the next ``get_window_state``
(``stale_element_token``); background clicks on "Refuse BG" are refused with
``background_unavailable`` and succeed in the foreground. Frames are screen points.
"""

from __future__ import annotations

import base64
import io
import sys
from typing import cast

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from PIL import Image

STATE = {"generation": 0, "count": 0}
WINDOW = {
    "window_id": 77,
    "pid": 501,
    "app_name": "Demo",
    "title": "Counter",
    "bounds": {"x": 100, "y": 100, "width": 400, "height": 300},
    "z_index": 3,
    "is_on_screen": True,
}
SCALE = 2.0


def _png(width: int, height: int) -> str:
    """A small PNG whose colour follows the counter (so screenshots change)."""
    img = Image.new("RGB", (width, height), (200, 200, 200 - STATE["count"] * 10 % 200))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def _elements() -> list[dict[str, object]]:
    """The window's elements in documented shape (frames in screen points)."""
    g = STATE["generation"]
    rows = [
        ("AXWindow", "Counter", None, (100, 100, 400, 300), None, 0),
        ("AXButton", "Increment", None, (120, 150, 100, 30), 0, 1),
        ("AXButton", "Refuse BG", None, (240, 150, 100, 30), 0, 1),
        ("AXStaticText", "Count", str(STATE["count"]), (120, 200, 200, 20), 0, 1),
        ("AXTextField", "Note", "", (120, 240, 200, 24), 0, 1),
    ]
    out: list[dict[str, object]] = []
    for i, (role, label, value, (x, y, w, h), parent, depth) in enumerate(rows):
        el: dict[str, object] = {
            "element_index": i,
            "element_token": f"tok-{g}-{i}",
            "role": role,
            "label": label,
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "depth": depth,
            "actions": ["AXPress"] if role == "AXButton" else [],
        }
        if value is not None:
            el["value"] = value
        if parent is not None:
            el["parent_index"] = parent
        out.append(el)
    return out


def _result(
    data: dict[str, object], *, image: str | None = None, error: bool = False
) -> types.CallToolResult:
    """A tool result with structured content (and an image)."""
    content: list[types.TextContent | types.ImageContent] = [types.TextContent(text="ok")]
    if image is not None:
        content.append(types.ImageContent(data=image, mime_type="image/png"))
    return types.CallToolResult(content=list(content), structured_content=data, is_error=error)


def handle(name: str, args: dict[str, object]) -> types.CallToolResult:
    """Dispatch one tool call."""
    if name == "get_screen_size":
        return _result({"width": 1512, "height": 982, "scale_factor": SCALE})
    if name == "list_windows":
        return _result({"windows": [WINDOW]})
    if name == "list_apps":
        return _result({"apps": [{"name": "Demo", "pid": 501, "running": True, "active": True}]})
    if name == "get_window_state":
        STATE["generation"] += 1
        data: dict[str, object] = {
            "elements": _elements(),
            "window_bounds": WINDOW["bounds"],
            "screenshot_scale": SCALE,
            "app_name": "Demo",
            "window_title": "Counter",
            "capture_id": f"cap-{STATE['generation']}",
        }
        if args.get("include_screenshot", True):
            data.update(screenshot_width=800, screenshot_height=600)
            return _result(data, image=_png(800, 600))
        return _result(data)
    if name in ("click", "double_click"):
        token = str(args.get("element_token", ""))
        if token and not token.startswith(f"tok-{STATE['generation']}-"):
            return _result(
                {
                    "ok": False,
                    "code": "stale_element_token",
                    "message": "token from a replaced snapshot",
                },
                error=True,
            )
        if token.endswith("-2") and args.get("delivery_mode") == "background":
            return _result(
                {
                    "ok": False,
                    "code": "background_unavailable",
                    "effect": "refused",
                    "message": "no background route",
                },
                error=True,
            )
        if token.endswith("-1") or ("x" in args and float(cast(float, args["x"])) >= 120 * SCALE):
            STATE["count"] += 1
        return _result(
            {"path": "ax" if token else "pixel", "verified": True, "effect": "confirmed"}
        )
    if name == "get_cursor_position":
        return _result({"x": 10, "y": 10})
    if name == "check_permissions":
        return _result({"accessibility": True, "screen_recording": True})
    if name == "health_report":
        return _result({"version": "0.33.0-fake", "summary": "ok"})
    if name == "get_desktop_state":
        return _result({"screenshot_width": 1512, "screenshot_height": 982}, image=_png(1512, 982))
    return _result({"path": "key_events", "effect": "unverifiable"})


async def list_tools(_ctx: object, _params: object) -> types.ListToolsResult:
    """Advertise a permissive schema for every tool name used."""
    names = [
        "get_screen_size",
        "list_windows",
        "list_apps",
        "get_window_state",
        "click",
        "double_click",
        "type_text",
        "press_key",
        "hotkey",
        "scroll",
        "drag",
        "set_value",
        "get_cursor_position",
        "check_permissions",
        "health_report",
        "get_desktop_state",
        "bring_to_front",
        "launch_app",
        "end_session",
        "parse_visual_regions",
    ]
    return types.ListToolsResult(
        tools=[types.Tool(name=n, input_schema={"type": "object"}) for n in names]
    )


async def call_tool(_ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
    """Answer a tool call."""
    return handle(params.name, dict(params.arguments or {}))


async def main() -> None:
    """Serve on stdio."""
    server = Server("fake-cua-driver", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    del sys.argv[1:]
    anyio.run(main)
