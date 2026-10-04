"""Guard rules for the Playwright ``browser`` server (goal.md §7.4 browser branch)."""

from __future__ import annotations

from typing import Dict

import pytest
from hookenv import HookEnv, run_guard


@pytest.mark.parametrize(
    "tool,inp",
    [
        ("browser__browser_snapshot", {}),
        ("browser__browser_take_screenshot", {"type": "png"}),
        ("browser__browser_console_messages", {"level": "error"}),
        ("browser__browser_verify_text_visible", {"text": "Saved"}),
    ],
)
def test_read_only_browser_tools_are_allowed(
    hooks: HookEnv, tool: str, inp: Dict[str, object]
) -> None:
    assert run_guard(hooks, tool, inp).decision == "allow"


@pytest.mark.parametrize(
    "tool",
    ["browser__browser_take_screenshot", "browser__browser_snapshot", "browser__browser_evaluate"],
)
def test_filename_arguments_are_denied(hooks: HookEnv, tool: str) -> None:
    result = run_guard(hooks, tool, {"filename": "src/main.rs", "function": "() => 1"})
    assert result.decision == "deny"
    assert "filename" in result.reason


def test_run_code_unsafe_is_denied(hooks: HookEnv) -> None:
    result = run_guard(hooks, "browser__browser_run_code_unsafe", {"code": "async (page) => 1"})
    assert result.decision == "deny"


def test_evaluate_asks(hooks: HookEnv) -> None:
    result = run_guard(hooks, "browser__browser_evaluate", {"function": "() => document.title"})
    assert result.decision == "ask"
    assert "document.title" in result.reason


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:5173/settings", "allow"),
        ("https://example.com", "allow"),
        ("about:blank", "allow"),
        ("file:///etc/passwd", "deny"),
        ("javascript:alert(1)", "deny"),
        ("chrome://settings", "ask"),
    ],
)
def test_navigation_by_scheme(hooks: HookEnv, url: str, expected: str) -> None:
    assert run_guard(hooks, "browser__browser_navigate", {"url": url}).decision == expected


def test_plain_click_is_allowed_in_isolated_browser(hooks: HookEnv) -> None:
    result = run_guard(
        hooks, "browser__browser_click", {"element": "Dark mode toggle", "target": "e12"}
    )
    assert result.decision == "allow"


def test_risky_element_description_asks(hooks: HookEnv) -> None:
    result = run_guard(
        hooks, "browser__browser_click", {"element": "Submit order button", "target": "e40"}
    )
    assert result.decision == "ask"


def test_missing_element_description_asks(hooks: HookEnv) -> None:
    result = run_guard(hooks, "browser__browser_click", {"target": "e40"})
    assert result.decision == "ask"
    assert "element description" in result.reason


def test_typing_into_password_field_is_denied(hooks: HookEnv) -> None:
    result = run_guard(
        hooks,
        "browser__browser_type",
        {"element": "Password input", "target": "e5", "text": "hunter2"},
    )
    assert result.decision == "deny"


def test_fill_form_with_card_number_is_denied(hooks: HookEnv) -> None:
    fields = [
        {"name": "Name", "type": "textbox", "target": "e1", "value": "Ana"},
        {"name": "Card number", "type": "textbox", "target": "e2", "value": "4111"},
    ]
    assert run_guard(hooks, "browser__browser_fill_form", {"fields": fields}).decision == "deny"


def test_fill_form_with_plain_fields_is_allowed(hooks: HookEnv) -> None:
    fields = [{"name": "Display name", "type": "textbox", "target": "e1", "value": "Ana"}]
    assert run_guard(hooks, "browser__browser_fill_form", {"fields": fields}).decision == "allow"


def test_file_upload_with_paths_asks(hooks: HookEnv) -> None:
    result = run_guard(hooks, "browser__browser_file_upload", {"paths": ["/Users/me/.ssh/id_rsa"]})
    assert result.decision == "ask"
    assert "id_rsa" in result.reason


def test_accepting_a_dialog_asks_and_dismissing_is_allowed(hooks: HookEnv) -> None:
    assert run_guard(hooks, "browser__browser_handle_dialog", {"accept": True}).decision == "ask"
    assert run_guard(hooks, "browser__browser_handle_dialog", {"accept": False}).decision == "allow"


def test_isolated_browser_can_be_turned_off_by_the_user(hooks: HookEnv) -> None:
    hooks.write_user_policy({"allow_isolated_browser": False})
    result = run_guard(hooks, "browser__browser_click", {"element": "Dark mode", "target": "e1"})
    assert result.decision == "ask"


def test_unknown_browser_tool_asks(hooks: HookEnv) -> None:
    assert run_guard(hooks, "browser__browser_mouse_click_xy", {"x": 1, "y": 2}).decision == "ask"


def test_main_agent_browser_call_is_denied(hooks: HookEnv) -> None:
    assert run_guard(hooks, "browser__browser_snapshot", {}, subagent=None).decision == "deny"
