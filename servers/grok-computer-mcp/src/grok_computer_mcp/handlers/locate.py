"""``locate``: external grounding when refs and marks cannot reach the target (goal.md §6.2).

Boundary: I/O orchestration; the network call happens in the configured ``Grounder``. The model
only ever sees the current, policy-checked screenshot of the observed window go out. Candidates
come back in that observation's image space and are remembered on the observation, so the strict
tier (goal.md §6.3) can accept exactly those points in ``click(point=...)``. Below the confidence
threshold the top three candidates are returned with an annotated screenshot.
"""

from __future__ import annotations

from ..errors import ErrorCode, FacadeError
from ..grounding.base import GroundingError
from ..limits import GROUNDING_CONFIDENCE_THRESHOLD, GROUNDING_MAX_CANDIDATES
from ..models.inputs import LocateIn
from ..models.outputs import CandidateOut, LocateResult
from ..observe import capture as capture_mod
from ..observe.som import draw_candidates
from .action_prep import prepare
from .context import FacadeContext, ToolOutput


async def locate(ctx: FacadeContext, inp: LocateIn) -> ToolOutput:
    """Ground a description in the current screenshot of the observation's window.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        Candidates (and an annotated image when not confident).

    Raises:
        FacadeError: ``GROUNDING_UNAVAILABLE`` without a configured model, ``BACKEND_ERROR`` when
            the model call fails, staleness and preamble failures.
    """
    grounder = ctx.grounder
    if grounder is None:
        await ctx.begin(needs_desktop=False)
        raise FacadeError(ErrorCode.GROUNDING_UNAVAILABLE,
                          "No grounding model is configured (GROK_COMPUTER_GROUNDING_URL).")
    prep = await prepare(ctx, inp.observation_id, needs_capture=True)
    snapshot_img = prep.image
    if snapshot_img is None:
        raise FacadeError(ErrorCode.BACKEND_ERROR, "The driver returned no screenshot.")
    encoded = capture_mod.encode(snapshot_img)
    try:
        found = await grounder.locate(encoded.data, encoded.width, encoded.height,
                                      inp.description)
    except GroundingError as exc:
        raise FacadeError(ErrorCode.BACKEND_ERROR, f"Grounding failed: {exc}") from exc
    candidates = found[:GROUNDING_MAX_CANDIDATES]
    prep.obs.grounded_points.extend(c.point for c in candidates)
    confident = bool(candidates) and candidates[0].confidence >= GROUNDING_CONFIDENCE_THRESHOLD
    shown = candidates[:1] if confident else candidates
    image = None
    if not confident and candidates:
        image = capture_mod.encode(draw_candidates(snapshot_img, [c.point for c in shown]))
    result = LocateResult(
        ok=True, observation_id=prep.obs.id, confident=confident, provider=grounder.name,
        image_attached=image is not None,
        candidates=[CandidateOut(point=[round(c.point.x, 1), round(c.point.y, 1)],
                                 confidence=round(c.confidence, 2),
                                 bbox=list(c.bbox.rounded()) if c.bbox else None) for c in shown])
    lines = [f"locate on {prep.obs.id} via {grounder.name}: "
             f"{'confident' if confident else 'not confident'}"]
    lines += [f"{n}. point ({c.point[0]:g}, {c.point[1]:g}) confidence {c.confidence:.2f}"
              for n, c in enumerate(result.candidates, start=1)]
    lines.append("-- click(point={x, y}, observation_id) with a candidate, or give up --"
                 if candidates else "-- nothing found; try a different description --")
    return ToolOutput(result.model_dump(mode="json", exclude_none=True), "\n".join(lines), image)

