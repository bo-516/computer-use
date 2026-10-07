"""``observe`` (goal.md §5.4, §5.5, §5.7).

Boundary: I/O orchestration over the backend. ``observe(mode="auto")`` stays on the tree when the
counted visible controls are three or more, or one or two with at least one static text line.
The count is the visible dialog's subtree when one is open. A poor-accessibility app, an
incomplete tree, zero controls, or a thin tree with no text still captures a Set-of-Mark
screenshot. ``scope="screen"`` lists the visible windows over a display screenshot in which
deny-listed windows are masked. ``wait_for`` lives in ``handlers/wait.py``.
"""

from __future__ import annotations

from ..backend.base import BackendError, RawElement
from ..coords import Space, make_frame, rect_to_image
from ..errors import ErrorCode, FacadeError
from ..geometry import Rect
from ..limits import AUTO_MODE_MIN_INTERACTIVE
from ..models.inputs import ObserveIn
from ..observe import capture as capture_mod
from ..observe.elements import ObservedElement
from ..observe.fingerprint import dhash, tree_fingerprint
from ..observe.sections import modal_root
from ..observe.som import draw_marks, marks_for
from ..observe.textsel import text_candidates
from ..observe.tree import build_elements, subtree
from ..safety.policy import SafetyRules
from ..session import Observation, now
from .context import FacadeContext, ToolOutput, map_backend_error
from .observing import Built, build, check_allowed, read, resolve_window
from .results import observe_output

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


def _auto_thin(elements: list[ObservedElement], app: str, rules: SafetyRules,
               degraded: str | None) -> tuple[bool, int]:
    """Whether auto mode should attach a Set-of-Mark image, and how many controls counted.

    Args:
        elements: The tree-mode observation.
        app: Front app name (poor-accessibility list).
        rules: Safety rules.
        degraded: Backend incomplete-tree reason, or None.

    Returns:
        ``(thin, interactive)``. ``interactive`` is the visible dialog subtree when a dialog
        is open, otherwise the whole window. Thin when that count is 0, when it is under 3
        and there is no static text line, when the app is poor-AX, or when the tree is
        incomplete. One or two controls plus a text line stay on the tree.
    """
    modal = modal_root(elements)
    scope_ref = modal.ref if modal else None
    scope = subtree(elements, scope_ref) if scope_ref else elements
    interactive = sum(1 for el in scope if el.interactive and el.visible)
    has_text = bool(text_candidates(elements, scope_ref=scope_ref, include_offscreen=False))
    poor = rules.poor_ax(app) or degraded is not None
    sparse = interactive < AUTO_MODE_MIN_INTERACTIVE and not has_text
    return poor or interactive == 0 or sparse, interactive


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
        thin, interactive = _auto_thin(built.obs.elements, window.app, rules, snapshot.degraded)
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
