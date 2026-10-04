"""The Cua Bench entry point (``main.py``) lists every variant and wires setup, evaluation and
the oracle, checked against a stand-in ``cua_bench`` module."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar, cast

import pytest
import taskkit
from benchfake import DirSession, localize

MAIN = Path(__file__).resolve().parents[1] / "tasks" / "computer_use" / "main.py"
_F = TypeVar("_F")


@dataclass
class FakeTask:
    """``cua_bench.Task`` stand-in."""

    description: str
    task_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict[str, object])
    computer: dict[str, object] = field(default_factory=dict[str, object])


def _passthrough(split: str = "train") -> Callable[[_F], _F]:
    """Decorator factory that registers nothing."""
    assert split == "train"

    def wrap(fn: _F) -> _F:
        return fn
    return wrap


class BenchMain(Protocol):
    """The parts of ``main.py`` the tests call."""

    VARIANTS: dict[str, taskkit.Variant]
    OS_TYPE: str

    def load(self) -> list[FakeTask]:
        """Task list."""
        ...

    async def start(self, task_cfg: FakeTask, session: DirSession) -> None:
        """Setup."""
        ...

    async def evaluate(self, task_cfg: FakeTask, session: DirSession) -> list[float]:
        """Checks."""
        ...

    async def solve(self, task_cfg: FakeTask, session: DirSession) -> None:
        """Oracle."""
        ...


@pytest.fixture()
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BenchMain:
    """``main.py`` loaded against the stand-in module, with host state under ``tmp_path``."""
    fake = types.ModuleType("cua_bench")
    fake.__dict__.update(Task=FakeTask, DesktopSession=object, tasks_config=_passthrough,
                         setup_task=_passthrough, evaluate_task=_passthrough,
                         solve_task=_passthrough)
    monkeypatch.setitem(sys.modules, "cua_bench", fake)
    monkeypatch.setenv("GROK_COMPUTER_EVAL_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("GROK_COMPUTER_EVAL_AUDIT_DIR", str(tmp_path / "audit"))
    spec = importlib.util.spec_from_file_location("computer_use_bench_main", MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(BenchMain, module)


def test_every_variant_is_a_task_with_its_metadata(bench: BenchMain):
    tasks = bench.load()
    assert [t.task_id for t in tasks] == [v.id for v in taskkit.load_all()]
    for task in tasks:
        variant = bench.VARIANTS[task.task_id]
        assert task.description == variant.description
        assert task.metadata["category"] == variant.category
        assert task.metadata["red_team"] == variant.red_team
        setup = cast(dict[str, object], task.computer["setup_config"])
        assert setup["os_type"] == bench.OS_TYPE == "linux"
        assert max(cast(int, setup["width"]), cast(int, setup["height"])) == 1280


@pytest.mark.parametrize("vid", ["webview-03", "browser-05", "native-08"])
def test_setup_evaluate_and_oracle_round_trip(bench: BenchMain, vid: str, tmp_path: Path):
    bench.VARIANTS[vid] = localize(bench.VARIANTS[vid], tmp_path)
    task = next(t for t in bench.load() if t.task_id == vid)
    session = DirSession(tmp_path, windows=True)

    async def flow() -> tuple[list[float], list[float]]:
        await bench.start(task, session)
        before = await bench.evaluate(task, session)
        await bench.solve(task, session)
        return before, await bench.evaluate(task, session)

    before, after = asyncio.run(flow())
    assert 0.0 in before and after == [1.0] * len(after)
