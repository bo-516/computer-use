"""In-process stand-ins for the Cua Bench session, for the eval tests only.

Boundary: test doubles. Variants are first *localized*: their two path roots (``/tmp/task`` on
the desktop under test, ``/tmp/grok-eval`` on the harness host) are rewritten under a pytest
temporary directory, so setup, oracle and violation steps touch nothing else. ``DirSession`` then
runs sandbox steps against real files there and executes only allow-listed file commands.
Anything that needs a real desktop either is skipped because no check reads its effect (package
installs, app launches) or raises ``DesktopOnly`` (gsettings, process checks), so a test can never
pass by silently ignoring a check. The harness side is the real ``hostenv.EnvHost``.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import taskkit
import taskrun

# Absolute path roots variants may use: desktop files and harness-host files (taskkit docstring).
PATH_ROOTS = ("/tmp/task", "/tmp/grok-eval")
TMP_PATH = re.compile(r"/tmp/[\w.\-/]*")
# File commands the double executes for real, inside the localized root.
FILE_COMMANDS = ("mkdir ", "cp ", "mv ", "touch ", "find ", "echo ", "test ", "rm ")
# Desktop setup the double skips: installs and app launches change nothing a check reads.
SKIPPED_COMMANDS = ("command -v ", "nohup ")
# Commands whose effect exists only on a real desktop; a variant using them cannot run here.
DESKTOP_COMMANDS = ("gsettings ", "pgrep ", "pkill ")
# Local file commands finish in milliseconds; a hang means a broken variant command.
COMMAND_TIMEOUT_S = 30


class DesktopOnly(Exception):
    """A step or check needs a real desktop session."""


def foreign_paths(text: str) -> list[str]:
    """``/tmp/...`` paths in ``text`` outside ``PATH_ROOTS``.

    Args:
        text: Serialized variant or command.

    Returns:
        Offending paths (empty when every path is under a known root).
    """
    paths = (p.rstrip("./") for p in TMP_PATH.findall(text))  # prose ends sentences with "."
    return [p for p in paths if not any(p == root or p.startswith(root + "/")
                                        for root in PATH_ROOTS)]


def localize(variant: taskkit.Variant, root: Path) -> taskkit.Variant:
    """Re-parse a variant with its path roots moved under ``root``.

    Args:
        variant: A variant from ``taskkit.load_all``.
        root: Test temporary directory.

    Returns:
        The same variant, touching only ``root``.

    Raises:
        AssertionError: The variant uses a ``/tmp`` path outside the known roots.
    """
    raw = json.dumps(dataclasses.asdict(variant))
    stray = foreign_paths(raw)
    assert not stray, f"{variant.id} uses paths outside {PATH_ROOTS}: {stray}"
    for prefix in PATH_ROOTS:
        target = json.dumps(str(root / prefix.strip("/").replace("/", "-")))[1:-1]
        raw = raw.replace(prefix, target)
    return taskkit.parse_variant(json.loads(raw))


@dataclass
class PageWindow:
    """A bench-ui window double: the page's ``window.__state``."""

    title: str
    html: str
    state: dict[str, object] = field(default_factory=dict[str, object])


class DirSession:
    """``taskkit.Session`` double backed by real files under a temporary root."""

    def __init__(self, root: Path, *, windows: bool) -> None:
        """Create the session.

        Args:
            root: Working directory for file commands.
            windows: True for the sandbox flavour (pages answer ``execute_javascript``); False
                for the host flavour (``launch_window`` returns None and page state comes from
                the fixture-server state, like ``LocalSession``).
        """
        self.root = root
        self.windows = windows
        self.opened: list[str] = []
        self.skipped: list[str] = []

    async def run_command(self, cmd: str,
                          check: bool = False) -> subprocess.CompletedProcess[bytes]:
        """Run an allow-listed file command; skip installs and launches.

        Raises:
            DesktopOnly: The command needs a real desktop.
            AssertionError: The command is not classified above.
        """
        if cmd.startswith(DESKTOP_COMMANDS):
            raise DesktopOnly(cmd)
        if cmd.startswith(SKIPPED_COMMANDS):
            self.skipped.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        assert cmd.startswith(FILE_COMMANDS), f"unclassified command: {cmd}"
        return subprocess.run(cmd, shell=True, cwd=self.root, check=check, capture_output=True,
                              timeout=COMMAND_TIMEOUT_S)

    async def read_file(self, path: str) -> str:
        """Read a file."""
        return Path(path).read_text(encoding="utf-8")

    async def write_file(self, path: str, content: str) -> None:
        """Write a file (parents created)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    async def file_exists(self, path: str) -> bool:
        """Whether a path exists."""
        return Path(path).exists()

    async def launch_window(self, html: str = "", title: str = "window") -> object:
        """Open a page; the shared library must already be inlined."""
        assert taskkit.LIB_MARKER not in html and "function record(" in html, title
        self.opened.append(title)
        return PageWindow(title, html) if self.windows else None

    async def execute_javascript(self, pid: object, script: str) -> object:
        """Answer the two script shapes ``taskrun`` sends: read state and ``record``."""
        assert isinstance(pid, PageWindow)
        read = "window.__state["
        if script.startswith(read) and script.endswith("]"):
            key: object = json.loads(script[len(read):-1])
            return pid.state.get(str(key))
        assert script.startswith("record(") and script.endswith(")"), script
        decoder = json.JSONDecoder()
        parsed_key = decoder.raw_decode(script, len("record("))
        assert script[parsed_key[1]:parsed_key[1] + 2] == ", ", script
        parsed_value = decoder.raw_decode(script, parsed_key[1] + 2)
        assert parsed_value[1] == len(script) - 1, script
        value: object = parsed_value[0]
        pid.state[str(parsed_key[0])] = value
        return None


@dataclass(frozen=True)
class Flow:
    """Check scores at each stage of a variant run."""

    initial: list[float]
    after_oracle: list[float]
    after_violation: list[float] | None


async def play(variant: taskkit.Variant, session: taskkit.Session, host: taskkit.Host) -> Flow:
    """Setup, score, oracle, score, then (red team) violation and score.

    Args:
        variant: A localized variant.
        session: Session double.
        host: Harness host services.

    Returns:
        Scores after setup, after the oracle and after the violation steps (None without any).
    """
    windows = await taskrun.run_steps(variant.setup, session, host)
    initial = await taskrun.evaluate(variant, session, host, windows)
    await taskrun.run_steps(variant.oracle, session, host, windows)
    after_oracle = await taskrun.evaluate(variant, session, host, windows)
    if not variant.violation:
        return Flow(initial, after_oracle, None)
    await taskrun.run_steps(variant.violation, session, host, windows)
    return Flow(initial, after_oracle, await taskrun.evaluate(variant, session, host, windows))
