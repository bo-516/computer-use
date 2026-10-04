"""Assemble observation results: compact text plus structured content, within one budget.

Boundary: pure. V15: it is unverified whether the host also feeds ``structuredContent`` to the
model; the facade therefore keeps text *and* structured JSON together under the 16 KB tool-text
budget (AGENTS.md), shrinking the element list until both fit. Once V15 is settled the structured
side can be budgeted separately.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..errors import ErrorCode, FacadeError
from ..limits import TOOL_TEXT_MAX_BYTES
from ..models.outputs import ElementOut, ImageOut, MarkOut, ObserveResult, WindowOut
from ..observe.capture import EncodedImage
from ..observe.elements import ObservedElement
from ..observe.render import render
from ..observe.tree import subtree
from ..session import Observation
from .context import ToolOutput
from .observing import listable, mark_list, marks_of

# Each shrink step keeps this share of the elements listed in the previous attempt.
SHRINK_FACTOR = 0.85
MIN_LISTED = 5


def element_out(el: ObservedElement) -> ElementOut:
    """Structured form of an element.

    Args:
        el: Observation element.

    Returns:
        The output model (secure fields never carry a value).
    """
    return ElementOut(ref=el.ref, role=el.role, label=el.label,
                      value=None if el.secure else el.value,
                      bbox=list(el.bbox.rounded()) if el.bbox else None, secure=el.secure,
                      enabled=el.enabled, focused=el.focused, visible=el.visible)


def header(obs: Observation, attached: bool) -> str:
    """First line of an observation text.

    Args:
        obs: Observation.
        attached: Whether an image is attached to this result.

    Returns:
        ``obs_x  app=..  window=.. "title"  image=WxH (attached|not attached)  mode=..``.
    """
    window = f'window={obs.window.window_id} "{obs.window_title}"' if obs.window else "scope=screen"
    image = f"image={obs.frame.image_w}x{obs.frame.image_h} " \
            f"({'attached' if attached else 'not attached'})"
    return f"{obs.id}  app={obs.app}  {window}  {image}  mode={obs.mode}"


def observe_output(obs: Observation, image: EncodedImage | None, *, max_elements: int,
                   root_ref: str | None = None, mark_pages: int = 1,
                   notes: Sequence[str] = ()) -> ToolOutput:
    """Build the result of an observation (observe, wait_for, post-screenshot).

    Args:
        obs: The observation.
        image: Image to attach (None for tree observations).
        max_elements: Line cap requested by the caller.
        root_ref: Expand this container instead of listing interactive elements.
        mark_pages: Number of Set-of-Mark pages.
        notes: Extra lines shown under the header (degraded tree, screenshot path ...).

    Returns:
        Text, structured content and the optional image.

    Raises:
        FacadeError: ``ELEMENT_NOT_FOUND`` for an unknown ``root_ref``.
    """
    if root_ref is not None:
        candidates = subtree(obs.elements, root_ref)
        if not candidates:
            raise FacadeError(ErrorCode.ELEMENT_NOT_FOUND,
                              f"{root_ref} is not part of {obs.id}.")
        indent_from: int | None = candidates[0].depth
    else:
        candidates, indent_from = listable(obs.elements), None
    head = "\n".join([header(obs, image is not None), *notes])
    limit = max_elements
    while True:
        rendered = render(head, candidates, obs.elements, max_elements=limit,
                          indent_from=indent_from, marks=marks_of(obs))
        listed = set(rendered.listed)
        result = ObserveResult(
            ok=True, observation_id=obs.id, app=obs.app, scope=obs.scope,
            mode=obs.mode,
            window=WindowOut(window_id=obs.window.window_id, title=obs.window_title,
                             app=obs.app) if obs.window else None,
            image=ImageOut(width=obs.frame.image_w, height=obs.frame.image_h,
                           attached=image is not None, screenshot_path=obs.screenshot_path),
            elements=[element_out(el) for el in candidates if el.ref in listed],
            marks=[MarkOut(mark=m.number, ref=m.ref, role=m.role, label=m.label,
                           bbox=list(m.bbox.rounded())) for m in mark_list(obs)],
            mark_pages=mark_pages, omitted=rendered.omitted, degraded=obs.degraded)
        structured = result.model_dump(mode="json", exclude_defaults=True)
        structured.update(ok=True, observation_id=obs.id)
        total = len(rendered.text.encode()) + len(json.dumps(structured).encode())
        if total <= TOOL_TEXT_MAX_BYTES or limit <= MIN_LISTED:
            return ToolOutput(structured, rendered.text, image)
        limit = max(MIN_LISTED, int(min(limit, len(listed)) * SHRINK_FACTOR))
