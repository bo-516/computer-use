"""Privacy redaction for logs and traces (AGENTS.md: typed text = length + first 2 chars).

Boundary: pure, stdlib only, Python 3.8. Every string under a text-bearing key is replaced by
``{"len": n, "head": first two characters}`` at any depth, and URL query strings and fragments are
replaced by their length (they often carry tokens). Shared by the audit hook and, vendored, by
the facade's trace recorder.
"""

from __future__ import annotations

from typing import Dict, List, cast

from .state import as_mapping

# Keys whose string values are user-typed or page-bound text.
REDACT_KEYS = ("text", "value", "values", "password", "secret", "token", "promptText", "data",
               "function", "code")
REDACT_HEAD_CHARS = 2


def _redact_leaf(value: object) -> object:
    """Redact every string inside a sensitive value."""
    if isinstance(value, str):
        return {"len": len(value), "head": value[:REDACT_HEAD_CHARS]}
    if isinstance(value, list):
        return [_redact_leaf(v) for v in cast(List[object], value)]
    mapping = as_mapping(value)
    if mapping:
        return {k: _redact_leaf(v) for k, v in mapping.items()}
    return value


def _redact_url(url: str) -> str:
    """Keep scheme, host and path; replace the query string and fragment by their length."""
    for sep in ("?", "#"):
        head, found, tail = url.partition(sep)
        if found:
            return f"{head}{sep}<redacted {len(tail)} chars>"
    return url


def redact(value: object) -> object:
    """Return a copy of tool arguments with typed text and URL queries redacted.

    Args:
        value: Decoded ``toolInput``.

    Returns:
        The redacted copy (same shape, sensitive strings replaced by ``{len, head}``).
    """
    if isinstance(value, list):
        return [redact(v) for v in cast(List[object], value)]
    mapping = as_mapping(value)
    if not mapping:
        return value
    out: Dict[str, object] = {}
    for key, item in mapping.items():
        if key in REDACT_KEYS:
            out[key] = _redact_leaf(item)
        elif key == "url" and isinstance(item, str):
            out[key] = _redact_url(item)
        else:
            out[key] = redact(item)
    return out
