"""Which window a call targets, and reading it (goal.md §5.6.3 window-first, §7.6).

Boundary: I/O through the backend. Deny-listed apps are refused here, before anything is captured
(goal.md §7.6 "黑名单应用的窗口一律不截"): by name before listing, by app and title once chosen.
"""

from __future__ import annotations

from ..backend.base import BackendError, WindowInfo, WindowSnapshot
from ..errors import ErrorCode, FacadeError
from ..limits import BACKEND_MAX_ELEMENTS
from ..safety.policy import SafetyRules
from .context import FacadeContext, map_backend_error


def check_allowed(rules: SafetyRules, window: WindowInfo) -> None:
    """Refuse deny-listed apps before capturing anything (R3).

    Args:
        rules: Safety rules.
        window: Target window.

    Raises:
        FacadeError: ``APP_DENIED``.
    """
    pattern = rules.denied_app(window.app, window.title)
    if pattern:
        raise FacadeError(ErrorCode.APP_DENIED,
                          f"'{window.app}' is on the deny list ({pattern}); it is not observed.")


async def resolve_window(ctx: FacadeContext, rules: SafetyRules, app: str | None,
                         window_id: int | None) -> WindowInfo:
    """Pick the window an observation or wait targets.

    Args:
        ctx: Facade context.
        rules: Safety rules.
        app: App name from the call, if any.
        window_id: Window id from the call, if any.

    Returns:
        The window (checked against the deny list).

    Raises:
        FacadeError: ``INVALID_ARGUMENT`` when nothing matches, ``APP_DENIED`` for deny-listed apps.
    """
    try:
        windows = await ctx.backend.list_windows(app)
    except BackendError as err:
        raise map_backend_error(err) from err
    if app and (pattern := rules.denied_app(app)):
        raise FacadeError(ErrorCode.APP_DENIED,
                          f"'{app}' is on the deny list ({pattern}); it is not observed.")
    chosen: WindowInfo | None = None
    if window_id is not None:
        chosen = next((w for w in windows if w.window_id == window_id), None)
        if chosen is None:
            raise FacadeError(ErrorCode.INVALID_ARGUMENT,
                              f"No window {window_id}; list windows with apps(action='windows').")
    elif app:
        visible = [w for w in windows if w.on_screen] or windows
        if not visible:
            raise FacadeError(ErrorCode.INVALID_ARGUMENT,
                              f"'{app}' has no window. Start it with apps(action='launch').")
        chosen = max(visible, key=lambda w: w.z_index if w.z_index is not None else -1)
    else:
        latest = ctx.store.latest()
        if latest is not None and latest.window is not None:
            chosen = next((w for w in windows if w.window_id == latest.window.window_id), None)
        if chosen is None:
            visible = [w for w in windows if w.on_screen and not rules.denied_app(w.app, w.title)]
            if not visible:
                raise FacadeError(ErrorCode.INVALID_ARGUMENT,
                                  "No window to observe. Pass app=... or launch the app first.")
            chosen = max(visible, key=lambda w: w.z_index if w.z_index is not None else -1)
    check_allowed(rules, chosen)
    return chosen


async def read(ctx: FacadeContext, window: WindowInfo, *, screenshot: bool) -> WindowSnapshot:
    """Read a window, mapping driver errors.

    Args:
        ctx: Facade context.
        window: Window to read.
        screenshot: Also capture it.

    Returns:
        The snapshot.

    Raises:
        FacadeError: Mapped backend failure.
    """
    try:
        return await ctx.backend.read_window(window, screenshot=screenshot,
                                             max_elements=BACKEND_MAX_ELEMENTS)
    except BackendError as err:
        raise map_backend_error(err) from err
