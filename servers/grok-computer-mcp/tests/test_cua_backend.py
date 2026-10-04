"""Cua Driver adapter: argument mapping, response parsing, error classification, transports."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from facade_helpers import settings_for

from grok_computer_mcp.app import create_context
from grok_computer_mcp.backend.base import (
    BackendError,
    BackendErrorKind,
    Delivery,
    ElementTarget,
    PointTarget,
    WindowInfo,
)
from grok_computer_mcp.backend.cua import CuaBackend
from grok_computer_mcp.backend.cua_cli import CliTransport
from grok_computer_mcp.backend.cua_parse import classify, elements_of, outcome_of, windows_of
from grok_computer_mcp.backend.cua_transport import CuaReply, McpTransport
from grok_computer_mcp.geometry import Point
from grok_computer_mcp.handlers.actions import click
from grok_computer_mcp.handlers.observe import observe
from grok_computer_mcp.models.inputs import ClickIn, ObserveIn

pytestmark = pytest.mark.anyio
HERE = Path(__file__).resolve().parent


@dataclass
class RecordingTransport:
    """Canned replies per tool; records every call."""

    replies: dict[str, CuaReply]
    calls: list[tuple[str, dict[str, object]]] = field(
        default_factory=list[tuple[str, dict[str, object]]]
    )

    async def start(self) -> None:
        """No-op."""

    async def close(self) -> None:
        """No-op."""

    async def call(self, tool: str, args: dict[str, object]) -> CuaReply:
        """Record and answer."""
        self.calls.append((tool, args))
        return self.replies.get(tool, CuaReply({"path": "ax", "effect": "confirmed"}))


def window() -> WindowInfo:
    """A parsed window record."""
    [win] = windows_of(
        {
            "windows": [
                {
                    "window_id": 7,
                    "pid": 42,
                    "app_name": "Demo",
                    "title": "T",
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 50},
                }
            ]
        }
    )
    return win


def test_parse_documented_shapes() -> None:
    elements = elements_of(
        {
            "elements": [
                {
                    "element_index": 3,
                    "element_token": "t",
                    "role": "AXButton",
                    "label": "OK",
                    "frame": {"x": 1, "y": 2, "w": 3, "h": 4},
                    "parent_index": 0,
                    "depth": 1,
                    "actions": ["AXPress"],
                    "value": 5,
                },
                {"role": "AXGroup"},
            ]
        }
    )
    assert elements[0].index == 3 and elements[0].handle == "t" and elements[0].value == "5"
    assert elements[1].index == 1 and elements[1].frame is None
    assert (
        outcome_of(
            {"path": "ax", "effect": "suspected_noop", "escalation": {"recommended": "px"}}
        ).escalation
        == "px"
    )
    assert outcome_of({"effect": "weird"}).effect == "unknown"


@pytest.mark.parametrize(
    "structured,text,kind",
    [
        (
            {"ok": False, "code": "background_unavailable"},
            "",
            BackendErrorKind.BACKGROUND_UNAVAILABLE,
        ),
        ({"ok": False, "error": {"code": "background_occluded"}}, "", BackendErrorKind.OCCLUDED),
        ({"effect": "refused", "code": "background_uipi_blocked"}, "", BackendErrorKind.ELEVATED),
        ({}, "error: stale_element_token (snapshot replaced)", BackendErrorKind.STALE_HANDLE),
        (
            {"ok": False, "message": "Accessibility permission not granted"},
            "",
            BackendErrorKind.PERMISSION,
        ),
        ({"ok": False, "code": "something_new"}, "", BackendErrorKind.PROTOCOL),
    ],
)
def test_error_classification(
    structured: dict[str, object], text: str, kind: BackendErrorKind
) -> None:
    error = classify(CuaReply(structured, text, is_error=True))
    assert error is not None and error.kind is kind
    assert classify(CuaReply({"path": "ax"})) is None


async def test_arguments_sent_to_the_driver() -> None:
    transport = RecordingTransport(
        {"get_screen_size": CuaReply({"width": 100, "height": 50, "scale_factor": 2})}
    )
    backend = CuaBackend(transport, None, "grok-computer-t")
    await backend.start()
    win = window()
    await backend.click(
        ElementTarget(win, 1, "tok"),
        button="left",
        count=1,
        modifiers=(),
        delivery=Delivery.BACKGROUND,
    )
    await backend.click(
        PointTarget(win, Point(10.04, 20.0), "cap-1"),
        button="right",
        count=2,
        modifiers=("cmd",),
        delivery=Delivery.FOREGROUND,
    )
    await backend.press_keys(win, "cmd+shift+s", delivery=Delivery.BACKGROUND)
    await backend.press_keys(win, "enter", delivery=Delivery.BACKGROUND)
    await backend.type_text(win, None, "hi", delivery=Delivery.BACKGROUND)
    calls = transport.calls
    assert all(args["session"] == "grok-computer-t" for _, args in calls)
    click_el = calls[1][1]
    assert click_el["element_token"] == "tok" and click_el["delivery_mode"] == "background"
    assert click_el["target"] == {"kind": "window", "pid": 42, "window_id": 7}
    click_px = calls[2][1]
    assert (click_px["x"], click_px["y"], click_px["count"]) == (10.0, 20.0, 2)
    assert click_px["capture_id"] == "cap-1" and click_px["modifier"] == ["cmd"]
    assert calls[3][0] == "hotkey" and calls[3][1]["keys"] == ["cmd", "shift", "s"]
    assert calls[4][0] == "press_key" and calls[4][1]["key"] == "return"
    assert calls[5][0] == "type_text" and "element_token" not in calls[5][1]


async def test_mcp_transport_against_a_fake_driver() -> None:
    transport = McpTransport((sys.executable, str(HERE / "fake_cua_driver.py")), None)
    backend = CuaBackend(transport, None, "s")
    await backend.start()
    try:
        [win] = await backend.list_windows("demo")
        snap = await backend.read_window(win, screenshot=True, max_elements=100)
        assert snap.capture is not None and snap.capture.width == 800
        assert snap.display.scale == 2.0 and [e.label for e in snap.elements][1] == "Increment"
        inc = snap.elements[1]
        outcome = await backend.click(
            ElementTarget(win, inc.index, inc.handle),
            button="left",
            count=1,
            modifiers=(),
            delivery=Delivery.BACKGROUND,
        )
        assert outcome.effect == "confirmed"
        await backend.read_window(win, screenshot=False, max_elements=100)
        with pytest.raises(BackendError) as stale:
            await backend.click(
                ElementTarget(win, inc.index, inc.handle),
                button="left",
                count=1,
                modifiers=(),
                delivery=Delivery.BACKGROUND,
            )
        assert stale.value.kind is BackendErrorKind.STALE_HANDLE
        status = await backend.status()
        assert status.version == "0.33.0-fake" and status.permissions["accessibility"]
    finally:
        await backend.close()


async def test_full_facade_over_the_fake_driver(tmp_path: Path) -> None:
    transport = McpTransport((sys.executable, str(HERE / "fake_cua_driver.py")), None)
    backend = CuaBackend(transport, None, "s")
    ctx = create_context(settings_for(tmp_path), backend=backend)
    try:
        obs = await observe(ctx, ObserveIn(app="Demo", mode="screenshot"))
        refs = {
            e["label"]: e["ref"] for e in cast(list[dict[str, object]], obs.structured["elements"])
        }
        # V14 fallback: frames in screen points are detected and land inside the image.
        assert obs.image is not None and obs.image.width == 800
        result = await click(
            ctx,
            ClickIn(
                observation_id=str(obs.structured["observation_id"]), ref=str(refs["Refuse BG"])
            ),
        )
        assert result.structured["delivery"] == "foreground_retry"
    finally:
        await ctx.close()


async def test_cli_transport_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "fake-cli.py"
    script.write_text(
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        "assert argv[0] == 'call'\n"
        "tool, args = argv[1], json.loads(argv[2])\n"
        "if tool == 'get_window_state' and args.get('screenshot_out_file'):\n"
        "    from PIL import Image\n"
        "    Image.new('RGB', (40, 20)).save(args['screenshot_out_file'])\n"
        "    print(json.dumps({'elements': [], 'screenshot_file_path': args['screenshot_out_file'],"
        " 'screenshot_width': 40, 'screenshot_height': 20}))\n"
        "elif tool == 'click':\n"
        "    print(json.dumps({'ok': False, 'code': 'background_occluded'}))\n"
        "    sys.exit(1)\n"
        "else:\n"
        "    print(json.dumps({'tool': tool, 'session': args.get('session')}))\n"
    )
    transport = CliTransport((sys.executable, str(script)), None)
    reply = await transport.call("get_window_state", {"include_screenshot": True})
    assert reply.images and reply.images[0][0][:8] == b"\x89PNG\r\n\x1a\n"
    refused = await transport.call("click", {})
    error = classify(refused)
    assert error is not None and error.kind is BackendErrorKind.OCCLUDED
    plain = await transport.call("list_apps", {"session": "s"})
    assert json.loads(plain.text)["session"] == "s"
    missing = CliTransport(("definitely-not-a-binary-xyz",), None)
    with pytest.raises(BackendError) as gone:
        await missing.call("list_apps", {})
    assert gone.value.kind is BackendErrorKind.UNAVAILABLE
