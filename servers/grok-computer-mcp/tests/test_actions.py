"""Actions against the fake desktop: diffs, targeting, staleness and the hard rules."""

from __future__ import annotations

import json
from typing import cast

import pytest
from facade_helpers import Facade, Result

from grok_computer_mcp.backend.base import BackendErrorKind, Delivery
from grok_computer_mcp.geometry import Point

pytestmark = pytest.mark.anyio


def ref_of(result: Result, label: str) -> str:
    """Ref of the listed element with this label."""
    for el in cast(list[dict[str, object]], result.structured["elements"]):
        if el["label"] == label:
            return str(el["ref"])
    raise AssertionError(f"{label!r} not listed:\n{result.text}")


def box_of(result: Result, label: str) -> list[int]:
    """bbox of the listed element with this label."""
    for el in cast(list[dict[str, object]], result.structured["elements"]):
        if el["label"] == label:
            return cast(list[int], el["bbox"])
    raise AssertionError(label)


async def test_save_reports_text_and_dialog_changes(dialogs: Facade) -> None:
    obs = await dialogs.call("observe", {"app": "Editor"})
    assert not obs.is_error, obs.text
    flag = ref_of(obs, "Flag")
    saved = await dialogs.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Save")})
    assert not saved.is_error, saved.text
    changes = cast(list[dict[str, object]], saved.structured["changes"])
    assert {"ref": flag, "field": "value", "before": "0", "after": "1"} in changes
    assert {"kind": "text_appeared", "text": "Changes saved"} in changes
    opened = [c for c in changes if c.get("kind") == "dialog_opened"]
    assert any(c.get("label") == "Delete file?" for c in opened)
    assert len(json.dumps(changes).encode()) <= 2048
    delete = next(c["ref"] for c in changes
                  if c.get("kind") == "appeared" and c.get("label") == "Delete")
    closed = await dialogs.call("click", {"observation_id": saved.obs, "ref": str(delete)})
    assert not closed.is_error, closed.text
    closed_changes = cast(list[dict[str, object]], closed.structured["changes"])
    assert any(c.get("kind") == "dialog_closed" and c.get("label") == "Delete file?"
               for c in closed_changes)
    again = await dialogs.call("observe", {"app": "Editor"})
    saved_again = await dialogs.call(
        "click", {"observation_id": again.obs, "ref": ref_of(again, "Save")})
    assert not saved_again.is_error, saved_again.text
    opened = await dialogs.call("observe", {"app": "Editor"})
    assert "modal=" in opened.text and "Ping" not in opened.text
    state = dialogs.state()
    recorded = cast(dict[str, dict[str, object]], state["elements"])
    ping = next(ref for ref, el in recorded.items() if el.get("label") == "Ping")
    background = await dialogs.call("click", {"observation_id": opened.obs, "ref": ping})
    assert not background.is_error, background.text


async def test_click_ref_reports_value_change_and_next_observation(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call(
        "click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")}
    )
    assert not result.is_error, result.text
    assert result.obs != obs.obs
    assert {"ref": ref_of(obs, "Dark mode"), "field": "value", "before": "0", "after": "1"} in cast(
        list[object], result.structured["changes"]
    )
    assert result.images == []
    assert len(result.text.encode()) <= 2048


async def test_refs_are_stable_across_observations(facade: Facade) -> None:
    first = await facade.observe()
    second = await facade.observe()
    assert ref_of(first, "Dark mode") == ref_of(second, "Dark mode")


async def test_click_point_echoes_hit_and_records_it_in_state(facade: Facade) -> None:
    obs = await facade.observe(mode="screenshot")
    x, y, w, h = box_of(obs, "Launch at login")
    result = await facade.call(
        "click", {"observation_id": obs.obs, "point": {"x": x + w / 2, "y": y + h / 2}}
    )
    assert not result.is_error, result.text
    hit = cast(dict[str, object], result.structured["hit"])
    assert hit["label"] == "Launch at login"
    clicks = [c for c in facade.backend.calls if c[0] == "click"]
    assert clicks, "the backend received the click"


async def test_click_mark_resolves_to_element(facade: Facade) -> None:
    obs = await facade.observe(mode="som")
    marks = cast(list[dict[str, object]], obs.structured["marks"])
    mark = next(m for m in marks if m["label"] == "Dark mode")
    result = await facade.call("click", {"observation_id": obs.obs, "mark": mark["mark"]})
    assert not result.is_error, result.text
    assert "Dark mode" in result.text


async def test_stale_when_target_value_changed(facade: Facade) -> None:
    obs = await facade.observe()
    dark = ref_of(obs, "Dark mode")
    await facade.call("click", {"observation_id": obs.obs, "ref": dark})
    again = await facade.call("click", {"observation_id": obs.obs, "ref": dark})
    assert again.code == "STALE_OBSERVATION"
    assert again.structured["retryable"] is True


