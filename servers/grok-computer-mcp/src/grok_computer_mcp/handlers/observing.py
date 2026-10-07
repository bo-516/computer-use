"""Building observations: window resolution, frames, elements, images, marks and the result.

Boundary: shared by the observe, wait_for, action and locate handlers. Talks to the backend only
through ``Backend``; all coordinate math goes through ``coords`` (AGENTS.md). Window resolution
and the deny-list check before capture live in ``windows.py`` (re-exported here).
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..backend.base import BackendError, Capture, WindowSnapshot
from ..coords import Space, detect_frame_space, make_frame, rect_to_image
from ..errors import ErrorCode, FacadeError
from ..geometry import Frame
from ..observe import capture as capture_mod
from ..observe.elements import ObservedElement
from ..observe.fingerprint import dhash, tree_fingerprint
from ..observe.som import Mark, Region, draw_marks, marks_for
from ..observe.textsel import mark_credential_text
from ..observe.tree import build_elements
from ..safety.policy import SafetyRules
from ..session import Mode, Observation, now
from .context import FacadeContext
from .windows import check_allowed, read, resolve_window

__all__ = ["Built", "build", "check_allowed", "frame_of", "listable", "mark_list",
           "marks_of", "read", "resolve_window"]

_SETTING_SPACES = {"screen_points": Space.DESKTOP_POINTS, "window_points": Space.WINDOW_POINTS,
                   "capture_pixels": Space.CAPTURE_PIXELS}

@dataclass
class Built:
    """An observation plus the image produced for it (if any)."""

    obs: Observation
    image: capture_mod.EncodedImage | None
    canonical: Image.Image | None
    mark_pages: int = 1


def frame_of(ctx: FacadeContext, snapshot: WindowSnapshot,
             last_capture: tuple[int, int] | None) -> tuple[Frame, Space]:
    """Frame and element-frame space of a snapshot.

    Args:
        ctx: Facade context (long edge, frame-space setting).
        snapshot: The snapshot.
        last_capture: Size of the previous capture of this window, for tree-only snapshots whose
            backend reports frames or actions in capture pixels.

    Returns:
        ``(frame, frame_space)``.
    """
    cap = snapshot.capture
    capture_size = (cap.width, cap.height) if cap else last_capture
    frame = make_frame(snapshot.window.bounds, snapshot.display, ctx.settings.long_edge,
                       capture_size)
    if snapshot.frame_space is not None:
        return frame, snapshot.frame_space
    setting = ctx.settings.cua_frame_space
    if setting in _SETTING_SPACES:
        return frame, _SETTING_SPACES[setting]
    # V14: frame space undocumented upstream; detect it from where the frames land.
    rects = [el.frame for el in snapshot.elements if el.frame is not None]
    return frame, detect_frame_space(rects, frame)


async def build(ctx: FacadeContext, rules: SafetyRules, snapshot: WindowSnapshot, *,
                mode: Mode, page: int = 1) -> Built:
    """Turn a snapshot into a stored observation (and its image when captured).

    Args:
        ctx: Facade context.
        rules: Safety rules.
        snapshot: Backend snapshot (with a capture for screenshot/som modes).
        mode: ``tree``, ``screenshot`` or ``som``.
        page: Set-of-Mark page.

    Returns:
        The built observation.

    Raises:
        FacadeError: ``BACKEND_ERROR`` when the capture cannot be decoded or encoded.
    """
    window = snapshot.window
    key_sizes = ctx.capture_sizes
    frame, space = frame_of(ctx, snapshot, key_sizes.get(window.window_id))
    if snapshot.capture is not None:
        key_sizes[window.window_id] = (snapshot.capture.width, snapshot.capture.height)
    elements = mark_credential_text(
        build_elements(snapshot.elements, frame, space, rules, ctx.store.allocator(window)),
        rules)
    obs = Observation(
        id=ctx.store.new_id(), created_at=now(), scope="window", app=window.app, pid=window.pid,
        window=window, frame=frame, frame_space=space, elements=elements, marks={},
        fingerprint=tree_fingerprint(elements, frame), degraded=snapshot.degraded, mode=mode,
        capture_id=snapshot.capture.capture_id if snapshot.capture else None)
    built = Built(obs, None, None)
    if snapshot.capture is not None:
        built = await _with_image(ctx, snapshot, snapshot.capture, obs, mode=mode, page=page)
    ctx.store.add(obs)
    return built


async def _with_image(ctx: FacadeContext, snapshot: WindowSnapshot, cap: Capture,
                      obs: Observation, *, mode: Mode, page: int) -> Built:
    """Decode, mark and encode the capture of an observation."""
    try:
        img = capture_mod.canonical(cap, obs.frame)
    except ValueError as exc:
        raise FacadeError(ErrorCode.BACKEND_ERROR,
                          f"Bad screenshot from the driver: {exc}") from exc
    obs.image_hash = dhash(img)
    pages = 1
    shown = img
    if mode == "som":
        regions: list[Region] = []
        if ctx.settings.som_regions:
            try:
                raw = await ctx.backend.regions(snapshot)
            except BackendError:
                raw = []
            regions = [Region(rect_to_image(obs.frame, r.frame, Space.CAPTURE_PIXELS), r.kind,
                              r.text) for r in raw]
        marks, pages = marks_for(obs.elements, regions, page)
        obs.marks = {m.number: m for m in marks}
        shown = draw_marks(img, marks)
    try:
        encoded = capture_mod.encode(shown)
    except ValueError as exc:
        raise FacadeError(ErrorCode.BACKEND_ERROR, str(exc)) from exc
    obs.screenshot_path = ctx.recorder.save_screenshot(obs.id, encoded.data)
    return Built(obs, encoded, img, pages)


def marks_of(obs: Observation) -> dict[str, int]:
    """Mark number of each ref on the current mark page.

    Args:
        obs: Observation.

    Returns:
        ``{ref: mark}`` for accessibility-backed marks.
    """
    return {m.ref: n for n, m in obs.marks.items() if m.ref}


def mark_list(obs: Observation) -> list[Mark]:
    """Marks in number order."""
    return [obs.marks[n] for n in sorted(obs.marks)]


def listable(elements: list[ObservedElement]) -> list[ObservedElement]:
    """Elements listed by default: interactive ones and secure fields.

    Args:
        elements: All observation elements.

    Returns:
        The elements to list, in document order.
    """
    return [el for el in elements if el.interactive or el.secure]
