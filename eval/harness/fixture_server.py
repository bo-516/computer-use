"""Local web server for the browser, webview and dev-loop tasks (127.0.0.1 only).

Boundary: serves fixture pages (with the shared library inlined), dev-loop apps from their host
directory, and in-memory window pages, and records page state posted by the fixtures into one
JSON file that the task checks read (``hostenv.EnvHost``). Nothing here is reachable off-host.

    python eval/harness/fixture_server.py --port 8765 --state /tmp/grok-eval/state.json
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1] / "tasks" / "computer_use"
sys.path.insert(0, str(ROOT))

import taskkit  # noqa: E402  (task module, path set up above)

# Variant descriptions name http://127.0.0.1:8765 (variants/browser.json, dev_loop.json).
DEFAULT_PORT = 8765
# Where dev-loop variants create their apps (variants/dev_loop.json).
DEFAULT_DEVLOOP = Path("/tmp/grok-eval/devloop")
# Fixture pages post small JSON values; anything larger is truncated, not trusted.
MAX_BODY_BYTES = 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    """Routes: GET /<fixture>.html, /devloop/..., /window/<name>, /state; POST /state/<key>;
    PUT /window/<name>."""

    server_version = "grok-eval-fixtures/1"
    state_file: ClassVar[Path]
    devloop: ClassVar[Path]
    windows: ClassVar[dict[str, str]]
    lock: ClassVar[threading.Lock]

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logs off stdout/stderr noise."""

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        """Write a response."""
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_state(self) -> dict[str, object]:
        """Current state ({} when absent or corrupt)."""
        try:
            data: object = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast(dict[str, object], data) if isinstance(data, dict) else {}

    def _body(self) -> bytes:
        """Request body (bounded)."""
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY_BYTES)
        return self.rfile.read(length)

    def do_GET(self) -> None:
        """Serve fixtures, dev-loop files, window pages and the state."""
        path = unquote(self.path.split("?", 1)[0])
        if path == "/state":
            self._send(200, json.dumps(self._read_state()).encode(), "application/json")
        elif path.startswith("/window/") and path[8:] in self.windows:
            self._send(200, self.windows[path[8:]].encode())
        elif path.startswith("/devloop/"):
            self._serve_file(self.devloop, path[len("/devloop/"):])
        elif path.endswith(".html") and (taskkit.FIXTURES / path.lstrip("/")).is_file():
            self._send(200, taskkit.fixture(path.lstrip("/")).encode())
        else:
            self._send(404, b"not found", "text/plain")

    def _serve_file(self, base: Path, rel: str) -> None:
        """Serve a file under ``base`` (directory index -> index.html); refuse escapes."""
        target = (base / rel).resolve()
        if target.is_dir():
            target = target / "index.html"
        if not target.is_relative_to(base.resolve()) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def do_POST(self) -> None:
        """Record page state: POST /state/<key> with a JSON value."""
        path = unquote(self.path)
        if not path.startswith("/state/"):
            self._send(404, b"not found", "text/plain")
            return
        try:
            value: object = json.loads(self._body() or b"null")
        except ValueError:
            self._send(400, b"bad json", "text/plain")
            return
        with self.lock:
            state = self._read_state()
            state[path[len("/state/"):]] = value
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self._send(204, b"")

    def do_PUT(self) -> None:
        """Store an in-memory window page: PUT /window/<name> with HTML."""
        path = unquote(self.path)
        if not path.startswith("/window/"):
            self._send(404, b"not found", "text/plain")
            return
        self.windows[path[len("/window/"):]] = self._body().decode("utf-8", "replace")
        self._send(204, b"")


def serve(port: int, state_file: Path, devloop: Path) -> ThreadingHTTPServer:
    """Start the server on a background thread.

    Args:
        port: TCP port on 127.0.0.1 (0 picks a free one).
        state_file: JSON state file shared with the checks.
        devloop: Root of the dev-loop apps.

    Returns:
        The running server (call ``shutdown()`` to stop it).
    """
    handler = type("Handler", (_Handler,), {"state_file": state_file, "devloop": devloop,
                                            "windows": {}, "lock": threading.Lock()})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    """CLI entry point (serves until interrupted)."""
    parser = argparse.ArgumentParser(description="Fixture server for the computer-use task set")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--devloop", type=Path, default=DEFAULT_DEVLOOP)
    args = parser.parse_args()
    server = serve(args.port, args.state, args.devloop)
    print(f"serving fixtures on http://127.0.0.1:{server.server_address[1]}", file=sys.stderr)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
