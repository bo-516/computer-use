"""Normalize accessibility roles from macOS AX, Windows UIA and Linux AT-SPI into one vocabulary.

Boundary: pure lookup tables. The compact observation text, the interactive filter (goal.md §5.5
"只列交互元素"), secure-field detection and the state file all use these normalized names, and
``TEXT_ENTRY_ROLES`` must stay equal to ``hooklib.state.TEXT_ENTRY_ROLES`` (tested).
"""

from __future__ import annotations

import re

# Raw role (lower-cased, "AX" prefix and separators removed) -> normalized role.
_ROLE_MAP: dict[str, str] = {
    # buttons
    "button": "button", "pushbutton": "button", "togglebutton": "button", "splitbutton": "button",
    "popupbutton": "popupbutton", "menubutton": "menubutton",
    # toggles
    "checkbox": "checkbox", "checkmenuitem": "menuitem", "switch": "switch",
    "toggleswitch": "switch", "radiobutton": "radio", "radio": "radio",
    "radiomenuitem": "menuitem", "tab": "tab", "pagetab": "tab", "tabitem": "tab",
    "tabbutton": "tab",
    # text entry
    "textfield": "textfield", "entry": "textfield", "edit": "textfield", "textbox": "textfield",
    "securetextfield": "securefield", "passwordtext": "securefield", "passwordbox": "securefield",
    "securefield": "securefield", "searchfield": "searchfield", "searchbox": "searchfield",
    # UIA "Text" is a static label while AT-SPI "text" is an editable view; labels are far more
    # common, so bare "text" is static and multi-line editors are reached through focus.
    "textarea": "textarea", "textview": "textarea", "document": "textarea",
    "combobox": "combobox", "editablecombobox": "combobox",
    # menus and lists
    "menuitem": "menuitem", "menubaritem": "menuitem", "menu": "menu", "menubar": "menubar",
    "link": "link", "hyperlink": "link", "slider": "slider", "incrementor": "stepper",
    "spinbutton": "stepper", "spinner": "stepper", "cell": "cell", "tablecell": "cell",
    "dataitem": "cell", "row": "row", "tablerow": "row", "outlinerow": "row",
    "listitem": "listitem", "treeitem": "treeitem", "disclosuretriangle": "disclosure",
    "expander": "disclosure", "colorwell": "picker", "datefield": "picker",
    "dateeditor": "picker", "timefield": "picker",
    # containers and static content
    "statictext": "text", "text": "text", "label": "text", "static": "text", "heading": "heading",
    "image": "image", "group": "group", "window": "window", "dialog": "dialog",
    "sheet": "dialog", "toolbar": "toolbar", "scrollarea": "scrollarea", "scrollbar": "scrollbar",
    "table": "table", "list": "list", "outline": "list", "tree": "list", "splitgroup": "group",
    "tabgroup": "tablist", "pagetablist": "tablist", "webarea": "webarea", "browser": "group",
    "valueindicator": "slider", "progressindicator": "progress", "progressbar": "progress",
}
# Subroles that override the role (macOS AX subroles, normalized the same way).
_SUBROLE_MAP: dict[str, str] = {
    "securetextfield": "securefield", "switch": "switch", "tabbutton": "tab",
    "searchfield": "searchfield", "closebutton": "button", "minimizebutton": "button",
    "zoombutton": "button", "togglebutton": "button",
}

INTERACTIVE_ROLES = frozenset((
    "button", "popupbutton", "menubutton", "checkbox", "switch", "radio", "tab", "textfield",
    "securefield", "searchfield", "textarea", "combobox", "menuitem", "link", "slider", "stepper",
    "cell", "row", "listitem", "treeitem", "disclosure", "picker",
))
# Roles that accept typed text. Must equal hooklib.state.TEXT_ENTRY_ROLES.
TEXT_ENTRY_ROLES = ("textfield", "textarea", "securefield", "searchfield", "combobox")
# Text inputs where Enter may submit a form (goal.md §7.3 R2); search fields only search.
FORM_INPUT_ROLES = ("textfield", "textarea", "securefield", "combobox")
TOGGLE_ROLES = ("checkbox", "switch", "radio")
# Named containers the tree view groups interactive rows under (refactor FR-2).
CONTAINER_ROLES = frozenset((
    "group", "toolbar", "tablist", "list", "table", "dialog", "menu", "scrollarea", "webarea",
))
# Static lines quoted in the tree without a ref (refactor FR-3).
TEXT_DATA_ROLES = frozenset(("text", "heading", "progress"))
# Containers that can hide rows above or below a scrollport (refactor FR-6).
SCROLL_ROLES = frozenset(("scrollarea", "list", "table", "webarea"))
# Accessibility actions that make an otherwise generic node clickable (web content groups).
PRESS_ACTIONS = frozenset(("press", "axpress", "click", "invoke", "activate", "pick", "confirm",
                           "open", "showmenu", "axshowmenu"))
_SEPARATORS = re.compile(r"[\s_\-]+")


def _key(raw: str) -> str:
    """Lower-case, drop separators and a leading ``AX``."""
    key = _SEPARATORS.sub("", raw.strip()).lower()
    return key[2:] if key.startswith("ax") and len(key) > 2 else key


def normalize_role(role: str, subrole: str = "") -> str:
    """Map a platform role (and macOS subrole) to the facade vocabulary.

    Args:
        role: Raw role such as ``AXButton``, ``push button`` or ``Edit``.
        subrole: Raw subrole such as ``AXSecureTextField`` ("" when none).

    Returns:
        The normalized role; unknown roles are returned lower-cased without separators.
    """
    sub = _key(subrole) if subrole else ""
    if sub in _SUBROLE_MAP:
        return _SUBROLE_MAP[sub]
    key = _key(role)
    return _ROLE_MAP.get(key, key or "unknown")


def is_interactive(role: str, actions: tuple[str, ...]) -> bool:
    """Whether an element is worth listing as actionable.

    Args:
        role: Normalized role.
        actions: Raw accessibility action names.

    Returns:
        True for interactive roles and for generic nodes that expose a press-like action.
    """
    if role in INTERACTIVE_ROLES:
        return True
    return any(_key(a) in PRESS_ACTIONS for a in actions)
