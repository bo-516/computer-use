"""``click``, ``type_text``, ``press_keys``, ``scroll``, ``drag`` (goal.md §5.4).

Boundary: I/O orchestration on top of ``action_prep``/``action_result``. Hard rules enforced here,
fail-closed, regardless of what the guard hook decided (AGENTS.md "every R3 rule is enforced in the
facade"): deny-listed apps (``APP_DENIED``, in ``prepare``), secure fields (``SECURE_FIELD``),
dangerous key combinations (``KEY_DENIED``), and the emergency stop (``USER_INTERRUPT``).
"""

from __future__ import annotations

import sys

from ..backend.base import ActionOutcome, Delivery, ElementTarget, PointTarget
from ..errors import ErrorCode, FacadeError
from ..hooklib import keys
from ..models.inputs import ClickIn, PressKeysIn, TypeTextIn
from ..models.outputs import HitOut
from ..observe.tree import focused_element
from .action_prep import prepare
from .action_result import finish
from .context import FacadeContext, ToolOutput
from .delivery import deliver, desktop_point
from .screen import click_screen_window
from .targeting import (
    describe,
    element_target,
    endpoint,
    hit_record,
    not_interactable,
    point_target,
    resolve_ref,
)

# Keys allowed while a secure field has focus: moving away from it, never typing into it.
SECURE_SAFE_KEYS = frozenset(("tab", "shift+tab", "esc", "up", "down", "left", "right", "home",
                              "end", "pageup", "pagedown"))

async def click(ctx: FacadeContext, inp: ClickIn) -> ToolOutput:
    """Click a ref, a mark or a point.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The action result.

    Raises:
        FacadeError: Staleness, targeting, deny-list, delivery and preamble failures.
    """
    obs = ctx.store.get(inp.observation_id)
    if obs is not None and obs.window is None:
        return await click_screen_window(ctx, obs, inp)
    prep = await prepare(ctx, inp.observation_id,
                         needs_capture=inp.point is not None or inp.mark is not None)
    space = ctx.backend.action_space
    el, p = endpoint(ctx, prep, inp.ref, inp.mark, inp.point)
    hit = hit_record(prep, p, el) if inp.point is not None and p is not None else None
    hit_out = None
    if hit is not None and p is not None:
        hit_out = HitOut(point=[round(p.x, 1), round(p.y, 1)], ref=el.ref if el else None,
                         role=el.role if el else "", label=el.label if el else "")
    target: ElementTarget | PointTarget
    if el is not None and inp.point is None:
        not_interactable(el)
        target = element_target(prep, el, space)
    elif p is not None:
        target = point_target(prep, p, space)
    else:
        raise FacadeError(ErrorCode.INVALID_ARGUMENT, "Nothing to click.")
    ctx.state.write(prep.obs, acting=True, last_hit=hit)
    ctx.check_stop()
    start = Delivery.FOREGROUND if inp.modifiers else Delivery.BACKGROUND
    expected = desktop_point(prep, p) if p is not None else None

    async def act(delivery: Delivery) -> ActionOutcome:
        return await ctx.backend.click(target, button=inp.button, count=inp.count,
                                       modifiers=tuple(inp.modifiers), delivery=delivery)

    outcome, delivery = await deliver(ctx, act, expected=expected, start=start)
    verb = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[inp.count]
    if inp.button != "left":
        verb = f"{inp.button.capitalize()}-clicked"
    return await finish(ctx, prep, action=verb, target=describe(el, p), outcome=outcome,
                        delivery=delivery, hit=hit_out)


async def type_text(ctx: FacadeContext, inp: TypeTextIn) -> ToolOutput:
    """Type into a field (never a secure one).

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The action result (the typed text is never echoed in the summary).

    Raises:
        FacadeError: ``SECURE_FIELD`` for password/credential fields or unknown focus next to one,
            plus staleness, delivery and preamble failures.
    """
    prep = await prepare(ctx, inp.observation_id, needs_capture=False)
    el = resolve_ref(ctx, prep, inp.ref) if inp.ref else focused_element(prep.elements)
    if el is not None and el.secure:
        raise FacadeError(ErrorCode.SECURE_FIELD,
                          f'{el.role} "{el.label}" ({el.ref}) is a secure or credential field.')
    if el is None and any(e.secure for e in prep.elements):
        raise FacadeError(ErrorCode.SECURE_FIELD,
                          "Focus is unknown and this window has a secure field; pass the ref of "
                          "the field to type into.")
    if el is not None:
        not_interactable(el)
    target = element_target(prep, el, ctx.backend.action_space) if inp.ref and el else None
    ctx.state.write(prep.obs, acting=True)
    ctx.check_stop()
    if inp.clear_first:
        await ctx.backend.clear_text(prep.window, target)

    async def act(delivery: Delivery) -> ActionOutcome:
        return await ctx.backend.type_text(prep.window, target, inp.text, delivery=delivery)

    outcome, delivery = await deliver(ctx, act, expected=None)
    return await finish(ctx, prep, action=f"Typed {len(inp.text)} chars into",
                        target=describe(el), outcome=outcome, delivery=delivery)


def _canonical(chord: str, ctx: FacadeContext) -> str:
    """Validate a chord against the policy and pick its platform reading."""
    readings = keys.canonical_chords(chord)
    if not readings:
        raise FacadeError(ErrorCode.INVALID_ARGUMENT, f"Cannot parse key combination '{chord}'.")
    entry = ctx.policy.rules().dangerous_chord(chord)
    if entry:
        raise FacadeError(ErrorCode.KEY_DENIED, f"'{chord}' is not allowed ({entry}).")
    prefer = "cmd" if sys.platform == "darwin" else "win"
    return next((r for r in readings if prefer in r.split("+")), readings[0])


async def press_keys(ctx: FacadeContext, inp: PressKeysIn) -> ToolOutput:
    """Press one chord or a short sequence of chords.

    Args:
        ctx: Facade context.
        inp: Validated arguments.

    Returns:
        The action result.

    Raises:
        FacadeError: ``KEY_DENIED`` for dangerous combinations (checked before anything runs),
            ``SECURE_FIELD`` when a secure field has focus, plus the usual failures.
    """
    chords = [inp.keys] if isinstance(inp.keys, str) else list(inp.keys)
    canon = [_canonical(c, ctx) for c in chords]
    prep = await prepare(ctx, inp.observation_id, needs_capture=False)
    focused = focused_element(prep.elements)
    if focused is not None and focused.secure and any(c not in SECURE_SAFE_KEYS for c in canon):
        raise FacadeError(ErrorCode.SECURE_FIELD,
                          f'Focus is in a secure field ({focused.ref}); only navigation keys are '
                          "allowed there.")
    ctx.state.write(prep.obs, acting=True)
    outcome: ActionOutcome | None = None
    delivery = "background"
    for chord in canon:
        ctx.check_stop()

        async def act(mode: Delivery, chord: str = chord) -> ActionOutcome:
            return await ctx.backend.press_keys(prep.window, chord, delivery=mode)

        outcome, delivery = await deliver(ctx, act, expected=None)
    assert outcome is not None
    return await finish(ctx, prep, action=f"Pressed {' '.join(canon)} in",
                        target=describe(focused), outcome=outcome, delivery=delivery)
