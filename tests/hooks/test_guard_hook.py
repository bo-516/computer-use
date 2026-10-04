"""Guard hook (PreToolUse) against JSON fixtures: one test per rule of goal.md §7.3/§7.4."""

from __future__ import annotations

import json
from typing import Dict, cast

import pytest
from hookenv import HookEnv, allow_myapp, event, run_guard

READ_ONLY_INPUT = {"mode": "auto"}
State = Dict[str, object]


def test_main_agent_is_denied(hooks: HookEnv) -> None:
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent=None)
    assert result.decision == "deny"
    assert "computer subagent" in result.reason


def test_other_subagent_type_is_denied(hooks: HookEnv) -> None:
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent="explore")
    assert result.decision == "deny"


@pytest.mark.parametrize("subagent", ["computer-use:computer", "computer"])
def test_read_only_is_allowed_for_the_computer_subagent(hooks: HookEnv, subagent: str) -> None:
    assert (
        run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent=subagent).decision
        == "allow"
    )


@pytest.mark.parametrize(
    "tool,inp",
    [
        ("computer__wait_for", {"text": "Saved", "timeout_ms": 2000}),
        ("computer__locate", {"description": "the Save button", "observation_id": "obs_1"}),
        ("computer__apps", {"action": "list"}),
        ("computer__apps", {"action": "windows", "name": "MyApp"}),
    ],
)
def test_other_read_only_calls_are_allowed(
    hooks: HookEnv, tool: str, inp: Dict[str, object]
) -> None:
    assert run_guard(hooks, tool, inp).decision == "allow"


def test_observe_of_deny_listed_app_is_denied(hooks: HookEnv) -> None:
    result = run_guard(hooks, "computer__observe", {"app": "1Password 8"})
    assert result.decision == "deny"
    assert "deny list" in result.reason


