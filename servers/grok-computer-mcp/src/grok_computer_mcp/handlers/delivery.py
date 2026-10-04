"""Acting and settling: the delivery ladder and waiting for the UI to settle (goal.md §5.8, §7.7).

Boundary: I/O orchestration. Background delivery first; one automatic foreground retry when the
driver refuses background delivery; in host mode the pointer is sampled around foreground input
and any move by the user aborts with ``USER_INTERRUPT``. After acting, the window is re-read until
two consecutive reads agree.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import anyio

from ..backend.base import (
    ActionOutcome,
    BackendError,
    BackendErrorKind,
    Delivery,
    WindowInfo,
    WindowSnapshot,
)
from ..coords import Space, from_image
from ..errors import ErrorCode, FacadeError
from ..geometry import Point
from ..limits import SETTLE_POLL_S, SETTLE_TIMEOUT_S, USER_INTERRUPT_MOVE_PT
from .action_prep import Prepared
from .context import FacadeContext, map_backend_error
from .observing import read

Act = Callable[[Delivery], Awaitable[ActionOutcome]]


async def deliver(ctx: FacadeContext, act: Act, *, expected: Point | None,
                  start: Delivery = Delivery.BACKGROUND) -> tuple[ActionOutcome, str]:
    """Run an action on the delivery ladder (goal.md §5.8 BACKGROUND_UNAVAILABLE).

    Args:
        ctx: Facade context.
        act: The driver call for one delivery mode.
        expected: DESKTOP_POINTS position the pointer may legitimately end at (pixel actions).
        start: First rung (foreground for modified clicks, which need physical modifier state).

    Returns:
        The outcome and the delivery label (``background``, ``foreground``, ``foreground_retry``).

    Raises:
        FacadeError: Mapped driver failure, or ``USER_INTERRUPT`` when the pointer moved during a
            foreground action in host mode (goal.md §7.7).
    """
    if start is Delivery.BACKGROUND:
        try:
            return await act(Delivery.BACKGROUND), "background"
        except BackendError as err:
            if err.kind is not BackendErrorKind.BACKGROUND_UNAVAILABLE:
                raise map_backend_error(err) from err
    ctx.check_stop()
    host = ctx.settings.mode == "host"
    before = await ctx.backend.cursor_position() if host else None
    try:
        outcome = await act(Delivery.FOREGROUND)
    except BackendError as err:
        raise map_backend_error(err) from err
    if host and before is not None:
        after = await ctx.backend.cursor_position()
        if after is not None and _moved(after, before) and (expected is None
                                                            or _moved(after, expected)):
            raise FacadeError(ErrorCode.USER_INTERRUPT,
                              "The pointer moved during a foreground action; stopping.")
    return outcome, ("foreground" if start is Delivery.FOREGROUND else "foreground_retry")


def _moved(a: Point, b: Point) -> bool:
    """Whether two pointer positions differ by more than the takeover threshold."""
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 > USER_INTERRUPT_MOVE_PT ** 2


def desktop_point(prep: Prepared, p: Point) -> Point:
    """IMAGE point in DESKTOP_POINTS (for the user-takeover check)."""
    return from_image(prep.frame, p, Space.DESKTOP_POINTS)


def _signature(snapshot: WindowSnapshot) -> tuple[object, ...]:
    """Cheap identity of a snapshot for settling."""
    return (snapshot.window.title, tuple(
        (el.role, el.label, el.value, el.enabled, el.focused,
         None if el.frame is None else (round(el.frame.x), round(el.frame.y),
                                        round(el.frame.w), round(el.frame.h)))
        for el in snapshot.elements))


async def settle(ctx: FacadeContext, window: WindowInfo) -> WindowSnapshot:
    """Wait until two consecutive tree reads agree (or the settle timeout passes).

    Args:
        ctx: Facade context.
        window: Window to read.

    Returns:
        The last snapshot.

    Raises:
        FacadeError: Mapped driver failure (``STALE_OBSERVATION`` when the window closed).
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    previous: tuple[object, ...] | None = None
    while True:
        await anyio.sleep(SETTLE_POLL_S)
        snapshot = await read(ctx, window, screenshot=False)
        sig = _signature(snapshot)
        if sig == previous or time.monotonic() >= deadline:
            return snapshot
        previous = sig
