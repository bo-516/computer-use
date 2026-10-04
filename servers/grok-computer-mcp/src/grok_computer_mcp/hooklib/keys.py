"""Key-chord parsing and canonicalization for the dangerous-key rule (R3) and Enter detection (R2).

Boundary: pure, stdlib only, Python 3.8. The facade validates ``press_keys`` input with the same
parser, so a chord the guard cannot parse is also one the facade refuses.

A chord is ``mod+mod+key`` (``cmd+shift+q``, Playwright's ``Control+Shift+T``). Modifier aliases
collapse to ``ctrl``, ``alt``, ``shift``, ``cmd``, ``win`` and ``fn``. ``meta`` is ambiguous (⌘ on
macOS, the Windows key elsewhere), so it expands to both readings and a chord is dangerous when
either reading is. ``cmd`` and ``win`` are kept apart on purpose: ``cmd+l`` focuses a browser's
address bar on macOS while ``win+l`` locks Windows.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple, cast

MODIFIER_ALIASES: Dict[str, str] = {
    "cmd": "cmd", "command": "cmd", "⌘": "cmd",
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "⌃": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt", "altgr": "alt", "⌥": "alt",
    "shift": "shift", "⇧": "shift",
    "fn": "fn",
    "win": "win", "windows": "win", "super": "win",
}
# Readings of modifiers whose meaning depends on the platform.
AMBIGUOUS_MODIFIERS: Dict[str, Tuple[str, ...]] = {"meta": ("cmd", "win")}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd", "win", "fn")

KEY_ALIASES: Dict[str, str] = {
    "escape": "esc", "return": "enter", "del": "delete", "spacebar": "space",
    "pgup": "pageup", "page_up": "pageup", "pgdn": "pagedown", "page_down": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "ins": "insert", "bksp": "backspace", "caps_lock": "capslock",
}
NAMED_KEYS: FrozenSet[str] = frozenset((
    "enter", "tab", "esc", "space", "backspace", "delete", "insert", "home", "end", "pageup",
    "pagedown", "up", "down", "left", "right", "capslock", "power", "plus", "minus",
    "printscreen", "pause", "menu", "numlock", "scrolllock",
))
_FUNCTION_KEY = re.compile(r"^f([1-9]|1[0-9]|2[0-4])$")
# Upper bound on chords in one press_keys call; longer sequences are typing, not shortcuts.
MAX_CHORDS_PER_CALL = 10


def _canonical_key(token: str) -> Optional[str]:
    """Map one non-modifier token to its canonical name.

    Args:
        token: Casefolded token without spaces.

    Returns:
        The canonical key name, or None when the token is not a known key.
    """
    key = KEY_ALIASES.get(token, token)
    if key in NAMED_KEYS or _FUNCTION_KEY.match(key) or len(key) == 1:
        return key
    return None


def canonical_chords(chord: str) -> Tuple[str, ...]:
    """Parse a chord into its canonical spellings.

    Args:
        chord: A chord such as ``"Cmd + Shift + Q"`` or ``"Control+W"``.

    Returns:
        One canonical string per reading (two when ``meta`` is present), or an empty tuple when
        the chord cannot be parsed (unknown key, two non-modifier keys, empty).
    """
    text = "".join(chord.casefold().split())
    if text.endswith("++"):
        text = text[:-2] + "+plus"
    tokens = text.split("+")
    if not text or any(not tok for tok in tokens):
        return ()
    readings: List[List[str]] = [[]]
    key: Optional[str] = None
    for token in tokens:
        if token in MODIFIER_ALIASES:
            for mods in readings:
                mods.append(MODIFIER_ALIASES[token])
        elif token in AMBIGUOUS_MODIFIERS:
            options = AMBIGUOUS_MODIFIERS[token]
            readings = [[*mods, opt] for mods in readings for opt in options]
        else:
            if key is not None:
                return ()
            key = _canonical_key(token)
            if key is None:
                return ()
    out: List[str] = []
    for mods in readings:
        ordered = [m for m in MODIFIER_ORDER if m in mods]
        out.append("+".join(ordered + ([key] if key else [])))
    return tuple(dict.fromkeys(out))


def chords_of(value: object) -> Optional[List[str]]:
    """Read the ``keys`` argument: one chord string or a list of chord strings.

    Args:
        value: Decoded tool input value.

    Returns:
        The chords in order, or None when the value has the wrong shape or is empty.
    """
    if isinstance(value, str):
        return [value] if value.strip() else None
    if isinstance(value, list):
        items = cast(List[object], value)
        if items and all(isinstance(item, str) and item.strip() for item in items):
            return [str(item) for item in items]
    return None


def dangerous_chord(chord: str, dangerous: Sequence[str]) -> Optional[str]:
    """Return the dangerous-key entry this chord matches, if any.

    Args:
        chord: Chord as typed by the model.
        dangerous: ``Policy.dangerous_keys``.

    Returns:
        The matching policy entry, or None. Unparseable chords return None; callers treat them
        as unknown (guard: ask, facade: invalid argument).
    """
    readings = set(canonical_chords(chord))
    for entry in dangerous:
        if readings & set(canonical_chords(entry)):
            return entry
    return None


def presses_enter(chord: str) -> bool:
    """Whether the chord's key is Enter/Return (a possible form submit, R2).

    Args:
        chord: Chord as typed by the model.

    Returns:
        True when any reading ends in ``enter``.
    """
    return any(r == "enter" or r.endswith("+enter") for r in canonical_chords(chord))


def closes_window(chord: str) -> bool:
    """Whether the chord is a close-window/tab shortcut (R2 when the window has unsaved work).

    Args:
        chord: Chord as typed by the model.

    Returns:
        True for cmd+w, ctrl+w and ctrl+f4.
    """
    return bool(set(canonical_chords(chord)) & {"cmd+w", "ctrl+w", "ctrl+f4"})
