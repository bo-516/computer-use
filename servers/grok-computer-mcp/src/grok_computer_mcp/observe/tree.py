"""Turn a backend tree into observation elements (goal.md §5.5).

Boundary: pure. Input: the backend's raw elements, the observation frame and the space the backend
reports frames in. Output: ``ObservedElement`` records with stable refs (``refs.py``), IMAGE-space
boxes, and the classification the renderer, the diff, the state file and the action handlers use:
interactive (listed by default), visible (on the image, not a virtualized 1 px row), secure
(password role, or a text input whose label names a credential) and in_form (Enter may submit).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..backend.base import RawElement
from ..coords import Space, rect_to_image
from ..geometry import Frame
from ..limits import LABEL_MAX_CHARS, MIN_VISIBLE_EXTENT_PX
from ..safety.policy import SafetyRules
from .elements import ObservedElement
from .refs import RefAllocator, assign_refs
from .roles import FORM_INPUT_ROLES, TEXT_ENTRY_ROLES, is_interactive, normalize_role


def clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut to ``limit`` characters with an ellipsis.

    Args:
        text: Any text.
        limit: Maximum characters.

    Returns:
        The clipped text.
    """
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_elements(raw: Sequence[RawElement], frame: Frame, frame_space: Space,
                   rules: SafetyRules, refs: RefAllocator) -> list[ObservedElement]:
    """Convert raw backend elements into observation elements.

    Args:
        raw: Elements of one snapshot, in document (depth-first) order.
        frame: Observation frame (defines IMAGE space).
        frame_space: Space the backend reported ``frame`` rectangles in.
        rules: Safety rules (credential words make a text input secure).
        refs: Ref memory of this window; updated in place.

    Returns:
        Elements in the same order. Secure elements never carry a value.
    """
    roles = [normalize_role(el.role, el.subrole) for el in raw]
    boxes = [rect_to_image(frame, el.frame, frame_space) if el.frame else None for el in raw]
    ref_list = assign_refs(raw, roles, boxes, refs)
    by_index = {el.index: pos for pos, el in enumerate(raw)}
    image = frame.image_rect
    out: list[ObservedElement] = []
    for pos, el in enumerate(raw):
        role, box = roles[pos], boxes[pos]
        visible = (box is not None and box.w >= MIN_VISIBLE_EXTENT_PX
                   and box.h >= MIN_VISIBLE_EXTENT_PX and image.intersection(box) is not None)
        label = clip(el.label, LABEL_MAX_CHARS)
        secure = role == "securefield" or (
            role in TEXT_ENTRY_ROLES and rules.credential_word(label) is not None)
        parent_pos = by_index.get(el.parent) if el.parent is not None else None
        out.append(ObservedElement(
            ref=ref_list[pos], index=el.index, handle=el.handle, role=role, raw_role=el.role,
            label=label, value=None if secure else el.value, bbox=box,
            interactive=is_interactive(role, el.actions), visible=visible, secure=secure,
            in_form=role in FORM_INPUT_ROLES, enabled=el.enabled, focused=el.focused,
            selected=el.selected,
            parent_ref=ref_list[parent_pos] if parent_pos is not None else None, depth=el.depth,
        ))
    refs.previous = out
    return out


def subtree(elements: Sequence[ObservedElement], root_ref: str) -> list[ObservedElement]:
    """Elements under ``root_ref`` (inclusive), in document order.

    Args:
        elements: All elements of an observation.
        root_ref: Ref of the subtree root.

    Returns:
        The root and its descendants; empty when the ref is unknown.
    """
    inside: set[str] = set()
    out: list[ObservedElement] = []
    for el in elements:
        if el.ref == root_ref or (el.parent_ref is not None and el.parent_ref in inside):
            inside.add(el.ref)
            out.append(el)
    return out


def focused_element(elements: Sequence[ObservedElement]) -> ObservedElement | None:
    """The focused element, preferring interactive ones.

    Args:
        elements: Observation elements.

    Returns:
        The innermost focused element, or None when focus is unknown.
    """
    focused = [el for el in elements if el.focused]
    interactive = [el for el in focused if el.interactive]
    candidates = interactive or focused
    return candidates[-1] if candidates else None