async def test_unrelated_value_change_does_not_stale(facade: Facade) -> None:
    obs = await facade.observe()
    await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Launch at login")})
    other = await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")})
    assert not other.is_error, other.text


async def test_stale_when_layout_changed(facade: Facade) -> None:
    obs = await facade.observe()
    await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Delete account")})
    result = await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Save")})
    assert result.code == "STALE_OBSERVATION"


async def test_unknown_observation_is_stale(facade: Facade) -> None:
    await facade.observe()
    result = await facade.call("click", {"observation_id": "obs_000000", "ref": "e1"})
    assert result.code == "STALE_OBSERVATION"


async def test_stale_ref_versus_missing_element(facade: Facade) -> None:
    obs = await facade.observe()
    await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Delete account")})
    dialog = await facade.observe()
    cancel = ref_of(dialog, "Cancel")
    closed = await facade.call("click", {"observation_id": dialog.obs, "ref": cancel})
    assert not closed.is_error, closed.text
    stale_ref = await facade.call("click", {"observation_id": closed.obs, "ref": cancel})
    assert stale_ref.code == "STALE_REF"
    missing = await facade.call("click", {"observation_id": closed.obs, "ref": "e999"})
    assert missing.code == "ELEMENT_NOT_FOUND"


async def test_disabled_element_is_not_interactable(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Sync now")})
    assert result.code == "NOT_INTERACTABLE"


async def test_point_outside_image_is_invalid(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call("click", {"observation_id": obs.obs, "point": {"x": 99999, "y": 5}})
    assert result.code == "INVALID_ARGUMENT"


async def test_invalid_arguments_are_reported(facade: Facade) -> None:
    obs = await facade.observe()
    both = await facade.call("click", {"observation_id": obs.obs, "ref": "e2", "mark": 1})
    assert both.code == "INVALID_ARGUMENT"
    extra = await facade.call("click", {"observation_id": obs.obs, "ref": "e2", "bogus": 1})
    assert extra.code == "INVALID_ARGUMENT"
    unknown = await facade.call("teleport", {})
    assert unknown.code == "INVALID_ARGUMENT"


async def test_secure_field_typing_is_refused(facade: Facade) -> None:
    obs = await facade.observe()
    for label in ("API token", "Card number"):
        result = await facade.call(
            "type_text", {"observation_id": obs.obs, "ref": ref_of(obs, label), "text": "x"}
        )
        assert result.code == "SECURE_FIELD", label
        assert result.structured["retryable"] is False
    assert not any(c[0] == "type_text" for c in facade.backend.calls)


async def test_typing_with_unknown_focus_next_to_secure_field_is_refused(facade: Facade) -> None:
    facade.backend.window("Settings").focused = None
    obs = await facade.observe()
    result = await facade.call("type_text", {"observation_id": obs.obs, "text": "x"})
    assert result.code == "SECURE_FIELD"


async def test_type_text_into_focused_field_and_clear_first(facade: Facade) -> None:
    obs = await facade.observe()
    typed = await facade.call(
        "type_text", {"observation_id": obs.obs, "text": "Bea", "clear_first": True}
    )
    assert not typed.is_error, typed.text
    assert facade.backend.window("Settings").nodes["name"].value == "Bea"
    assert "Bea" not in str(typed.structured["summary"])


async def test_dangerous_keys_are_refused_before_anything_runs(facade: Facade) -> None:
    obs = await facade.observe()
    for keys in ("cmd+q", "Meta+Q", ["tab", "cmd+q"], "alt+f4", "ctrl+alt+del"):
        result = await facade.call("press_keys", {"observation_id": obs.obs, "keys": keys})
        assert result.code == "KEY_DENIED", keys
    assert not any(c[0] == "press_keys" for c in facade.backend.calls)


async def test_bad_chord_is_invalid(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call("press_keys", {"observation_id": obs.obs, "keys": "cmd+notakey"})
    assert result.code == "INVALID_ARGUMENT"


async def test_keys_into_focused_secure_field_are_refused(facade: Facade) -> None:
    facade.backend.window("Settings").focused = "token"
    obs = await facade.observe()
    typing = await facade.call("press_keys", {"observation_id": obs.obs, "keys": "a"})
    assert typing.code == "SECURE_FIELD"
    leaving = await facade.call("press_keys", {"observation_id": obs.obs, "keys": "tab"})
    assert not leaving.is_error, leaving.text


async def test_enter_submits_and_diff_shows_it(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call("press_keys", {"observation_id": obs.obs, "keys": "enter"})
    assert not result.is_error, result.text
    assert facade.backend.window("Settings").nodes["status"].value == "Ana"


async def test_background_refusal_retries_in_foreground(facade: Facade) -> None:
    facade.backend.fail_next(
        "click", BackendErrorKind.BACKGROUND_UNAVAILABLE, delivery=Delivery.BACKGROUND
    )
    obs = await facade.observe()
    result = await facade.call(
        "click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")}
    )
    assert not result.is_error, result.text
    assert result.structured["delivery"] == "foreground_retry"


async def test_background_and_foreground_refusal_is_background_unavailable(facade: Facade) -> None:
    facade.backend.fail_next("click", BackendErrorKind.BACKGROUND_UNAVAILABLE, times=2)
    obs = await facade.observe()
    result = await facade.call(
        "click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")}
    )
    assert result.code == "BACKGROUND_UNAVAILABLE"


