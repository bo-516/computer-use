"""Pytest fixtures for the facade tests (helpers live in ``facade_helpers``)."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from facade_helpers import Facade, start_facade

from grok_computer_mcp.backend.fake import DEFAULT_SCENE, FakeBackend


@pytest.fixture()
def anyio_backend() -> str:
    """Run async tests on asyncio (the MCP SDK's default loop)."""
    return "asyncio"


@pytest.fixture()
async def facade(tmp_path: Path) -> AsyncIterator[Facade]:
    """A facade over a fresh copy of the bundled fake scene."""
    running = await start_facade(tmp_path)
    try:
        yield running
    finally:
        await running.close()


@pytest.fixture()
async def dialogs(tmp_path: Path) -> AsyncIterator[Facade]:
    """A facade over the dialog, scroll, and credential scene."""
    scene = DEFAULT_SCENE.with_name("dialog_app.json")
    running = await start_facade(tmp_path / "dialogs", FakeBackend.from_file(scene))
    try:
        yield running
    finally:
        await running.close()
