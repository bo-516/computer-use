"""``scroll`` and ``drag`` (goal.md §5.4).

Boundary: I/O orchestration on top of ``action_prep``/``targeting``/``delivery``. Both accept refs,
marks or points in the observation's image space; drags always read a fresh capture because the
driver acts in capture pixels.
"""

from __future__ import annotations

from ..backend.base import ActionOutcome, Delivery, ElementTarget, PointTarget
from ..errors import ErrorCode, FacadeError
from ..geometry import Point
from ..models.inputs import DragIn, ScrollIn, TargetIn
from ..observe.elements import ObservedElement
from .action_prep import prepare
from .action_result import finish
from .context import FacadeContext, ToolOutput
from .delivery import deliver, desktop_point
from .targeting import describe, element_target, endpoint, hit_record, point_target


async def scroll(ctx: FacadeContext, inp: ScrollIn) -> ToolOutput:
    """Scroll the window or a target.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The action result.

    Raises:
        FacadeError: Staleness, targeting, delivery and preamble failures.
    """
    prep = await prepare(ctx, inp.observation_id,
                         needs_capture=inp.point is not None or inp.mark is not None)
    space = ctx.backend.action_space
    el, p = endpoint(ctx, prep, inp.ref, inp.mark, inp.point)
    target: ElementTarget | PointTarget | None = None
    if el is not None and inp.ref is not None:
        target = element_target(prep, el, space)
    elif p is not None:
        target = point_target(prep, p, space)
    ctx.state.write(prep.obs, acting=True)
    ctx.check_stop()

    async def act(delivery: Delivery) -> ActionOutcome:
        return await ctx.backend.scroll(target, prep.window, direction=inp.direction,
                                        amount=inp.amount, delivery=delivery)

    outcome, delivery = await deliver(ctx, act, expected=None)
    where = describe(el, p) if (el or p) else "the window"
    return await finish(ctx, prep, action=f"Scrolled {inp.direction} x{inp.amount} on",
                        target=where, outcome=outcome, delivery=delivery)


async def drag(ctx: FacadeContext, inp: DragIn) -> ToolOutput:
    """Press-drag-release between two targets.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The action result.

    Raises:
        FacadeError: Staleness, targeting, delivery and preamble failures.
    """
    prep = await prepare(ctx, inp.observation_id, needs_capture=True)

    def resolve(end: TargetIn) -> tuple[ObservedElement | None, Point]:
        el, p = endpoint(ctx, prep, end.ref, end.mark, end.point)
        if p is None:
            raise FacadeError(ErrorCode.INVALID_ARGUMENT, "Drag endpoints need a box or a point.")
        return el, p

    (el_a, a), (el_b, b) = resolve(inp.from_), resolve(inp.to)
    space = ctx.backend.action_space
    start, end = point_target(prep, a, space).point, point_target(prep, b, space).point
    ctx.state.write(prep.obs, acting=True, last_hit=hit_record(prep, b, el_b))
    ctx.check_stop()

    async def act(delivery: Delivery) -> ActionOutcome:
        return await ctx.backend.drag(prep.window, start, end, delivery=delivery)

    outcome, delivery = await deliver(ctx, act, expected=desktop_point(prep, b))
    return await finish(ctx, prep, action="Dragged",
                        target=f"{describe(el_a, a)} to {describe(el_b, b)}", outcome=outcome,
                        delivery=delivery)
