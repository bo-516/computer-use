"""Pytest fixtures for the plugin hook tests; helpers live in ``hookenv``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hookenv import HookEnv, load_fixture


@pytest.fixture()
def hooks(tmp_path: Path) -> HookEnv:
    """An isolated hook environment."""
    return HookEnv(tmp_path)


@pytest.fixture()
def settings_state() -> Dict[str, object]:
    """The MyApp settings-window observation used across guard tests."""
    return load_fixture("state_settings.json")
