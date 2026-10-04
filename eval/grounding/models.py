"""Grounding models under test: spec parsing, clients and cost metering (goal.md §9.2).

Boundary: builds the facade's own grounding clients, so the benchmark measures exactly the code
path ``locate`` uses. Network I/O happens inside those clients; this module only wraps their
HTTP transport to read token usage from each response. Specs (``--model``)::

    xai:grok-build-0.1                        xAI API (key: XAI_API_KEY)
    xai:grok-4.7
    uitars:ui-tars-1.5-7b@http://gpu:8000/v1  OpenAI-compatible endpoint (vLLM, HF Inference)

API keys come from the environment and are never logged or written to results.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import httpx2

from grok_computer_mcp.app import XAI_BASE_URL
from grok_computer_mcp.config import GroundingCoords
from grok_computer_mcp.grounding.base import Grounder
from grok_computer_mcp.grounding.uitars import UITarsGrounder
from grok_computer_mcp.grounding.xai import XaiGrounder

PROVIDERS = ("xai", "uitars")
XAI_KEY_ENV = "XAI_API_KEY"
UITARS_KEY_ENV = "GROK_COMPUTER_GROUNDING_API_KEY"
TOKENS_PER_PRICE_UNIT = 1_000_000  # prices are quoted per million tokens
SECONDS_PER_HOUR = 3600


class SpecError(ValueError):
    """A ``--model``, ``--price`` or ``--hourly`` argument is malformed."""


@dataclass(frozen=True)
class ModelSpec:
    """A parsed ``--model`` value."""

    spec: str
    provider: str
    model: str
    url: str | None


@dataclass(frozen=True)
class Price:
    """How a model's calls are charged: per token (API) or per hour (self-hosted)."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    hourly_usd: float = 0.0

    def cost(self, input_tokens: int, output_tokens: int, seconds: float) -> float:
        """USD for one call.

        Args:
            input_tokens: Prompt tokens.
            output_tokens: Completion tokens.
            seconds: Wall time (self-hosted endpoints bill the GPU while it works).

        Returns:
            The cost.
        """
        return ((input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok)
                / TOKENS_PER_PRICE_UNIT + seconds * self.hourly_usd / SECONDS_PER_HOUR)


def parse_spec(spec: str) -> ModelSpec:
    """Parse ``provider:model[@url]``.

    Args:
        spec: The ``--model`` value.

    Returns:
        The parsed spec.

    Raises:
        SpecError: Unknown provider, empty model, or a UI-TARS spec without an endpoint.
    """
    provider, sep, rest = spec.partition(":")
    model, _, url = rest.partition("@")
    if not sep or provider not in PROVIDERS or not model:
        raise SpecError(f"{spec!r}: expected one of {PROVIDERS} as provider:model[@url]")
    if provider == "uitars" and not url:
        raise SpecError(f"{spec!r}: UI-TARS needs an endpoint, e.g. uitars:{model}@http://host/v1")
    return ModelSpec(spec, provider, model, url or None)


def parse_prices(per_token: list[str], hourly: list[str]) -> dict[str, Price]:
    """Parse ``spec=IN,OUT`` (USD per million tokens) and ``spec=USD`` (per hour) arguments.

    Args:
        per_token: ``--price`` values.
        hourly: ``--hourly`` values.

    Returns:
        Prices by model spec.

    Raises:
        SpecError: A value is not of that form.
    """
    out: dict[str, Price] = {}
    try:
        for item in per_token:
            spec, _, rates = item.rpartition("=")
            rate_in, rate_out = (float(r) for r in rates.split(","))
            out[spec] = Price(input_per_mtok=rate_in, output_per_mtok=rate_out)
        for item in hourly:
            spec, _, rate = item.rpartition("=")
            out[spec] = Price(hourly_usd=float(rate))
    except ValueError as exc:
        raise SpecError(f"bad price argument: {exc}") from exc
    if any(not spec for spec in out):
        raise SpecError("price arguments look like spec=IN,OUT or spec=USD_PER_HOUR")
    return out


class UsageMeter(httpx2.AsyncBaseTransport):
    """Transport wrapper that totals the ``usage`` of chat-completion responses.

    The grounding clients open and close an ``AsyncClient`` per call, which closes its
    transport; this wrapper ignores that close so one inner transport serves the whole run, and
    ``shutdown`` closes it at the end.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        """Wrap ``inner`` (a pooled HTTP transport by default)."""
        self.inner = inner or httpx2.AsyncHTTPTransport()
        self.input_tokens = 0
        self.output_tokens = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Forward the request and record token usage from the JSON body."""
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        try:
            data: object = json.loads(body)
        except ValueError:
            data = None
        usage = cast(Mapping[str, object], data).get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            fields = cast(Mapping[str, object], usage)
            prompt, completion = fields.get("prompt_tokens"), fields.get("completion_tokens")
            self.input_tokens += prompt if isinstance(prompt, int) else 0
            self.output_tokens += completion if isinstance(completion, int) else 0
        return httpx2.Response(response.status_code, headers=response.headers, content=body,
                               request=request)

    async def aclose(self) -> None:
        """Keep the inner transport open across the clients' per-call closes."""

    async def shutdown(self) -> None:
        """Close the inner transport (end of run)."""
        await self.inner.aclose()

    def tokens(self) -> tuple[int, int]:
        """``(input, output)`` tokens so far."""
        return self.input_tokens, self.output_tokens


def create(spec: ModelSpec, meter: UsageMeter, coords: GroundingCoords = "smart_resize",
           env: Mapping[str, str] | None = None) -> Grounder:
    """The facade grounding client for a spec, metered.

    Args:
        spec: Parsed spec.
        meter: Transport that records usage.
        coords: UI-TARS output coordinate space, as deployed (``GROK_COMPUTER_GROUNDING_COORDS``).
        env: Environment for API keys (``os.environ`` by default).

    Returns:
        A grounder.
    """
    source = os.environ if env is None else env
    if spec.provider == "xai":
        return XaiGrounder(spec.url or XAI_BASE_URL, spec.model, source.get(XAI_KEY_ENV),
                           transport=meter)
    return UITarsGrounder(spec.url or "", spec.model, source.get(UITARS_KEY_ENV), coords,
                          transport=meter)
