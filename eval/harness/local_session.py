"""A ``taskkit.Session`` on the local desktop, for host-mode runs (CI container, Linux host).

Boundary: subprocess and file I/O on the machine grok drives. Windows are fixture pages served by
the fixture server and opened as browser app windows (Chromium ``--app`` with a throwaway
profile), so page state reaches the checks through the server; ``launch_window`` therefore
returns None and ``taskkit`` reads that state from the host instead of evaluating JavaScript.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

# Setup commands include package installs on a fresh container (goal.md §9.5 nightly job).
COMMAND_TIMEOUT_S = 120
# Publishing a page to the local fixture server; anything slower means the server is down.
PUBLISH_TIMEOUT_S = 10
# Browsers that can open a fixture page as an app window, in preference order.
BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


class LocalSession:
    """Runs task steps on this machine."""

    def __init__(self, fixture_url: str, profile: Path) -> None:
        """Create the session.

        Args:
            fixture_url: Base URL of the fixture server, e.g. ``http://127.0.0.1:8765``.
            profile: Throwaway browser profile directory for the app windows (per variant, so
                no page state leaks between variants).
        """
        self.fixture_url = fixture_url.rstrip("/")
        self.profile = profile

    async def run_command(self, cmd: str,
                          check: bool = False) -> subprocess.CompletedProcess[bytes]:
        """Run through the shell."""
        return subprocess.run(cmd, shell=True, check=check,
                              timeout=COMMAND_TIMEOUT_S, capture_output=True)

    async def read_file(self, path: str) -> str:
        """Read a local file."""
        return Path(path).read_text(encoding="utf-8")

    async def write_file(self, path: str, content: str) -> None:
        """Write a local file (parents created)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    async def file_exists(self, path: str) -> bool:
        """Whether a local path exists."""
        return Path(path).exists()

    async def launch_window(self, html: str = "", title: str = "window") -> object:
        """Publish the page on the fixture server and open it as an app window."""
        name = re.sub(r"[^A-Za-z0-9_-]", "-", title) + ".html"
        request = urllib.request.Request(f"{self.fixture_url}/window/{name}", data=html.encode(),
                                         method="PUT")
        urllib.request.urlopen(request, timeout=PUBLISH_TIMEOUT_S).close()
        browser = os.environ.get("GROK_COMPUTER_EVAL_BROWSER") or next(
            (b for b in BROWSERS if shutil.which(b)), None)
        if browser:
            subprocess.Popen([browser, f"--app={self.fixture_url}/window/{name}",
                              f"--user-data-dir={self.profile}", "--no-first-run"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None

    async def execute_javascript(self, pid: object, script: str) -> object:
        """Not available on host runs (state is read from the fixture server instead)."""
        raise NotImplementedError("host runs read page state from the fixture server")
