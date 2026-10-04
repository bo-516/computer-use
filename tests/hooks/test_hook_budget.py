"""Hook runtime budget: no network-capable or heavy imports, fast cold start (AGENTS.md)."""

from __future__ import annotations

import subprocess
import time
from typing import List

import pytest
from hookenv import HOOKS_BIN, PYTHON, HookEnv, event

# Modules whose presence would mean a hook can reach the network or spawn processes.
FORBIDDEN_MODULES = ("socket", "ssl", "http.client", "urllib.request", "subprocess", "asyncio")
# The real budget is < 100 ms of hook work; a CI runner can be several times slower than a
# laptop, so the test only catches gross regressions (a heavy import, a sleep).
COLD_START_CEILING_S = 1.0
RUNS = 3


@pytest.mark.parametrize("module", ["entry"])
def test_hooklib_imports_no_network_or_process_modules(module: str) -> None:
    probe = (
        f"import sys; sys.path.insert(0, {str(HOOKS_BIN)!r}); import hooklib.{module}; "
        f"print(','.join(m for m in {FORBIDDEN_MODULES!r} if m in sys.modules))"
    )
    out = subprocess.run([PYTHON, "-S", "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


@pytest.mark.slow
def test_guard_cold_start_is_fast(hooks: HookEnv) -> None:
    timings: List[float] = []
    for _ in range(RUNS):
        start = time.perf_counter()
        result = hooks.run("guard.py", event("computer__observe", {"mode": "auto"}))
        timings.append(time.perf_counter() - start)
        assert result.decision == "allow"
    assert min(timings) < COLD_START_CEILING_S, timings
