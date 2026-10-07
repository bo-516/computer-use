"""The MCP contract (AGENTS.md "MCP contract"): names, annotations, schemas, result shapes."""

from __future__ import annotations

import json
import re
from typing import cast

import jsonschema
import pytest
from facade_helpers import REPO, Facade, Result

from grok_computer_mcp.errors import HINTS, RETRYABLE, ErrorCode
from grok_computer_mcp.tools import TOOLS, tool_definitions

pytestmark = pytest.mark.anyio

PUBLIC_TOOLS = [
    "observe",
    "click",
    "type_text",
    "press_keys",
    "scroll",
    "drag",
    "apps",
    "wait_for",
    "locate",
]
EXPECTED_HINTS = {  # (readOnly, destructive, idempotent)
    "observe": (True, False, True),
    "click": (False, True, False),
    "type_text": (False, True, False),
    "press_keys": (False, True, False),
    "scroll": (False, False, False),
    "drag": (False, True, False),
    "apps": (False, False, True),
    "wait_for": (True, False, True),
    "locate": (True, False, True),
}


def _walk(node: object) -> list[dict[str, object]]:
    """Every object node of a schema."""
    out: list[dict[str, object]] = []
    if isinstance(node, dict):
        item = cast(dict[str, object], node)
        out.append(item)
        for value in item.values():
            out += _walk(value)
    elif isinstance(node, list):
        for value in cast(list[object], node):
            out += _walk(value)
    return out


async def test_exactly_the_nine_public_tools_are_listed(facade: Facade) -> None:
    listed = await facade.client.list_tools()
    assert [t.name for t in listed.tools] == PUBLIC_TOOLS
    assert "status" not in [t.name for t in listed.tools]


def test_annotations_match_the_contract() -> None:
    for tool in tool_definitions():
        ann = tool.annotations
        assert ann is not None
        hints = (ann.read_only_hint, ann.destructive_hint, ann.idempotent_hint)
        assert hints == EXPECTED_HINTS[tool.name], tool.name


def test_wait_for_schema_advertises_enabled_and_title() -> None:
    assert [tool.name for tool in TOOLS] == PUBLIC_TOOLS
    wait = next(tool for tool in tool_definitions() if tool.name == "wait_for")
    props = cast(dict[str, object], wait.input_schema["properties"])
    assert "enabled" in props and "title" in props
    assert "text" in props and "ref_role" in props and "gone" in props


def test_schemas_are_self_contained_objects() -> None:
    for tool in tool_definitions():
        assert tool.input_schema.get("type") == "object"
        assert tool.output_schema is not None and tool.output_schema.get("type") == "object"
        for node in _walk(tool.input_schema) + _walk(tool.output_schema):
            assert "$ref" not in node and "$defs" not in node, tool.name
    drag = next(t for t in tool_definitions() if t.name == "drag")
    props = cast(dict[str, object], drag.input_schema["properties"])
    assert "from" in props and "from_" not in props


def test_every_action_tool_takes_an_observation_id() -> None:
    for spec in TOOLS:
        if not spec.read_only and spec.name != "apps":
            assert "observation_id" in spec.input_model.model_fields, spec.name


def _validate(result: Result, tool: str) -> None:
    """Validate structured content against the tool's output schema."""
    schema = next(t for t in tool_definitions() if t.name == tool).output_schema
    jsonschema.validate(result.structured, cast(dict[str, object], schema))


async def test_results_validate_against_output_schemas(facade: Facade) -> None:
    obs = await facade.observe()
    _validate(obs, "observe")
    som = await facade.observe(mode="som")
    _validate(som, "observe")
    dark = next(
        e
        for e in cast(list[dict[str, object]], obs.structured["elements"])
        if e["label"] == "Dark mode"
    )
    click = await facade.call("click", {"observation_id": obs.obs, "ref": dark["ref"]})
    _validate(click, "click")
    _validate(await facade.call("apps", {"action": "list"}), "apps")
    _validate(await facade.call("wait_for", {"app": "MyApp", "text": "Save"}), "wait_for")
    error = await facade.call("type_text", {"observation_id": "obs_00", "text": "x"})
    assert error.is_error
    _validate(error, "type_text")
    assert set(error.structured) == {"ok", "code", "message", "retryable", "hint"}


async def test_results_respect_output_budgets(facade: Facade) -> None:
    obs = await facade.observe(mode="screenshot")
    assert len(obs.images) <= 1
    assert len(obs.text.encode()) <= 16 * 1024
    click = await facade.call(
        "click",
        {
            "observation_id": obs.obs,
            "ref": cast(list[dict[str, object]], obs.structured["elements"])[0]["ref"],
        },
    )
    assert click.images == []
    assert len(json.dumps(click.structured["changes"]).encode()) <= 2048


def test_every_error_code_has_hint_retryable_and_doc_row() -> None:
    goal = (REPO / "docs" / "goal.md").read_text(encoding="utf-8")
    table = goal[goal.index("### 5.8") : goal.index("### 5.9")]
    documented = set(re.findall(r"^\| `([A-Z_]+)` \|", table, re.M))
    for code in ErrorCode:
        assert HINTS[code] and code in RETRYABLE, code
        assert code.value in documented, f"{code.value} missing from goal.md §5.8"
    assert documented == {c.value for c in ErrorCode}
