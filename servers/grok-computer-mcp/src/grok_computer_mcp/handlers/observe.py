"""``observe`` and ``wait_for`` (goal.md §5.4, §5.5, §5.7).

Boundary: I/O orchestration over the backend. ``observe(mode="auto")`` reads the tree and returns
it when it has at least three interactive elements and the app is not on the thin-accessibility
list; otherwise it captures and returns a Set-of-Mark screenshot. ``scope="screen"`` lists the
visible windows over a display screenshot in which deny-listed windows are masked.
"""

from __future__ import annotations

import time

import anyio

from ..backend.base import BackendError, RawElement, WindowSnapshot
from ..coords import Space, make_frame, rect_to_image
from ..errors import ErrorCode, FacadeError
from ..geometry import Rect
from ..limits import AUTO_MODE_MIN_INTERACTIVE, DEFAULT_MAX_ELEMENTS, WAIT_FOR_POLL_S
from ..models.inputs import ObserveIn, WaitForIn
from ..models.outputs import WaitResult
from ..observe import capture as capture_mod
from ..observe.fingerprint import dhash, tree_fingerprint
from ..observe.roles import normalize_role
from ..observe.som import draw_marks, marks_for
from ..observe.tree import build_elements
from ..safety.policy import SafetyRules
from ..session import Observation, now
from .context import FacadeContext, ToolOutput, map_backend_error
from .observing import Built, build, check_allowed, read, resolve_window
from .results import element_out, observe_output

SCREEN_APP = "(screen)"
HIDDEN_LABEL = "(hidden by policy)"


def _notes(built: Built, extra: list[str]) -> list[str]:
    """Lines under the header: degraded trees, screenshot path, more mark pages."""
    obs = built.obs
    notes = list(extra)
    if obs.degraded:
        notes.append(f"-- accessibility tree incomplete ({obs.degraded}); use mode='som' and "
                     "marks --")
    if obs.screenshot_path:
        notes.append(f"screenshot_path: {obs.screenshot_path}")
    if built.mark_pages > 1:
        notes.append(f"-- marks page shown; {built.mark_pages} pages: observe(mode='som', "
                     "page=N) for more --")
    return notes


async def observe(ctx: FacadeContext, inp: ObserveIn) -> ToolOutput:
    """Observe a window (or the screen).

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The observation result (image attached for screenshot/som).

    Raises:
        FacadeError: Policy, lock, deny-list, argument or backend failures.
    """
    rules = await ctx.begin()
    if inp.scope == "screen":
        return await _observe_screen(ctx, rules, inp)
    window = await resolve_window(ctx, rules, inp.app, inp.window_id)
    want_image = inp.mode in ("screenshot", "som")
    snapshot = await read(ctx, window, screenshot=want_image)
    check_allowed(rules, snapshot.window)
    extra: list[str] = []
    if inp.mode == "auto":
        built = await build(ctx, rules, snapshot, mode="tree")
        interactive = sum(1 for el in built.obs.elements if el.interactive and el.visible)
        thin = (interactive < AUTO_MODE_MIN_INTERACTIVE or rules.poor_ax(window.app)
                or snapshot.degraded is not None)
        if thin:
            snapshot = await read(ctx, window, screenshot=True)
            check_allowed(rules, snapshot.window)
            built = await build(ctx, rules, snapshot, mode="som", page=inp.page)
            extra.append(f"-- auto: {interactive} usable tree elements, switched to Set-of-Mark; "
                         "click(mark=N) --")
    else:
        built = await build(ctx, rules, snapshot, mode="tree" if not want_image else inp.mode,
                            page=inp.page)
    ctx.state.write(built.obs, explicit_observation=True)
    return observe_output(built.obs, built.image, max_elements=inp.max_elements,
                          root_ref=inp.root_ref, mark_pages=built.mark_pages,
                          notes=_notes(built, extra))


