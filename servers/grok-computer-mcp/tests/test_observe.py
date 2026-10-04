"""Observation: modes, budgets, masking, deny-before-capture, root_ref, wait_for and apps."""

from __future__ import annotations

import io
import json
from typing import cast

import pytest
from facade_helpers import Facade, Result
from PIL import Image

from grok_computer_mcp.limits import TARGET_IMAGE_BYTES, TOOL_TEXT_MAX_BYTES
from grok_computer_mcp.observe.capture import MASK_FILL

pytestmark = pytest.mark.anyio


def elements(result: Result) -> list[dict[str, object]]:
    """Structured elements of a result."""
    return cast(list[dict[str, object]], result.structured["elements"])


async def test_tree_mode_lists_interactive_elements_without_image(facade: Facade) -> None:
    result = await facade.observe(mode="tree")
    labels = {e["label"] for e in elements(result)}
    assert {"Dark mode", "Display name", "Save", "Delete account"} <= labels
    assert "Profile" not in labels  # containers are not listed by default
    assert result.images == []
    assert "(not attached)" in result.text
    secure = next(e for e in elements(result) if e["label"] == "API token")
    assert secure["secure"] is True and "value" not in secure


async def test_screenshot_mode_attaches_one_canonical_jpeg(facade: Facade) -> None:
    result = await facade.observe(mode="screenshot")
    assert len(result.images) == 1
    data, mime = result.images[0]
    assert mime == "image/jpeg" and len(data) <= TARGET_IMAGE_BYTES
    image = Image.open(io.BytesIO(data))
    structured = cast(dict[str, object], result.structured["image"])
    assert image.size == (structured["width"], structured["height"]) == (1280, 853)
    path = structured["screenshot_path"]
    assert isinstance(path, str) and path.endswith(f"{result.obs}.jpg")


async def test_tree_and_screenshot_share_one_coordinate_space(facade: Facade) -> None:
    tree = await facade.observe(mode="tree")
    shot = await facade.observe(mode="screenshot")
    by_label = {e["label"]: e["bbox"] for e in elements(tree)}
    for el in elements(shot):
        assert by_label[el["label"]] == el["bbox"]


async def test_som_mode_draws_marks_for_interactive_elements(facade: Facade) -> None:
    result = await facade.observe(mode="som")
    marks = cast(list[dict[str, object]], result.structured["marks"])
    assert marks and all(m["ref"] for m in marks)
    assert " m1" in result.text or "m1\n" in result.text or result.text.count(" m") >= len(marks)


async def test_auto_mode_uses_tree_when_rich_and_marks_when_thin(facade: Facade) -> None:
    rich = await facade.observe(mode="auto")
    assert rich.structured["mode"] == "tree" and rich.images == []
    thin = await facade.call("observe", {"app": "Paint"})
    assert thin.structured["mode"] == "som" and len(thin.images) == 1


async def test_deny_listed_app_is_refused_before_any_capture(facade: Facade) -> None:
    result = await facade.call("observe", {"app": "1Password", "mode": "screenshot"})
    assert result.code == "APP_DENIED"
    reads = [c for c in facade.backend.calls if c[0] == "read_window"]
    assert reads == []


