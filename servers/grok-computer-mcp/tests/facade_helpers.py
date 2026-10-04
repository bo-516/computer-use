"""Test harness: an isolated facade over the fake backend, driven through a real MCP client.

Every test gets its own home, plugin data dir, lock dir and workspace, and a fresh copy of the
bundled scene, so tests never touch the user's desktop or ``~/.grok-computer``.
"""

from __future__ import annotations

import base64
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import mcp_types as types
from mcp import Client

from grok_computer_mcp.app import create_context
from grok_computer_mcp.backend.fake import FakeBackend
from grok_computer_mcp.config import Settings, load_settings
from grok_computer_mcp.grounding.base import Grounder
from grok_computer_mcp.handlers.context import FacadeContext
from grok_computer_mcp.server import build_server

REPO = Path(__file__).resolve().parents[3]
SCHEMA = REPO / "docs" / "schemas" / "last_observation.schema.json"


@dataclass
class Result:
    """A decoded tool result."""

    structured: dict[str, object]
    text: str
    images: list[tuple[bytes, str]]
    is_error: bool

    @property
    def code(self) -> str:
        """Error code ("" for successes)."""
        return str(self.structured.get("code", "")) if self.is_error else ""

    @property
    def obs(self) -> str:
        """``observation_id`` of the result."""
        return str(self.structured["observation_id"])


def decode(result: types.CallToolResult) -> Result:
    """Decode an MCP result into text, images and structured content."""
    texts: list[str] = []
    images: list[tuple[bytes, str]] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            texts.append(block.text)
        elif isinstance(block, types.ImageContent):
            images.append((base64.b64decode(block.data), block.mime_type))
    raw: object = result.structured_content
    structured = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    return Result(structured, "\n".join(texts), images, bool(result.is_error))


@dataclass
class Facade:
    """A running facade plus handles on its internals."""

    root: Path
    settings: Settings
    ctx: FacadeContext
    client: Client
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)

    @property
    def backend(self) -> FakeBackend:
        """The fake backend."""
        assert isinstance(self.ctx.backend, FakeBackend)
        return self.ctx.backend

    async def call(self, tool: str, args: dict[str, object] | None = None) -> Result:
        """Call a tool through the MCP client."""
        return decode(await self.client.call_tool(tool, dict(args or {})))

    async def observe(self, **args: object) -> Result:
        """``observe`` that must succeed."""
        result = await self.call("observe", {"app": "MyApp", **args})
        assert not result.is_error, result.text
        return result

    def state(self) -> dict[str, object]:
        """The current ``last_observation.json``."""
        path = self.settings.state_dir / "last_observation.json"
        value: object = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return cast(dict[str, object], value)

    def write_user_policy(self, policy: object) -> None:
        """Write ``~/.grok-computer/policy.json``."""
        path = self.settings.home / ".grok-computer" / "policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")

    async def close(self) -> None:
        """Stop the client and the server."""
        await self.stack.aclose()


def settings_for(root: Path, **env: str) -> Settings:
    """Settings for an isolated facade under ``root``."""
    home = root / "home"
    workspace = root / "workspace"
    for path in (home, workspace):
        path.mkdir(parents=True, exist_ok=True)
    base = {
        "GROK_COMPUTER_BACKEND": "fake",
        "GROK_PLUGIN_DATA": str(root / "plugin-data"),
        "GROK_COMPUTER_LOCK_DIR": str(root / "locks"),
        "GROK_SESSION_ID": "test",
    }
    base.update(env)
    return load_settings(base, home, workspace)


async def start_facade(
    root: Path, backend: FakeBackend | None = None, grounder: Grounder | None = None, **env: str
) -> Facade:
    """Start a facade with an in-process MCP client."""
    settings = settings_for(root, **env)
    ctx = create_context(settings, backend or FakeBackend.from_file(), grounder)
    stack = AsyncExitStack()
    client = await stack.enter_async_context(Client(build_server(ctx)))
    return Facade(root, settings, ctx, client, stack)
