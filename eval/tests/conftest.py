"""Pytest setup for the eval tests: import paths for the task, harness and helper modules.

The eval code is a set of scripts, not a package (Cua Bench loads ``main.py`` by path), so the
tests put their directories on ``sys.path`` the same way the scripts do. Helpers live in
``benchfake`` rather than here, so they can be imported by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
REPO = EVAL.parent
sys.path[:0] = [str(EVAL / "tests"), str(EVAL / "tasks" / "computer_use"), str(EVAL / "harness"),
                str(EVAL / "grounding"), str(EVAL / "phase0"),
                str(REPO / "plugins" / "computer-use" / "hooks" / "bin")]