async def test_screen_scope_masks_deny_listed_windows(facade: Facade) -> None:
    result = await facade.call("observe", {"scope": "screen", "mode": "screenshot"})
    assert not result.is_error, result.text
    labels = [e["label"] for e in elements(result)]
    assert "1Password: (hidden by policy)" in labels
    image = Image.open(io.BytesIO(result.images[0][0])).convert("RGB")
    window = next(e for e in elements(result) if str(e["label"]).startswith("1Password"))
    x, y, w, h = cast(list[int], window["bbox"])
    pixel = cast(tuple[int, int, int], image.getpixel((x + w // 4, y + h // 2 + 30)))
    assert all(abs(a - b) <= 12 for a, b in zip(pixel, MASK_FILL, strict=True))


async def test_root_ref_expands_a_container(facade: Facade) -> None:
    result = await facade.observe()
    first_row = next(e for e in elements(result) if e["label"] == "file-01.txt")
    rows = await facade.call("observe", {"app": "MyApp", "root_ref": "e10"})
    assert not rows.is_error, rows.text
    assert str(first_row["ref"]) in rows.text
    missing = await facade.call("observe", {"app": "MyApp", "root_ref": "e999"})
    assert missing.code == "ELEMENT_NOT_FOUND"


async def test_max_elements_truncates_with_a_footer(facade: Facade) -> None:
    result = await facade.observe(max_elements=3)
    assert len(elements(result)) == 3
    assert "more elements not listed" in result.text
    assert result.structured["omitted"]


async def test_text_and_structured_stay_within_budget(facade: Facade) -> None:
    window = facade.backend.window("Settings")
    rows = window.nodes["files"]
    for i in range(13, 600):
        node_id = f"row{i}"
        window.nodes[node_id] = type(window.nodes["row1"])(
            id=node_id,
            role="AXRow",
            label=f"a-rather-long-file-name-{i:04d}-final-v2.txt",
            frame=window.nodes["row1"].frame,
        )
        rows.children.append(node_id)
    result = await facade.observe(max_elements=600)
    total = len(result.text.encode()) + len(json.dumps(result.structured).encode())
    assert total <= TOOL_TEXT_MAX_BYTES


async def test_unknown_app_and_window_are_invalid(facade: Facade) -> None:
    assert (await facade.call("observe", {"app": "NoSuchApp"})).code == "INVALID_ARGUMENT"
    assert (await facade.call("observe", {"window_id": 424242})).code == "INVALID_ARGUMENT"


async def test_observe_without_app_follows_the_last_window(facade: Facade) -> None:
    await facade.observe()
    again = await facade.call("observe", {})
    assert again.structured["app"] == "MyApp"


async def test_wait_for_appearing_and_gone(facade: Facade) -> None:
    obs = await facade.observe()
    delete = next(e for e in elements(obs) if e["label"] == "Delete account")
    await facade.call("click", {"observation_id": obs.obs, "ref": delete["ref"]})
    seen = await facade.call(
        "wait_for", {"app": "MyApp", "text": "your account", "timeout_ms": 500}
    )
    assert not seen.is_error, seen.text
    matched = seen.structured["matched"]
    assert isinstance(matched, dict)
    assert cast(dict[str, object], matched)["label"] == "Delete your account?"
    gone = await facade.call(
        "wait_for", {"app": "MyApp", "text": "file-01", "gone": True, "timeout_ms": 300}
    )
    assert gone.code == "TIMEOUT"


async def test_wait_for_needs_a_condition(facade: Facade) -> None:
    result = await facade.call("wait_for", {"app": "MyApp"})
    assert result.code == "INVALID_ARGUMENT"


async def test_apps_list_windows_launch_and_focus(facade: Facade) -> None:
    listed = await facade.call("apps", {"action": "list"})
    apps = {a["name"]: a for a in cast(list[dict[str, object]], listed.structured["apps"])}
    assert apps["1Password"]["denied"] is True
    assert apps["Calculator"]["running"] is False
    launched = await facade.call("apps", {"action": "launch", "name": "Calculator"})
    assert not launched.is_error, launched.text
    windows = cast(list[dict[str, object]], launched.structured["windows"])
    assert windows[0]["title"] == "Calculator"
    focused = await facade.call("apps", {"action": "focus", "name": "Notes"})
    assert not focused.is_error, focused.text
    titles = await facade.call("apps", {"action": "windows"})
    hidden = [
        w
        for w in cast(list[dict[str, object]], titles.structured["windows"])
        if w["app"] == "1Password"
    ]
    assert hidden and hidden[0]["title"] == "(hidden by policy)"


async def test_apps_refuses_deny_listed_and_unknown(facade: Facade) -> None:
    assert (
        await facade.call("apps", {"action": "launch", "name": "Terminal"})
    ).code == "APP_DENIED"
    assert (
        await facade.call("apps", {"action": "focus", "name": "1Password"})
    ).code == "APP_DENIED"
    assert (
        await facade.call("apps", {"action": "launch", "name": "Nope"})
    ).code == "INVALID_ARGUMENT"
    assert (await facade.call("apps", {"action": "launch"})).code == "INVALID_ARGUMENT"
