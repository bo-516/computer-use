"""Before an action: validate that the observation still describes the screen (goal.md §5.6.4).

Boundary: I/O orchestration shared by every action: fail-closed preamble, observation lookup, deny
list, a fresh read, and the staleness checks (window size, layout fingerprint, screenshot hash for
coordinate and mark actions). Targeting is ``targeting.py``; delivery and settling ``delivery.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..backend.base import WindowInfo
from ..errors import ErrorCode, FacadeError
from ..geometry import Frame
from ..limits import DHASH_STALE_BITS
from ..observe import capture as capture_mod
from ..observe.elements import ObservedElement
from ..observe.fingerprint import dhash, hamming, tree_fingerprint
from ..observe.tree import build_elements
from ..safety.policy import SafetyRules
from ..session import Observation
from .context import FacadeContext
from .observing import check_allowed, frame_of, read


@dataclass
class Prepared:
    """A validated action context: the observation used and the screen as it is now."""

    obs: Observation
    rules: SafetyRules
    window: WindowInfo
    frame: Frame
    elements: list[ObservedElement]
    capture_id: str | None
    image: Image.Image | None = None

    @property
    def by_ref(self) -> dict[str, ObservedElement]:
        """Current elements by ref."""
        return {el.ref: el for el in self.elements}


def stale(obs: Observation, why: str) -> FacadeError:
    """A ``STALE_OBSERVATION`` error naming the observation."""
    return FacadeError(ErrorCode.STALE_OBSERVATION, f"{why} since {obs.id}.")


async def prepare(ctx: FacadeContext, obs_id: str, *, needs_capture: bool) -> Prepared:
    """Validate that ``obs_id`` still describes the screen and read the current state.

    Args:
        ctx: Facade context.
        obs_id: The observation the model acts on.
        needs_capture: Read a fresh screenshot (coordinate and mark actions: the driver needs a
            current capture context and the screenshot hash is compared).

    Returns:
        The prepared context.

    Raises:
        FacadeError: ``STALE_OBSERVATION`` (unknown id, resized window, changed layout or
            screenshot), ``APP_DENIED``, ``INVALID_ARGUMENT`` for screen-scope observations, and
            preamble failures.
    """
    rules = await ctx.begin()
    obs = ctx.store.get(obs_id)
    if obs is None:
        raise FacadeError(ErrorCode.STALE_OBSERVATION, f"{obs_id} is unknown or expired.")
    if obs.window is None:
        raise FacadeError(ErrorCode.INVALID_ARGUMENT,
                          "Screen-scope observations only support click on a window ref. "
                          "Call observe(app=...) to act inside a window.")
    check_allowed(rules, obs.window)
    snapshot = await read(ctx, obs.window, screenshot=needs_capture)
    check_allowed(rules, snapshot.window)
    frame, space = frame_of(ctx, snapshot, ctx.capture_sizes.get(snapshot.window.window_id))
    if snapshot.capture is not None:
        ctx.capture_sizes[snapshot.window.window_id] = (snapshot.capture.width,
                                                        snapshot.capture.height)
    if (frame.image_w, frame.image_h) != (obs.frame.image_w, obs.frame.image_h):
        raise stale(obs, "The window was resized")
    elements = build_elements(snapshot.elements, frame, space, rules,
                              ctx.store.allocator(snapshot.window))
    if tree_fingerprint(elements, frame) != obs.fingerprint:
        raise stale(obs, "The screen changed")
    image: Image.Image | None = None
    if needs_capture and snapshot.capture is not None:
        try:
            image = capture_mod.canonical(snapshot.capture, frame)
        except ValueError as exc:
            raise FacadeError(ErrorCode.BACKEND_ERROR, f"Bad screenshot: {exc}") from exc
        if obs.image_hash is not None and hamming(dhash(image), obs.image_hash) > DHASH_STALE_BITS:
            raise stale(obs, "The screenshot changed")
    capture_id = snapshot.capture.capture_id if snapshot.capture else obs.capture_id
    return Prepared(obs, rules, snapshot.window, frame, elements, capture_id, image)
