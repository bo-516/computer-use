"""``wait_for``: poll a window until text, role, enabled state, or title holds.

Boundary: I/O orchestration. Matching is a pure scan of the backend snapshot; the observation
the tool returns is built by the same path as ``observe``. ``enabled=false`` does not mean
"wait until disabled" — only ``enabled=true`` filters. A title-only wait matches no element.
A closed window uses ``read`` → ``map_backend_error`` (``WINDOW_GONE`` becomes
``STALE_OBSERVATION``), the same mapping as ``observe``.
"""

from __future__ import annotations

import time

import anyio

from ..backend.base import RawElement, WindowSnapshot
from ..errors import ErrorCode, FacadeError
from ..limits import DEFAULT_MAX_ELEMENTS, WAIT_FOR_POLL_S
from ..models.inputs import WaitForIn
from ..models.outputs import WaitResult
from ..observe.roles import normalize_role
from .context import FacadeContext, ToolOutput
from .observing import build, check_allowed, read, resolve_window
from .results import element_out, observe_output


def _matches(snapshot: WindowSnapshot, needle: str | None, role: str | None,
             require_enabled: bool) -> RawElement | None:
    """First raw element matching the element condition.

    Args:
        snapshot: Current window snapshot.
        needle: Casefolded substring of label or value, or None.
        role: Normalized role, or None.
        require_enabled: Skip elements that are disabled.

    Returns:
        The first match, or None.
    """
    for el in snapshot.elements:
        if role and normalize_role(el.role, el.subrole) != role:
            continue
        if require_enabled and not el.enabled:
            continue
        if needle is None:
            return el
        haystack = f"{el.label} {el.value or ''}".casefold()
        if needle in haystack:
            return el
    return None


def _title_ok(snapshot: WindowSnapshot, title: str | None) -> bool:
    """Whether the window title contains ``title`` (case-insensitive)."""
    if not title:
        return True
    return title.casefold() in snapshot.window.title.casefold()


async def wait_for(ctx: FacadeContext, inp: WaitForIn) -> ToolOutput:
    """Poll the window until every given condition holds.

    Args:
        ctx: Facade context.
        inp: Validated arguments. At least one of text, ref_role, or title is set.
            ``enabled`` together with ``gone`` is rejected before this runs.

    Returns:
        The observation in which the condition held. ``matched`` is the element that
        satisfied an element condition, or None for a title-only wait.

    Raises:
        FacadeError: ``TIMEOUT`` when the condition does not hold in time, plus the usual
            policy, lock, deny-list and backend failures. A closed window is
            ``STALE_OBSERVATION``.
    """
    rules = await ctx.begin()
    window = await resolve_window(ctx, rules, inp.app, inp.window_id)
    role = normalize_role(inp.ref_role) if inp.ref_role else None
    needle = inp.text.casefold() if inp.text else None
    wants_element = needle is not None or role is not None
    require_enabled = inp.enabled is True
    start = time.monotonic()
    deadline = start + inp.timeout_ms / 1000
    while True:
        ctx.check_stop()
        snapshot = await read(ctx, window, screenshot=False)
        check_allowed(rules, snapshot.window)
        hit = _matches(snapshot, needle, role, require_enabled) if wants_element else None
        element_ok = (hit is None) == inp.gone if wants_element else True
        if element_ok and _title_ok(snapshot, inp.title):
            built = await build(ctx, rules, snapshot, mode="tree")
            ctx.state.write(built.obs, explicit_observation=True)
            waited = int((time.monotonic() - start) * 1000)
            out = observe_output(built.obs, None, max_elements=DEFAULT_MAX_ELEMENTS,
                                 notes=[f"wait_for: condition met after {waited} ms"])
            matched = next((el for el in built.obs.elements if hit and el.index == hit.index),
                           None)
            result = WaitResult.model_validate(
                {**out.structured, "waited_ms": waited,
                 "matched": element_out(matched) if matched else None})
            structured: dict[str, object] = result.model_dump(mode="json", exclude_defaults=True)
            structured["ok"] = True
            return ToolOutput(structured, out.text, None)
        if time.monotonic() >= deadline:
            raise FacadeError(ErrorCode.TIMEOUT, _timeout(inp))
        await anyio.sleep(WAIT_FOR_POLL_S)


def _timeout(inp: WaitForIn) -> str:
    """TIMEOUT message naming what was waited for."""
    parts: list[str] = []
    if inp.text:
        parts.append(f"'{inp.text}'")
    elif inp.ref_role:
        parts.append(f"'{inp.ref_role}'")
    if inp.title:
        parts.append(f"title '{inp.title}'")
    what = " and ".join(parts) if parts else "condition"
    if inp.enabled is True and not inp.gone:
        verb = "become enabled"
    elif inp.gone:
        verb = "disappear"
    else:
        verb = "appear"
    return f"{what} did not {verb} within {inp.timeout_ms} ms."