@pytest.mark.parametrize(
    "kind,code",
    [
        (BackendErrorKind.OCCLUDED, "OCCLUDED"),
        (BackendErrorKind.PERMISSION, "PERMISSION_MISSING"),
        (BackendErrorKind.ELEVATED, "PERMISSION_MISSING"),
        (BackendErrorKind.PROTOCOL, "BACKEND_ERROR"),
        (BackendErrorKind.STALE_HANDLE, "STALE_OBSERVATION"),
    ],
)
async def test_driver_failures_map_to_codes(
    facade: Facade, kind: BackendErrorKind, code: str
) -> None:
    facade.backend.fail_next("click", kind)
    obs = await facade.observe()
    result = await facade.call(
        "click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")}
    )
    assert result.code == code
    assert result.structured["hint"]


async def test_pointer_moved_during_foreground_action_is_user_interrupt(facade: Facade) -> None:
    facade.backend.fail_next(
        "click", BackendErrorKind.BACKGROUND_UNAVAILABLE, delivery=Delivery.BACKGROUND
    )
    facade.backend.move_pointer_during_next_foreground_action(Point(900, 900))
    obs = await facade.observe()
    result = await facade.call(
        "click", {"observation_id": obs.obs, "ref": ref_of(obs, "Dark mode")}
    )
    assert result.code == "USER_INTERRUPT"


async def test_emergency_stop_refuses_everything(facade: Facade) -> None:
    obs = await facade.observe()
    flag = facade.settings.state_dir / "STOP"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    result = await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Save")})
    assert result.code == "USER_INTERRUPT"
    observe = await facade.call("observe", {"app": "MyApp"})
    assert observe.code == "USER_INTERRUPT"
    flag.unlink()
    assert not (await facade.call("observe", {"app": "MyApp"})).is_error


async def test_per_user_stop_flag_refuses_actions(facade: Facade) -> None:
    obs = await facade.observe()
    flag = facade.settings.home / ".grok-computer" / "STOP"
    assert facade.settings.stop_flags[0] == flag
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    result = await facade.call("click", {"observation_id": obs.obs, "ref": ref_of(obs, "Save")})
    assert result.code == "USER_INTERRUPT"
    flag.unlink()
    assert not (await facade.call("observe", {"app": "MyApp"})).is_error


async def test_scroll_and_drag_reach_the_backend_in_capture_pixels(facade: Facade) -> None:
    obs = await facade.observe(mode="screenshot")
    scrolled = await facade.call(
        "scroll", {"observation_id": obs.obs, "direction": "down", "amount": 2}
    )
    assert not scrolled.is_error, scrolled.text
    shot = await facade.observe(mode="screenshot")
    x, y, w, h = box_of(shot, "file-01.txt")
    dragged = await facade.call(
        "drag",
        {
            "observation_id": shot.obs,
            "from": {"ref": ref_of(shot, "file-01.txt")},
            "to": {"point": {"x": x + w / 2, "y": y + 3 * h}},
        },
    )
    assert not dragged.is_error, dragged.text
    drag_call = next(c for c in facade.backend.calls if c[0] == "drag")
    start = cast(Point, drag_call[1]["start"])
    # Image 1280x853 of a 2400x1600 capture: capture pixels are image pixels x 1.875.
    assert abs(start.x - (x + w / 2) * 2400 / 1280) < 2


async def test_screen_scope_click_focuses_window(facade: Facade) -> None:
    screen = await facade.call("observe", {"scope": "screen", "mode": "tree"})
    assert not screen.is_error, screen.text
    notes = ref_of(screen, "Notes: Untitled")
    result = await facade.call("click", {"observation_id": screen.obs, "ref": notes})
    assert not result.is_error, result.text
    assert result.structured["app"] == "Notes"
    denied = next(
        el
        for el in cast(list[dict[str, object]], screen.structured["elements"])
        if str(el["label"]).startswith("1Password")
    )
    blocked = await facade.call("click", {"observation_id": screen.obs, "ref": denied["ref"]})
    assert blocked.code == "APP_DENIED"
