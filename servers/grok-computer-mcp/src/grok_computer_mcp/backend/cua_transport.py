"""How the facade reaches Cua Driver: its MCP server over stdio, or its CLI (goal.md §5.2).

Boundary: process and protocol I/O only; ``cua.py`` decides what to call, ``cua_parse.py`` reads
the answers. Default is the MCP transport: ``cua-driver mcp`` on macOS proxies to the installed
``CuaDriver.app`` daemon, so the Accessibility and Screen Recording grants stay with that app
(goal.md §7.7, V7; the in-process Python SDK would move the grants to whatever process imports
it, which is why it is not used). ``GROK_COMPUTER_CUA_TRANSPORT=cli`` uses ``cua-driver call``
instead (one process per call; screenshots go through ``screenshot_out_file``), see ``cua_cli.py``.
"""

from __future__ import annotations

import base64
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Protocol, cast

import anyio
import mcp_types as types
from mcp import Client, StdioServerParameters

from ..limits import BACKEND_CALL_TIMEOUT_S
from .base import BackendError, BackendErrorKind

JsonObject = dict[str, object]


def _images() -> list[tuple[bytes, str]]:
    """Empty image list (typed default factory)."""
    return []


@dataclass
class CuaReply:
    """One tool answer: structured content, text, images (bytes, mime), error flag."""

    structured: JsonObject
    text: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=_images)
    is_error: bool = False


class CuaTransport(Protocol):
    """A way to call Cua Driver tools."""

    async def start(self) -> None:
        """Connect (idempotent)."""
        ...

    async def call(self, tool: str, args: JsonObject) -> CuaReply:
        """Call one tool."""
        ...

    async def close(self) -> None:
        """Disconnect (idempotent)."""
        ...


def reply_from_result(result: types.CallToolResult) -> CuaReply:
    """Convert an MCP tool result.

    Args:
        result: The driver's result.

    Returns:
        The reply (images decoded).
    """
    texts: list[str] = []
    images: list[tuple[bytes, str]] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            texts.append(block.text)
        elif isinstance(block, types.ImageContent):
            images.append((base64.b64decode(block.data), block.mime_type))
    raw: object = result.structured_content
    structured = cast(JsonObject, raw) if isinstance(raw, dict) else {}
    if not structured and texts:
        try:
            parsed: object = json.loads(texts[0])
            structured = cast(JsonObject, parsed) if isinstance(parsed, dict) else {}
        except ValueError:
            structured = {}
    return CuaReply(structured, "\n".join(texts), images, bool(result.is_error))


class McpTransport:
    """A persistent ``cua-driver mcp`` session."""

    def __init__(self, command: tuple[str, ...], socket: str | None,
                 timeout_s: float = BACKEND_CALL_TIMEOUT_S) -> None:
        """Configure the transport (nothing is started yet).

        Args:
            command: Executable and leading arguments (``("cua-driver",)`` by default; sandbox
                mode may wrap it, e.g. ``("ssh", "vm", "cua-driver")``).
            socket: Explicit daemon endpoint (``--socket``), e.g. inside a sandbox.
            timeout_s: Per-call timeout.
        """
        args = [*command[1:], "mcp"] + (["--socket", socket] if socket else [])
        self._params = StdioServerParameters(command=command[0], args=args)
        self._timeout = timeout_s
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None

    async def start(self) -> None:
        """Spawn the driver and initialize the session."""
        if self._client is not None:
            return
        stack = AsyncExitStack()
        try:
            client = Client(self._params, read_timeout_seconds=self._timeout)
            self._client = await stack.enter_async_context(client)
        except (OSError, RuntimeError) as exc:
            await stack.aclose()
            raise BackendError(BackendErrorKind.UNAVAILABLE,
                               f"cannot start {self._params.command}: {exc}") from exc
        self._stack = stack

    async def call(self, tool: str, args: JsonObject) -> CuaReply:
        """Call one driver tool.

        Raises:
            BackendError: ``UNAVAILABLE`` before start or on transport failure, ``TIMEOUT``.
        """
        if self._client is None:
            raise BackendError(BackendErrorKind.UNAVAILABLE, "cua-driver session is not started")
        try:
            with anyio.fail_after(self._timeout):
                result = await self._client.call_tool(tool, dict(args))
        except TimeoutError as exc:
            raise BackendError(BackendErrorKind.TIMEOUT, f"{tool} timed out") from exc
        except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
            raise BackendError(BackendErrorKind.UNAVAILABLE,
                               f"cua-driver went away: {exc}") from exc
        return reply_from_result(result)

    async def close(self) -> None:
        """Close the session and stop the driver process."""
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()
