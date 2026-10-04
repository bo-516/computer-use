"""Every checker is meaningful: it fails before the work, passes after the oracle, and (red team)
fails after the forbidden effect.

Each variant runs in both flavours: ``sandbox`` (bench-ui windows answer JavaScript) and ``host``
(page state goes through the fixture-server state file, as on ``run_grok.py`` runs).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import hostenv
import pytest
import taskkit
from benchfake import DesktopOnly, DirSession, Flow, localize, play

VARIANTS = taskkit.load_all()
# Variants whose setup or checks need a real desktop (gsettings, process state); they are
# covered by ``cb run --oracle`` in the nightly container instead.
DESKTOP_ONLY = {"native-12": "gsettings", "rt-06": "pgrep/pkill"}


def _run(variant: taskkit.Variant, root: Path, windows: bool) -> Flow:
    """Localize and play one variant."""
    local = localize(variant, root)
    host = hostenv.EnvHost(root / "fixture-state.json", root / "audit")
    return asyncio.run(play(local, DirSession(root, windows=windows), host))


@pytest.mark.parametrize("windows", [True, False], ids=["sandbox", "host"])
@pytest.mark.parametrize("variant", VARIANTS, ids=[v.id for v in VARIANTS])
def test_checker_is_meaningful(variant: taskkit.Variant, windows: bool, tmp_path: Path):
    uses_node = any("node " in str(c) for c in variant.check)
    if uses_node and shutil.which("node") is None:
        pytest.skip("node is not installed")
    try:
        flow = _run(variant, tmp_path, windows)
    except DesktopOnly as exc:
        assert variant.id in DESKTOP_ONLY, f"{variant.id} needs a desktop: {exc}"
        pytest.skip(f"needs a desktop session ({DESKTOP_ONLY[variant.id]})")
    assert variant.id not in DESKTOP_ONLY, "runs in-process now; drop it from DESKTOP_ONLY"
    assert flow.after_oracle == [1.0] * len(variant.check), "the oracle does not pass"
    if variant.red_team:
        # Doing nothing is safe, the safe completion stays safe, the violation is caught.
        assert flow.initial == [1.0] * len(variant.check)
        assert flow.after_violation is not None and 0.0 in flow.after_violation
    else:
        assert 0.0 in flow.initial, "the task already passes before any work"
        assert flow.after_violation is None


def test_red_team_oracles_leave_no_trace_of_violation():
    """The oracle of a red-team variant is the safe completion, never part of the violation."""
    for variant in (v for v in VARIANTS if v.red_team):
        assert not set(map(str, variant.oracle)) & set(map(str, variant.violation)), variant.id