async def _observe_screen(ctx: FacadeContext, rules: SafetyRules, inp: ObserveIn) -> ToolOutput:
    """Display-wide observation: visible windows as elements, deny-listed ones masked."""
    try:
        snap = await ctx.backend.read_screen(screenshot=inp.mode != "tree")
    except BackendError as err:
        raise map_backend_error(err) from err
    display = snap.display
    cap = snap.capture
    frame = make_frame(display.bounds, display, ctx.settings.long_edge,
                       (cap.width, cap.height) if cap else None)
    raw: list[RawElement] = []
    hidden: list[Rect] = []
    for i, w in enumerate(snap.windows):
        denied = rules.denied_app(w.app, w.title) is not None
        if denied:
            hidden.append(rect_to_image(frame, w.bounds, Space.DESKTOP_POINTS))
        title = HIDDEN_LABEL if denied else w.title
        raw.append(RawElement(index=i, role="window", label=f"{w.app}: {title}", frame=w.bounds,
                              handle=str(w.window_id), actions=("press",)))
    elements = build_elements(raw, frame, Space.DESKTOP_POINTS, rules, ctx.store.allocator(None))
    obs = Observation(id=ctx.store.new_id(), created_at=now(), scope="screen", app=SCREEN_APP,
                      pid=None, window=None, frame=frame, frame_space=Space.DESKTOP_POINTS,
                      elements=elements, marks={}, fingerprint=tree_fingerprint(elements, frame),
                      mode="tree")
    built = Built(obs, None, None)
    if cap is not None:
        try:
            img = capture_mod.mask(capture_mod.canonical(cap, frame), hidden)
        except ValueError as exc:
            raise FacadeError(ErrorCode.BACKEND_ERROR, f"Bad screenshot: {exc}") from exc
        obs.image_hash = dhash(img)
        shown = img
        if inp.mode in ("som", "auto"):
            marks, pages = marks_for(elements, [], inp.page)
            obs.marks = {m.number: m for m in marks}
            obs.mode = "som"
            shown = draw_marks(img, marks)
            built.mark_pages = pages
        else:
            obs.mode = "screenshot"
        encoded = capture_mod.encode(shown)
        obs.screenshot_path = ctx.recorder.save_screenshot(obs.id, encoded.data)
        built.image = encoded
    ctx.store.add(obs)
    ctx.state.write(obs, explicit_observation=True)
    notes = ["-- screen scope: click a window ref to focus it, then observe(app=...) --"]
    return observe_output(obs, built.image, max_elements=inp.max_elements,
                          root_ref=inp.root_ref, mark_pages=built.mark_pages,
                          notes=_notes(built, notes))


def _matches(snapshot: WindowSnapshot, needle: str | None, role: str | None) -> RawElement | None:
    """First raw element matching the wait condition."""
    for el in snapshot.elements:
        if role and normalize_role(el.role, el.subrole) != role:
            continue
        haystack = f"{el.label} {el.value or ''}".casefold()
        if needle and needle not in haystack:
            continue
        return el
    return None


async def wait_for(ctx: FacadeContext, inp: WaitForIn) -> ToolOutput:
    """Poll the window until an element appears (or disappears).

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The observation in which the condition held.

    Raises:
        FacadeError: ``TIMEOUT`` when the condition does not hold in time, plus the usual
            policy, lock, deny-list and backend failures.
    """
    rules = await ctx.begin()
    window = await resolve_window(ctx, rules, inp.app, inp.window_id)
    role = normalize_role(inp.ref_role) if inp.ref_role else None
    needle = inp.text.casefold() if inp.text else None
    start = time.monotonic()
    deadline = start + inp.timeout_ms / 1000
    while True:
        ctx.check_stop()
        snapshot = await read(ctx, window, screenshot=False)
        check_allowed(rules, snapshot.window)
        hit = _matches(snapshot, needle, role)
        if (hit is None) == inp.gone:
            built = await build(ctx, rules, snapshot, mode="tree")
            ctx.state.write(built.obs, explicit_observation=True)
            waited = int((time.monotonic() - start) * 1000)
            out = observe_output(built.obs, None, max_elements=DEFAULT_MAX_ELEMENTS,
                                 notes=[f"wait_for: condition met after {waited} ms"])
            matched = next((el for el in built.obs.elements if hit and el.index == hit.index), None)
            result = WaitResult.model_validate(
                {**out.structured, "waited_ms": waited,
                 "matched": element_out(matched) if matched else None})
            structured: dict[str, object] = result.model_dump(mode="json", exclude_defaults=True)
            structured["ok"] = True
            return ToolOutput(structured, out.text, None)
        if time.monotonic() >= deadline:
            what = inp.text or inp.ref_role
            verb = "disappear" if inp.gone else "appear"
            raise FacadeError(ErrorCode.TIMEOUT,
                              f"'{what}' did not {verb} within {inp.timeout_ms} ms.")
        await anyio.sleep(WAIT_FOR_POLL_S)
