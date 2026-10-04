"""The facade -> hooks contract: ``last_observation.json`` as written is what the guard reads."""

from __future__ import annotations

import json
import time
from typing import cast

import jsonschema
import pytest
from facade_helpers import SCHEMA, Facade, Result

from grok_computer_mcp.hooklib.defaults import DEFAULT_POLICY
from grok_computer_mcp.hooklib.guard import decide
from grok_computer_mcp.hooklib.policy import LayerResult
from grok_computer_mcp.hooklib.state import pick_fresh_state
from grok_computer_mcp.limits import STATE_FILE_MAX_BYTES

pytestmark = pytest.mark.anyio
ALLOW_MYAPP = LayerResult(DEFAULT_POLICY._replace(allow_apps=("MyApp",)), (), ())


def schema() -> dict[str, object]:
    """The published state schema."""
    return cast(dict[str, object], json.loads(SCHEMA.read_text(encoding="utf-8")))


def ref(result: Result, label: str) -> str:
    """Ref of a listed element."""
    return str(
        next(
            e["ref"]
            for e in cast(list[dict[str, object]], result.structured["elements"])
            if e["label"] == label
        )
    )


def guard(state: dict[str, object], tool: str, args: dict[str, object]) -> str:
    """Decision of the hooks' guard for a facade call on this state."""
    fresh = pick_fresh_state([state], time.time(), DEFAULT_POLICY.state_max_age_s)
    event: dict[str, object] = {
        "toolName": f"computer__{tool}",
        "toolInput": args,
        "subagentType": "computer-use:computer",
        "sessionId": "s",
    }
    return decide(event, ALLOW_MYAPP, fresh, frozenset()).decision


async def test_written_state_validates_and_lists_interactive_elements(facade: Facade) -> None:
    obs = await facade.observe(mode="som")
    state = facade.state()
    jsonschema.validate(state, schema())
    assert state["observation_id"] == obs.obs
    elements = cast(dict[str, dict[str, object]], state["elements"])
    labels = {e["label"] for e in elements.values()}
    assert "Profile" not in labels and "Dark mode" in labels
    token = next(e for e in elements.values() if e["label"] == "API token")
    assert token["secure"] is True
    assert state["marks"] and state["last_observe_at"] == state["updated_at"]
    assert cast(dict[str, object], state["focused"])["label"] == "Display name"


async def test_hooks_reach_the_expected_decisions_on_facade_state(facade: Facade) -> None:
    obs = await facade.observe()
    state = facade.state()
    oid = obs.obs
    assert guard(state, "click", {"observation_id": oid, "ref": ref(obs, "Dark mode")}) == "allow"
    assert (
        guard(state, "click", {"observation_id": oid, "ref": ref(obs, "Delete account")}) == "ask"
    )
    assert (
        guard(
            state, "type_text", {"observation_id": oid, "ref": ref(obs, "API token"), "text": "x"}
        )
        == "deny"
    )
    assert (
        guard(
            state, "type_text", {"observation_id": oid, "ref": ref(obs, "Card number"), "text": "x"}
        )
        == "deny"
    )
    delete_box = next(
        e["bbox"]
        for e in cast(list[dict[str, object]], obs.structured["elements"])
        if e["label"] == "Delete account"
    )
    x, y, w, h = cast(list[int], delete_box)
    assert (
        guard(state, "click", {"observation_id": oid, "point": {"x": x + w / 2, "y": y + h / 2}})
        == "ask"
    )
    assert guard(state, "press_keys", {"observation_id": oid, "keys": "enter"}) == "ask"


async def test_action_timestamps_drive_the_verify_gate(facade: Facade) -> None:
    obs = await facade.observe()
    await facade.call("click", {"observation_id": obs.obs, "ref": ref(obs, "Dark mode")})
    acted = facade.state()
    assert acted["last_action_at"] is not None and acted["last_observe_at"] is not None
    assert float(cast(float, acted["last_action_at"])) > float(
        cast(float, acted["last_observe_at"])
    )
    await facade.observe()
    observed = facade.state()
    assert float(cast(float, observed["last_observe_at"])) > float(
        cast(float, observed["last_action_at"])
    )


async def test_point_clicks_record_last_hit(facade: Facade) -> None:
    obs = await facade.observe(mode="screenshot")
    box = next(
        e["bbox"]
        for e in cast(list[dict[str, object]], obs.structured["elements"])
        if e["label"] == "Save"
    )
    x, y, w, h = cast(list[int], box)
    clicked = await facade.call(
        "click", {"observation_id": obs.obs, "point": {"x": x + w / 2, "y": y + h / 2}}
    )
    assert not clicked.is_error, clicked.text
    assert facade.backend.calls  # the click happened after the hit was recorded
    traces = list((facade.settings.trace_dir).glob("facade-*/trace.jsonl"))
    assert traces and "hunter" not in traces[0].read_text()


async def test_state_stays_under_64_kb(facade: Facade) -> None:
    window = facade.backend.window("Settings")
    files = window.nodes["files"]
    template = window.nodes["row1"]
    for i in range(13, 1500):
        node_id = f"row{i}"
        window.nodes[node_id] = type(template)(
            id=node_id,
            role="AXRow",
            label=f"{'x' * 150}-{i}",
            frame=template.frame,
            on_click=["toggle"],
        )
        files.children.append(node_id)
    await facade.observe(max_elements=10)
    path = facade.settings.state_dir / "last_observation.json"
    assert path.stat().st_size <= STATE_FILE_MAX_BYTES
    jsonschema.validate(facade.state(), schema())
    assert not list(path.parent.glob("*.tmp"))
