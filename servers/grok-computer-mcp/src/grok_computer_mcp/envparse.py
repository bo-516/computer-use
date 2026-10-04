"""Reading typed values from environment variables (fail on anything ambiguous).

Boundary: pure helpers for ``config.py``. An invalid value raises ``ConfigError`` rather than
falling back, so a misconfigured facade refuses to start instead of running on guesses.
"""

from __future__ import annotations

from typing import TypeVar

_Choice = TypeVar("_Choice", bound=str)


class ConfigError(ValueError):
    """An environment variable holds a value the facade refuses to guess around."""


def choice(env: dict[str, str], name: str, allowed: tuple[_Choice, ...],
            default: _Choice) -> _Choice:
    """Read an enumerated setting.

    Args:
        env: Environment.
        name: Variable name.
        allowed: Accepted values.
        default: Value when unset or empty.

    Returns:
        The value.

    Raises:
        ConfigError: The value is not one of ``allowed``.
    """
    raw = env.get(name, "").strip()
    if not raw:
        return default
    for option in allowed:
        if raw == option:
            return option
    raise ConfigError(f"{name}={raw!r} is not one of {', '.join(allowed)}")


def integer(env: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    """Read a bounded integer setting.

    Args:
        env: Environment.
        name: Variable name.
        default: Value when unset or empty.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The value.

    Raises:
        ConfigError: Not an integer or out of range.
    """
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} is not an integer") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name}={value} must be between {low} and {high}")
    return value


def optional(env: dict[str, str], name: str) -> str | None:
    """Return a non-empty, stripped value or None (unexpanded ``${..}`` counts as unset)."""
    raw = env.get(name, "").strip()
    return raw if raw and "${" not in raw else None
