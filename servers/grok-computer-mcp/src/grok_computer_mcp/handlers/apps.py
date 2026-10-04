"""``apps``: list apps and windows, launch or focus an app (goal.md §5.4).

Boundary: I/O orchestration. Listing is read-only and does not take the desktop lock; launching
and focusing are actions on the shared desktop and do. Deny-listed apps are never launched or
focused (``APP_DENIED``), and their window titles are hidden from listings (titles of password
managers and banking apps can carry account names).
"""

from __future__ import annotations

from ..backend.base import BackendError
from ..errors import ErrorCode, FacadeError
from ..models.inputs import AppsIn
from ..models.outputs import AppOut, AppsResult, WindowListOut
from .context import FacadeContext, ToolOutput, map_backend_error

HIDDEN_TITLE = "(hidden by policy)"
LIST_LIMIT = 60


async def apps(ctx: FacadeContext, inp: AppsIn) -> ToolOutput:
    """Run one ``apps`` action.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        Apps or windows (list/windows), or the launched/focused app's windows.

    Raises:
        FacadeError: ``APP_DENIED``, mapped backend failures, preamble failures.
    """
    acting = inp.action in ("launch", "focus")
    rules = await ctx.begin(needs_desktop=acting)
    name = (inp.name or "").strip()
    if acting and (pattern := rules.denied_app(name)):
        raise FacadeError(ErrorCode.APP_DENIED, f"'{name}' is on the deny list ({pattern}).")
    try:
        if inp.action == "list":
            found = await ctx.backend.list_apps()
            apps_out = [AppOut(name=a.name, running=a.running, active=a.active,
                               denied=rules.denied_app(a.name) is not None)
                        for a in found[:LIST_LIMIT]]
            result = AppsResult(ok=True, action="list", apps=apps_out)
            lines = [f"{a.name}{' (running)' if a.running else ''}{' (front)' if a.active else ''}"
                     f"{' (denied)' if a.denied else ''}" for a in apps_out]
        else:
            if inp.action == "launch":
                windows = await ctx.backend.launch_app(name)
            else:
                if acting:
                    await ctx.backend.focus_app(name)
                windows = await ctx.backend.list_windows(name or None)
            if acting and (latest := ctx.store.latest()) is not None:
                ctx.state.write(latest, acting=True)
            listed: list[WindowListOut] = []
            for w in windows[:LIST_LIMIT]:
                denied = rules.denied_app(w.app, w.title) is not None
                listed.append(WindowListOut(window_id=w.window_id, app=w.app,
                                            title=HIDDEN_TITLE if denied else w.title,
                                            on_screen=w.on_screen, denied=denied))
            result = AppsResult(ok=True, action=inp.action, windows=listed, app=name or None)
            lines = [f'{w.window_id}  {w.app}  "{w.title}"{"" if w.on_screen else " (hidden)"}'
                     f"{' (denied)' if w.denied else ''}" for w in listed]
    except BackendError as err:
        raise map_backend_error(err) from err
    head = {"list": "Apps:", "windows": "Windows:", "launch": f"Launched {name}; windows:",
            "focus": f"Focused {name}; windows:"}[inp.action]
    text = "\n".join([head, *lines, "-- observe(app=...) to see one --"])
    return ToolOutput(result.model_dump(mode="json", exclude_none=True), text)
