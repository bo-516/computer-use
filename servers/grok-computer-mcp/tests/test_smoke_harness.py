"""The harness itself works: tools are listed and an observation round-trips."""

from __future__ import annotations

import pytest
from facade_helpers import Facade

pytestmark = pytest.mark.anyio


async def test_observe_round_trip(facade: Facade) -> None:
    result = await facade.observe()
    assert result.structured["ok"] is True
    assert result.obs.startswith("obs_")
    assert "Dark mode" in result.text
