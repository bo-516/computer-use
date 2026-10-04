"""Clicking a window in a screen-scope observation focuses it (goal.md §5.6.3 "窗口优先").

Boundary: I/O orchestration. Screen observations exist for cross-app work: they list windows, and
the only action on them is choosing a window, which brings its app to the front and returns an
observation of that window to act in.
"""

from __future__ import annotations

from ..backend.base import BackendError
from ..errors import ErrorCode, FacadeError
from ..geometry import Point
from ..models.inputs import ClickIn
from ..models.outputs import ActionResult
from ..session import Observation
from .context import FacadeContext, ToolOutput, map_backend_error
from .observing import build, check_allowed, read
from .targeting import hit


async def click_screen_window(ctx: FacadeContext, obs: Observation, inp: ClickIn) -> ToolOutput:
    """Focus the window a screen-scope ref, mark or point designates.

    Args:
        ctx: Facade context.
        obs: The screen-scope observation.
        inp: Validated click arguments.

    Returns:
        An action result whose observation is the focused window (tree).

    Raises:
        FacadeError: ``ELEMENT_NOT_FOUND`` when nothing is designated, ``APP_DENIED`` for
            deny-listed apps, and mapped backend failures.
    """
    rules = await ctx.begin()
    el = None
    if inp.ref is not None:
        el = obs.refs.get(inp.ref)
    elif inp.mark is not None and (mark := obs.marks.get(inp.mark)) is not None and mark.ref:
        el = obs.refs.get(mark.ref)
    elif inp.point is not None:
        el = hit(obs.elements, Point(inp.point.x, inp.point.y))
    if el is None or el.handle is None:
        raise FacadeError(ErrorCode.ELEMENT_NOT_FOUND, f"No window there in {obs.id}.")
    try:
        windows = await ctx.backend.list_windows()
        window = next((w for w in windows if str(w.window_id) == el.handle), None)
        if window is None:
            raise FacadeError(ErrorCode.STALE_OBSERVATION, f"That window closed since {obs.id}.")
        check_allowed(rules, window)
        ctx.state.write(obs, acting=True)
        await ctx.backend.focus_app(window.app)
    except BackendError as err:
        raise map_backend_error(err) from err
    snapshot = await read(ctx, window, screenshot=False)
    built = await build(ctx, rules, snapshot, mode="tree")
    ctx.state.write(built.obs)
    summary = f'Focused {window.app} "{window.title}"; observe it or act on {built.obs.id}'
    result = ActionResult(ok=True, observation_id=built.obs.id, app=window.app, summary=summary,
                          changes=[{"kind": "window", "after": f'{window.app} "{window.title}"'}],
                          delivery="foreground", effect="confirmed")
    return ToolOutput(result.model_dump(mode="json", exclude_none=True),
                      f"{summary}\nobservation_id: {built.obs.id}")