def test_allow_listed_app_normal_click_is_allowed(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert result.decision == "allow"


def test_risky_label_asks_even_for_allow_listed_app(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e14"})
    assert result.decision == "ask"
    assert "Delete account" in result.reason


def test_secure_field_typing_is_denied(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks,
        "computer__type_text",
        {"observation_id": "obs_7f3a", "ref": "e12", "text": "hunter2"},
    )
    assert result.decision == "deny"
    assert "secure" in result.reason


def test_credential_labelled_field_typing_is_denied(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks, "computer__type_text", {"observation_id": "obs_7f3a", "ref": "e10", "text": "4111"}
    )
    assert result.decision == "deny"
    assert "credential" in result.reason


def test_typing_without_ref_when_focus_unknown_and_secure_field_present_is_denied(
    hooks: HookEnv, settings_state: State
) -> None:
    allow_myapp(hooks)
    state = dict(settings_state)
    state["focused"] = None
    hooks.write_state(state)
    result = run_guard(hooks, "computer__type_text", {"observation_id": "obs_7f3a", "text": "x"})
    assert result.decision == "deny"


def test_typing_into_focused_plain_field_is_allowed(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__type_text", {"observation_id": "obs_7f3a", "text": "Ana"})
    assert result.decision == "allow"


@pytest.mark.parametrize(
    "keys",
    [
        "cmd+q",
        "Cmd + Q",
        "command+Q",
        "meta+q",
        ["cmd+a", "cmd+q"],
        "ctrl+alt+del",
        "Control+Alt+Delete",
        "alt+F4",
        "win+l",
    ],
)
def test_dangerous_keys_are_denied(hooks: HookEnv, settings_state: State, keys: object) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__press_keys", {"observation_id": "obs_7f3a", "keys": keys})
    assert result.decision == "deny", result.reason


@pytest.mark.parametrize("keys", ["cmd+l", "cmd+s", ["cmd+a", "delete"], "tab"])
def test_ordinary_keys_are_allowed(hooks: HookEnv, settings_state: State, keys: object) -> None:
    allow_myapp(hooks)
    state = dict(settings_state)
    state["focused"] = {"ref": "e7", "role": "switch", "label": "Dark mode", "app": "MyApp"}
    hooks.write_state(state)
    result = run_guard(hooks, "computer__press_keys", {"observation_id": "obs_7f3a", "keys": keys})
    assert result.decision == "allow", result.reason


def test_enter_in_form_asks(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks, "computer__press_keys", {"observation_id": "obs_7f3a", "keys": "enter"}
    )
    assert result.decision == "ask"
    assert "submit" in result.reason


def test_unparseable_keys_ask(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks, "computer__press_keys", {"observation_id": "obs_7f3a", "keys": "cmd+notakey"}
    )
    assert result.decision == "ask"


def test_close_shortcut_on_unsaved_window_asks(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    state = dict(settings_state)
    state["window_unsaved"] = True
    state["focused"] = {"ref": "e7", "role": "switch", "label": "Dark mode", "app": "MyApp"}
    hooks.write_state(state)
    result = run_guard(
        hooks, "computer__press_keys", {"observation_id": "obs_7f3a", "keys": "cmd+w"}
    )
    assert result.decision == "ask"
    assert "unsaved" in result.reason


def test_missing_state_and_unknown_app_asks(hooks: HookEnv) -> None:
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_1", "ref": "e1"})
    assert result.decision == "ask"


def test_stale_state_is_treated_as_unknown(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_state(settings_state, age=120)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert result.decision == "ask"
    assert "Cannot verify" in result.reason


def test_malformed_state_is_treated_as_unknown(hooks: HookEnv) -> None:
    hooks.state_dir.mkdir(parents=True)
    (hooks.state_dir / "last_observation.json").write_text("{not json", encoding="utf-8")
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert result.decision == "ask"


def test_state_in_fallback_dir_is_used(hooks: HookEnv, settings_state: State) -> None:
    """V5: the facade may only see ~/.grok-computer while hooks get GROK_PLUGIN_DATA."""
    allow_myapp(hooks)
    hooks.write_state(settings_state, directory=hooks.fallback_state_dir)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e14"})
    assert result.decision == "ask"
    assert "Delete account" in result.reason


def test_observation_mismatch_is_unknown_target(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_0000", "ref": "e7"})
    assert result.decision == "ask"


def test_observation_mismatch_on_allow_listed_app_is_allowed(
    hooks: HookEnv, settings_state: State
) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_0000", "ref": "e7"})
    assert result.decision == "allow"


@pytest.mark.parametrize(
    "point,expected",
    [
        ({"x": 950, "y": 750}, "ask"),  # inside "Delete account"
        ({"x": 1120, "y": 750}, "allow"),  # inside "Save"
        ([1120, 750], "allow"),
    ],
)
def test_point_clicks_are_hit_tested_by_the_guard(
    hooks: HookEnv, settings_state: State, point: object, expected: str
) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "point": point})
    assert result.decision == expected, result.reason


def test_point_hitting_nothing_in_unknown_app_asks(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_state(settings_state)
    result = run_guard(
        hooks, "computer__click", {"observation_id": "obs_7f3a", "point": {"x": 640, "y": 600}}
    )
    assert result.decision == "ask"


def test_mark_click_resolves_to_the_marked_element(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    assert (
        run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "mark": 13}).decision
        == "ask"
    )
    assert (
        run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "mark": 12}).decision
        == "allow"
    )


def test_drag_onto_risky_target_asks(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks,
        "computer__drag",
        {"observation_id": "obs_7f3a", "from": {"ref": "e7"}, "to": {"ref": "e14"}},
    )
    assert result.decision == "ask"


def test_first_touch_of_unlisted_app_asks_until_approved(
    hooks: HookEnv, settings_state: State
) -> None:
    hooks.write_state(settings_state)
    first = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert first.decision == "ask"
    assert "First GUI action" in first.reason
    approved = {"approved": ["MyApp"]}
    (hooks.state_dir / "asked_apps_s1.json").write_text(json.dumps(approved), encoding="utf-8")
    again = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert again.decision == "allow"


def test_deny_pattern_with_window_title(hooks: HookEnv, settings_state: State) -> None:
    state = dict(settings_state)
    state["app"] = "System Settings"
    elements = cast(Dict[str, Dict[str, object]], state["elements"])
    state["elements"] = {ref: dict(item, app="System Settings") for ref, item in elements.items()}
    state["window_title"] = "Privacy & Security"
    hooks.write_state(state)
    denied = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert denied.decision == "deny"
    state["window_title"] = "General"
    hooks.write_state(state)
    other = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert other.decision == "ask"


@pytest.mark.parametrize("name", ["Terminal", "iTerm2", "Keychain Access", "PowerShell"])
def test_launching_deny_listed_apps_is_denied(hooks: HookEnv, name: str) -> None:
    result = run_guard(hooks, "computer__apps", {"action": "launch", "name": name})
    assert result.decision == "deny"


def test_launching_unlisted_app_asks_and_allow_listed_app_is_allowed(hooks: HookEnv) -> None:
    assert (
        run_guard(hooks, "computer__apps", {"action": "launch", "name": "Calculator"}).decision
        == "ask"
    )
    allow_myapp(hooks)
    assert (
        run_guard(hooks, "computer__apps", {"action": "focus", "name": "MyApp"}).decision == "allow"
    )


def test_scroll_without_target_is_allowed(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_state(settings_state)
    result = run_guard(
        hooks, "computer__scroll", {"observation_id": "obs_7f3a", "direction": "down"}
    )
    assert result.decision == "allow"


def test_payload_parse_failure_asks(hooks: HookEnv) -> None:
    for payload in ("not json", "", "[1, 2]"):
        result = hooks.run("guard.py", payload)
        assert result.code == 0
        assert result.decision == "ask"


def test_missing_tool_name_asks(hooks: HookEnv) -> None:
    result = hooks.run("guard.py", {"toolInput": {}, "subagentType": "computer"})
    assert result.decision == "ask"


def test_ask_as_deny_turns_asks_into_logged_denies(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    result = run_guard(
        hooks,
        "computer__click",
        {"observation_id": "obs_7f3a", "ref": "e14"},
        extra_env={"GROK_COMPUTER_ASK_AS_DENY": "1"},
    )
    assert result.decision == "deny"
    assert result.reason.startswith("[ask->deny in unattended mode]")
    records = hooks.audit_records()
    assert records and records[-1]["event"] == "guard"
    assert records[-1]["decision"] == "deny" and records[-1]["original"] == "ask"


def test_raw_cua_tools_are_denied(hooks: HookEnv) -> None:
    result = run_guard(hooks, "cua__click", {"pid": 1, "element_token": "t"})
    assert result.decision == "deny"
    assert "facade" in result.reason


def test_other_servers_get_no_opinion(hooks: HookEnv) -> None:
    result = hooks.run("guard.py", event("linear__save_issue", {"title": "x"}))
    assert result.decision == "defer"


def test_use_tool_envelope_is_unwrapped(hooks: HookEnv, settings_state: State) -> None:
    allow_myapp(hooks)
    hooks.write_state(settings_state)
    wrapped = {
        "tool_name": "computer__click",
        "tool_input": {"observation_id": "obs_7f3a", "ref": "e14"},
    }
    assert run_guard(hooks, "computer__click", wrapped).decision == "ask"
    unresolved = {"tool_name": "computer__click", "tool_input_file": "/tmp/args.json"}
    assert run_guard(hooks, "computer__click", unresolved).decision == "ask"


def test_malformed_user_policy_asks(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_user_policy("{oops")
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT)
    assert result.decision == "ask"
    assert "policy" in result.reason


def test_wrongly_typed_policy_value_asks(hooks: HookEnv) -> None:
    hooks.write_project_policy({"deny_apps": "Slack"})
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT)
    assert result.decision == "ask"


def test_project_policy_cannot_disable_require_subagent(hooks: HookEnv) -> None:
    hooks.write_project_policy({"require_subagent": False, "allow_apps": ["MyApp"]})
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent=None)
    assert result.decision == "deny"


def test_user_policy_may_disable_require_subagent(hooks: HookEnv) -> None:
    hooks.write_user_policy({"require_subagent": False})
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent=None)
    assert result.decision == "allow"


def test_project_policy_cannot_add_subagent_types(hooks: HookEnv) -> None:
    hooks.write_project_policy({"subagent_types": ["general-purpose"]})
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT, subagent="general-purpose")
    assert result.decision == "deny"


def test_project_policy_appends_deny_apps(hooks: HookEnv, settings_state: State) -> None:
    hooks.write_project_policy({"deny_apps": ["MyApp"], "allow_apps": ["MyApp"]})
    hooks.write_state(settings_state)
    result = run_guard(hooks, "computer__click", {"observation_id": "obs_7f3a", "ref": "e7"})
    assert result.decision == "deny"


def test_output_is_one_json_object_and_exit_zero(hooks: HookEnv) -> None:
    result = run_guard(hooks, "computer__observe", READ_ONLY_INPUT)
    assert result.code == 0
    assert set((result.output or {}).keys()) <= {"decision", "reason"}
