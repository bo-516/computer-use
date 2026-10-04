"""Audit hook (PostToolUse / PostToolUseFailure): redaction, metadata, R1 bookkeeping."""

from __future__ import annotations

import json
from typing import Dict

from hookenv import HookEnv, event

State = Dict[str, object]


def post(tool: str, tool_input: object, result: object, **extra: object) -> Dict[str, object]:
    """A PostToolUse payload."""
    payload = event(
        tool,
        tool_input,
        hookEventName="post_tool_use",
        hook_event_name="PostToolUse",
        toolResult=result,
    )
    payload.update(extra)
    return payload


def facade_result(**structured: object) -> Dict[str, object]:
    """An MCP CallToolResult as the host may pass it."""
    return {
        "content": [{"type": "text", "text": 'Clicked button "Save" (e15)'}],
        "structuredContent": dict(structured),
        "isError": False,
    }


def test_typed_text_is_redacted_to_length_and_two_chars(hooks: HookEnv) -> None:
    payload = post(
        "computer__type_text",
        {"observation_id": "obs_1", "ref": "e9", "text": "correct horse battery"},
        facade_result(ok=True, observation_id="obs_2", app="MyApp"),
    )
    result = hooks.run("audit.py", payload)
    assert result.code == 0 and result.stdout == ""
    [record] = hooks.audit_records()
    assert record["input"] == {
        "observation_id": "obs_1",
        "ref": "e9",
        "text": {"len": 21, "head": "co"},
    }
    assert "correct horse" not in json.dumps(record)


def test_nested_values_and_url_queries_are_redacted(hooks: HookEnv) -> None:
    fields = [{"name": "Name", "value": "Ana Lovelace"}]
    hooks.run("audit.py", post("browser__browser_fill_form", {"fields": fields}, "ok"))
    hooks.run(
        "audit.py",
        post("browser__browser_navigate", {"url": "https://x.test/cb?token=abcdef#frag"}, "ok"),
    )
    first, second = hooks.audit_records()
    assert first["input"] == {"fields": [{"name": "Name", "value": {"len": 12, "head": "An"}}]}
    nav_input = second["input"]
    assert isinstance(nav_input, dict)
    assert nav_input["url"] == "https://x.test/cb?<redacted 17 chars>"


def test_result_text_is_never_copied(hooks: HookEnv) -> None:
    payload = post(
        "computer__observe",
        {"mode": "tree"},
        'obs_9 app=MyApp\ne9 textfield "Display name" "secret value"',
    )
    hooks.run("audit.py", payload)
    [record] = hooks.audit_records()
    assert "secret value" not in json.dumps(record)
    assert record["observe"] is True and record["action"] is False


def test_structured_metadata_is_kept(hooks: HookEnv) -> None:
    structured = facade_result(
        ok=True,
        observation_id="obs_2",
        app="MyApp",
        delivery="background_ax",
        changes=[{"ref": "e7"}],
    )
    hooks.run(
        "audit.py",
        post("computer__click", {"observation_id": "obs_1", "ref": "e7"}, json.dumps(structured)),
    )
    [record] = hooks.audit_records()
    assert record["result"] == {
        "ok": True,
        "observation_id": "obs_2",
        "app": "MyApp",
        "delivery": "background_ax",
        "changes": 1,
    }
    assert record["action"] is True


def test_failure_events_are_recorded_without_result(hooks: HookEnv) -> None:
    payload = event(
        "computer__click",
        {"observation_id": "obs_1", "ref": "e7"},
        hookEventName="post_tool_use_failure",
        hook_event_name="PostToolUseFailure",
        error="STALE_OBSERVATION: re-observe",
    )
    hooks.run("audit.py", payload)
    [record] = hooks.audit_records()
    assert record["event"] == "tool_failure"
    assert record["error_len"] == len("STALE_OBSERVATION: re-observe")


def test_successful_action_marks_app_approved(hooks: HookEnv) -> None:
    hooks.run(
        "audit.py",
        post(
            "computer__click",
            {"observation_id": "obs_1", "ref": "e7"},
            facade_result(ok=True, app="Calculator"),
        ),
    )
    assert hooks.approved_apps() == ["Calculator"]
    hooks.run(
        "audit.py",
        post("computer__apps", {"action": "launch", "name": "Notes"}, facade_result(ok=True)),
    )
    assert hooks.approved_apps() == ["Calculator", "Notes"]


def test_read_only_calls_and_failures_do_not_approve(hooks: HookEnv) -> None:
    hooks.run(
        "audit.py",
        post("computer__observe", {"app": "Calculator"}, facade_result(ok=True, app="Calculator")),
    )
    failure = event(
        "computer__click",
        {"observation_id": "obs_1", "ref": "e7"},
        hookEventName="post_tool_use_failure",
        error="denied",
    )
    hooks.run("audit.py", failure)
    assert hooks.approved_apps() == []


def test_garbage_input_exits_zero_silently(hooks: HookEnv) -> None:
    for payload in ("not json", "", "[]", '{"toolName": 3}'):
        result = hooks.run("audit.py", payload)
        assert result.code == 0
        assert result.stdout == ""
