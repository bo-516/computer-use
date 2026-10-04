"""``cua-driver call``: the CLI transport (``GROK_COMPUTER_CUA_TRANSPORT=cli``).

Boundary: one subprocess per call, JSON on stdout. Useful where a persistent MCP session is not
possible (wrapped commands such as ``ssh vm cua-driver`` in sandbox mode). Screenshots are written
by the driver to a temporary file through ``screenshot_out_file`` and read back.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import anyio

from ..limits import BACKEND_CALL_TIMEOUT_S
from .base import BackendError, BackendErrorKind
from .cua_transport import CuaReply, JsonObject


class CliTransport:
    """``cua-driver call <tool> <json>`` per call (no persistent session)."""

    def __init__(self, command: tuple[str, ...], socket: str | None,
                 timeout_s: float = BACKEND_CALL_TIMEOUT_S) -> None:
        """Configure the transport.

        Args:
            command: Executable and leading arguments.
            socket: Explicit daemon endpoint (``--socket``).
            timeout_s: Per-call timeout.
        """
        self._command = command
        self._socket = socket
        self._timeout = timeout_s

    async def start(self) -> None:
        """Nothing to start; each call spawns the CLI."""

    async def close(self) -> None:
        """Nothing to close."""

    async def call(self, tool: str, args: JsonObject) -> CuaReply:
        """Run the CLI once and parse its JSON output.

        V-unverified CLI output shape: the CLI prints the tool's JSON result; a capture is
        requested through ``screenshot_out_file`` and read back from disk.

        Raises:
            BackendError: ``UNAVAILABLE`` (missing binary), ``TIMEOUT``, ``PROTOCOL`` (bad output).
        """
        with tempfile.TemporaryDirectory(prefix="grok-computer-") as tmp:
            call_args = dict(args)
            shot = Path(tmp) / "capture.png"
            if call_args.get("include_screenshot") is True:
                call_args["screenshot_out_file"] = str(shot)
            argv = [*self._command, "call", tool, json.dumps(call_args)]
            if self._socket:
                argv[len(self._command):len(self._command)] = ["--socket", self._socket]
            try:
                proc = await anyio.run_process(argv, check=False)
            except FileNotFoundError as exc:
                raise BackendError(BackendErrorKind.UNAVAILABLE,
                                   f"{self._command[0]} not found on PATH") from exc
            except subprocess.SubprocessError as exc:
                raise BackendError(BackendErrorKind.PROTOCOL, str(exc)) from exc
            text = proc.stdout.decode(errors="replace").strip()
            try:
                parsed: object = json.loads(text) if text else {}
            except ValueError as exc:
                raise BackendError(BackendErrorKind.PROTOCOL,
                                   f"{tool}: unreadable CLI output") from exc
            structured = cast(JsonObject, parsed) if isinstance(parsed, dict) else {}
            images = [(shot.read_bytes(), "image/png")] if shot.exists() else []
            return CuaReply(structured, text, images, proc.returncode != 0)
