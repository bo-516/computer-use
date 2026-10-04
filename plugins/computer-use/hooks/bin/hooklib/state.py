"""Reader side of ``last_observation.json``, the facade <-> hooks contract (goal.md A.10).

Boundary: pure functions over already-decoded JSON. The facade writes the file atomically after
every observation and action (``grok_computer_mcp.safety.state_file``); the guard and the verify
gate only read it. A schema change updates the writer, ``guard``, ``verify_gate`` and their
fixtures together (AGENTS.md); ``docs/schemas/last_observation.schema.json`` is the reference.
Target resolution (refs, marks, hit tests) lives in ``hooklib.targets``.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Sequence, Tuple, cast

STATE_SCHEMA_VERSION = 1
# Tolerated clock skew between the facade writing and the hook reading (same machine; covers
# coarse clocks and NTP slews). A state stamped further in the future is treated as invalid.
FUTURE_SKEW_S = 5.0

# Roles that accept typed text; used for credential-label checks and Enter-in-form detection.
TEXT_ENTRY_ROLES = ("textfield", "textarea", "securefield", "searchfield", "combobox")


def as_mapping(value: object) -> Dict[str, object]:
    """Return ``value`` as a ``str``-keyed dict, or an empty dict.

    Args:
        value: Any decoded JSON value.

    Returns:
        The dict, or ``{}`` when ``value`` is not an object.
    """
    if isinstance(value, dict):
        return {str(k): v for k, v in cast(Dict[object, object], value).items()}
    return {}


def number(value: object) -> Optional[float]:
    """Return a JSON number as float (booleans excluded), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def text(value: object) -> str:
    """Return ``value`` if it is a string, else ""."""
    return value if isinstance(value, str) else ""


def pick_fresh_state(states: Sequence[object], now: float,
                     max_age_s: float) -> Optional[Dict[str, object]]:
    """Choose the freshest usable state among the candidate files.

    Args:
        states: Decoded contents of each candidate ``last_observation.json`` (None if missing).
        now: Current time (``time.time()``).
        max_age_s: ``Policy.state_max_age_s``.

    Returns:
        The newest state that is an object, has a numeric ``updated_at`` within the freshness
        window and an ``observation_id``; None when there is none (callers treat as unknown).
    """
    best: Optional[Dict[str, object]] = None
    best_at = float("-inf")
    for raw in states:
        state = as_mapping(raw)
        updated = number(state.get("updated_at"))
        if updated is None or not text(state.get("observation_id")):
            continue
        if updated > now + FUTURE_SKEW_S or now - updated > max_age_s:
            continue
        if updated > best_at:
            best, best_at = state, updated
    return best


def latest_state(states: Sequence[object]) -> Optional[Dict[str, object]]:
    """Choose the most recently updated state regardless of age (verify gate use).

    Args:
        states: Decoded candidate state files.

    Returns:
        The state with the largest ``updated_at``, or None.
    """
    best: Optional[Dict[str, object]] = None
    best_at = float("-inf")
    for raw in states:
        state = as_mapping(raw)
        updated = number(state.get("updated_at"))
        if updated is not None and updated > best_at:
            best, best_at = state, updated
    return best


def any_secure(state: Optional[Mapping[str, object]]) -> bool:
    """Whether the observation lists any secure (password) field.

    Args:
        state: Fresh state or None.

    Returns:
        True when at least one element has ``secure: true``.
    """
    if state is None:
        return False
    return any(as_mapping(raw).get("secure") is True
               for raw in as_mapping(state.get("elements")).values())


def window_context(state: Optional[Mapping[str, object]]) -> Tuple[str, str, bool]:
    """Return ``(app, window_title, window_unsaved)`` from the state.

    Args:
        state: Fresh state or None.

    Returns:
        Empty strings and False when unknown.
    """
    if state is None:
        return "", "", False
    return (text(state.get("app")), text(state.get("window_title")),
            state.get("window_unsaved") is True)
