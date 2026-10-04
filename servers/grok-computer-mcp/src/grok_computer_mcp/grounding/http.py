"""Minimal JSON-over-HTTP for OpenAI-compatible chat completions (grounding endpoints).

Boundary: network I/O to the endpoint the user configured (``GROK_COMPUTER_GROUNDING_URL``);
nothing else in the facade opens connections. The transport is injectable so tests run without a
network. Failures surface as ``GroundingError``; the API key is never logged or echoed.
"""

from __future__ import annotations

import base64
from typing import cast

import httpx2

from ..limits import GROUNDING_TIMEOUT_S
from .base import GroundingError

JsonObject = dict[str, object]


def image_part(jpeg: bytes) -> JsonObject:
    """An ``image_url`` content part carrying the screenshot as a data URI.

    Args:
        jpeg: JPEG bytes.

    Returns:
        The content part.
    """
    data = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


async def chat(base_url: str, payload: JsonObject, api_key: str | None,
               transport: httpx2.AsyncBaseTransport | None = None) -> list[str]:
    """POST ``/chat/completions`` and return the text of every choice.

    Args:
        base_url: Endpoint base, e.g. ``http://localhost:8000/v1``.
        payload: Request body (model, messages, sampling parameters).
        api_key: Bearer token, if the endpoint needs one.
        transport: Test transport.

    Returns:
        One string per returned choice.

    Raises:
        GroundingError: Network failure, non-2xx status or an unexpected body.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx2.AsyncClient(timeout=GROUNDING_TIMEOUT_S, transport=transport) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx2.HTTPError as exc:
        raise GroundingError(f"request to {base_url} failed: {type(exc).__name__}") from exc
    if response.status_code >= 300:
        raise GroundingError(f"{base_url} answered HTTP {response.status_code}")
    try:
        body: object = response.json()
    except ValueError as exc:
        raise GroundingError("endpoint returned non-JSON") from exc
    choices = cast(JsonObject, body).get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list):
        raise GroundingError("endpoint response has no choices")
    texts: list[str] = []
    for choice in cast(list[object], choices):
        message = cast(JsonObject, choice).get("message") if isinstance(choice, dict) else None
        content = cast(JsonObject, message).get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            texts.append(content)
    return texts
