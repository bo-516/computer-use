"""Settings parsing, trace retention, and the fake backend's driver-like behaviour."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from grok_computer_mcp.app import lock_name
from grok_computer_mcp.backend.base import BackendError, BackendErrorKind, Delivery, ElementTarget
from grok_computer_mcp.backend.fake import FakeBackend
from grok_computer_mcp.config import ConfigError, load_settings
from grok_computer_mcp.trace.recorder import TraceRecorder, purge

pytestmark = pytest.mark.anyio
HOME = Path("/home/u")
CWD = Path("/work/project")


def test_defaults_and_paths() -> None:
    s = load_settings({}, HOME, CWD)
    assert (s.backend, s.mode, s.long_edge, s.cua_transport) == ("cua-driver", "host", 1280, "mcp")
    assert s.state_dir == HOME / ".grok-computer" / "state"
    assert s.lock_dir == HOME / ".grok-computer" / "run"
    assert s.workspace == CWD and s.grounding_provider == "none"
    data = load_settings({"GROK_PLUGIN_DATA": "/data/p"}, HOME, CWD)
    assert data.state_dir == Path("/data/p/state") and data.trace_dir == Path("/data/p/traces")


def test_unexpanded_plugin_data_falls_back() -> None:
    """V5: a literal ${GROK_PLUGIN_DATA} must not become a relative directory."""
    s = load_settings({"GROK_PLUGIN_DATA": "${GROK_PLUGIN_DATA}"}, HOME, CWD)
    assert s.state_dir == HOME / ".grok-computer" / "state"


@pytest.mark.parametrize(
    "env",
    [
        {"GROK_COMPUTER_BACKEND": "playwright"},
        {"GROK_COMPUTER_SCREENSHOT_LONG_EDGE": "2000"},
        {"GROK_COMPUTER_SCREENSHOT_LONG_EDGE": "big"},
        {"GROK_COMPUTER_GROUNDING_TIER": "lax"},
        {"GROK_COMPUTER_TRACE_RETENTION_DAYS": "0"},
        {"GROK_COMPUTER_SOM_REGIONS": "yes"},
    ],
)
def test_invalid_values_raise(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_settings(env, HOME, CWD)


def test_grounding_defaults_to_uitars_when_url_given() -> None:
    s = load_settings({"GROK_COMPUTER_GROUNDING_URL": "http://gpu:8000/v1"}, HOME, CWD)
    assert s.grounding_provider == "uitars"


def test_sandbox_targets_get_their_own_lock() -> None:
    host = load_settings({}, HOME, CWD)
    sandbox = load_settings(
        {"GROK_COMPUTER_MODE": "sandbox", "GROK_COMPUTER_CUA_SOCKET": "tcp://vm:9000"}, HOME, CWD
    )
    assert lock_name(host) == "desktop-host.lock"
    assert lock_name(sandbox).startswith("desktop-") and lock_name(sandbox) != lock_name(host)


def test_trace_recorder_redacts_and_purge_respects_age(tmp_path: Path) -> None:
    rec = TraceRecorder(tmp_path, "abc")
    rec.record("type_text", {"text": "secret words", "observation_id": "obs_1"}, {"ok": True}, 3)
    line = (tmp_path / "facade-abc" / "trace.jsonl").read_text()
    assert "secret" not in line and '"len": 12' in line
    old = tmp_path / "audit-old.jsonl"
    old.write_text("{}")
    stamp = time.time() - 10 * 86_400
    os.utime(old, (stamp, stamp))
    removed = purge(tmp_path, 7)
    assert removed == [old] and (tmp_path / "facade-abc").exists()


async def test_fake_backend_behaves_like_a_driver() -> None:
    backend = FakeBackend.from_file()
    [settings] = await backend.list_windows("MyApp")
    first = await backend.read_window(settings, screenshot=False, max_elements=100)
    save = next(e for e in first.elements if e.label == "Save")
    await backend.read_window(settings, screenshot=False, max_elements=100)
    with pytest.raises(BackendError) as stale:
        await backend.click(
            ElementTarget(settings, save.index, save.handle),
            button="left",
            count=1,
            modifiers=(),
            delivery=Delivery.BACKGROUND,
        )
    assert stale.value.kind is BackendErrorKind.STALE_HANDLE
    windows = await backend.launch_app("calculator")
    assert windows and windows[0].title == "Calculator"
    with pytest.raises(BackendError):
        await backend.launch_app("NoSuchApp")
