"""Data-driven Cua Bench variants: specs and the interfaces they run against (goal.md §9.3).

Boundary: pure parsing and validation; ``taskrun`` executes steps and checks. Variants live in
``variants/<category>.json``::

    {"id": "native-01", "category": "native", "description": "...",
     "modes": ["host", "sandbox"], "setup": [steps], "oracle": [steps], "check": [checks],
     "allow_apps": ["Mousepad"], "red_team": false, "tags": [], "violation": [steps]}

Steps and checks are one-key objects. Sandbox-side kinds go through the Cua Bench session (the
desktop under test); ``host_*`` kinds run where grok and the browser server run (the harness
host), which is where dev-loop workspaces and the fixture web server live. ``page_state`` checks
and ``page_record`` steps address a fixture page's recorded state: in a sandbox through the
bench-ui window, on a host run through the fixture server (every page posts its state there).

Each check scores 1.0 or 0.0; Cua Bench averages them into the reward. Red-team checks assert
that a forbidden effect did *not* happen. The oracle is an independent route to success, so
``cb run --oracle`` proves every checker can pass; ``eval/tests`` proves each one fails on the
initial state and passes after the oracle. Red-team variants also carry ``violation`` steps that
produce the forbidden effect, so the tests prove their checkers can fail (a red-team check that
cannot fail would report a safe agent no matter what it did).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

TASK_DIR = Path(__file__).resolve().parent
VARIANT_DIR = TASK_DIR / "variants"
FIXTURES = TASK_DIR / "fixtures"
# goal.md §9.3: variants per category at the end of Phase 2 (60 in total).
CATEGORIES = {"native": 12, "webview": 10, "browser": 12, "cross_app": 6, "dev_loop": 10,
              "red_team": 10}
SANDBOX_STEPS = ("run", "write_file", "launch", "window", "page_record")
HOST_STEPS = ("host_run", "host_write", "host_post")
SANDBOX_CHECKS = ("file_equals", "file_contains", "file_absent", "file_exists", "json_field",
                  "command_ok", "page_state")
HOST_CHECKS = ("host_state", "host_state_absent", "host_command_ok", "audit_has")
MODES = ("host", "sandbox")

Json = dict[str, object]


class Session(Protocol):
    """The part of ``cua_bench.DesktopSession`` the variants use."""

    async def run_command(self, cmd: str, check: bool = ...) -> object:
        """Run a shell command on the desktop under test."""
        ...

    async def read_file(self, path: str) -> str:
        """Read a file on the desktop under test."""
        ...

    async def write_file(self, path: str, content: str) -> None:
        """Write a file on the desktop under test."""
        ...

    async def file_exists(self, path: str) -> bool:
        """Whether a file exists on the desktop under test."""
        ...

    async def launch_window(self, html: str = ..., title: str = ...) -> object:
        """Open an HTML window (bench-ui) and return its id."""
        ...

    async def execute_javascript(self, pid: object, script: str) -> object:
        """Evaluate JavaScript in a task window."""
        ...


class Host(Protocol):
    """Harness-side services (``hostenv.EnvHost``)."""

    def run(self, cmd: str) -> int:
        """Run a shell command on the harness host; return its exit code."""
        ...

    def write(self, path: str, text: str) -> None:
        """Write a file on the harness host."""
        ...

    def post(self, key: str, payload: object) -> None:
        """Seed fixture-server state."""
        ...

    def state(self) -> Json:
        """Fixture-server state (what browser pages recorded)."""
        ...

    def audit_records(self) -> list[Json]:
        """Audit-log records of the run (guard asks/denies included)."""
        ...


@dataclass(frozen=True)
class Variant:
    """One task variant."""

    id: str
    category: str
    description: str
    modes: tuple[str, ...]
    setup: tuple[Json, ...]
    oracle: tuple[Json, ...]
    check: tuple[Json, ...]
    allow_apps: tuple[str, ...]
    red_team: bool
    tags: tuple[str, ...] = ()
    violation: tuple[Json, ...] = ()


def _entries(value: object, where: str, kinds: tuple[str, ...]) -> tuple[Json, ...]:
    """Validate a list of one-key objects with known kinds."""
    if not isinstance(value, list):
        raise TypeError(f"{where} must be a list")
    out: list[Json] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict) or len(cast(Json, item)) != 1:
            raise ValueError(f"{where}: each entry is a one-key object")
        entry = cast(Json, item)
        if next(iter(entry)) not in kinds:
            raise ValueError(f"{where}: unknown kind {next(iter(entry))!r}")
        out.append(entry)
    return tuple(out)


def parse_variant(spec: Json) -> Variant:
    """Validate one variant spec.

    Args:
        spec: Decoded JSON object.

    Returns:
        The variant.

    Raises:
        ValueError: Missing fields or unknown kinds.
        KeyError: A required field is absent.
    """
    vid = str(spec["id"])
    steps = SANDBOX_STEPS + HOST_STEPS
    modes = tuple(str(m) for m in cast(list[object], spec.get("modes", list(MODES))))
    if not modes or any(m not in MODES for m in modes):
        raise ValueError(f"{vid}: modes must be a subset of {MODES}")
    return Variant(
        id=vid, category=str(spec["category"]), description=str(spec["description"]),
        modes=modes, setup=_entries(spec.get("setup", []), f"{vid}.setup", steps),
        oracle=_entries(spec.get("oracle", []), f"{vid}.oracle", steps),
        check=_entries(spec["check"], f"{vid}.check", SANDBOX_CHECKS + HOST_CHECKS),
        allow_apps=tuple(str(a) for a in cast(list[object], spec.get("allow_apps", []))),
        red_team=spec.get("red_team") is True,
        tags=tuple(str(t) for t in cast(list[object], spec.get("tags", []))),
        violation=_entries(spec.get("violation", []), f"{vid}.violation", steps))


def load_all() -> list[Variant]:
    """Every variant, in category order (the order of ``CATEGORIES``).

    Returns:
        The 60 variants.
    """
    out: list[Variant] = []
    for category in CATEGORIES:
        raw: object = json.loads((VARIANT_DIR / f"{category}.json").read_text(encoding="utf-8"))
        out += [parse_variant(cast(Json, item)) for item in cast(list[object], raw)]
    return out


# Placeholder in every fixture page where the shared record()/restore() library is inlined.
LIB_MARKER = "<!-- fixture-lib -->"


def fixture(name: str) -> str:
    """HTML of a fixture page with the shared state/record library inlined.

    Pages are self-contained because bench-ui windows receive HTML, not a URL; the fixture web
    server serves the same expanded HTML to the browser.
    """
    lib = (FIXTURES / "fixture-lib.html").read_text(encoding="utf-8")
    return (FIXTURES / name).read_text(encoding="utf-8").replace(LIB_MARKER, lib)
