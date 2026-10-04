"""Resolve what an action targets: refs, marks and raw points (goal.md §5.4, §5.6.5, §6.3).

Boundary: pure apart from reading the observation store. A target is refused as stale when its
ref, role, label, value or enabled state changed since the observation the model used (a switch
already flipped, a button that now says Delete), while unrelated changes do not stale it.
Coordinate clicks are hit-tested and the hit is recorded in the state file before acting.
"""

from __future__ import annotations

from ..backend.base import ElementTarget, PointTarget
from ..coords import Space, from_image, in_image
from ..errors import ErrorCode, FacadeError
from ..geometry import Point
from ..limits import GROUNDED_POINT_TOLERANCE_PX
from ..models.inputs import PointIn
from ..observe.elements import ObservedElement
from .action_prep import Prepared, stale
from .context import FacadeContext


def resolve_ref(ctx: FacadeContext, prep: Prepared, ref: str) -> ObservedElement:
    """Current element for a ref of the observation, refusing changed targets.

    Args:
        ctx: Facade context (to tell STALE_REF from ELEMENT_NOT_FOUND).
        prep: Prepared context.
        ref: Ref from the model.

    Returns:
        The current element.

    Raises:
        FacadeError: ``STALE_REF``, ``ELEMENT_NOT_FOUND`` or ``STALE_OBSERVATION``.
    """
    old = prep.obs.refs.get(ref)
    if old is None:
        other = ctx.store.ref_in_other_observation(ref, prep.obs.id)
        if other:
            raise FacadeError(ErrorCode.STALE_REF, f"{ref} belongs to {other}, not {prep.obs.id}.")
        raise FacadeError(ErrorCode.ELEMENT_NOT_FOUND, f"{ref} is not part of {prep.obs.id}.")
    new = prep.by_ref.get(ref)
    if new is None or _identity(new) != _identity(old):
        raise stale(prep.obs, f"{ref} changed")
    return new


def _identity(el: ObservedElement | None) -> tuple[object, ...] | None:
    """What acting on an element means: ref, role, label, value and enabled state.

    A change in any of these makes the observation stale for an action on that element (the
    switch is already on, the button now says Delete), while changes elsewhere (a ticking timer)
    do not.
    """
    return None if el is None else (el.ref, el.role, el.label, el.value, el.enabled)


def hit(elements: list[ObservedElement], p: Point) -> ObservedElement | None:
    """Innermost element under an IMAGE point, preferring interactive ones.

    Args:
        elements: Observation elements.
        p: Image point.

    Returns:
        The element, or None when nothing is there.
    """
    under = [el for el in elements if el.bbox is not None and el.visible and el.bbox.contains(p)]
    pool = [el for el in under if el.interactive] or under
    return min(pool, key=lambda el: el.bbox.area if el.bbox else 0.0, default=None)


def check_point(ctx: FacadeContext, prep: Prepared,
                point: PointIn) -> tuple[Point, ObservedElement | None]:
    """Validate a raw coordinate and hit-test it against the observation and the screen now.

    Args:
        ctx: Facade context (grounding tier).
        prep: Prepared context.
        point: Point from the model.

    Returns:
        The point and the element it hits (None for empty space).

    Raises:
        FacadeError: ``INVALID_ARGUMENT`` off the image, ``POINT_UNGROUNDED`` in the strict tier
            (goal.md §6.3), ``STALE_OBSERVATION`` when the hit element changed.
    """
    p = Point(point.x, point.y)
    if not in_image(prep.frame, p):
        raise FacadeError(ErrorCode.INVALID_ARGUMENT,
                          f"Point ({p.x:g}, {p.y:g}) is outside the {prep.frame.image_w}x"
                          f"{prep.frame.image_h} image of {prep.obs.id}.")
    if ctx.settings.grounding_tier == "strict" and not any(
            (p.x - g.x) ** 2 + (p.y - g.y) ** 2 <= GROUNDED_POINT_TOLERANCE_PX ** 2
            for g in prep.obs.grounded_points):
        raise FacadeError(ErrorCode.POINT_UNGROUNDED,
                          "Raw coordinates are disabled (strict grounding tier).")
    before, now = hit(prep.obs.elements, p), hit(prep.elements, p)
    if _identity(before) != _identity(now):
        raise stale(prep.obs, "The element under the point changed")
    return p, now


def element_target(prep: Prepared, el: ObservedElement, space: Space) -> ElementTarget:
    """Driver target for an element (with a fallback point in the driver's action space)."""
    fallback = from_image(prep.frame, el.bbox.center, space) if el.bbox else None
    return ElementTarget(prep.window, el.index, el.handle, fallback)


def point_target(prep: Prepared, p: Point, space: Space) -> PointTarget:
    """Driver target for an IMAGE point."""
    return PointTarget(prep.window, from_image(prep.frame, p, space), prep.capture_id)


def describe(el: ObservedElement | None, p: Point | None = None) -> str:
    """Target description for summaries (labels only; typed text is never echoed)."""
    if el is not None:
        return f'{el.role} "{el.label}" ({el.ref})'
    return f"point ({p.x:g}, {p.y:g})" if p is not None else "the focused element"


def hit_record(prep: Prepared, p: Point, el: ObservedElement | None) -> dict[str, object]:
    """``last_hit`` for the state file (written before acting, AGENTS.md)."""
    rec: dict[str, object] = {"point": [round(p.x, 1), round(p.y, 1)], "app": prep.window.app,
                              "role": el.role if el else "", "label": el.label if el else ""}
    if el is not None:
        rec["ref"] = el.ref
    return rec


def not_interactable(el: ObservedElement) -> None:
    """Refuse disabled elements before calling the driver."""
    if not el.enabled:
        raise FacadeError(ErrorCode.NOT_INTERACTABLE, f'{el.role} "{el.label}" ({el.ref}) is '
                          "disabled.")


def mark_target(prep: Prepared, ctx: FacadeContext,
                mark: int) -> tuple[ObservedElement | None, Point]:
    """Resolve a Set-of-Mark number to its element (if accessibility-backed) and centre."""
    found = prep.obs.marks.get(mark)
    if found is None:
        raise FacadeError(ErrorCode.ELEMENT_NOT_FOUND,
                          f"Mark {mark} is not on {prep.obs.id}; use observe(mode='som').")
    if found.ref:
        el = resolve_ref(ctx, prep, found.ref)
        return el, el.bbox.center if el.bbox else found.bbox.center
    return None, found.bbox.center


def endpoint(ctx: FacadeContext, prep: Prepared,
              ref: str | None, mark: int | None,
              point: PointIn | None) -> tuple[ObservedElement | None, Point | None]:
    """Resolve one of ref/mark/point to an element and/or IMAGE point (None, None for none)."""
    if ref is not None:
        el = resolve_ref(ctx, prep, ref)
        return el, el.bbox.center if el.bbox else None
    if mark is not None:
        return mark_target(prep, ctx, mark)
    if point is not None:
        p, el = check_point(ctx, prep, point)
        return el, p
    return None, None
