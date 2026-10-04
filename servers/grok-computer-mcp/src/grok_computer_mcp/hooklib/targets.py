"""Resolving what a facade call targets from the latest observation (guard side).

Boundary: pure. The guard runs *before* the facade sees a call, so it cannot rely on the facade
having hit-tested a coordinate click yet: every element in ``last_observation.json`` carries its
``bbox`` (image coordinates of that observation) and the guard hit-tests points itself.
``last_hit`` in the state records what the facade's own hit test found for the previous click
(audit and echo only).
"""

from __future__ import annotations

from typing import List, Mapping, NamedTuple, Optional, Tuple, cast

from .state import as_mapping, number, text

TEXT_TOOLS = ("type_text",)
KEY_TOOLS = ("press_keys",)


class TargetInfo(NamedTuple):
    """What the guard knows about the element a call targets."""

    role: str
    label: str
    app: str
    secure: bool
    in_form: bool
    source: str  # "ref", "mark", "point" or "focused"
    ref: str


def _target(raw: object, source: str, ref: str) -> Optional[TargetInfo]:
    """Build a ``TargetInfo`` from one element/mark/focused record."""
    item = as_mapping(raw)
    if not item:
        return None
    return TargetInfo(
        role=text(item.get("role")),
        label=text(item.get("label")),
        app=text(item.get("app")),
        secure=item.get("secure") is True,
        in_form=item.get("in_form") is True,
        source=source,
        ref=text(item.get("ref")) or ref,
    )


def point_of(value: object) -> Optional[Tuple[float, float]]:
    """Read a point given as ``{"x": .., "y": ..}`` or ``[x, y]``.

    Args:
        value: Decoded tool input value.

    Returns:
        ``(x, y)`` or None when malformed.
    """
    item = as_mapping(value)
    if item:
        x, y = number(item.get("x")), number(item.get("y"))
    elif isinstance(value, list) and len(cast(List[object], value)) == 2:
        pair = cast(List[object], value)
        x, y = number(pair[0]), number(pair[1])
    else:
        return None
    if x is None or y is None:
        return None
    return x, y


def bbox_of(value: object) -> Optional[Tuple[float, float, float, float]]:
    """Read ``[x, y, w, h]`` with non-negative size.

    Args:
        value: Decoded bbox value.

    Returns:
        The bbox, or None when malformed.
    """
    if not isinstance(value, list):
        return None
    parts = [number(v) for v in cast(List[object], value)]
    if len(parts) != 4:
        return None
    x, y, w, h = parts
    if x is None or y is None or w is None or h is None or w < 0 or h < 0:
        return None
    return x, y, w, h


def hit_test(point: Tuple[float, float],
             elements: Mapping[str, object]) -> Optional[Tuple[str, object]]:
    """Find the smallest element whose bbox contains ``point``.

    Args:
        point: Image coordinates of the click.
        elements: ``state["elements"]``.

    Returns:
        ``(ref, record)`` of the innermost hit, or None when nothing contains the point.
    """
    px, py = point
    best: Optional[Tuple[str, object]] = None
    best_area = float("inf")
    for ref, raw in elements.items():
        box = bbox_of(as_mapping(raw).get("bbox"))
        if box is None:
            continue
        x, y, w, h = box
        if x <= px <= x + w and y <= py <= y + h and w * h < best_area:
            best, best_area = (ref, raw), w * h
    return best


def resolve_target(tool: str, inp: Mapping[str, object], state: Optional[Mapping[str, object]],
                   observation_id: str = "") -> Optional[TargetInfo]:
    """Resolve what a facade call targets from the latest observation.

    Args:
        tool: Facade tool name (``click``, ``type_text`` ...).
        inp: The call's arguments, or one endpoint of a ``drag``.
        state: Fresh state from ``pick_fresh_state``, or None.
        observation_id: The call's ``observation_id`` when ``inp`` is a nested endpoint.

    Returns:
        The target, or None when it cannot be determined (no or stale state, a different
        observation, an unknown ref/mark, a point that hits nothing, or unknown focus).
    """
    if state is None:
        return None
    obs = text(inp.get("observation_id")) or observation_id
    if obs and obs != text(state.get("observation_id")):
        return None
    elements = as_mapping(state.get("elements"))
    ref = inp.get("ref")
    if isinstance(ref, str) and ref:
        return _target(elements.get(ref), "ref", ref)
    mark = inp.get("mark")
    if mark is not None and not isinstance(mark, bool) and isinstance(mark, (int, str)):
        return _target(as_mapping(state.get("marks")).get(str(mark)), "mark", "")
    if "point" in inp:
        point = point_of(inp.get("point"))
        hit = hit_test(point, elements) if point is not None else None
        return _target(hit[1], "point", hit[0]) if hit is not None else None
    if tool in TEXT_TOOLS or tool in KEY_TOOLS:
        return _target(state.get("focused"), "focused", "")
    return None


