"""JSON Schemas for tool inputs and outputs, generated from the Pydantic models.

Boundary: pure. Schemas are made self-contained (``$ref``/``$defs`` inlined) because MCP clients
on older protocol versions only promise ``type``/``properties``/``required`` and some resolve
references poorly. Output schemas are ``{"type": "object", "anyOf": [success, error]}``: the root
must be an object through protocol 2025-11-25, and every tool can fail (goal.md §5.8).
"""

from __future__ import annotations

import copy
from typing import cast

from pydantic import BaseModel

JsonObject = dict[str, object]


def _inline(node: object, defs: JsonObject) -> object:
    """Replace ``{"$ref": "#/$defs/X"}`` with a copy of definition X, recursively."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in cast(list[object], node)]
    if not isinstance(node, dict):
        return node
    item = cast(JsonObject, node)
    ref = item.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = copy.deepcopy(defs[ref.removeprefix("#/$defs/")])
        merged = cast(JsonObject, _inline(target, defs))
        extra = {k: _inline(v, defs) for k, v in item.items() if k != "$ref"}
        return {**merged, **extra}
    return {k: _inline(v, defs) for k, v in item.items() if k != "$defs"}


def model_schema(model: type[BaseModel]) -> JsonObject:
    """Self-contained schema of a model (field aliases such as ``from`` are used).

    Args:
        model: Pydantic model.

    Returns:
        The schema with every reference inlined.
    """
    raw = cast(JsonObject, model.model_json_schema(by_alias=True))
    defs = cast(JsonObject, raw.get("$defs", {}))
    return cast(JsonObject, _inline(raw, defs))


def tool_input_schema(model: type[BaseModel]) -> JsonObject:
    """Input schema of a tool (root ``type: object``).

    Args:
        model: Input model.

    Returns:
        The schema.
    """
    schema = model_schema(model)
    schema["type"] = "object"
    return schema


def output_schema(success: type[BaseModel], error: type[BaseModel]) -> JsonObject:
    """Output schema accepting a success result or an error result.

    Args:
        success: Success model.
        error: Error model.

    Returns:
        ``{"type": "object", "anyOf": [...]}``.
    """
    return {"type": "object", "anyOf": [model_schema(success), model_schema(error)]}
