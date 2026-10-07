"""After an action: settle, re-observe, diff, and report without an image (goal.md §5.4, §5.9).

Boundary: I/O orchestration. The action result names the new observation the model should act on
next and lists what changed (<= 2 KB); a screenshot is only taken when the model asks for one.
"""

from __future__ import annotations

import json

from ..backend.base import ActionOutcome
from ..errors import ErrorCode, FacadeError
from ..limits import ACTION_SUMMARY_MAX_BYTES
from ..models.outputs import ActionResult, HitOut
from ..observe.diff import (
    WindowContext,
    cap_changes,
    dialog_changes,
    element_changes,
    summarize,
    text_changes,
    window_changes,
)
from ..session import Observation
from .action_prep import Prepared
from .context import FacadeContext, ToolOutput
from .delivery import settle
from .observing import build, read, resolve_window

NOOP_HINT = (
    "The driver suspects the action did not land. Observe before trying again; do not repeat it "
    "blindly.")
ESCALATION_HINTS = {
    "px": "If nothing changed, observe(mode='screenshot') and click the element by point.",
    "page": "This is web content: use the browser tools for actions inside the page.",
    "foreground": "If nothing changed, bring the app to the front with apps(action='focus').",
}
# Room kept in the 2 KB action budget for everything except the change list.
RESULT_OVERHEAD_BYTES = 600


def _context(obs: Observation) -> WindowContext:
    """Window identity of an observation."""
    wid = obs.window.window_id if obs.window else None
    return WindowContext(obs.app, wid, obs.window_title)


def _hint(outcome: ActionOutcome) -> str | None:
    """Next-step hint from the driver's verification."""
    if outcome.effect == "suspected_noop":
        return NOOP_HINT
    return ESCALATION_HINTS.get(outcome.escalation or "")


async def finish(ctx: FacadeContext, prep: Prepared, *, action: str, target: str,
                 outcome: ActionOutcome, delivery: str, hit: HitOut | None = None) -> ToolOutput:
    """Re-observe after an action and build its result.

    Args:
        ctx: Facade context.
        prep: The prepared context the action ran in.
        action: Past-tense verb for the summary.
        target: Target description (labels only).
        outcome: Driver outcome.
        delivery: Delivery label from ``deliver``.
        hit: What a coordinate click hit (echoed so the model can tell a miss).

    Returns:
        The action result (no image).

    Raises:
        FacadeError: ``STALE_OBSERVATION`` when the window closed and no other window can be
            observed (the action itself ran).
    """
    try:
        snapshot = await settle(ctx, prep.window)
    except FacadeError as err:
        if err.code is not ErrorCode.STALE_OBSERVATION:
            raise
        window = await resolve_window(ctx, prep.rules, None, None)
        snapshot = await read(ctx, window, screenshot=False)
    built = await build(ctx, prep.rules, snapshot, mode="tree")
    after = built.obs
    changes = window_changes(_context(prep.obs), _context(after))
    changes += element_changes(prep.obs.elements, after.elements)
    changes += text_changes(prep.obs.elements, after.elements)
    changes += dialog_changes(prep.obs.elements, after.elements)
    capped = cap_changes(changes, ACTION_SUMMARY_MAX_BYTES, reserve=RESULT_OVERHEAD_BYTES)
    summary = summarize(action, target, capped)
    ctx.state.write(after)
    result = ActionResult(ok=True, observation_id=after.id, app=after.app, summary=summary,
                          changes=capped, delivery=delivery, effect=outcome.effect, hit=hit,
                          hint=_hint(outcome))
    structured: dict[str, object] = result.model_dump(mode="json", exclude_none=True)
    lines = [summary, f"observation_id: {after.id}  delivery: {delivery}  effect: {outcome.effect}"]
    lines += [json.dumps(c, ensure_ascii=False) for c in capped]
    if result.hint:
        lines.append(f"hint: {result.hint}")
    return ToolOutput(structured, "\n".join(lines))
