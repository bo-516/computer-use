"""Fail-closed policy, the desktop lock, grounding tiers and locate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from facade_helpers import Facade, Result, start_facade

from grok_computer_mcp.geometry import Point
from grok_computer_mcp.grounding.base import Candidate
from grok_computer_mcp.lock import DesktopLock

pytestmark = pytest.mark.anyio


@dataclass
class StubGrounder:
    """Grounder returning fixed candidates."""

    candidates: list[Candidate]
    name: str = "stub"
    calls: int = 0

    async def locate(
        self, image_jpeg: bytes, width: int, height: int, description: str
    ) -> list[Candidate]:
        """Return the configured candidates."""
        self.calls += 1
        assert image_jpeg[:2] == b"\xff\xd8" and width == 1280 and description
        return self.candidates


def first_ref(result: Result, label: str) -> str:
    """Ref of a listed element."""
    elements = cast(list[dict[str, object]], result.structured["elements"])
    return str(next(e["ref"] for e in elements if e["label"] == label))


async def test_malformed_policy_refuses_everything_until_fixed(facade: Facade) -> None:
    facade.write_user_policy("{broken")
    calls: list[tuple[str, dict[str, object]]] = [
        ("observe", {"app": "MyApp"}),
        ("apps", {"action": "list"}),
    ]
    for tool, args in calls:
        result = await facade.call(tool, args)
        assert result.code == "POLICY_ERROR", tool
        assert result.structured["retryable"] is False
    facade.write_user_policy({"deny_apps": ["Notes"]})
    assert not (await facade.call("observe", {"app": "MyApp"})).is_error
    assert (await facade.call("observe", {"app": "Notes"})).code == "APP_DENIED"


async def test_wrongly_typed_policy_value_is_a_policy_error(facade: Facade) -> None:
    facade.write_user_policy({"deny_apps": "Notes"})
    assert (await facade.call("observe", {"app": "MyApp"})).code == "POLICY_ERROR"


async def test_project_policy_tightens_but_cannot_loosen(tmp_path: Path) -> None:
    running = await start_facade(tmp_path)
    try:
        project = running.settings.workspace
        assert project is not None
        policy = project / ".grok" / "computer-policy.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"deny_apps": ["MyApp"], "require_subagent": false}')
        assert (await running.call("observe", {"app": "MyApp"})).code == "APP_DENIED"
    finally:
        await running.close()


async def test_second_facade_on_the_same_desktop_is_busy(tmp_path: Path) -> None:
    first = await start_facade(tmp_path / "a", GROK_COMPUTER_LOCK_DIR=str(tmp_path / "locks"))
    second = await start_facade(tmp_path / "b", GROK_COMPUTER_LOCK_DIR=str(tmp_path / "locks"))
    try:
        assert not (await first.call("observe", {"app": "MyApp"})).is_error
        busy = await second.call("observe", {"app": "MyApp"})
        assert busy.code == "BUSY"
        assert "pid" in str(busy.structured["message"])
        listing = await second.call("apps", {"action": "list"})
        assert not listing.is_error  # read-only listings do not need the desktop
        first.ctx.lock.release()
        assert not (await second.call("observe", {"app": "MyApp"})).is_error
    finally:
        await second.close()  # LIFO: both clients' task groups live in this task
        await first.close()


def test_lock_releases_after_idle(tmp_path: Path) -> None:
    clock = [0.0]
    lock = DesktopLock(tmp_path / "desktop.lock", idle_release_s=10, clock=lambda: clock[0])
    other = DesktopLock(tmp_path / "desktop.lock")
    lock.acquire()
    clock[0] = 5.0
    assert not lock.release_if_idle()
    clock[0] = 20.0
    assert lock.release_if_idle()
    other.acquire()
    other.release()


async def test_locate_without_grounder_is_unavailable(facade: Facade) -> None:
    obs = await facade.observe()
    result = await facade.call("locate", {"observation_id": obs.obs, "description": "Save"})
    assert result.code == "GROUNDING_UNAVAILABLE"


async def test_confident_locate_returns_one_point_without_image(tmp_path: Path) -> None:
    grounder = StubGrounder(
        [Candidate(Point(640.0, 400.0), 0.9), Candidate(Point(10.0, 10.0), 0.1)]
    )
    running = await start_facade(tmp_path, grounder=grounder)
    try:
        obs = await running.observe()
        result = await running.call(
            "locate", {"observation_id": obs.obs, "description": "the Save button"}
        )
        assert not result.is_error, result.text
        assert result.structured["confident"] is True
        assert result.images == []
        assert cast(list[object], result.structured["candidates"]) == [
            {"point": [640.0, 400.0], "confidence": 0.9}
        ]
    finally:
        await running.close()


async def test_unsure_locate_returns_candidates_and_annotated_image(tmp_path: Path) -> None:
    grounder = StubGrounder(
        [Candidate(Point(100.0, 100.0), 0.34), Candidate(Point(200.0, 120.0), 0.33)]
    )
    running = await start_facade(tmp_path, grounder=grounder)
    try:
        obs = await running.observe()
        result = await running.call("locate", {"observation_id": obs.obs, "description": "x"})
        assert result.structured["confident"] is False
        assert len(cast(list[object], result.structured["candidates"])) == 2
        assert len(result.images) == 1
    finally:
        await running.close()


async def test_strict_tier_accepts_only_located_points(tmp_path: Path) -> None:
    grounder = StubGrounder([Candidate(Point(1173 + 40, 789 + 15), 0.95)])
    running = await start_facade(tmp_path, grounder=grounder, GROK_COMPUTER_GROUNDING_TIER="strict")
    try:
        obs = await running.observe(mode="screenshot")
        raw = await running.call(
            "click", {"observation_id": obs.obs, "point": {"x": 300, "y": 300}}
        )
        assert raw.code == "POINT_UNGROUNDED"
        located = await running.call("locate", {"observation_id": obs.obs, "description": "Save"})
        point = cast(
            list[float], cast(list[dict[str, object]], located.structured["candidates"])[0]["point"]
        )
        ok = await running.call(
            "click", {"observation_id": obs.obs, "point": {"x": point[0], "y": point[1]}}
        )
        assert not ok.is_error, ok.text
        refs = await running.call(
            "click", {"observation_id": ok.obs, "ref": first_ref(obs, "Launch at login")}
        )
        assert not refs.is_error, refs.text  # refs are always allowed
    finally:
        await running.close()
