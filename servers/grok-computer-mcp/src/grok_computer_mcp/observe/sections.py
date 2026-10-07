"""Container grouping, modal scope, and scroll hints (refactor FR-2, FR-4, FR-6).

Boundary: pure functions over an observation's element list. No I/O. The renderer turns the
rows into text; handlers only choose what to ask for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..geometry import Rect
from ..limits import MIN_VISIBLE_EXTENT_PX
from .elements import ObservedElement
from .roles import CONTAINER_ROLES, SCROLL_ROLES, TEXT_DATA_ROLES
from .textsel import text_body
from .tree import subtree

Kind = Literal["element", "container", "text"]


@dataclass(frozen=True)
class Planned:
    """One row the renderer prints."""

    indent: int
    element: ObservedElement
    kind: Kind
    scroll: tuple[int, int] | None = None


def modal_root(elements: Sequence[ObservedElement]) -> ObservedElement | None:
    """The topmost visible dialog, if any.

    Args:
        elements: Observation elements in document order.

    Returns:
        The last visible ``dialog`` (an ``AXSheet`` normalizes to that role), or None.
        Several dialogs use the last one. A non-modal panel classified as ``dialog`` only
        narrows the list; it does not block actions.
    """
    dialogs = [el for el in elements if el.role == "dialog" and el.visible]
    return dialogs[-1] if dialogs else None


def window_root_ref(elements: Sequence[ObservedElement]) -> str | None:
    """Ref of the window element, used to expand past a modal.

    Args:
        elements: Observation elements.

    Returns:
        The first parentless element's ref, or None when the tree is empty.
    """
    for el in elements:
        if el.parent_ref is None:
            return el.ref
    return None


def hidden_background(elements: Sequence[ObservedElement], modal: ObservedElement) -> int:
    """How many elements a modal listing hides.

    Args:
        elements: The whole window.
        modal: The dialog whose subtree stays visible.

    Returns:
        The count of elements outside that subtree.
    """
    inside = {el.ref for el in subtree(elements, modal.ref)}
    return sum(1 for el in elements if el.ref not in inside)


def modal_footnote(modal: ObservedElement, hidden: int, root_ref: str | None) -> str:
    """Footer telling the model how to list the whole window.

    Args:
        modal: The dialog currently scoping the list.
        hidden: Elements outside that dialog.
        root_ref: Window ref to pass as ``root_ref`` (None when unknown).

    Returns:
        One footer line.
    """
    expand = f"observe(root_ref={root_ref})" if root_ref else "observe(root_ref=<window>)"
    return (f"-- modal dialog {modal.ref} open: {hidden} background elements hidden; "
            f"{expand} lists the whole window --")


def _named_ancestors(el: ObservedElement,
                     by_ref: dict[str, ObservedElement]) -> list[ObservedElement]:
    """Named containers from the outermost ancestor down to the nearest one."""
    chain: list[ObservedElement] = []
    parent_ref = el.parent_ref
    while parent_ref:
        parent = by_ref.get(parent_ref)
        if parent is None:
            break
        if parent.role in CONTAINER_ROLES and parent.label.strip():
            chain.append(parent)
        parent_ref = parent.parent_ref
    chain.reverse()
    return chain


def layout_rows(items: Sequence[ObservedElement], elements: Sequence[ObservedElement],
                hints: dict[str, tuple[int, int]]) -> list[Planned]:
    """Group ``items`` under the nearest named container.

    Args:
        items: Interactive rows and selected text lines, any order.
        elements: The whole observation (parent links).
        hints: Scroll counts keyed by container ref.

    Returns:
        Rows in document order. A container with no item under it is omitted. Indent is the
        number of open containers (the renderer caps it).
    """
    by_ref = {el.ref: el for el in elements}
    rows: list[Planned] = []
    open_refs: list[str] = []
    for el in sorted(items, key=lambda item: item.index):
        chain = _named_ancestors(el, by_ref)
        chain_refs = [container.ref for container in chain]
        common = 0
        while (common < len(open_refs) and common < len(chain_refs)
               and open_refs[common] == chain_refs[common]):
            common += 1
        open_refs = open_refs[:common]
        for container in chain[common:]:
            rows.append(Planned(len(open_refs), container, "container", hints.get(container.ref)))
            open_refs.append(container.ref)
        kind: Kind = "text" if el.role in TEXT_DATA_ROLES and not el.interactive else "element"
        rows.append(Planned(len(open_refs), el, kind, None))
    return rows


def _port(el: ObservedElement, by_ref: dict[str, ObservedElement], image: Rect) -> Rect:
    """Nearest ``scrollarea`` ancestor's box, or the image rectangle."""
    parent_ref = el.parent_ref
    while parent_ref:
        parent = by_ref.get(parent_ref)
        if parent is None:
            break
        if parent.role == "scrollarea" and parent.bbox is not None:
            return parent.bbox
        parent_ref = parent.parent_ref
    return image


def _side(box: Rect, port: Rect) -> str | None:
    """``above`` or ``below`` when a box is clipped or is a 1 px virtual row."""
    virtual = box.h < MIN_VISIBLE_EXTENT_PX or box.w < MIN_VISIBLE_EXTENT_PX
    if box.bottom <= port.y:
        return "above"
    if box.y >= port.bottom:
        return "below"
    if not virtual:
        return None
    midpoint = box.y + box.h / 2
    return "above" if midpoint < port.y + port.h / 2 else "below"


def _scroll_countable(el: ObservedElement) -> bool:
    """A descendant that would be listed: a control, or a non-empty static line."""
    if el.interactive or el.secure:
        return True
    return el.role in TEXT_DATA_ROLES and bool(text_body(el))


def scroll_hints(elements: Sequence[ObservedElement], image: Rect) -> dict[str, tuple[int, int]]:
    """Clipped-row counts for each scroll container.

    Args:
        elements: Observation elements.
        image: Image rectangle (origin top-left). Used when a container has no ``scrollarea``
            ancestor.

    Returns:
        ``{ref: (above, below)}`` for containers where either count is non-zero. ``above`` /
        ``below`` count listable descendants whose box lies wholly outside the scrollport, plus
        1 px virtual rows.
    """
    by_ref = {el.ref: el for el in elements}
    hints: dict[str, tuple[int, int]] = {}
    for el in elements:
        if el.role not in SCROLL_ROLES:
            continue
        port = _port(el, by_ref, image)
        above = 0
        below = 0
        for desc in subtree(elements, el.ref)[1:]:
            if not _scroll_countable(desc) or desc.bbox is None:
                continue
            side = _side(desc.bbox, port)
            if side == "above":
                above += 1
            elif side == "below":
                below += 1
        if above or below:
            hints[el.ref] = (above, below)
    return hints
