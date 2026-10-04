"""Cua Bench task: GUI work for the Grok Build computer-use plugin (goal.md §9.3).

Sixty variants in six categories (native 12, webview 10, browser 12, cross-app 6, dev loop 10,
red team 10), defined in ``variants/*.json`` and interpreted by ``taskkit``. The agent is grok in
headless mode, driven by ``eval/harness/run_grok.py``; the harness also provides host services
(fixture web server state, audit logs) through ``hostenv.EnvHost``.

    cb task info eval/tasks/computer_use
    cb run eval/tasks/computer_use --variant-id 3 --oracle    # prove the checker can pass
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cua_bench as cb

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hostenv
import taskkit
import taskrun

VARIANTS = {v.id: v for v in taskkit.load_all()}
WINDOWS: dict[str, dict[str, object]] = {}
HOST = hostenv.EnvHost.from_env()
# Desktop under test: Linux sandbox by default; macOS release runs set GROK_COMPUTER_EVAL_OS.
OS_TYPE = os.environ.get("GROK_COMPUTER_EVAL_OS", "linux")
# Sandbox screen size: long edge equals the canonical screenshot edge (goal.md §5.4).
SCREEN = {"width": 1280, "height": 800}


@cb.tasks_config(split="train")
def load() -> list[cb.Task]:
    """All variants, in a stable order (``--variant-id`` indexes this list)."""
    return [cb.Task(description=v.description, task_id=v.id,
                    metadata={"category": v.category, "modes": list(v.modes),
                              "allow_apps": list(v.allow_apps), "red_team": v.red_team},
                    computer={"provider": "native",
                              "setup_config": {"os_type": OS_TYPE, **SCREEN}})
            for v in VARIANTS.values()]


@cb.setup_task(split="train")
async def start(task_cfg: cb.Task, session: cb.DesktopSession) -> None:
    """Prepare files, apps, windows and fixture state for the variant."""
    variant = VARIANTS[task_cfg.task_id]
    WINDOWS[variant.id] = await taskrun.run_steps(variant.setup, session, HOST)


@cb.evaluate_task(split="train")
async def evaluate(task_cfg: cb.Task, session: cb.DesktopSession) -> list[float]:
    """Score the variant's checks (red-team checks score safety)."""
    variant = VARIANTS[task_cfg.task_id]
    return await taskrun.evaluate(variant, session, HOST, WINDOWS.get(variant.id, {}))


@cb.solve_task(split="train")
async def solve(task_cfg: cb.Task, session: cb.DesktopSession) -> None:
    """Oracle: reach the success state without the agent."""
    variant = VARIANTS[task_cfg.task_id]
    await taskrun.run_steps(variant.oracle, session, HOST, WINDOWS.get(variant.id, {}))
